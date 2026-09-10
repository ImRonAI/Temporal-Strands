"""Immutable observation metadata and physical-coordinate validation.

No bytes, storage keys, URLs, or provider credentials enter these records. The
service must resolve the artifact in its trusted scoped catalog before use.
"""

import hashlib
import io
import json
import os
import stat
import tempfile
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal
from uuid import UUID, uuid4

from PIL import Image
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from config import DESKTOP_MAX_DIMENSION, DESKTOP_OBSERVATION_MAX_BYTES
from workspace_state import StateConflict


class ObservationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: Annotated[str, Field(min_length=1, max_length=128)]
    generation: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mime_type: Literal["image/png", "image/jpeg"]
    byte_size: Annotated[int, Field(strict=True, gt=0, le=DESKTOP_OBSERVATION_MAX_BYTES)]
    width: Annotated[int, Field(strict=True, gt=0, le=DESKTOP_MAX_DIMENSION)]
    height: Annotated[int, Field(strict=True, gt=0, le=DESKTOP_MAX_DIMENSION)]
    captured_at: AwareDatetime
    desktop_epoch: Annotated[int, Field(strict=True, gt=0)]
    operation_id: Annotated[str, Field(min_length=1, max_length=128)]
    # Uncropped physical display geometry is authoritative, not CSS dimensions.
    display_width: Annotated[int, Field(strict=True, gt=0, le=DESKTOP_MAX_DIMENSION)]
    display_height: Annotated[int, Field(strict=True, gt=0, le=DESKTOP_MAX_DIMENSION)]
    crop_x: Annotated[int, Field(strict=True, ge=0)] = 0
    crop_y: Annotated[int, Field(strict=True, ge=0)] = 0
    # Image pixels per physical pixel, permitting downscaled observations only.
    scale: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 1.0

    @model_validator(mode="after")
    def crop_fits_display(self) -> "ObservationRef":
        if (
            self.crop_x + self.width / self.scale > self.display_width
            or self.crop_y + self.height / self.scale > self.display_height
        ):
            raise ValueError("Observation crop exceeds physical display")
        return self


@contextmanager
def _artifact_directory(namespace: str, workflow_id: str, *, create: bool = False) -> Iterator[tuple[int, int]]:
    if any(not isinstance(value, str) or not value or "\0" in value for value in (namespace, workflow_id)):
        raise ValueError("Observation requires a namespace and workflow ID")
    scope = hashlib.sha256(f"{namespace}\0{workflow_id}".encode()).hexdigest()
    root = Path(os.environ.get("DESKTOP_ARTIFACT_ROOT", str(Path(tempfile.gettempdir()) / "kilo" / "gwen-desktop-artifacts")))
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(root, flags)
    try:
        if create:
            with suppress(FileExistsError):
                os.mkdir(scope, mode=0o700, dir_fd=root_fd)
        scope_fd = os.open(scope, flags, dir_fd=root_fd)
        try:
            yield root_fd, scope_fd
        finally:
            os.close(scope_fd)
    finally:
        os.close(root_fd)


def _read_artifact(directory: int, name: str, limit: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Observation file must be regular with exactly one link")
        if not 0 < info.st_size <= limit:
            raise ValueError("Observation file exceeds size limit")
        data = file.read(limit + 1)
        if len(data) != info.st_size:
            raise ValueError("Observation file changed during read")
        return data


def _check_runtime(root: int, ref: ObservationRef, namespace: str, workflow_id: str, *, storing: bool = False, for_display: bool = False) -> dict[str, Any]:
    runtime = json.loads(_read_artifact(root, "runtime.json", 4096))
    if (
        not isinstance(runtime, dict)
        or type(runtime.get("epoch")) is not int
        or (not for_display and runtime["epoch"] != ref.desktop_epoch)
        or runtime.get("owner") != {"namespace": namespace, "workflow_id": workflow_id}
        or (not for_display and runtime.get("mode") not in ({"agent", "stopping"} if storing else {"agent"}))
    ):
        raise StateConflict("Desktop observation is not owned by the current agent runtime")
    return runtime


def _check_image_size(width: int, height: int) -> None:
    # Bound decoded memory independently of PNG compression: at most one pixel
    # per configured encoded-byte budget (10 Mi pixels with the default budget).
    if not (
        0 < width <= DESKTOP_MAX_DIMENSION and 0 < height <= DESKTOP_MAX_DIMENSION
        and width * height <= DESKTOP_OBSERVATION_MAX_BYTES
    ):
        raise ValueError("Observation dimensions or pixel count exceed limit")


def store_observation(
    image: Image.Image, *, namespace: str, workflow_id: str, operation_id: str, desktop_epoch: int,
) -> ObservationRef:
    """Store one immutable PNG and scoped manifest; never write runtime ownership.

    The root and its parents must be controlled by the trusted runtime, not by
    desktop/project processes. Each exclusive UUID artifact has generation 1.
    """
    _check_image_size(*image.size)
    pixels = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    pixels.info.clear()
    buffer = io.BytesIO()
    try:
        pixels.save(buffer, format="PNG")
        data = buffer.getvalue()
    finally:
        pixels.close()
    if len(data) > DESKTOP_OBSERVATION_MAX_BYTES:
        raise ValueError("Observation PNG exceeds size limit")
    ref = ObservationRef(
        artifact_id=str(uuid4()), generation="1", sha256=hashlib.sha256(data).hexdigest(),
        mime_type="image/png", byte_size=len(data), width=image.width, height=image.height,
        captured_at=datetime.now(timezone.utc), desktop_epoch=desktop_epoch, operation_id=operation_id,
        display_width=image.width, display_height=image.height,
    )
    manifest = json.dumps({
        "namespace": namespace, "workflow_id": workflow_id, "observation": ref.model_dump(mode="json"),
    }, separators=(",", ":"), allow_nan=False).encode()
    if len(manifest) > 16_384:
        raise ValueError("Observation manifest exceeds size limit")
    with _artifact_directory(namespace, workflow_id, create=True) as (root, directory):
        _check_runtime(root, ref, namespace, workflow_id, storing=True)
        created = []
        try:
            for suffix, content in (("png", data), ("json", manifest)):
                name = f"{ref.artifact_id}.{suffix}"
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
                created.append(name)
                with os.fdopen(fd, "wb") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
            _check_runtime(root, ref, namespace, workflow_id, storing=True)
            os.fsync(directory)
        except BaseException:
            for name in created:
                os.unlink(name, dir_fd=directory)
            raise
    return ref


def resolve_observation(ref: ObservationRef, *, namespace: str, workflow_id: str, for_display: bool = False) -> bytes:
    """Read exact stored pixels only after scope, runtime and integrity checks."""
    ref = ObservationRef.model_validate(ref.model_dump())
    artifact = UUID(ref.artifact_id)
    if artifact.version != 4 or str(artifact) != ref.artifact_id:
        raise ValueError("Observation artifact must be a canonical UUID4")
    _check_image_size(ref.width, ref.height)
    with _artifact_directory(namespace, workflow_id) as (root, directory):
        runtime = _check_runtime(root, ref, namespace, workflow_id, for_display=for_display)
        manifest = json.loads(_read_artifact(directory, f"{ref.artifact_id}.json", 16_384))
        if manifest != {
            "namespace": namespace, "workflow_id": workflow_id, "observation": ref.model_dump(mode="json"),
        }:
            raise ValueError("Observation metadata does not match trusted manifest")
        data = _read_artifact(directory, f"{ref.artifact_id}.png", DESKTOP_OBSERVATION_MAX_BYTES)
        if len(data) != ref.byte_size or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError("Observation integrity check failed")
        with Image.open(io.BytesIO(data)) as image:
            _check_image_size(*image.size)
            if image.format != "PNG" or ref.mime_type != "image/png" or image.size != (ref.width, ref.height):
                raise ValueError("Observation image does not match manifest")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        if _check_runtime(root, ref, namespace, workflow_id, for_display=for_display) != runtime:
            raise StateConflict("Desktop runtime changed while resolving observation")
        return data


def observation_image(artifact_id: str, *, namespace: str, workflow_id: str) -> bytes:
    """Display immutable evidence from this owner's history, not fresh model input."""
    artifact = UUID(artifact_id)
    if artifact.version != 4 or str(artifact) != artifact_id:
        raise ValueError("Observation artifact must be a canonical UUID4")
    with _artifact_directory(namespace, workflow_id) as (_, directory):
        manifest = json.loads(_read_artifact(directory, f"{artifact_id}.json", 16_384))
    ref = ObservationRef.model_validate(manifest["observation"])
    if ref.artifact_id != artifact_id:
        raise ValueError("Observation artifact does not match manifest")
    return resolve_observation(ref, namespace=namespace, workflow_id=workflow_id, for_display=True)


def latest_observation(messages: list[dict[str, Any]]) -> tuple[str, str, ObservationRef] | None:
    """Locate only the latest desktop tool result's top-level observation.

    Tool output text and nested page content are not a source of references.
    Names are correlated with assistant tool calls, not supplied by the result.
    """
    from computer_use_activity import COMPUTER_USE_TOOL_NAMES

    def find_result(items, *, include_think):
        names = {}
        latest = None
        for message in items:
            if not isinstance(message, dict):
                continue
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                tool = block.get("toolUse")
                if message.get("role") == "assistant" and isinstance(tool, dict):
                    names[tool["toolUseId"]] = tool["name"]
                result = block.get("toolResult")
                if message.get("role") != "user" or not isinstance(result, dict):
                    continue
                call_id = result.get("toolUseId")
                name = names.get(call_id)
                if name == "browser" or name in COMPUTER_USE_TOOL_NAMES:
                    latest = call_id, name, result
                elif name == "think" and include_think:
                    # Only our correlated Think tool's native structured evidence,
                    # never JSON parsed from arbitrary page text/tool strings.
                    for evidence in result.get("content") or []:
                        data = evidence.get("json") if isinstance(evidence, dict) else None
                        nested = data.get("messages") if isinstance(data, dict) else None
                        if isinstance(nested, list):
                            latest = find_result(nested, include_think=False) or latest
        return latest

    latest = find_result(messages, include_think=True)
    if latest is None:
        return None
    call_id, name, result = latest
    if result.get("status") == "error":
        return None
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, dict):
            if payload.get("status") == "error":
                return None
            if "observation" in payload:
                return call_id, name, ObservationRef.model_validate(payload["observation"])
    raise ValueError("Successful desktop result is missing its observation")


def physical_coordinates(
    observation: ObservationRef,
    x: int,
    y: int,
    *,
    desktop_epoch: int,
    display_width: int,
    display_height: int,
    observed_after: datetime,
) -> tuple[int, int]:
    """Validate a trusted current observation, then map its pixels to the display.

    observed_after is the service's most recent invalidation time (input/resize/
    handoff), never a model-provided timestamp. Controller and artifact ownership
    checks remain separate prerequisites at dispatch.
    """
    if observed_after.tzinfo is None or observed_after.utcoffset() is None:
        raise ValueError("Observation invalidation time must include a timezone")
    if (
        observation.desktop_epoch != desktop_epoch
        or observation.display_width != display_width
        or observation.display_height != display_height
        or observation.captured_at < observed_after
    ):
        raise StateConflict("Fresh desktop observation required")
    if type(x) is not int or type(y) is not int or not (
        0 <= x < observation.width and 0 <= y < observation.height
    ):
        raise ValueError("Coordinates outside observation")
    return (
        observation.crop_x + int(x / observation.scale),
        observation.crop_y + int(y / observation.scale),
    )
