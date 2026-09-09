"""Bounded POSIX file operations inside an already authorized project root.

Call in a workspace-side service thread, as the unprivileged desktop user, not
in workflows or on the credential-bearing worker host. root_fd is opened from a
server-resolved project registration, never from model input. The runtime must
restrict this process to the selected workspace: descriptors prevent symlink
redirection, but do not sandbox directory renames performed by other processes.

Writes require controller/journal admission upstream. staging_fd must reference
a private service-owned directory on the project's filesystem, inaccessible to
project programs. Only create-if-absent is supported: POSIX replace does not
provide atomic compare-and-swap against a concurrently modified content hash.
Published files are mode 0600 for the desktop UID; project programs must run as
that same UID. Staging privacy also requires runtime mount/process isolation:
mode bits alone do not isolate programs sharing that UID.
The private workspace_service calls read/list/search; no model tool registers
these functions. File creation remains unexposed pending runtime write policy.
"""

import errno
import hashlib
import os
import stat
import time
from contextlib import ExitStack, contextmanager
from typing import Iterator
from uuid import uuid4

from config import (
    WORKSPACE_DIRECTORY_MAX_ENTRIES,
    WORKSPACE_EXCLUDED_NAMES,
    WORKSPACE_FILE_MAX_BYTES,
    WORKSPACE_PATH_MAX_BYTES,
    WORKSPACE_PATH_MAX_DEPTH,
    WORKSPACE_SEARCH_MAX_BYTES,
    WORKSPACE_SEARCH_MAX_FILES,
    WORKSPACE_SEARCH_MAX_DIRECTORIES,
    WORKSPACE_SEARCH_MAX_PENDING_DIRECTORIES,
    WORKSPACE_SEARCH_QUERY_MAX_BYTES,
    WORKSPACE_SEARCH_MAX_MATCHES,
    WORKSPACE_SEARCH_TIMEOUT_SECONDS,
    WORKSPACE_UPLOAD_PREFIX,
)


class FileUnavailable(Exception):
    """Unavailable, unsafe, or unsupported file; map to a scoped HTTP 404."""


class FileServiceUnavailable(Exception):
    """Filesystem dependency failure; map to HTTP 503 without exposing its cause."""


def _file_error(error: OSError) -> Exception:
    if error.errno in (errno.ENOENT, errno.ENOTDIR, errno.EISDIR, errno.ELOOP, errno.EACCES, errno.EPERM):
        return FileUnavailable("Project path unavailable")
    return FileServiceUnavailable("Workspace filesystem unavailable")


@contextmanager
def _filesystem_errors() -> Iterator[None]:
    try:
        yield
    except OSError as error:
        raise _file_error(error) from error


class FilePreconditionFailed(Exception):
    """Create-only or content version precondition failed; map to HTTP 412."""


class FileLimitExceeded(Exception):
    """File or directory exceeds the configured budget; map to HTTP 413."""


class FileOutcomeUnknown(Exception):
    """Publication may have happened; reconcile journal, never retry blindly."""


class _SearchByteLimit(Exception):
    pass


def _allowed(name: str) -> bool:
    return (
        not name.casefold().startswith(".env")
        and name.casefold() not in WORKSPACE_EXCLUDED_NAMES
        and not name.casefold().startswith(WORKSPACE_UPLOAD_PREFIX)
    )


def _parts(path: str, *, root: bool = False) -> list[str]:
    if not isinstance(path, str):
        raise ValueError("Project path must be a string")
    if root and path == "":
        return []
    if len(path) > WORKSPACE_PATH_MAX_BYTES:
        raise ValueError("Project path exceeds limit")
    try:
        size = len(path.encode("utf-8"))
    except UnicodeError:
        raise ValueError("Invalid project path encoding") from None
    parts = path.split("/")
    if (
        not path or size > WORKSPACE_PATH_MAX_BYTES
        or len(parts) > WORKSPACE_PATH_MAX_DEPTH
        or "\\" in path or ":" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError("Invalid relative project path")
    if any(not _allowed(part) for part in parts):
        raise FileUnavailable("Project path unavailable")
    return parts


@contextmanager
def _directory(root_fd: int, parts: list[str]) -> Iterator[int]:
    with ExitStack() as descriptors:
        try:
            # dup shares the directory cursor. A fresh open description isolates
            # concurrent scandir calls while keeping resolution descriptor-relative.
            descriptor = root_fd
            for part in [".", *parts]:
                descriptor = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
                descriptors.callback(os.close, descriptor)
        except OSError as error:
            raise _file_error(error) from error
        yield descriptor


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def read_project_file(root_fd: int, path: str, *, expected_sha256: str | None = None) -> dict:
    """Read a bounded, stable UTF-8 regular file; refuse symlinks and hardlinks."""
    return _read_project_file(root_fd, path, expected_sha256=expected_sha256)


def _read_project_file(
    root_fd: int, path: str, *, expected_sha256: str | None = None,
    read_budget: list[int] | None = None,
) -> dict:
    parts = _parts(path)
    if expected_sha256 is not None:
        if (
            not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in expected_sha256)
        ):
            raise ValueError("Expected version must be a SHA256 digest")
        expected_sha256 = expected_sha256.lower()
    with _directory(root_fd, parts[:-1]) as parent, ExitStack() as opened:
        try:
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=parent,
            )
            opened.callback(os.close, descriptor)
            with os.fdopen(descriptor, "rb", buffering=0, closefd=False) as handle:
                before = os.fstat(handle.fileno())
                if before.st_nlink == 0:
                    raise FilePreconditionFailed("Project file changed during read")
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise FileUnavailable("Only single-link regular project files are supported")
                if before.st_size > WORKSPACE_FILE_MAX_BYTES:
                    raise FileLimitExceeded("Project file exceeds read limit")
                limit = WORKSPACE_FILE_MAX_BYTES + 1
                if read_budget is not None:
                    if before.st_size > read_budget[0]:
                        raise _SearchByteLimit
                    limit = min(limit, read_budget[0])
                content = bytearray()
                while len(content) < limit:
                    chunk = handle.read(limit - len(content))
                    if not chunk:
                        break
                    content.extend(chunk)
                    if read_budget is not None:
                        read_budget[0] -= len(chunk)
                after = os.fstat(handle.fileno())
                named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if _identity(before) != _identity(after) or _identity(after) != _identity(named):
                    raise FilePreconditionFailed("Project file changed during read")
        except OSError as error:
            raise _file_error(error) from error
    if len(content) > WORKSPACE_FILE_MAX_BYTES:
        raise FileLimitExceeded("Project file exceeds read limit")
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise FilePreconditionFailed("Project file version changed")
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise FileUnavailable("Project file is not UTF-8 text") from None
    if "\x00" in text:
        raise FileUnavailable("Binary project file is not supported")
    return {"path": path, "sha256": digest, "byte_size": len(content), "text": text}


def list_project_files(root_fd: int, path: str = "") -> dict:
    """List one directory; all entries (including excluded names) count to limit."""
    parts = _parts(path, root=True)
    entries = []
    excluded = 0
    with _directory(root_fd, parts) as directory, _filesystem_errors():
        before = os.fstat(directory)
        with os.scandir(directory) as scan:
            for count, entry in enumerate(scan, start=1):
                if count > WORKSPACE_DIRECTORY_MAX_ENTRIES:
                    raise FileLimitExceeded("Project directory exceeds entry limit")
                relative = "/".join([*parts, entry.name])
                try:
                    _parts(relative)
                    info = entry.stat(follow_symlinks=False)
                except OSError as error:
                    mapped = _file_error(error)
                    if isinstance(mapped, FileServiceUnavailable):
                        raise mapped from error
                    excluded += 1
                    continue
                except (ValueError, FileUnavailable):
                    excluded += 1
                    continue
                kind = "directory" if stat.S_ISDIR(info.st_mode) else "file"
                if not stat.S_ISDIR(info.st_mode) and (
                    not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                ):
                    excluded += 1
                    continue
                entries.append({"path": relative, "name": entry.name, "kind": kind,
                                "byte_size": info.st_size if kind == "file" else None})
        if _identity(before) != _identity(os.fstat(directory)):
            raise FilePreconditionFailed("Project directory changed during listing")
    return {"path": path, "entries": sorted(entries, key=lambda item: item["name"]),
            "excluded_entries": excluded}


def create_project_file(root_fd: int, staging_fd: int, path: str, text: str) -> dict:
    """Publish a fully written file atomically, only if destination is absent.

    No source paths or secrets are returned. After a successful link, failures
    are outcome_unknown, not retryable failure. The private staging directory
    and target parent must not be writable by an adversary during publication.
    """
    parts = _parts(path)
    if not isinstance(text, str) or "\x00" in text:
        raise ValueError("Project content must be UTF-8 text")
    if len(text) > WORKSPACE_FILE_MAX_BYTES:
        raise FileLimitExceeded("Project file exceeds write limit")
    try:
        content = text.encode("utf-8")
    except UnicodeError:
        raise ValueError("Project content must be UTF-8 text") from None
    if len(content) > WORKSPACE_FILE_MAX_BYTES:
        raise FileLimitExceeded("Project file exceeds write limit")
    with _directory(root_fd, parts[:-1]) as parent:
        with _filesystem_errors():
            staging = os.fstat(staging_fd)
        if (
            not stat.S_ISDIR(staging.st_mode)
            or staging.st_dev != os.fstat(parent).st_dev
            or staging.st_uid != os.geteuid()
            or staging.st_mode & 0o077
        ):
            raise FileUnavailable("Private staging filesystem unavailable")
        temporary = WORKSPACE_UPLOAD_PREFIX + uuid4().hex
        created = False
        published = False
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=staging_fd,
            )
            created = True
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                written = os.fstat(handle.fileno())
            try:
                os.link(temporary, parts[-1], src_dir_fd=staging_fd, dst_dir_fd=parent,
                        follow_symlinks=False)
            except FileExistsError:
                raise FilePreconditionFailed("Project file already exists") from None
            published = True
            os.unlink(temporary, dir_fd=staging_fd)
            created = False
            os.fsync(parent)
            os.fsync(staging_fd)
            # A held directory fd cannot follow a replacement symlink, but its
            # original path may no longer name it. Do not report that as success.
            try:
                with _directory(root_fd, parts[:-1]) as current_parent:
                    held = os.fstat(parent)
                    current = os.fstat(current_parent)
                    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                        raise FileOutcomeUnknown("Project parent changed during publication")
                observed = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
                    or (observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns)
                    != (written.st_dev, written.st_ino, written.st_size, written.st_mtime_ns)
                ):
                    raise FileOutcomeUnknown("Project publication requires reconciliation")
            except (FileUnavailable, FileServiceUnavailable):
                raise FileOutcomeUnknown("Project parent changed during publication") from None
        except OSError as error:
            if published:
                raise FileOutcomeUnknown("Project publication requires reconciliation") from None
            if error.errno in (errno.ENOSPC, errno.EDQUOT):
                raise FileLimitExceeded("Workspace storage limit reached") from None
            raise _file_error(error) from error
        finally:
            if created:
                try:
                    os.unlink(temporary, dir_fd=staging_fd)
                except OSError:
                    # Private staging cleanup is best-effort; never unlink the
                    # destination, which may already be an acknowledged file.
                    pass
    return {"path": path, "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content)}


def search_project_files(root_fd: int, query: str, path: str = "") -> dict:
    """Bounded literal search, not regex execution; explicitly reports truncation."""
    _parts(path, root=True)
    if (
        not isinstance(query, str) or not query or len(query) > WORKSPACE_SEARCH_QUERY_MAX_BYTES
        or len(query.encode("utf-8")) > WORKSPACE_SEARCH_QUERY_MAX_BYTES
    ):
        raise ValueError("A bounded non-empty search query is required")
    deadline = time.monotonic() + WORKSPACE_SEARCH_TIMEOUT_SECONDS
    pending = [path]
    matches = []
    files = attempted = visited = skipped = 0
    read_budget = [WORKSPACE_SEARCH_MAX_BYTES]
    reason = None
    while pending and reason is None:
        if time.monotonic() >= deadline:
            reason = "time_limit"
            break
        directory = pending.pop()
        visited += 1
        if visited > WORKSPACE_SEARCH_MAX_DIRECTORIES:
            reason = "directory_limit"
            break
        try:
            listing = list_project_files(root_fd, directory)
        except FileLimitExceeded:
            skipped += 1
            reason = "entry_limit"
            break
        except (FileUnavailable, FilePreconditionFailed):
            skipped += 1
            reason = "subtree_unavailable"
            break
        skipped += listing["excluded_entries"]
        for entry in listing["entries"]:
            if time.monotonic() >= deadline:
                reason = "time_limit"
                break
            if entry["kind"] == "directory":
                if len(pending) >= WORKSPACE_SEARCH_MAX_PENDING_DIRECTORIES:
                    reason = "directory_limit"
                    break
                pending.append(entry["path"])
                continue
            if attempted >= WORKSPACE_SEARCH_MAX_FILES:
                reason = "file_limit"
                break
            attempted += 1
            try:
                result = _read_project_file(root_fd, entry["path"], read_budget=read_budget)
            except _SearchByteLimit:
                reason = "byte_limit"
                break
            except (FileUnavailable, FilePreconditionFailed, FileLimitExceeded):
                skipped += 1
                continue
            files += 1
            for number, line in enumerate(result["text"].split("\n"), start=1):
                if time.monotonic() >= deadline:
                    reason = "time_limit"
                    break
                if query in line:
                    matches.append({"path": entry["path"], "line": number,
                                    "sha256": result["sha256"]})
                    if len(matches) >= WORKSPACE_SEARCH_MAX_MATCHES:
                        reason = "match_limit"
                        break
            if reason:
                break
    return {"matches": matches, "truncated": reason is not None, "limit": reason,
            "complete": reason is None and skipped == 0,
            "files_scanned": files, "files_attempted": attempted,
            "read_budget_bytes": WORKSPACE_SEARCH_MAX_BYTES - read_budget[0],
            "skipped_entries": skipped}
