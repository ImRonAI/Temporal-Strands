import errno
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import workspace_files as files


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    stage = tmp_path / "private-staging"
    root.mkdir()
    stage.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    stage_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield root, stage, root_fd, stage_fd
    finally:
        os.close(root_fd)
        os.close(stage_fd)


def test_create_read_list_and_search_same_filesystem(project):
    root, stage, root_fd, stage_fd = project
    (root / "src").mkdir()
    text = "first line\nUnicode: caf\u00e9 \u03bb\nlast line\n"
    created = files.create_project_file(root_fd, stage_fd, "src/main.txt", text)
    expected = hashlib.sha256(text.encode()).hexdigest()
    assert created == {"path": "src/main.txt", "sha256": expected, "byte_size": len(text.encode())}
    assert files.read_project_file(root_fd, "src/main.txt", expected_sha256=expected)["text"] == text
    assert files.list_project_files(root_fd)["entries"] == [
        {"path": "src", "name": "src", "kind": "directory", "byte_size": None}
    ]
    result = files.search_project_files(root_fd, "Unicode")
    assert result["matches"] == [{"path": "src/main.txt", "line": 2, "sha256": expected}]
    assert result["truncated"] is False
    assert result["complete"] is True
    assert list(stage.iterdir()) == []
    assert (root / "src/main.txt").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("path", [
    "../outside", "/absolute", "src/../../outside", "src//name", "./name",
    "src/./name", "name/", "", "a\\b", "C:drive", "a\x00b", "a\nb",
    "/".join(["deep"] * 33), "x" * 4097, "\ud800",
])
def test_invalid_paths_fail_before_file_access(project, path, monkeypatch):
    _, _, root_fd, stage_fd = project
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid path must fail before opening files")
    monkeypatch.setattr(files.os, "open", forbidden)
    with pytest.raises(ValueError):
        files.read_project_file(root_fd, path)
    with pytest.raises(ValueError):
        files.create_project_file(root_fd, stage_fd, path, "data")


@pytest.mark.parametrize("path", [
    ".env", "src/.env.production", "src/.env.d/secret", ".ENV.local",
    ".git/config", "node_modules/pkg/index.js", ".ssh/id_rsa",
    "fair-expanse-493212-h8-138622c839d2.json",
    "src/fair-expanse-493212-h8-138622c839d2.json", ".gwen-upload-hidden",
])
def test_sensitive_paths_are_excluded_by_name_without_opening(project, path, monkeypatch):
    _, _, root_fd, stage_fd = project
    def forbidden(*args, **kwargs):
        pytest.fail("Excluded path must not be opened")
    monkeypatch.setattr(files.os, "open", forbidden)
    with pytest.raises(files.FileUnavailable):
        files.read_project_file(root_fd, path)
    with pytest.raises(files.FileUnavailable):
        files.create_project_file(root_fd, stage_fd, path, "data")


def test_symlink_and_hardlink_escape_denied(project, tmp_path):
    root, _, root_fd, stage_fd = project
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("private")
    (root / "directory-link").symlink_to(outside, target_is_directory=True)
    (root / "file-link").symlink_to(outside / "secret")
    os.link(outside / "secret", root / "hard-link")
    for path in ("directory-link/secret", "file-link", "hard-link"):
        with pytest.raises(files.FileUnavailable):
            files.read_project_file(root_fd, path)
    with pytest.raises(files.FileUnavailable):
        files.create_project_file(root_fd, stage_fd, "directory-link/new", "must not escape")
    with pytest.raises(files.FilePreconditionFailed):
        files.create_project_file(root_fd, stage_fd, "file-link", "must not replace")
    assert files.list_project_files(root_fd)["excluded_entries"] == 3
    assert files.list_project_files(root_fd)["entries"] == []
    assert not (outside / "new").exists()
    assert (outside / "secret").read_text() == "private"


def test_fifo_is_not_opened_as_blocking_stream(project):
    root, _, root_fd, _ = project
    os.mkfifo(root / "pipe")
    with pytest.raises(files.FileUnavailable):
        files.read_project_file(root_fd, "pipe")
    assert files.list_project_files(root_fd)["entries"] == []


def test_missing_and_binary_files_are_not_exposed(project):
    root, _, root_fd, _ = project
    (root / "binary").write_bytes(b"\xff\x00")
    (root / "nul").write_bytes(b"text\x00text")
    for name in ("missing", "binary", "nul"):
        with pytest.raises(files.FileUnavailable) as error:
            files.read_project_file(root_fd, name)
        assert str(root) not in str(error.value)


def test_create_only_precondition_does_not_overwrite(project):
    root, stage, root_fd, stage_fd = project
    (root / "existing").write_text("original")
    with pytest.raises(files.FilePreconditionFailed):
        files.create_project_file(root_fd, stage_fd, "existing", "replacement")
    assert (root / "existing").read_text() == "original"
    assert list(stage.iterdir()) == []
    with pytest.raises(files.FilePreconditionFailed):
        files.read_project_file(root_fd, "existing", expected_sha256="0" * 64)


def test_concurrent_create_has_one_winner(project):
    root, stage, root_fd, stage_fd = project
    barrier = Barrier(2)
    def create(text):
        barrier.wait()
        try:
            files.create_project_file(root_fd, stage_fd, "race", text)
            return text
        except files.FilePreconditionFailed:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ["one", "two"]))
    assert results.count(None) == 1
    assert (root / "race").read_text() == next(result for result in results if result)
    assert list(stage.iterdir()) == []


def test_file_and_directory_budgets(project, monkeypatch):
    root, _, root_fd, stage_fd = project
    monkeypatch.setattr(files, "WORKSPACE_FILE_MAX_BYTES", 4)
    (root / "large").write_text("large")
    with pytest.raises(files.FileLimitExceeded):
        files.read_project_file(root_fd, "large")
    with pytest.raises(files.FileLimitExceeded):
        files.create_project_file(root_fd, stage_fd, "new", "large")
    assert not (root / "new").exists()
    monkeypatch.setattr(files, "WORKSPACE_DIRECTORY_MAX_ENTRIES", 1)
    (root / "second").touch()
    with pytest.raises(files.FileLimitExceeded):
        files.list_project_files(root_fd)


def test_read_detects_inode_swap_after_open(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "file").write_text("old")
    (root / "replacement").write_text("new")
    original = files.os.open
    def swapped(path, *args, **kwargs):
        descriptor = original(path, *args, **kwargs)
        if path == "file":
            os.replace(root / "replacement", root / "file")
        return descriptor
    monkeypatch.setattr(files.os, "open", swapped)
    with pytest.raises(files.FilePreconditionFailed):
        files.read_project_file(root_fd, "file")


def test_parent_symlink_swap_does_not_redirect_create(project, tmp_path, monkeypatch):
    root, _, root_fd, stage_fd = project
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sub").mkdir()
    original = files.os.link
    def swap(*args, **kwargs):
        (root / "sub").rename(root / "moved")
        (root / "sub").symlink_to(outside, target_is_directory=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(files.os, "link", swap)
    with pytest.raises(files.FileOutcomeUnknown):
        files.create_project_file(root_fd, stage_fd, "sub/new", "content")
    assert not (outside / "new").exists()
    assert (root / "moved/new").read_text() == "content"


def test_publish_failure_is_unknown_and_never_deletes_destination(project, monkeypatch):
    root, stage, root_fd, stage_fd = project
    original = files.os.fsync
    calls = 0
    def fail_parent(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "simulated sync error")
        return original(descriptor)
    monkeypatch.setattr(files.os, "fsync", fail_parent)
    with pytest.raises(files.FileOutcomeUnknown):
        files.create_project_file(root_fd, stage_fd, "published", "data")
    assert (root / "published").read_text() == "data"
    assert list(stage.iterdir()) == []


def test_disk_full_before_publish_is_bounded_failure(project, monkeypatch):
    root, stage, root_fd, stage_fd = project
    def fail(descriptor):
        raise OSError(errno.ENOSPC, "simulated disk full")
    monkeypatch.setattr(files.os, "fsync", fail)
    with pytest.raises(files.FileLimitExceeded):
        files.create_project_file(root_fd, stage_fd, "not-published", "data")
    assert not (root / "not-published").exists()
    assert list(stage.iterdir()) == []


def test_search_is_literal_and_reports_limits(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "a.txt").write_text("[literal.*]\n[literal.*]\n")
    (root / "b.txt").write_text("text")
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_MAX_MATCHES", 1)
    result = files.search_project_files(root_fd, "[literal.*]")
    assert result["truncated"] and result["limit"] == "match_limit"
    assert len(result["matches"]) == 1
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_MAX_FILES", 1)
    result = files.search_project_files(root_fd, "missing")
    assert result["limit"] == "file_limit"
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_MAX_BYTES", 1)
    result = files.search_project_files(root_fd, "missing")
    assert result["limit"] == "byte_limit"
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_TIMEOUT_SECONDS", 0)
    result = files.search_project_files(root_fd, "missing")
    assert result["limit"] == "time_limit"


def test_search_reports_excluded_and_unreadable_entries(project):
    root, _, root_fd, _ = project
    (root / "node_modules").mkdir()
    (root / "binary").write_bytes(b"\xff")
    (root / "allowed").write_text("needle")
    result = files.search_project_files(root_fd, "needle")
    assert result["skipped_entries"] == 2
    assert result["complete"] is False
    assert [match["path"] for match in result["matches"]] == ["allowed"]


def test_descriptor_ownership_is_preserved(project):
    _, _, root_fd, stage_fd = project
    files.create_project_file(root_fd, stage_fd, "empty", "")
    files.list_project_files(root_fd)
    files.read_project_file(root_fd, "empty")
    assert os.fstat(root_fd)
    assert os.fstat(stage_fd)


def test_shared_staging_permissions_are_rejected(project):
    root, stage, root_fd, stage_fd = project
    stage.chmod(0o755)
    with pytest.raises(files.FileUnavailable):
        files.create_project_file(root_fd, stage_fd, "file", "data")
    assert not (root / "file").exists()


def test_destination_replacement_after_publish_reports_unknown(project, monkeypatch):
    root, _, root_fd, stage_fd = project
    (root / "replacement").write_text("different")
    original = files.os.link
    def replaced(*args, **kwargs):
        original(*args, **kwargs)
        os.replace(root / "replacement", root / "target")
    monkeypatch.setattr(files.os, "link", replaced)
    with pytest.raises(files.FileOutcomeUnknown):
        files.create_project_file(root_fd, stage_fd, "target", "intended")
    assert (root / "target").read_text() == "different"


def test_directory_listing_detects_entry_changes(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "first").touch()
    original = files.os.scandir
    def changed(descriptor):
        scan = original(descriptor)
        before = os.fstat(descriptor)
        (root / "second").touch()
        # Linux filesystem timestamps can coalesce rapid edits into one tick.
        # Exercise metadata-change detection without relying on clock resolution.
        os.utime(descriptor, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        return scan
    monkeypatch.setattr(files.os, "scandir", changed)
    with pytest.raises(files.FilePreconditionFailed):
        files.list_project_files(root_fd)


@pytest.mark.parametrize("text", ["\ud800", "bad\x00data", 5])
def test_invalid_content_is_rejected_without_staging(project, text):
    root, stage, root_fd, stage_fd = project
    with pytest.raises(ValueError):
        files.create_project_file(root_fd, stage_fd, "new", text)
    assert list(stage.iterdir()) == []
    assert not (root / "new").exists()


def test_read_detects_same_inode_content_change(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "file").write_text("original")
    original = files.os.fstat
    observations = 0
    def changed(descriptor):
        nonlocal observations
        info = original(descriptor)
        if info.st_ino == (root / "file").stat().st_ino:
            observations += 1
            if observations == 1:
                (root / "file").write_text("modified")
        return info
    monkeypatch.setattr(files.os, "fstat", changed)
    with pytest.raises(files.FilePreconditionFailed):
        files.read_project_file(root_fd, "file")


def test_destination_admission_race_preserves_competing_file(project, monkeypatch):
    root, stage, root_fd, stage_fd = project
    original = files.os.link
    def competing(*args, **kwargs):
        (root / "target").write_text("concurrent writer")
        return original(*args, **kwargs)
    monkeypatch.setattr(files.os, "link", competing)
    with pytest.raises(files.FilePreconditionFailed):
        files.create_project_file(root_fd, stage_fd, "target", "our data")
    assert (root / "target").read_text() == "concurrent writer"
    assert list(stage.iterdir()) == []


def test_staging_fsync_failure_after_publication_is_unknown(project, monkeypatch):
    root, _, root_fd, stage_fd = project
    original = files.os.fsync
    def fail_staging(descriptor):
        if descriptor == stage_fd:
            raise OSError(errno.EIO, "staging sync failed")
        return original(descriptor)
    monkeypatch.setattr(files.os, "fsync", fail_staging)
    with pytest.raises(files.FileOutcomeUnknown):
        files.create_project_file(root_fd, stage_fd, "target", "data")
    assert (root / "target").read_text() == "data"


def test_directory_quota_cannot_report_search_complete(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "a").touch()
    (root / "b").touch()
    monkeypatch.setattr(files, "WORKSPACE_DIRECTORY_MAX_ENTRIES", 1)
    result = files.search_project_files(root_fd, "needle")
    assert result["truncated"] is True
    assert result["complete"] is False
    assert result["limit"] == "entry_limit"


def test_search_directory_queue_is_bounded(project, monkeypatch):
    root, _, root_fd, _ = project
    for name in ("a", "b", "c"):
        (root / name).mkdir()
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_MAX_PENDING_DIRECTORIES", 1)
    result = files.search_project_files(root_fd, "needle")
    assert result["limit"] == "directory_limit"
    assert result["truncated"] and not result["complete"]


def test_search_unreadable_files_charge_actual_bytes(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "a").write_bytes(b"\xff")
    (root / "b").write_text("text")
    monkeypatch.setattr(files, "WORKSPACE_FILE_MAX_BYTES", 4)
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_MAX_BYTES", 5)
    result = files.search_project_files(root_fd, "text")
    assert result["limit"] is None
    assert result["read_budget_bytes"] == 5
    assert result["files_scanned"] == 1
    assert result["files_attempted"] == 2
    assert result["matches"][0]["path"] == "b"


def test_root_directory_descriptions_are_independent(project):
    _, _, root_fd, _ = project
    with files._directory(root_fd, []) as first, files._directory(root_fd, []) as second:
        # Seek a directory cookie without reading it. Independent open file
        # descriptions must not propagate this offset to another caller.
        os.lseek(first, 123, os.SEEK_SET)
        assert os.lseek(second, 0, os.SEEK_CUR) == 0
        assert os.lseek(root_fd, 0, os.SEEK_CUR) == 0
        assert not os.get_inheritable(first)


def test_concurrent_root_listing_returns_every_entry(project, monkeypatch):
    root, _, root_fd, _ = project
    names = [f"file-{index:04d}" for index in range(1000)]
    for name in names:
        (root / name).touch()
    barrier = Barrier(2)
    original = files.os.scandir
    def simultaneous(descriptor):
        barrier.wait(timeout=10)
        return original(descriptor)
    monkeypatch.setattr(files.os, "scandir", simultaneous)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: files.list_project_files(root_fd), range(2)))
    for result in results:
        assert [entry["name"] for entry in result["entries"]] == names


@pytest.mark.parametrize("error_number", [errno.EIO, errno.EMFILE])
def test_listing_infrastructure_error_is_not_missing_file(project, monkeypatch, error_number):
    _, _, root_fd, _ = project
    cause = OSError(error_number, "internal host path")
    def fail(descriptor):
        raise cause
    monkeypatch.setattr(files.os, "scandir", fail)
    with pytest.raises(files.FileServiceUnavailable) as error:
        files.list_project_files(root_fd)
    assert error.value.__cause__ is cause
    assert "internal host path" not in str(error.value)


def test_directory_context_preserves_body_oserror(project):
    _, _, root_fd, _ = project
    cause = OSError(errno.EIO, "body error")
    with pytest.raises(OSError) as error:
        with files._directory(root_fd, []):
            raise cause
    assert error.value is cause


def test_invalid_root_and_staging_descriptors_are_service_errors(project):
    _, _, root_fd, _ = project
    with pytest.raises(files.FileServiceUnavailable) as root_error:
        files.list_project_files(-1)
    assert root_error.value.__cause__.errno == errno.EBADF
    with pytest.raises(files.FileServiceUnavailable) as stage_error:
        files.create_project_file(root_fd, -1, "new", "content")
    assert stage_error.value.__cause__.errno == errno.EBADF


def test_search_oversized_files_do_not_exhaust_byte_budget(project, monkeypatch):
    root, _, root_fd, _ = project
    for index in range(10):
        (root / f"a{index}").write_text("too large")
    (root / "z").write_text("find")
    monkeypatch.setattr(files, "WORKSPACE_FILE_MAX_BYTES", 4)
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_MAX_BYTES", 4)
    result = files.search_project_files(root_fd, "find")
    assert result["limit"] is None
    assert result["read_budget_bytes"] == 4
    assert result["files_scanned"] == 1
    assert result["files_attempted"] == 11
    assert result["matches"][0]["path"] == "z"


def test_nested_search_uses_editor_line_numbers(project):
    root, _, root_fd, _ = project
    (root / "nested").mkdir()
    (root / "nested/file").write_text("a\x0bb\u2028c\nneedle\n")
    result = files.search_project_files(root_fd, "needle", "nested")
    assert result["matches"][0]["line"] == 2
    assert files.list_project_files(root_fd, "nested")["entries"][0]["path"] == "nested/file"
    with pytest.raises(files.FileUnavailable):
        files.read_project_file(root_fd, "nested")


def test_changed_subtree_is_reported_as_truncated(project, monkeypatch):
    _, _, root_fd, _ = project
    def changed(*args, **kwargs):
        raise files.FilePreconditionFailed("directory changed")
    monkeypatch.setattr(files, "list_project_files", changed)
    result = files.search_project_files(root_fd, "needle")
    assert result["truncated"] and not result["complete"]
    assert result["limit"] == "subtree_unavailable"


def test_version_hash_is_validated_and_case_normalized(project):
    root, _, root_fd, _ = project
    (root / "file").write_text("data")
    digest = hashlib.sha256(b"data").hexdigest()
    assert files.read_project_file(root_fd, "file", expected_sha256=digest.upper())["sha256"] == digest
    with pytest.raises(ValueError):
        files.read_project_file(root_fd, "file", expected_sha256="invalid")


def test_failed_directory_read_closes_its_file_descriptor(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "nested").mkdir()
    original = files.os.open
    captured = []
    def capture(path, *args, **kwargs):
        descriptor = original(path, *args, **kwargs)
        if path == "nested":
            captured.append(descriptor)
        return descriptor
    monkeypatch.setattr(files.os, "open", capture)
    with pytest.raises(files.FileUnavailable):
        files.read_project_file(root_fd, "nested")
    for descriptor in captured:
        with pytest.raises(OSError) as error:
            os.fstat(descriptor)
        assert error.value.errno == errno.EBADF


def test_read_failure_retains_actual_consumed_budget(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "a").write_text("text")
    original = files.os.stat
    def changed(path, *args, **kwargs):
        if path == "a" and kwargs.get("dir_fd") is not None:
            raise FileNotFoundError(errno.ENOENT, "removed")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(files.os, "stat", changed)
    result = files.search_project_files(root_fd, "text")
    assert result["read_budget_bytes"] == 4
    assert result["files_scanned"] == 0
    assert result["skipped_entries"] == 1


def test_read_growth_never_exceeds_remaining_search_budget(project, monkeypatch):
    root, _, root_fd, _ = project
    (root / "a").write_text("x")
    original = files.os.fstat
    identity = (root / "a").stat().st_ino
    mutated = False
    def grow(descriptor):
        nonlocal mutated
        info = original(descriptor)
        if info.st_ino == identity and not mutated:
            mutated = True
            (root / "a").write_text("much bigger now")
        return info
    monkeypatch.setattr(files.os, "fstat", grow)
    monkeypatch.setattr(files, "WORKSPACE_SEARCH_MAX_BYTES", 3)
    result = files.search_project_files(root_fd, "x")
    assert result["read_budget_bytes"] == 3
    assert result["files_scanned"] == 0
    assert not result["complete"]
