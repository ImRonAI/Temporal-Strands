"""Tests for scripts/validate_desktop_release.py.

All fixtures are synthetic: no real source trees, credentials, or ``.env``
files are read or written. The forbidden credential basename is only used as a
tar member *name* with placeholder bytes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_desktop_release.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_desktop_release", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolve string annotations via sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vdr = _load_module()

SYNTHETIC_COMMIT = "0" * 40
SYNTHETIC_BUNDLE_FILES: dict[str, bytes] = {
    "app/package.json": b'{"name": "synthetic-app", "private": true}\n',
    "app/.next/BUILD_ID": b"synthetic-build\n",
    "app/.next/server/app/page.js": b"module.exports = {};\n",
    "app/orchestrator/config.py": b"TASK_QUEUE = 'synthetic'\n",
    "strands-tools/src/strands_graph_tool/__init__.py": b"__all__ = []\n",
    "strands-tools/src/skills/example/SKILL.md": b"# synthetic skill\n",
}
DEFAULT_ARTIFACT_PATHS = ("app/.next/BUILD_ID", "app/.next/server/app/page.js")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_manifest(
    files: dict[str, bytes],
    artifact_paths: tuple[str, ...] = DEFAULT_ARTIFACT_PATHS,
    **overrides: Any,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release_id": "synthetic-2026.09.08-1",
        "created_at": "2026-09-08T12:00:00Z",
        "platform": {"os": "linux", "arch": "amd64"},
        "provenance": {
            "builder": "synthetic-builder",
            "build_id": "synthetic-build-1",
            "source_repository": "https://example.invalid/synthetic/repo",
            "source_commit": SYNTHETIC_COMMIT,
        },
        "artifacts": [
            {"path": path, "sha256": _sha(files[path]), "size_bytes": len(files[path])}
            for path in artifact_paths
        ],
    }
    manifest.update(overrides)
    return manifest


def _add_file(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.type = tarfile.REGTYPE
    tar.addfile(info, io.BytesIO(data))


def _add_dir(tar: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    tar.addfile(info)


def build_archive(
    tmp_path: Path,
    files: dict[str, bytes] | None = None,
    manifest: dict[str, Any] | None | bool = True,
    extra: Callable[[tarfile.TarFile], None] | None = None,
    directories: tuple[str, ...] = (),
    name: str = "release.tar.gz",
) -> tuple[Path, str]:
    """Write a synthetic release archive; return (path, sha256 of the file)."""
    files = dict(SYNTHETIC_BUNDLE_FILES if files is None else files)
    path = tmp_path / name
    with tarfile.open(path, mode="w:gz") as tar:
        for directory in directories:
            _add_dir(tar, directory)
        for member_name, data in files.items():
            _add_file(tar, member_name, data)
        if manifest is True:
            manifest = make_manifest(files)
        if manifest is not False and manifest is not None:
            payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
            _add_file(tar, vdr.MANIFEST_NAME, payload)
        if extra is not None:
            extra(tar)
    return path, _sha(path.read_bytes())


def validate(path: Path, digest: str, **limits: Any) -> Any:
    return vdr.validate_release(path, digest, vdr.Limits(**limits) if limits else None)


def assert_fails(result: Any, fragment: str) -> None:
    assert not result.ok
    assert any(fragment in error for error in result.errors), result.errors


# --- Happy path --------------------------------------------------------------


def test_valid_synthetic_release_passes(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    result = validate(path, digest)
    assert result.ok, result.errors
    assert result.errors == []
    assert result.sha256 == digest
    assert result.release_id == "synthetic-2026.09.08-1"
    assert result.artifact_count == len(DEFAULT_ARTIFACT_PATHS)
    assert result.member_count == len(SYNTHETIC_BUNDLE_FILES) + 1
    assert result.to_dict()["gate_version"] == vdr.GATE_VERSION


def test_explicit_directory_entries_are_accepted(tmp_path: Path) -> None:
    path, digest = build_archive(
        tmp_path, directories=("app", "strands-tools", "strands-tools/src", "strands-tools/src/skills")
    )
    result = validate(path, digest)
    assert result.ok, result.errors


def test_sha256_prefix_and_case_are_normalized(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert validate(path, "sha256:" + digest.upper()).ok
    assert validate(path, f"  {digest}  ").ok


def test_validation_does_not_write_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, digest = build_archive(tmp_path)
    monkeypatch.chdir(tmp_path)
    before = {p.name for p in tmp_path.iterdir()}
    assert validate(path, digest).ok
    after = {p.name for p in tmp_path.iterdir()}
    assert before == after == {"release.tar.gz"}


# --- Outer digest ------------------------------------------------------------


def test_hash_mismatch_fails_before_archive_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = build_archive(tmp_path)
    opened: list[str] = []
    real_open = vdr.tarfile.open

    def spy(*args: Any, **kwargs: Any):
        opened.append("opened")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(vdr.tarfile, "open", spy)
    result = validate(path, "0" * 64)
    assert_fails(result, "sha256 mismatch")
    assert opened == []
    assert result.member_count == 0


@pytest.mark.parametrize("bad", ["", "abc", "g" * 64, "0" * 63, "sha1:" + "0" * 64])
def test_malformed_expected_sha256_is_rejected(tmp_path: Path, bad: str) -> None:
    path, _ = build_archive(tmp_path)
    assert_fails(validate(path, bad), "invalid expected sha256")


def test_missing_archive_fails_closed(tmp_path: Path) -> None:
    assert_fails(validate(tmp_path / "absent.tar.gz", "0" * 64), "archive is not readable")


def test_symlinked_archive_path_is_rejected(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    link = tmp_path / "link.tar.gz"
    link.symlink_to(path)
    assert_fails(validate(link, digest), "must not be a symlink")


def test_directory_archive_path_is_rejected(tmp_path: Path) -> None:
    assert_fails(validate(tmp_path, "0" * 64), "must be a regular file")


def test_archive_is_opened_once_and_parsed_from_same_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, digest = build_archive(tmp_path)
    opens: list[str] = []
    real_os_open = os.open

    def spy_open(p: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        opens.append(os.fspath(p))
        assert flags & os.O_NOFOLLOW
        return real_os_open(p, flags, *args, **kwargs)

    tar_fileobjs: list[Any] = []
    real_tar_open = vdr.tarfile.open

    def spy_tar(*args: Any, **kwargs: Any):
        assert not args and "name" not in kwargs, "tarfile must not reopen by pathname"
        tar_fileobjs.append(kwargs["fileobj"])
        return real_tar_open(*args, **kwargs)

    monkeypatch.setattr(vdr.os, "open", spy_open)
    monkeypatch.setattr(vdr.tarfile, "open", spy_tar)
    assert validate(path, digest).ok
    assert opens == [str(path)]
    assert len(tar_fileobjs) == 1


def test_pathname_swap_after_hash_parses_original_handle_and_fails_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renaming a hostile archive over the pathname after hashing.

    Two properties: (a) the tar parse reads the *original verified descriptor*,
    never the replacement (the replacement's forbidden ``.env`` member must not
    appear in the errors); (b) the gate still fails closed, because the inode it
    validated is no longer what the pathname refers to.
    """
    good_path, good_digest = build_archive(tmp_path, name="good.tar.gz")
    target = tmp_path / "release.tar.gz"
    target.write_bytes(good_path.read_bytes())

    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, "app/.env", b"X=1\n")

    hostile_path, _ = build_archive(tmp_path, extra=extra, name="hostile.tar.gz")

    real_hash = vdr.sha256_fileobj
    swapped: list[bool] = []

    def hash_then_swap(handle: Any) -> str:
        digest = real_hash(handle)
        os.replace(hostile_path, target)  # atomic rename over the validated pathname
        swapped.append(True)
        return digest

    monkeypatch.setattr(vdr, "sha256_fileobj", hash_then_swap)
    result = validate(target, good_digest)
    assert swapped == [True]
    assert result.sha256 == good_digest
    # (a) Parsed members are the original archive's: hostile .env never seen.
    assert result.member_count == len(SYNTHETIC_BUNDLE_FILES) + 1
    assert not any(".env" in error for error in result.errors), result.errors
    assert result.release_id == "synthetic-2026.09.08-1"
    assert result.artifact_count == len(DEFAULT_ARTIFACT_PATHS)
    # (b) Still fails closed: the pathname no longer names the validated inode.
    assert result.errors == ["archive identity changed during validation"]
    assert not result.ok
    assert _sha(target.read_bytes()) != good_digest


def test_in_place_rewrite_during_validation_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, digest = build_archive(tmp_path)
    real_check = vdr.check_layout

    def rewrite_then_check(members: Any) -> list[str]:
        # Simulate an in-place writer touching the same inode mid-validation.
        with path.open("r+b") as fh:
            fh.seek(0, os.SEEK_END)
            fh.write(b"\x00")
        return real_check(members)

    monkeypatch.setattr(vdr, "check_layout", rewrite_then_check)
    assert_fails(validate(path, digest), "archive identity changed during validation")


def test_non_gzip_bytes_with_matching_digest_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "plain.tar.gz"
    payload = b"not a gzip stream"
    path.write_bytes(payload)
    assert_fails(validate(path, _sha(payload)), "not gzip-compressed")


def test_truncated_gzip_is_reported_not_raised(tmp_path: Path) -> None:
    path, _ = build_archive(tmp_path)
    data = path.read_bytes()[: len(path.read_bytes()) // 2]
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(data)
    result = validate(broken, _sha(data))
    assert not result.ok
    assert result.errors, "expected at least one error for a truncated archive"


def test_compressed_archive_size_limit(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert_fails(validate(path, digest, max_archive_bytes=10), "max compressed size")


# --- Member name safety ------------------------------------------------------


@pytest.mark.parametrize(
    "name, fragment",
    [
        ("/etc/passwd", "absolute"),
        ("app/../escape.txt", "'..' component"),
        ("../escape.txt", "'..' component"),
        ("app/./same.txt", "'.' component"),
        ("app\\windows\\path.txt", "backslash"),
        ("C:/drive.txt", "drive prefix"),
        ("app//double.txt", "empty path component"),
        ("app/evil\nname.txt", "control characters"),
    ],
)
def test_unsafe_member_names_fail(tmp_path: Path, name: str, fragment: str) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, name, b"x")

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), fragment)


def test_check_member_name_unit() -> None:
    assert vdr.check_member_name("app/package.json") == []
    assert vdr.check_member_name("") == ["member has an empty name"]
    assert any("'..'" in e for e in vdr.check_member_name("a/../b"))
    assert any("backslash" in e for e in vdr.check_member_name("a\\b"))
    assert any("absolute" in e for e in vdr.check_member_name("/a"))
    # tarfile truncates names at NUL when writing, so this is unit-only coverage.
    assert any("NUL" in e for e in vdr.check_member_name("app/evil\x00name.txt"))


def test_duplicate_member_fails(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, "app/package.json", b"{}")

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "duplicate member")


def test_file_and_directory_collision_fails(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, "app/orchestrator", b"not a directory")

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "file and directory share a path")


# --- Member types ------------------------------------------------------------


def test_symlink_member_fails(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("app/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "symlink entries are not allowed")


def test_hardlink_member_fails(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("app/hard")
        info.type = tarfile.LNKTYPE
        info.linkname = "app/package.json"
        tar.addfile(info)

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "hardlink entries are not allowed")


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_device_and_fifo_members_fail(tmp_path: Path, kind: bytes) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("app/dev")
        info.type = kind
        tar.addfile(info)

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "device/FIFO entries are not allowed")


@pytest.mark.parametrize("mode", [0o4755, 0o2755])
def test_setuid_setgid_members_fail(tmp_path: Path, mode: int) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, "app/bin/tool", b"#!/bin/sh\n", mode=mode)

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "setuid/setgid")


# --- Limits ------------------------------------------------------------------


def test_member_count_limit(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert_fails(validate(path, digest, max_members=3), "max member count")


def test_total_uncompressed_limit(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert_fails(validate(path, digest, max_total_uncompressed_bytes=16), "max total uncompressed")


def test_single_member_size_limit(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert_fails(validate(path, digest, max_member_bytes=8), "exceeds max size")


def _spy_member_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reads: list[str] = []
    real_extractfile = vdr.tarfile.TarFile.extractfile

    def spy(self: tarfile.TarFile, member: Any):
        reads.append(member.name if hasattr(member, "name") else str(member))
        return real_extractfile(self, member)

    monkeypatch.setattr(vdr.tarfile.TarFile, "extractfile", spy)
    return reads


def test_quota_violation_skips_manifest_and_artifact_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, digest = build_archive(tmp_path)
    reads = _spy_member_reads(monkeypatch)
    result = validate(path, digest, max_total_uncompressed_bytes=16)
    assert_fails(result, "max total uncompressed")
    assert_fails(result, "manifest not evaluated")
    assert reads == []
    assert result.release_id is None
    assert result.artifact_count == 0


@pytest.mark.parametrize(
    "extra",
    [
        lambda tar: _add_file(tar, "app/../escape.txt", b"x"),
        lambda tar: _add_file(tar, "app/node_modules/x.js", b"x"),
        lambda tar: _add_file(tar, "README.md", b"x"),
    ],
    ids=["traversal", "forbidden-path", "layout"],
)
def test_member_or_layout_errors_skip_member_content_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: Callable[[tarfile.TarFile], None]
) -> None:
    path, digest = build_archive(tmp_path, extra=extra)
    reads = _spy_member_reads(monkeypatch)
    result = validate(path, digest)
    assert not result.ok
    assert reads == []
    assert result.release_id is None


def test_clean_archive_reads_only_manifest_and_declared_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, digest = build_archive(tmp_path)
    reads = _spy_member_reads(monkeypatch)
    assert validate(path, digest).ok
    assert set(reads) == {vdr.MANIFEST_NAME, *DEFAULT_ARTIFACT_PATHS}


def test_error_list_is_bounded(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        for index in range(vdr.MAX_ERRORS + 20):
            _add_file(tar, f"app/node_modules/pkg{index}/index.js", b"x")

    path, digest = build_archive(tmp_path, extra=extra)
    result = validate(path, digest)
    assert not result.ok
    assert len(result.errors) == vdr.MAX_ERRORS + 1
    assert "suppressed" in result.errors[-1]


# --- Forbidden content -------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "app/.env",
        "app/.env.local",
        "app/.env.production",
        "app/orchestrator/.env.example",
        # Directory descendants: the .env* component is not the basename.
        "app/.env/production",
        "app/.env.d/local.conf",
        ".env/anything",
        "strands-tools/src/.envrc.d/skills/x",
    ],
)
def test_env_files_fail(tmp_path: Path, name: str) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, name, b"PLACEHOLDER=1\n")

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "forbidden environment file")


@pytest.mark.parametrize(
    "name",
    [
        "fair-expanse-493212-h8-138622c839d2.json",
        "app/fair-expanse-493212-h8-138622c839d2.json",
        "strands-tools/src/skills/FAIR-EXPANSE-493212-H8-138622C839D2.JSON",
    ],
)
def test_credential_basename_fails_without_reading_real_key(tmp_path: Path, name: str) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, name, b"{}")  # placeholder bytes only

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "forbidden credential basename")


def test_other_json_files_are_allowed(tmp_path: Path) -> None:
    files = dict(SYNTHETIC_BUNDLE_FILES)
    files["app/tsconfig.json"] = b"{}"
    files["app/orchestrator/agent.json"] = b'{"name": "synthetic"}'
    path, digest = build_archive(tmp_path, files=files)
    assert validate(path, digest).ok


@pytest.mark.parametrize(
    "name, fragment",
    [
        ("app/node_modules/left-pad/index.js", "forbidden path component 'node_modules'"),
        ("app/.git/HEAD", "forbidden path component '.git'"),
        ("app/orchestrator/.venv/bin/python", "forbidden path component '.venv'"),
        ("app/orchestrator/__pycache__/x.pyc", "forbidden path component '__pycache__'"),
        ("app/orchestrator/.runtime/browser-observations/x.jpg", "forbidden path component '.runtime'"),
        ("app/.next/cache/webpack/x.pack", "forbidden path sequence '.next/cache'"),
        ("app/.kilo/plans/x.md", "forbidden path component '.kilo'"),
        ("app/.worktrees/x/package.json", "forbidden path component '.worktrees'"),
        ("app/.DS_Store", "forbidden basename"),
        ("strands-tools/src/skills/x.pyc", "forbidden compiled artifact"),
    ],
)
def test_runtime_cache_and_vcs_paths_fail(tmp_path: Path, name: str, fragment: str) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, name, b"x")

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), fragment)


def test_prebuilt_next_output_outside_cache_is_allowed(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert validate(path, digest).ok


# --- Layout ------------------------------------------------------------------


@pytest.mark.parametrize(
    "removed, fragment",
    [
        ("app/package.json", "required file missing"),
        ("strands-tools/src/strands_graph_tool/__init__.py", "required file missing"),
        ("strands-tools/src/skills/example/SKILL.md", "required directory missing"),
    ],
)
def test_required_layout_entries(tmp_path: Path, removed: str, fragment: str) -> None:
    files = {k: v for k, v in SYNTHETIC_BUNDLE_FILES.items() if k != removed}
    manifest = make_manifest(files)
    path, digest = build_archive(tmp_path, files=files, manifest=manifest)
    assert_fails(validate(path, digest), fragment)


def test_missing_strands_tools_root_fails(tmp_path: Path) -> None:
    files = {k: v for k, v in SYNTHETIC_BUNDLE_FILES.items() if not k.startswith("strands-tools/")}
    path, digest = build_archive(tmp_path, files=files, manifest=make_manifest(files))
    assert_fails(validate(path, digest), "required root directory missing: 'strands-tools/src'")


def test_unexpected_top_level_entry_fails(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, "README.md", b"# nope\n")

    path, digest = build_archive(tmp_path, extra=extra)
    assert_fails(validate(path, digest), "unexpected top-level entry: 'README.md'")


def test_missing_manifest_fails(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path, manifest=False)
    assert_fails(validate(path, digest), "release manifest missing")


def test_manifest_as_directory_fails(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_dir(tar, vdr.MANIFEST_NAME)

    path, digest = build_archive(tmp_path, manifest=False, extra=extra)
    assert_fails(validate(path, digest), "release manifest missing or not a regular file")


# --- Manifest schema ---------------------------------------------------------


def _archive_with_manifest(tmp_path: Path, manifest: Any) -> tuple[Path, str]:
    if isinstance(manifest, (bytes, str)):
        raw = manifest if isinstance(manifest, bytes) else manifest.encode("utf-8")

        def extra(tar: tarfile.TarFile) -> None:
            _add_file(tar, vdr.MANIFEST_NAME, raw)

        return build_archive(tmp_path, manifest=False, extra=extra)
    return build_archive(tmp_path, manifest=manifest)


def test_manifest_invalid_json_fails(tmp_path: Path) -> None:
    path, digest = _archive_with_manifest(tmp_path, b"{not json")
    assert_fails(validate(path, digest), "not valid JSON")


def test_manifest_non_utf8_fails(tmp_path: Path) -> None:
    path, digest = _archive_with_manifest(tmp_path, b"\xff\xfe\x00")
    assert_fails(validate(path, digest), "not valid UTF-8")


def test_manifest_non_object_fails(tmp_path: Path) -> None:
    path, digest = _archive_with_manifest(tmp_path, b"[]")
    assert_fails(validate(path, digest), "top level must be a JSON object")


def test_manifest_size_limit(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert_fails(validate(path, digest, max_manifest_bytes=8), "manifest exceeds max size")


def test_manifest_unknown_and_missing_keys_fail(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    del manifest["provenance"]
    manifest["extra"] = True
    path, digest = _archive_with_manifest(tmp_path, manifest)
    result = validate(path, digest)
    assert_fails(result, "unknown key 'extra'")
    assert_fails(result, "missing key 'provenance'")


@pytest.mark.parametrize("version", [0, 2, "1", 1.0, True, None])
def test_manifest_schema_version_must_be_1(tmp_path: Path, version: Any) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES, schema_version=version)
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "schema_version must be 1")


@pytest.mark.parametrize("release_id", ["", "-leading", "has space", "a" * 129, 7, None])
def test_manifest_release_id_pattern(tmp_path: Path, release_id: Any) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES, release_id=release_id)
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "release_id must match")


@pytest.mark.parametrize(
    "created_at", ["2026-09-08", "2026-09-08T12:00:00+00:00", "2026-13-40T12:00:00Z", 0]
)
def test_manifest_created_at_format(tmp_path: Path, created_at: Any) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES, created_at=created_at)
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "created_at")


@pytest.mark.parametrize(
    "platform",
    [
        {"os": "linux", "arch": "arm64"},
        {"os": "darwin", "arch": "amd64"},
        {"os": "linux", "arch": "x86_64"},
        {"os": "linux"},
        {"os": "linux", "arch": "amd64", "variant": "v3"},
        "linux/amd64",
    ],
)
def test_manifest_platform_must_be_linux_amd64(tmp_path: Path, platform: Any) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES, platform=platform)
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "platform")


@pytest.mark.parametrize(
    "field, value, fragment",
    [
        ("builder", "", "provenance.builder"),
        ("build_id", "x" * 300, "provenance.build_id"),
        ("source_repository", 5, "provenance.source_repository"),
        ("source_commit", "abc", "source_commit must be 40 lowercase hex"),
        ("source_commit", "A" * 40, "source_commit must be 40 lowercase hex"),
        ("builder", "bad\x01name", "control characters"),
    ],
)
def test_manifest_provenance_fields(tmp_path: Path, field: str, value: Any, fragment: str) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    manifest["provenance"][field] = value
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), fragment)


def test_manifest_provenance_unknown_key_fails(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    manifest["provenance"]["signature"] = "not-verified-here"
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "provenance has unknown key 'signature'")


# --- Manifest artifacts ------------------------------------------------------


def test_manifest_requires_at_least_one_artifact(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES, artifacts=[])
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "at least one prebuilt artifact")


def test_manifest_artifacts_must_be_list(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES, artifacts={"path": "x"})
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "artifacts must be a list")


def test_manifest_artifact_count_limit(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    assert_fails(validate(path, digest, max_artifacts=1), "more than 1 artifacts")


def test_manifest_artifact_digest_mismatch_fails(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    manifest["artifacts"][0]["sha256"] = "f" * 64
    path, digest = _archive_with_manifest(tmp_path, manifest)
    result = validate(path, digest)
    assert_fails(result, "artifacts[0] sha256 mismatch")
    assert result.artifact_count == len(DEFAULT_ARTIFACT_PATHS) - 1


def test_manifest_artifact_size_mismatch_fails(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    manifest["artifacts"][0]["size_bytes"] += 1
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "does not match archive member size")


def test_manifest_artifact_missing_member_fails(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    manifest["artifacts"].append({"path": "app/absent.js", "sha256": "0" * 64, "size_bytes": 0})
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "not a regular file in the archive: 'app/absent.js'")


@pytest.mark.parametrize(
    "artifact_path, fragment",
    [
        ("../escape", "path is unsafe"),
        ("/abs", "path is unsafe"),
        ("release-manifest.json", "outside required roots"),
        ("other/file", "outside required roots"),
        ("strands-tools/pyproject.toml", "outside required roots"),
    ],
)
def test_manifest_artifact_path_policy(tmp_path: Path, artifact_path: str, fragment: str) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    manifest["artifacts"].append({"path": artifact_path, "sha256": "0" * 64, "size_bytes": 0})
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), fragment)


def test_manifest_duplicate_artifact_path_fails(tmp_path: Path) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    manifest["artifacts"].append(dict(manifest["artifacts"][0]))
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), "path is a duplicate")


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        (lambda a: a.pop("size_bytes"), "missing key 'size_bytes'"),
        (lambda a: a.__setitem__("digest", "x"), "unknown key 'digest'"),
        (lambda a: a.__setitem__("sha256", "ABC"), "sha256 must be 64 lowercase hex"),
        (lambda a: a.__setitem__("size_bytes", -1), "size_bytes must be a non-negative integer"),
        (lambda a: a.__setitem__("size_bytes", True), "size_bytes must be a non-negative integer"),
        (lambda a: a.__setitem__("path", 3), "path must be a string"),
    ],
)
def test_manifest_artifact_field_validation(
    tmp_path: Path, mutation: Callable[[dict[str, Any]], Any], fragment: str
) -> None:
    manifest = make_manifest(SYNTHETIC_BUNDLE_FILES)
    mutation(manifest["artifacts"][0])
    path, digest = _archive_with_manifest(tmp_path, manifest)
    assert_fails(validate(path, digest), fragment)


# --- Module hygiene ----------------------------------------------------------


def test_module_uses_only_stdlib_and_embeds_no_digests_or_pins() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "__future__" not in line
    ]
    stdlib = set(sys.stdlib_module_names)
    for line in import_lines:
        module = line.split()[1].split(".")[0]
        assert module in stdlib, f"non-stdlib import: {line}"
    assert "extractall" not in source
    assert "subprocess" not in source
    assert "os.system" not in source
    # No embedded 64-hex digests or 40-hex commits: pins come from the manifest.
    import re

    assert not re.search(r"\b[0-9a-f]{64}\b", source)
    assert not re.search(r"\b[0-9a-f]{40}\b", source)
    assert vdr.REQUIRED_PLATFORM == {"os": "linux", "arch": "amd64"}
    assert "fair-expanse-493212-h8-138622c839d2.json" in vdr.FORBIDDEN_CREDENTIAL_BASENAMES


# --- CLI ---------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_cli_passes_valid_release(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    proc = _run_cli("--archive", str(path), "--sha256", digest)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK release=synthetic-2026.09.08-1")
    assert proc.stderr == ""


def test_cli_json_output(tmp_path: Path) -> None:
    path, digest = build_archive(tmp_path)
    proc = _run_cli("--archive", str(path), "--sha256", digest, "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert payload["sha256"] == digest
    assert payload["gate_version"] == vdr.GATE_VERSION


def test_cli_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    path, _ = build_archive(tmp_path)
    proc = _run_cli("--archive", str(path), "--sha256", "0" * 64)
    assert proc.returncode == 1
    assert "FAIL: sha256 mismatch" in proc.stderr
    assert proc.stdout == ""


def test_cli_json_reports_failure_with_exit_1(tmp_path: Path) -> None:
    def extra(tar: tarfile.TarFile) -> None:
        _add_file(tar, "app/.env", b"X=1\n")

    path, digest = build_archive(tmp_path, extra=extra)
    proc = _run_cli("--archive", str(path), "--sha256", digest, "--json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert any("forbidden environment file" in e for e in payload["errors"])


def test_cli_usage_errors_exit_2(tmp_path: Path) -> None:
    proc = _run_cli("--archive", str(tmp_path / "x.tar.gz"))
    assert proc.returncode == 2
    proc = _run_cli("--archive", str(tmp_path / "x.tar.gz"), "--sha256", "0" * 64, "--max-members", "0")
    assert proc.returncode == 2


def test_main_function_returns_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, digest = build_archive(tmp_path)
    assert vdr.main(["--archive", str(path), "--sha256", digest]) == 0
    assert vdr.main(["--archive", str(path), "--sha256", "1" * 64]) == 1
    captured = capsys.readouterr()
    assert "OK release=" in captured.out
    assert "sha256 mismatch" in captured.err
