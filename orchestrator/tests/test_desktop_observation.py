from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from desktop_observation import ObservationRef, physical_coordinates
from workspace_state import StateConflict


@pytest.fixture
def observation():
    return ObservationRef(
        artifact_id="artifact", generation="18446744073709551615", sha256="a" * 64,
        mime_type="image/png", byte_size=1024, width=500, height=300,
        display_width=1920, display_height=1080, crop_x=100, crop_y=50, scale=0.5,
        captured_at=datetime.now(timezone.utc), desktop_epoch=2, operation_id="capture",
    )


def coordinates(observation, x=20, y=30, **overrides):
    options = {
        "desktop_epoch": 2, "display_width": 1920, "display_height": 1080,
        "observed_after": observation.captured_at,
    }
    options.update(overrides)
    return physical_coordinates(observation, x, y, **options)


def test_crop_coordinates_use_physical_pixels_not_viewport(observation):
    assert coordinates(observation) == (140, 110)
    assert coordinates(observation, 499, 299) == (1098, 648)


@pytest.mark.parametrize("changes", [
    {"desktop_epoch": 3}, {"display_width": 1280}, {"display_height": 720},
])
def test_epoch_or_geometry_change_requires_observation(observation, changes):
    with pytest.raises(StateConflict):
        coordinates(observation, **changes)


def test_mutation_invalidates_earlier_observation(observation):
    with pytest.raises(StateConflict):
        coordinates(observation, observed_after=observation.captured_at + timedelta(microseconds=1))


def test_invalidation_time_requires_timezone(observation):
    with pytest.raises(ValueError, match="timezone"):
        coordinates(observation, observed_after=datetime(2026, 9, 8))


@pytest.mark.parametrize("x,y", [(-1, 0), (500, 0), (0, 300), (True, 0), (0.5, 1)])
def test_coordinates_are_bounded_integers(observation, x, y):
    with pytest.raises(ValueError):
        coordinates(observation, x, y)


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"generation": 123}, {"generation": "0"},
    {"mime_type": "text/html"}, {"url": "https://untrusted.invalid"},
    {"scale": 0}, {"scale": float("nan")}, {"crop_x": 1900},
    {"width": True}, {"byte_size": 100_000_000}, {"sha256": "invalid"},
    {"captured_at": "2026-09-08T00:00:00"},
])
def test_descriptor_rejects_invalid_or_untrusted_fields(observation, changes):
    data = observation.model_dump()
    data.update(changes)
    with pytest.raises(ValidationError):
        ObservationRef.model_validate(data)


def test_json_roundtrip_preserves_generation_without_precision_loss(observation):
    restored = ObservationRef.model_validate_json(observation.model_dump_json())
    assert restored == observation
    assert restored.generation == "18446744073709551615"
    assert "bytes" not in observation.model_dump()


def test_observation_is_descriptor_only_in_temporal_payload(observation):
    from temporalio.contrib.pydantic import pydantic_data_converter

    # The payload converter is the installed native Pydantic boundary, not an
    # application image envelope. Image bytes are hydrated in model activities.
    payload = pydantic_data_converter.payload_converter.to_payloads([observation])[0]
    assert len(payload.data) < 2048
    assert b"18446744073709551615" in payload.data
    assert b"input_image" not in payload.data
    restored = pydantic_data_converter.payload_converter.from_payloads([payload], [ObservationRef])
    assert restored == [observation]
