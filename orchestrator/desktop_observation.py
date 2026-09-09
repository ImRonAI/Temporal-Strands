"""Immutable observation metadata and physical-coordinate validation.

No bytes, storage keys, URLs, or provider credentials enter these records. The
service must resolve the artifact in its trusted scoped catalog before use.
"""

from datetime import datetime
from typing import Annotated, Literal

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
