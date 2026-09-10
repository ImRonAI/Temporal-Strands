import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from PIL import Image
from pydantic import ValidationError

import desktop_observation
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


@pytest.fixture
def artifact_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    runtime = {"epoch": 7, "owner": {"namespace": "test", "workflow_id": "chat-1"}, "mode": "agent"}
    (tmp_path / "runtime.json").write_text(json.dumps(runtime))
    return tmp_path, runtime


def store_image(**kwargs):
    return desktop_observation.store_observation(
        Image.new("RGB", (16, 12), "blue"), namespace="test", workflow_id="chat-1",
        operation_id="capture-1", desktop_epoch=7, **kwargs,
    )


def resolve_image(ref, **overrides):
    return desktop_observation.resolve_observation(
        ref, **{"namespace": "test", "workflow_id": "chat-1", **overrides},
    )


def artifact_paths(root, ref):
    scope = hashlib.sha256(b"test\0chat-1").hexdigest()
    return root / scope / f"{ref.artifact_id}.png", root / scope / f"{ref.artifact_id}.json"


def test_store_resolve_exact_png_and_metadata(artifact_store):
    root, runtime = artifact_store
    ref = store_image()
    png, manifest = artifact_paths(root, ref)
    data = resolve_image(ref)
    assert UUID(ref.artifact_id).version == 4
    assert ref.generation == "1"
    assert data == png.read_bytes()
    assert hashlib.sha256(data).hexdigest() == ref.sha256
    assert len(data) == ref.byte_size
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (ref.width, ref.height) == (16, 12)
        assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 255)
    assert json.loads(manifest.read_text())["observation"] == ref.model_dump(mode="json")
    assert json.loads((root / "runtime.json").read_text()) == runtime
    assert len(ref.model_dump_json()) < 2048


def test_inflight_capture_can_finish_while_stopping_but_cannot_feed_model(artifact_store):
    root, runtime = artifact_store
    (root / "runtime.json").write_text(json.dumps({**runtime, "mode": "stopping"}))
    ref = store_image()
    assert artifact_paths(root, ref)[0].is_file()
    with pytest.raises(StateConflict):
        resolve_image(ref)


def test_evidence_remains_visible_during_takeover_but_is_not_model_input(artifact_store):
    root, runtime = artifact_store
    ref = store_image()
    original = resolve_image(ref)
    (root / "runtime.json").write_text(json.dumps({**runtime, "mode": "human", "epoch": 8}))
    assert desktop_observation.observation_image(ref.artifact_id, namespace="test", workflow_id="chat-1") == original
    with pytest.raises(StateConflict):
        resolve_image(ref)
    with pytest.raises((StateConflict, OSError)):
        desktop_observation.observation_image(ref.artifact_id, namespace="test", workflow_id="other")


def test_evidence_checks_integrity_and_owner(artifact_store):
    root, runtime = artifact_store
    ref = store_image()
    png, _ = artifact_paths(root, ref)
    png.write_bytes(b"x" * ref.byte_size)
    with pytest.raises(ValueError, match="integrity"):
        desktop_observation.observation_image(ref.artifact_id, namespace="test", workflow_id="chat-1")
    (root / "runtime.json").write_text(json.dumps({**runtime, "owner": None}))
    with pytest.raises(StateConflict):
        desktop_observation.observation_image(ref.artifact_id, namespace="test", workflow_id="chat-1")


@pytest.mark.parametrize("scope", [{"namespace": "other"}, {"workflow_id": "other"}])
def test_resolve_rejects_cross_scope(artifact_store, scope):
    ref = store_image()
    with pytest.raises((OSError, ValueError, StateConflict)):
        resolve_image(ref, **scope)


@pytest.mark.parametrize("changes", [
    {"epoch": 8}, {"epoch": True}, {"mode": "human"}, {"owner": None},
    {"owner": {"namespace": "test", "workflow_id": "other"}},
])
def test_resolve_requires_current_runtime_owner_and_epoch(artifact_store, changes):
    root, runtime = artifact_store
    ref = store_image()
    (root / "runtime.json").write_text(json.dumps({**runtime, **changes}))
    with pytest.raises((ValueError, StateConflict)):
        resolve_image(ref)


@pytest.mark.parametrize("changes", [
    {"sha256": "b" * 64}, {"generation": "2"}, {"operation_id": "forged"},
    {"width": 15}, {"captured_at": "2026-01-01T00:00:00Z"},
])
def test_resolve_compares_complete_metadata_with_manifest(artifact_store, changes):
    ref = store_image()
    forged = ObservationRef.model_validate({**ref.model_dump(mode="json"), **changes})
    with pytest.raises((ValueError, StateConflict)):
        resolve_image(forged)


@pytest.mark.parametrize("name", ["../runtime", "/tmp/image", "not-a-uuid"])
def test_resolve_rejects_unvalidated_artifact_names(artifact_store, name):
    ref = store_image().model_copy(update={"artifact_id": name})
    with pytest.raises(ValueError):
        resolve_image(ref)


@pytest.mark.parametrize("target", ["png", "manifest", "runtime", "scope"])
def test_resolve_rejects_symlinks(artifact_store, target):
    root, _ = artifact_store
    ref = store_image()
    png, manifest = artifact_paths(root, ref)
    path = {"png": png, "manifest": manifest, "runtime": root / "runtime.json", "scope": png.parent}[target]
    renamed = path.with_name(path.name + ".original")
    path.rename(renamed)
    path.symlink_to(renamed, target_is_directory=target == "scope")
    with pytest.raises(OSError):
        resolve_image(ref)


def test_resolve_rejects_corrupt_or_missing_pixels(artifact_store):
    root, _ = artifact_store
    ref = store_image()
    png, _ = artifact_paths(root, ref)
    png.write_bytes(b"x" * ref.byte_size)
    with pytest.raises(ValueError, match="integrity"):
        resolve_image(ref)
    png.unlink()
    with pytest.raises(OSError):
        resolve_image(ref)


def test_store_never_overwrites_existing_artifact(artifact_store, monkeypatch):
    root, _ = artifact_store
    ref = store_image()
    png, manifest = artifact_paths(root, ref)
    original = (png.read_bytes(), manifest.read_bytes())
    monkeypatch.setattr(desktop_observation, "uuid4", lambda: UUID(ref.artifact_id))
    with pytest.raises(FileExistsError):
        store_image()
    assert (png.read_bytes(), manifest.read_bytes()) == original


@pytest.mark.parametrize("limit", ["dimensions", "pixels", "bytes"])
def test_store_bounds_dimensions_pixels_and_encoded_bytes(artifact_store, monkeypatch, limit):
    if limit == "dimensions":
        monkeypatch.setattr(desktop_observation, "DESKTOP_MAX_DIMENSION", 8)
        image = Image.new("RGB", (9, 1))
    elif limit == "pixels":
        monkeypatch.setattr(desktop_observation, "DESKTOP_OBSERVATION_MAX_BYTES", 100)
        image = Image.new("RGB", (11, 10))
    else:
        monkeypatch.setattr(desktop_observation, "DESKTOP_OBSERVATION_MAX_BYTES", 10)
        image = Image.new("RGB", (1, 1))
    with pytest.raises(ValueError, match="limit"):
        desktop_observation.store_observation(
            image, namespace="test", workflow_id="chat-1", operation_id="capture", desktop_epoch=7,
        )


def test_artifact_root_symlink_is_rejected(artifact_store, tmp_path, monkeypatch):
    root, _ = artifact_store
    ref = store_image()
    link = tmp_path / "root-link"
    link.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(link))
    with pytest.raises(OSError):
        resolve_image(ref)
    with pytest.raises(OSError):
        store_image()


def test_artifact_hardlinks_are_rejected(artifact_store):
    root, _ = artifact_store
    ref = store_image()
    png, _ = artifact_paths(root, ref)
    os.link(png, root / "linked.png")
    with pytest.raises(ValueError, match="link"):
        resolve_image(ref)


def test_runtime_change_during_resolution_is_rejected(artifact_store, monkeypatch):
    root, runtime = artifact_store
    ref = store_image()
    read = desktop_observation._read_artifact

    def change_runtime(directory, name, limit):
        data = read(directory, name, limit)
        if name.endswith(".png"):
            (root / "runtime.json").write_text(json.dumps({**runtime, "mode": "human"}))
        return data

    monkeypatch.setattr(desktop_observation, "_read_artifact", change_runtime)
    with pytest.raises(StateConflict):
        resolve_image(ref)


@pytest.mark.parametrize("damage", ["missing-runtime", "malformed-runtime", "oversized-runtime", "oversized-image", "invalid-image"])
def test_resolve_fails_closed_on_unavailable_or_invalid_files(artifact_store, damage):
    root, _ = artifact_store
    ref = store_image()
    png, manifest = artifact_paths(root, ref)
    if damage == "missing-runtime":
        (root / "runtime.json").unlink()
    elif damage == "malformed-runtime":
        (root / "runtime.json").write_text("{")
    elif damage == "oversized-runtime":
        (root / "runtime.json").write_bytes(b"x" * 4097)
    elif damage == "oversized-image":
        with png.open("r+b") as file:
            file.truncate(desktop_observation.DESKTOP_OBSERVATION_MAX_BYTES + 1)
    else:
        data = b"not an image"
        png.write_bytes(data)
        ref = ref.model_copy(update={"byte_size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        record = json.loads(manifest.read_text())
        record["observation"] = ref.model_dump(mode="json")
        manifest.write_text(json.dumps(record))
    with pytest.raises((OSError, ValueError, StateConflict)):
        resolve_image(ref)


@pytest.mark.parametrize("namespace,workflow_id", [("", "id"), ("ns", ""), ("ns\0x", "id"), ("ns", "id\0x")])
def test_ambiguous_or_empty_scope_is_rejected(artifact_store, namespace, workflow_id):
    with pytest.raises(ValueError):
        desktop_observation.store_observation(
            Image.new("RGB", (1, 1)), namespace=namespace, workflow_id=workflow_id,
            operation_id="capture", desktop_epoch=7,
        )


def test_latest_result_missing_observation_cannot_reuse_old_pixels(artifact_store):
    ref = store_image()
    messages = [
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": call_id, "name": "browser", "input": {}}} for call_id in ("old", "new")
        ]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "old", "status": "success", "content": [
                {"text": json.dumps({"observation": ref.model_dump(mode="json")})},
            ]}},
            {"toolResult": {"toolUseId": "new", "status": "success", "content": [
                {"text": json.dumps({"status": "success", "content": [{"text": json.dumps({
                    "observation": ref.model_dump(mode="json"),
                })}]})},
            ]}},
        ]},
    ]
    with pytest.raises(ValueError, match="missing"):
        desktop_observation.latest_observation(messages)
    messages[-1]["content"][-1]["toolResult"]["status"] = "error"
    assert desktop_observation.latest_observation(messages) is None


def test_think_evidence_exposes_actual_child_screenshot_to_parent_model(artifact_store):
    ref = store_image()
    evidence = [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "capture", "name": "take_screenshot", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "capture", "status": "success", "content": [
            {"text": json.dumps({"observation": ref.model_dump(mode="json")})},
        ]}}]},
    ]
    messages = [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "think-id", "name": "think", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "think-id", "status": "success", "content": [
            {"text": "Observed the desktop"}, {"json": {"messages": evidence}},
        ]}}]},
    ]
    assert desktop_observation.latest_observation(messages) == ("capture", "take_screenshot", ref)
    # Renaming a page/tool to look like structured Think evidence is insufficient.
    messages[0]["content"][0]["toolUse"]["name"] = "http_request"
    assert desktop_observation.latest_observation(messages) is None
    messages[0]["content"][0]["toolUse"]["name"] = "think"
    messages[1]["content"][0]["toolResult"]["content"][-1] = {"text": json.dumps({"messages": evidence})}
    assert desktop_observation.latest_observation(messages) is None
