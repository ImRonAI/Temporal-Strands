#!/usr/bin/env python3
"""Local release safety gate for GCE desktop release bundles.

Validates a prebuilt, trusted ``release.tar.gz`` against an externally supplied
SHA256 digest *without* extracting members to disk and *without* executing any
bundle content. The gate fails closed: any policy violation, malformed input,
or unexpected archive structure produces a non-zero exit.

Checks performed, in order:

1. The supplied SHA256 is well formed and matches the archive bytes. The
   archive is opened exactly once (``O_NOFOLLOW``, no symlink following); the
   same descriptor is ``fstat``-checked, hashed, and then handed to
   ``tarfile`` so the parsed bytes are the hashed bytes. On mismatch no tar
   structure is parsed. Descriptor identity metadata is re-checked after
   validation.
2. Every tar member is a regular file or directory (no symlinks, hardlinks,
   devices, FIFOs), has a safe relative path (no absolute paths, backslashes,
   ``.``/``..`` components, control characters), is unique, carries no
   setuid/setgid bits, and stays within member/total size and count limits.
3. No member matches forbidden content: ``.env*`` basenames, the exact
   supplied service-account credential basename, or known runtime/cache/VCS
   paths (``node_modules``, ``.git``, ``.venv``, ``__pycache__``, ``.next/cache``,
   compiled Python, ...). Member contents are never inspected for this step.
4. The layout contains the sibling ``app/`` and ``strands-tools/src/`` roots,
   the marker files the startup script requires, and nothing else at the top
   level except the release manifest.
5. ``release-manifest.json`` (schema version 1) is present, small, valid JSON,
   matches a narrow explicit schema, declares Linux amd64, and lists at least
   one prebuilt artifact whose declared size and SHA256 match the member bytes
   streamed from the archive.

Scope and honesty notes:

- Artifact digests are verified by streaming member bytes through
  ``hashlib``; nothing is written to disk.
- Member/layout safety errors short-circuit manifest parsing and artifact
  reads: once the archive is known unsafe, no further member content is read.
- The single-descriptor design defeats pathname swaps between hash and parse.
  It does **not** defend against a writer that modifies the file *in place*
  through another descriptor while validation runs. The archive must live in
  an approved staging directory writable only by the deployer identity; that
  operational prerequisite is outside this gate.
- Manifest provenance fields (builder, build id, source repository, source
  commit) are *declarations* attested only by the outer archive digest. This
  gate does not verify signatures, SLSA attestations, or the existence of the
  referenced commit. Treat a passing result as "structurally safe and
  internally consistent", not "independently proven provenance".
- No version pins or digests are embedded here; publishers supply them in the
  manifest of each approved release.

CLI::

    python scripts/validate_desktop_release.py --archive release.tar.gz \
        --sha256 <64 lowercase hex> [--json]

Exit codes: 0 pass, 1 policy failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import errno
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any

GATE_VERSION = 1

# --- Bundle layout -----------------------------------------------------------

MANIFEST_NAME = "release-manifest.json"
REQUIRED_ROOTS: tuple[str, ...] = ("app", "strands-tools/src")
REQUIRED_FILES: tuple[str, ...] = (
    "app/package.json",
    "strands-tools/src/strands_graph_tool/__init__.py",
)
REQUIRED_DIRECTORIES: tuple[str, ...] = ("strands-tools/src/skills",)
ALLOWED_TOP_LEVEL = frozenset({"app", "strands-tools", MANIFEST_NAME})

# --- Forbidden content -------------------------------------------------------

FORBIDDEN_BASENAME_PREFIXES: tuple[str, ...] = (".env",)
# Exact basename of the user-supplied service-account key. The gate compares
# names only; it never opens or reads the real credential.
FORBIDDEN_CREDENTIAL_BASENAMES = frozenset({"fair-expanse-493212-h8-138622c839d2.json"})
FORBIDDEN_BASENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
FORBIDDEN_EXTENSIONS: tuple[str, ...] = (".pyc", ".pyo")
FORBIDDEN_PATH_COMPONENTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".turbo",
        ".pnpm-store",
        ".runtime",
        ".worktrees",
        ".kilo",
        ".kilocode",
        ".opencode",
        ".omo",
        ".codegraph",
        ".cache",
    }
)
# Consecutive path components that are forbidden even though the first
# component alone (e.g. a prebuilt ``.next``) may be legitimate.
FORBIDDEN_PATH_SEQUENCES: tuple[tuple[str, ...], ...] = ((".next", "cache"),)
FORBIDDEN_MODE_BITS = stat.S_ISUID | stat.S_ISGID

# --- Limits ------------------------------------------------------------------

MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_MEMBERS = 200_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 4 * 1024**3
MAX_MEMBER_BYTES = 1 * 1024**3
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACTS = 1024
MAX_ERRORS = 100
READ_CHUNK_BYTES = 1024 * 1024
GZIP_MAGIC = b"\x1f\x8b"

# --- Manifest schema (version 1) ---------------------------------------------

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_KEYS = frozenset(
    {"schema_version", "release_id", "created_at", "platform", "provenance", "artifacts"}
)
PLATFORM_KEYS = frozenset({"os", "arch"})
REQUIRED_PLATFORM: dict[str, str] = {"os": "linux", "arch": "amd64"}
PROVENANCE_KEYS = frozenset({"builder", "build_id", "source_repository", "source_commit"})
ARTIFACT_KEYS = frozenset({"path", "sha256", "size_bytes"})
MAX_SHORT_STRING = 256
MAX_REPOSITORY_STRING = 512

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
CREATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclasses.dataclass(frozen=True)
class Limits:
    max_archive_bytes: int = MAX_ARCHIVE_BYTES
    max_members: int = MAX_MEMBERS
    max_total_uncompressed_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES
    max_member_bytes: int = MAX_MEMBER_BYTES
    max_manifest_bytes: int = MAX_MANIFEST_BYTES
    max_artifacts: int = MAX_ARTIFACTS


@dataclasses.dataclass
class ValidationResult:
    archive: str
    ok: bool = False
    errors: list[str] = dataclasses.field(default_factory=list)
    sha256: str | None = None
    member_count: int = 0
    total_uncompressed_bytes: int = 0
    release_id: str | None = None
    artifact_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["gate_version"] = GATE_VERSION
        return data


class _Errors:
    """Bounded error collector."""

    def __init__(self, cap: int = MAX_ERRORS) -> None:
        self.items: list[str] = []
        self._cap = cap
        self._suppressed = 0

    def add(self, message: str) -> None:
        if len(self.items) < self._cap:
            self.items.append(message)
        else:
            self._suppressed += 1

    def finish(self) -> list[str]:
        if self._suppressed:
            self.items.append(f"{self._suppressed} additional error(s) suppressed")
        return self.items

    def __bool__(self) -> bool:
        return bool(self.items) or self._suppressed > 0


# --- Digest helpers ----------------------------------------------------------


def normalize_sha256(value: str) -> str:
    """Return a lowercase 64-hex digest or raise ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError("sha256 must be a string")
    candidate = value.strip().lower()
    if candidate.startswith("sha256:"):
        candidate = candidate[len("sha256:") :]
    if not SHA256_RE.fullmatch(candidate):
        raise ValueError("sha256 must be exactly 64 hexadecimal characters")
    return candidate


def sha256_fileobj(handle: Any) -> str:
    """Hash an already-open binary file object from its current position."""
    digest = hashlib.sha256()
    while True:
        chunk = handle.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _fd_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Metadata that must not change between hashing and parsing.

    ``st_nlink`` catches the hashed inode being unlinked or renamed over (the
    pathname a deployer extracts later would then refer to different bytes);
    size/mtime/ctime catch in-place rewrites that the OS surfaces.
    """
    return (
        info.st_dev,
        info.st_ino,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_archive_once(path: Path, limits: Limits) -> tuple[Any, os.stat_result] | str:
    """Open the archive with ``O_NOFOLLOW`` and validate via ``fstat``.

    Returns ``(binary file object, stat)`` or an error string. All identity,
    size, and type checks are made on the opened descriptor so that a later
    pathname replacement cannot substitute different bytes.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return "archive path must not be a symlink"
        return f"archive is not readable: {exc.strerror or exc}"
    try:
        info = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        return f"archive is not readable: {exc.strerror or exc}"
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        return "archive path must be a regular file"
    if info.st_size > limits.max_archive_bytes:
        os.close(fd)
        return (
            f"archive exceeds max compressed size {limits.max_archive_bytes} bytes: size={info.st_size}"
        )
    return os.fdopen(fd, "rb", closefd=True), info


def _member_sha256(tar: tarfile.TarFile, member: tarfile.TarInfo) -> str | None:
    """Stream a regular member's bytes through SHA256 without writing to disk."""
    handle = tar.extractfile(member)
    if handle is None:
        return None
    digest = hashlib.sha256()
    with handle:
        while True:
            chunk = handle.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


# --- Member checks -----------------------------------------------------------


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def check_member_name(name: str) -> list[str]:
    """Path-safety checks on a tar member name (already ``/``-stripped for dirs)."""
    errors: list[str] = []
    if name == "":
        return ["member has an empty name"]
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return [f"member name is not valid UTF-8: {name!r}"]
    if "\x00" in name:
        errors.append(f"member name contains NUL: {name!r}")
    if _has_control_chars(name):
        errors.append(f"member name contains control characters: {name!r}")
    if "\\" in name:
        errors.append(f"member name contains a backslash: {name!r}")
    if name.startswith("/"):
        errors.append(f"member name is absolute: {name!r}")
    if re.match(r"^[A-Za-z]:", name):
        errors.append(f"member name has a drive prefix: {name!r}")
    parts = name.split("/")
    if any(part == "" for part in parts):
        errors.append(f"member name has an empty path component: {name!r}")
    if "." in parts:
        errors.append(f"member name contains a '.' component: {name!r}")
    if ".." in parts:
        errors.append(f"member name contains a '..' component: {name!r}")
    return errors


def check_member_policy(name: str) -> list[str]:
    """Forbidden secrets, credentials, and runtime/cache/VCS paths by name only."""
    errors: list[str] = []
    parts = name.split("/")
    basename = parts[-1]
    for component in parts:
        if component in FORBIDDEN_PATH_COMPONENTS:
            errors.append(f"forbidden path component {component!r}: {name!r}")
            break
    for sequence in FORBIDDEN_PATH_SEQUENCES:
        width = len(sequence)
        if any(tuple(parts[i : i + width]) == sequence for i in range(len(parts) - width + 1)):
            errors.append(f"forbidden path sequence {'/'.join(sequence)!r}: {name!r}")
    # Any component (not just the basename) so ``.env/`` or ``.env.d/`` directory
    # descendants cannot smuggle environment material past the check.
    if any(
        component.startswith(prefix)
        for component in parts
        for prefix in FORBIDDEN_BASENAME_PREFIXES
    ):
        errors.append(f"forbidden environment file: {name!r}")
    if basename in FORBIDDEN_CREDENTIAL_BASENAMES or basename.casefold() in {
        item.casefold() for item in FORBIDDEN_CREDENTIAL_BASENAMES
    }:
        errors.append(f"forbidden credential basename: {name!r}")
    if basename in FORBIDDEN_BASENAMES:
        errors.append(f"forbidden basename: {name!r}")
    if any(basename.endswith(ext) for ext in FORBIDDEN_EXTENSIONS):
        errors.append(f"forbidden compiled artifact: {name!r}")
    return errors


def check_member_type(member: tarfile.TarInfo, limits: Limits) -> list[str]:
    errors: list[str] = []
    name = member.name
    if member.issym():
        errors.append(f"symlink entries are not allowed: {name!r} -> {member.linkname!r}")
    elif member.islnk():
        errors.append(f"hardlink entries are not allowed: {name!r} -> {member.linkname!r}")
    elif member.isdev():
        errors.append(f"device/FIFO entries are not allowed: {name!r}")
    elif not (member.isreg() or member.isdir()):
        errors.append(f"unsupported entry type {member.type!r}: {name!r}")
    if member.mode & FORBIDDEN_MODE_BITS:
        errors.append(f"setuid/setgid bits are not allowed: {name!r} mode={oct(member.mode)}")
    if member.size < 0:
        errors.append(f"negative member size: {name!r}")
    if member.isdir() and member.size != 0:
        errors.append(f"directory entry declares non-zero size: {name!r}")
    if member.size > limits.max_member_bytes:
        errors.append(
            f"member exceeds max size {limits.max_member_bytes} bytes: {name!r} size={member.size}"
        )
    return errors


def _scan_members(
    tar: tarfile.TarFile, limits: Limits, errors: _Errors
) -> tuple[dict[str, tarfile.TarInfo], int, int]:
    """Single pass over headers. Returns (members-by-name, count, total bytes)."""
    members: dict[str, tarfile.TarInfo] = {}
    count = 0
    total = 0
    for member in tar:
        count += 1
        if count > limits.max_members:
            errors.add(f"archive exceeds max member count {limits.max_members}")
            break
        total += max(member.size, 0)
        if total > limits.max_total_uncompressed_bytes:
            errors.add(
                f"archive exceeds max total uncompressed size {limits.max_total_uncompressed_bytes} bytes"
            )
            break
        name = member.name
        for message in check_member_name(name):
            errors.add(message)
        for message in check_member_policy(name):
            errors.add(message)
        for message in check_member_type(member, limits):
            errors.add(message)
        if name in members:
            errors.add(f"duplicate member: {name!r}")
            continue
        members[name] = member
    return members, count, total


# --- Layout checks -----------------------------------------------------------


def _implied_directories(names: set[str]) -> set[str]:
    implied: set[str] = set()
    for name in names:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            implied.add("/".join(parts[:depth]))
    return implied


def check_layout(members: dict[str, tarfile.TarInfo]) -> list[str]:
    errors: list[str] = []
    names = set(members)
    implied = _implied_directories(names)
    explicit_dirs = {name for name, member in members.items() if member.isdir()}
    all_dirs = implied | explicit_dirs

    for name, member in members.items():
        if member.isreg() and name in implied:
            errors.append(f"file and directory share a path: {name!r}")

    top_level = {name.split("/", 1)[0] for name in names}
    for entry in sorted(top_level - ALLOWED_TOP_LEVEL):
        errors.append(f"unexpected top-level entry: {entry!r}")

    for root in REQUIRED_ROOTS:
        if root not in all_dirs:
            errors.append(f"required root directory missing: {root!r}")
    for path in REQUIRED_FILES:
        member = members.get(path)
        if member is None or not member.isreg():
            errors.append(f"required file missing or not a regular file: {path!r}")
    for path in REQUIRED_DIRECTORIES:
        if path not in all_dirs:
            errors.append(f"required directory missing: {path!r}")
    manifest = members.get(MANIFEST_NAME)
    if manifest is None or not manifest.isreg():
        errors.append(f"release manifest missing or not a regular file: {MANIFEST_NAME!r}")
    return errors


# --- Manifest checks ---------------------------------------------------------


def load_manifest(
    tar: tarfile.TarFile, member: tarfile.TarInfo, limits: Limits
) -> tuple[dict[str, Any] | None, list[str]]:
    if member.size > limits.max_manifest_bytes:
        return None, [
            f"manifest exceeds max size {limits.max_manifest_bytes} bytes: size={member.size}"
        ]
    handle = tar.extractfile(member)
    if handle is None:
        return None, ["manifest member is not readable"]
    with handle:
        raw = handle.read(limits.max_manifest_bytes + 1)
    if len(raw) > limits.max_manifest_bytes:
        return None, [f"manifest exceeds max size {limits.max_manifest_bytes} bytes"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["manifest is not valid UTF-8"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"manifest is not valid JSON: {exc.msg} at line {exc.lineno}"]
    if not isinstance(data, dict):
        return None, ["manifest top level must be a JSON object"]
    return data, []


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_short_string(value: Any, field: str, max_len: int, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"manifest {field} must be a non-empty string")
        return False
    if len(value) > max_len:
        errors.append(f"manifest {field} exceeds {max_len} characters")
        return False
    if _has_control_chars(value):
        errors.append(f"manifest {field} contains control characters")
        return False
    return True


def _check_exact_keys(obj: Any, expected: frozenset[str], field: str, errors: list[str]) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"manifest {field} must be an object")
        return False
    unknown = sorted(set(obj) - expected)
    missing = sorted(expected - set(obj))
    for key in unknown:
        errors.append(f"manifest {field} has unknown key {key!r}")
    for key in missing:
        errors.append(f"manifest {field} is missing key {key!r}")
    return not unknown and not missing


def _under_required_root(path: str) -> bool:
    return any(path == root or path.startswith(root + "/") for root in REQUIRED_ROOTS)


def validate_manifest(
    manifest: dict[str, Any],
    members: dict[str, tarfile.TarInfo],
    tar: tarfile.TarFile,
    limits: Limits,
) -> tuple[list[str], str | None, int]:
    """Validate the manifest schema and artifact digests.

    Returns ``(errors, release_id, verified_artifact_count)``.
    """
    errors: list[str] = []
    if not _check_exact_keys(manifest, MANIFEST_KEYS, "root", errors):
        return errors, None, 0

    schema_version = manifest["schema_version"]
    if not _is_int(schema_version) or schema_version != MANIFEST_SCHEMA_VERSION:
        errors.append(
            f"manifest schema_version must be {MANIFEST_SCHEMA_VERSION}, got {schema_version!r}"
        )

    release_id: str | None = None
    if isinstance(manifest["release_id"], str) and RELEASE_ID_RE.fullmatch(manifest["release_id"]):
        release_id = manifest["release_id"]
    else:
        errors.append("manifest release_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")

    created_at = manifest["created_at"]
    if not isinstance(created_at, str) or not CREATED_AT_RE.fullmatch(created_at):
        errors.append("manifest created_at must be UTC in the form YYYY-MM-DDTHH:MM:SSZ")
    else:
        try:
            datetime.strptime(created_at, CREATED_AT_FORMAT)
        except ValueError:
            errors.append(f"manifest created_at is not a valid timestamp: {created_at!r}")

    platform = manifest["platform"]
    if _check_exact_keys(platform, PLATFORM_KEYS, "platform", errors):
        if platform != REQUIRED_PLATFORM:
            errors.append(
                f"manifest platform must be {REQUIRED_PLATFORM}, got {platform!r}"
            )

    provenance = manifest["provenance"]
    if _check_exact_keys(provenance, PROVENANCE_KEYS, "provenance", errors):
        _check_short_string(provenance["builder"], "provenance.builder", MAX_SHORT_STRING, errors)
        _check_short_string(provenance["build_id"], "provenance.build_id", MAX_SHORT_STRING, errors)
        _check_short_string(
            provenance["source_repository"],
            "provenance.source_repository",
            MAX_REPOSITORY_STRING,
            errors,
        )
        commit = provenance["source_commit"]
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            errors.append("manifest provenance.source_commit must be 40 lowercase hex characters")

    artifacts = manifest["artifacts"]
    verified = 0
    if not isinstance(artifacts, list):
        errors.append("manifest artifacts must be a list")
        return errors, release_id, verified
    if not artifacts:
        errors.append("manifest artifacts must declare at least one prebuilt artifact")
        return errors, release_id, verified
    if len(artifacts) > limits.max_artifacts:
        errors.append(f"manifest declares more than {limits.max_artifacts} artifacts")
        return errors, release_id, verified

    pending: list[tuple[tarfile.TarInfo, str, int]] = []
    seen_paths: set[str] = set()
    for index, artifact in enumerate(artifacts):
        field = f"artifacts[{index}]"
        if not _check_exact_keys(artifact, ARTIFACT_KEYS, field, errors):
            continue
        path = artifact["path"]
        digest = artifact["sha256"]
        size = artifact["size_bytes"]
        if not isinstance(path, str):
            errors.append(f"manifest {field}.path must be a string")
            continue
        name_errors = check_member_name(path)
        if name_errors:
            errors.append(f"manifest {field}.path is unsafe: {path!r}")
            continue
        if path in seen_paths:
            errors.append(f"manifest {field}.path is a duplicate: {path!r}")
            continue
        seen_paths.add(path)
        if not _under_required_root(path):
            errors.append(f"manifest {field}.path is outside required roots: {path!r}")
            continue
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append(f"manifest {field}.sha256 must be 64 lowercase hex characters")
            continue
        if not _is_int(size) or size < 0:
            errors.append(f"manifest {field}.size_bytes must be a non-negative integer")
            continue
        member = members.get(path)
        if member is None or not member.isreg():
            errors.append(f"manifest {field}.path is not a regular file in the archive: {path!r}")
            continue
        if member.size != size:
            errors.append(
                f"manifest {field}.size_bytes {size} does not match archive member size {member.size}: {path!r}"
            )
            continue
        pending.append((member, digest, index))

    # Verify digests in archive order so the gzip stream is read forward once.
    for member, digest, index in sorted(pending, key=lambda item: item[0].offset_data):
        actual = _member_sha256(tar, member)
        if actual is None:
            errors.append(f"manifest artifacts[{index}] member is not readable: {member.name!r}")
        elif actual != digest:
            errors.append(f"manifest artifacts[{index}] sha256 mismatch: {member.name!r}")
        else:
            verified += 1

    return errors, release_id, verified


# --- Entry point -------------------------------------------------------------


def validate_release(
    archive: str | os.PathLike[str], expected_sha256: str, limits: Limits | None = None
) -> ValidationResult:
    """Validate ``archive`` against ``expected_sha256`` and the bundle policy."""
    limits = limits or Limits()
    path = Path(archive)
    result = ValidationResult(archive=str(path))
    errors = _Errors()

    try:
        expected = normalize_sha256(expected_sha256)
    except ValueError as exc:
        result.errors = [f"invalid expected sha256: {exc}"]
        return result

    opened = _open_archive_once(path, limits)
    if isinstance(opened, str):
        result.errors = [opened]
        return result
    handle, info = opened
    identity = _fd_identity(info)

    try:
        with handle:
            # Hash, magic check, and tar parse all read from this one descriptor.
            actual = sha256_fileobj(handle)
            result.sha256 = actual
            if actual != expected:
                # Fail closed before parsing any untrusted structure.
                result.errors = [f"sha256 mismatch: expected {expected}, got {actual}"]
                return result

            handle.seek(0)
            if handle.read(len(GZIP_MAGIC)) != GZIP_MAGIC:
                result.errors = ["archive is not gzip-compressed"]
                return result
            handle.seek(0)

            try:
                with tarfile.open(fileobj=handle, mode="r:gz") as tar:
                    members, count, total = _scan_members(tar, limits, errors)
                    result.member_count = count
                    result.total_uncompressed_bytes = total
                    for message in check_layout(members):
                        errors.add(message)
                    # Once the archive is known unsafe (quota, traversal, link,
                    # forbidden content, layout), do not read member content.
                    if not errors:
                        manifest_member = members.get(MANIFEST_NAME)
                        if manifest_member is not None and manifest_member.isreg():
                            manifest, load_errors = load_manifest(tar, manifest_member, limits)
                            for message in load_errors:
                                errors.add(message)
                            if manifest is not None:
                                manifest_errors, release_id, verified = validate_manifest(
                                    manifest, members, tar, limits
                                )
                                for message in manifest_errors:
                                    errors.add(message)
                                result.release_id = release_id
                                result.artifact_count = verified
                    else:
                        errors.add("manifest not evaluated: archive failed member/layout safety")
            except (tarfile.TarError, EOFError, ValueError) as exc:
                errors.add(f"archive could not be parsed: {exc.__class__.__name__}: {exc}")

            # The descriptor is still the one we hashed; confirm the inode it
            # refers to was not replaced/rewritten while we read it.
            if _fd_identity(os.fstat(handle.fileno())) != identity:
                errors.add("archive identity changed during validation")
    except OSError as exc:
        errors.add(f"archive read failed: {exc.__class__.__name__}: {exc.strerror or exc}")

    result.errors = errors.finish()
    result.ok = not result.errors
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_desktop_release.py",
        description=(
            "Fail-closed local safety gate for a prebuilt GCE desktop release "
            "tar.gz. Nothing is extracted or executed."
        ),
    )
    parser.add_argument("--archive", required=True, help="Path to release .tar.gz")
    parser.add_argument(
        "--sha256", required=True, help="Externally supplied SHA256 of the archive (64 hex)"
    )
    parser.add_argument("--max-members", type=int, default=MAX_MEMBERS)
    parser.add_argument(
        "--max-total-uncompressed-bytes", type=int, default=MAX_TOTAL_UNCOMPRESSED_BYTES
    )
    parser.add_argument("--max-member-bytes", type=int, default=MAX_MEMBER_BYTES)
    parser.add_argument("--max-archive-bytes", type=int, default=MAX_ARCHIVE_BYTES)
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON on stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for name in ("max_members", "max_total_uncompressed_bytes", "max_member_bytes", "max_archive_bytes"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be a positive integer")
    limits = Limits(
        max_archive_bytes=args.max_archive_bytes,
        max_members=args.max_members,
        max_total_uncompressed_bytes=args.max_total_uncompressed_bytes,
        max_member_bytes=args.max_member_bytes,
    )
    result = validate_release(args.archive, args.sha256, limits)
    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    elif result.ok:
        sys.stdout.write(
            f"OK release={result.release_id} members={result.member_count} "
            f"artifacts={result.artifact_count} sha256={result.sha256}\n"
        )
    else:
        for message in result.errors:
            sys.stderr.write(f"FAIL: {message}\n")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
