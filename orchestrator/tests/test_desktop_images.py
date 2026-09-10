"""Private desktop evidence API; never launches a desktop or model."""

import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

import server
from desktop_observation import store_observation


@pytest.mark.asyncio
async def test_evidence_api_serves_verified_pixels_only_to_current_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setitem(server._state, "client", SimpleNamespace(namespace="test"))
    runtime = {"epoch": 7, "owner": {"namespace": "test", "workflow_id": "chat-1"}, "mode": "agent"}
    (tmp_path / "runtime.json").write_text(json.dumps(runtime))
    ref = store_observation(Image.new("RGB", (10, 10), "blue"), namespace="test",
                            workflow_id="chat-1", operation_id="capture", desktop_epoch=7)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
        url = f"/sessions/chat-1/desktop-images/{ref.artifact_id}"
        response = await client.get(url)
        assert response.status_code == 200
        assert response.content.startswith(b"\x89PNG")
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, no-store"
        assert (await client.get(url.replace("chat-1", "other"))).status_code == 409
        assert (await client.get("/sessions/chat-1/desktop-images/invalid")).status_code == 404
        (tmp_path / "runtime.json").write_text(json.dumps({**runtime, "mode": "human"}))
        assert (await client.get(url)).content == response.content
