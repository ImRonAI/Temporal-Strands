"""Immutable local catalog for native browser screenshots.

Image bytes never enter Temporal history. The browser activity captures a
screenshot through the native ``execute_cdp`` action, persists the bytes here
under an exclusive-create 0600 file with a JSON sidecar, and returns only a
compact descriptor (id, hash, mime, size, workflow scope). Readers resolve the
descriptor back to bytes through :func:`load_browser_observation`, which
re-verifies provenance and integrity from the sidecar, never from a caller
supplied path.

Constants below are module-local until the config owner reconciles them into
``config.py`` (see ``BROWSER_OBSERVATION_*``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

# --- Constants (temporary home; parent reconciles into config.py) ---
# Trusted private directory for observation bytes and sidecars. Relative paths
# resolve against the orchestrator working directory like AGENT_FILE_STORE_DIR.
BROWSER_OBSERVATION_DIR = Path(
    os.environ.get("BROWSER_OBSERVATION_DIR", ".runtime/browser-observations")
)
# Hard upper bound for a single persisted screenshot.
BROWSER_OBSERVATION_MAX_BYTES = 10 * 1024 * 1024
# Native CDP capture settings (Page.captureScreenshot params).
BROWSER_OBSERVATION_CAPTURE_FORMAT: Literal["png", "jpeg"] = "jpeg"
BROWSER_OBSERVATION_CAPTURE_QUALITY = 85
# Public URL the UI loads screenshots from. The server route (owned by the
# parent) resolves it with load_browser_observation(); loopback-local only.
BROWSER_OBSERVATION_BASE_URL = os.environ.get(
    "ORCHESTRATOR_PUBLIC_URL", "http://localhost:8787"
).rstrip("/")
BROWSER_OBSERVATION_CONTENT_PATH = "/browser-observations/{observation_id}/content"

BROWSER_OBSERVATION_SCHEMA_VERSION = 1
BROWSER_OBSERVATION_ID_PATTERN = r"^[0-9a-f]{32}$"
_OBSERVATION_ID_RE = re.compile(BROWSER_OBSERVATION_ID_PATTERN)
_MIME_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg"}
_SIDECAR_SUFFIX = ".json"
_TOOL_NAME = "browser"


class BrowserObservationError(Exception):
    """Base class for catalog failures."""


class BrowserObservationNotFound(BrowserObservationError, LookupError):
    """No committed observation matches the id within the requested scope."""


class BrowserObservationInvalid(BrowserObservationError, ValueError):
    """Persisted bytes or sidecar fail integrity, bound, or schema checks."""


class BrowserObservation(BaseModel):
    """Compact, bytes-free descriptor carried in tool output and sidecars."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = BROWSER_OBSERVATION_SCHEMA_VERSION
    observation_id: Annotated[str, Field(pattern=BROWSER_OBSERVATION_ID_PATTERN)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime_type: Literal["image/png", "image/jpeg"]
    byte_size: Annotated[int, Field(strict=True, gt=0, le=BROWSER_OBSERVATION_MAX_BYTES)]
    captured_at: AwareDatetime
    workflow_id: Annotated[str, Field(min_length=1, max_length=256)]
    session_name: Annotated[str, Field(min_length=1, max_length=256)]
    action: Annotated[str, Field(min_length=1, max_length=64)]
    tool: Literal["browser"] = _TOOL_NAME

    def descriptor(self) -> dict[str, Any]:
        """JSON-safe descriptor for browserPreview / structured tool output."""
        return self.model_dump(mode="json")


def observation_dir() -> Path:
    return BROWSER_OBSERVATION_DIR


def browser_observation_url(observation_id: str, workflow_id: str | None = None) -> str:
    """URL the UI fetches. The workflow query binds the read to its scope."""
    if not _OBSERVATION_ID_RE.fullmatch(observation_id):
        raise BrowserObservationInvalid("Malformed observation id")
    url = BROWSER_OBSERVATION_BASE_URL + BROWSER_OBSERVATION_CONTENT_PATH.format(
        observation_id=observation_id
    )
    if workflow_id:
        url += "?" + urlencode({"workflow_id": workflow_id})
    return url


def _paths(observation_id: str, mime_type: str) -> tuple[Path, Path]:
    if not _OBSERVATION_ID_RE.fullmatch(observation_id):
        raise BrowserObservationInvalid("Malformed observation id")
    base = observation_dir()
    return base / f"{observation_id}{_MIME_EXTENSIONS[mime_type]}", base / f"{observation_id}{_SIDECAR_SUFFIX}"


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def record_browser_observation(
    image: bytes,
    *,
    mime_type: str,
    workflow_id: str,
    session_name: str,
    action: str,
    captured_at: datetime | None = None,
) -> BrowserObservation:
    """Persist screenshot bytes immutably and return the trusted descriptor.

    Bytes are written first, then the sidecar. A sidecar's presence marks the
    record committed; a crash between the two leaves an orphan image that is
    never resolvable. Records are never updated in place.
    """
    if not isinstance(image, (bytes, bytearray)) or len(image) == 0:
        raise BrowserObservationInvalid("Empty screenshot")
    if len(image) > BROWSER_OBSERVATION_MAX_BYTES:
        raise BrowserObservationInvalid("Screenshot exceeds BROWSER_OBSERVATION_MAX_BYTES")
    if mime_type not in _MIME_EXTENSIONS:
        raise BrowserObservationInvalid(f"Unsupported observation mime type: {mime_type}")
    record = BrowserObservation(
        observation_id=uuid4().hex,
        sha256=hashlib.sha256(image).hexdigest(),
        mime_type=mime_type,  # type: ignore[arg-type]
        byte_size=len(image),
        captured_at=captured_at or datetime.now(timezone.utc),
        workflow_id=workflow_id,
        session_name=session_name,
        action=action,
    )
    base = observation_dir()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    image_path, sidecar_path = _paths(record.observation_id, record.mime_type)
    _write_exclusive(image_path, bytes(image))
    _write_exclusive(sidecar_path, json.dumps(record.descriptor(), sort_keys=True).encode("utf-8"))
    return record


def _read_bounded(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if not os.path.isfile(path) or os.path.islink(path):
            raise BrowserObservationInvalid("Observation path is not a regular file")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise BrowserObservationInvalid("Observation exceeds size bound")
    return data


def _load_record(observation_id: str) -> BrowserObservation:
    if not isinstance(observation_id, str) or not _OBSERVATION_ID_RE.fullmatch(observation_id):
        raise BrowserObservationNotFound("Unknown observation")
    sidecar_path = observation_dir() / f"{observation_id}{_SIDECAR_SUFFIX}"
    try:
        raw = _read_bounded(sidecar_path, 64 * 1024)
    except FileNotFoundError as error:
        raise BrowserObservationNotFound("Unknown observation") from error
    try:
        record = BrowserObservation.model_validate_json(raw)
    except ValidationError as error:
        raise BrowserObservationInvalid("Corrupt observation sidecar") from error
    if record.observation_id != observation_id:
        raise BrowserObservationInvalid("Observation sidecar id mismatch")
    return record


def load_browser_observation(
    observation_id: str, workflow_id: str | None
) -> tuple[bytes, str]:
    """Return ``(bytes, mime_type)`` for a committed observation.

    ``workflow_id`` binds the read to the owning workflow; a mismatch is
    reported as not found so ids cannot be probed across scopes. ``None``
    skips the scope check and is only acceptable for the loopback-local server
    route until desktop auth exists. Bytes are re-hashed against the sidecar
    on every read; a mismatch raises :class:`BrowserObservationInvalid`.
    """
    record = _load_record(observation_id)
    if workflow_id is not None and record.workflow_id != workflow_id:
        raise BrowserObservationNotFound("Unknown observation")
    image_path, _ = _paths(record.observation_id, record.mime_type)
    try:
        data = _read_bounded(image_path, BROWSER_OBSERVATION_MAX_BYTES)
    except FileNotFoundError as error:
        raise BrowserObservationNotFound("Unknown observation") from error
    if len(data) != record.byte_size or hashlib.sha256(data).hexdigest() != record.sha256:
        raise BrowserObservationInvalid("Observation bytes do not match sidecar")
    return data, record.mime_type


def resolve_browser_observation(descriptor: Any, *, workflow_id: str) -> BrowserObservation | None:
    """Validate an untrusted descriptor against the catalog for one workflow.

    Returns the trusted catalog record only when the descriptor is well formed
    and every provenance field (id, sha256, mime, size, workflow) matches the
    committed sidecar exactly. Anything else returns ``None``.
    """
    if not isinstance(descriptor, dict):
        return None
    try:
        claimed = BrowserObservation.model_validate(descriptor)
    except ValidationError:
        return None
    if claimed.workflow_id != workflow_id:
        return None
    try:
        record = _load_record(claimed.observation_id)
    except BrowserObservationError:
        return None
    if record != claimed:
        return None
    return record


def browser_result_observation(result: Any, *, workflow_id: str) -> BrowserObservation | None:
    """Extract and resolve the descriptor from a native browser tool envelope.

    ``result`` is the parsed outer JSON the activity returned
    (``{"status", "content", "browserPreview", "browserObservation"}``). Only a
    successful envelope with a top-level ``browserObservation`` is considered.
    """
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    if not isinstance(result.get("content"), list):
        return None
    return resolve_browser_observation(result.get("browserObservation"), workflow_id=workflow_id)
