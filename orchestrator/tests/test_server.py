"""FastAPI bridge: file proxy, health, session validation, topic contract.

No network and no Temporal server: the Temporal connect is patched to fail
(the lifespan degrades gracefully), the readiness read is patched per test,
and the pooled httpx client is replaced with a fake for proxy tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import perplexity_operations
import server


class FakeUpstream:
    """Minimal stand-in for an httpx streaming response."""

    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []
        self.closed = False

    async def aiter_bytes(self) -> Any:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def make_fake_http(upstream: FakeUpstream) -> MagicMock:
    fake = MagicMock()
    fake.build_request = MagicMock(return_value=MagicMock())
    fake.send = AsyncMock(return_value=upstream)
    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        server.Client,
        "connect",
        AsyncMock(side_effect=RuntimeError("no temporal in tests")),
    )
    with TestClient(server.app) as test_client:
        yield test_client


def test_agent_runs_topic_matches_operations_module() -> None:
    assert server.AGENT_RUNS_TOPIC == perplexity_operations.AGENT_RUNS_TOPIC
    assert server.AGENT_RUNS_TOPIC == "agent_runs"
    assert server.AGENT_RUNS_TOPIC in server.CHAT_TOPICS


def test_file_proxy_happy_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    upstream = FakeUpstream(
        headers={
            "content-type": "image/png",
            "content-disposition": 'attachment; filename="plot.png"',
        },
        chunks=[b"chunk1", b"chunk2"],
    )
    fake_http = make_fake_http(upstream)
    monkeypatch.setitem(server._state, "http", fake_http)

    response = client.get("/responses/resp_123/files/file-abc/content")

    assert response.status_code == 200
    assert response.content == b"chunk1chunk2"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'attachment; filename="plot.png"'
    assert upstream.closed

    # Upstream URL is the documented Agent API path with the bearer key.
    args, kwargs = fake_http.build_request.call_args
    assert args[0] == "GET"
    assert args[1] == (
        "https://api.perplexity.ai/v1/agent/resp_123/files/file-abc/content"
    )
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"


def test_file_proxy_alias_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    upstream = FakeUpstream(
        headers={"content-type": "text/plain"}, chunks=[b"hello"]
    )
    monkeypatch.setitem(server._state, "http", make_fake_http(upstream))

    response = client.get("/agent/resp_123/files/file-abc/content")

    assert response.status_code == 200
    assert response.content == b"hello"
    # Starlette appends a charset to text/* media types.
    assert response.headers["content-type"].startswith("text/plain")


def test_file_proxy_missing_key_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    response = client.get("/responses/resp_123/files/file-abc/content")
    assert response.status_code == 503


@pytest.mark.parametrize(
    "response_id,file_id",
    [
        ("..", "file-abc"),
        ("resp_123", ".."),
        ("resp$123", "file-abc"),
        ("resp_123", "file abc"),
    ],
)
def test_file_proxy_bad_id_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    response_id: str,
    file_id: str,
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    response = client.get(f"/responses/{response_id}/files/{file_id}/content")
    # ".." collapses in URL normalization and misses the route (404); anything
    # that still reaches the handler is rejected by the id pattern (422).
    assert response.status_code in (404, 422)
    assert response.status_code != 200


def test_file_proxy_upstream_error_passthrough(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    upstream = FakeUpstream(status_code=404)
    monkeypatch.setitem(server._state, "http", make_fake_http(upstream))

    response = client.get("/responses/resp_123/files/file-abc/content")
    assert response.status_code == 404
    assert upstream.closed


def test_health_surfaces_default_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {
        "task_queue": "perplexity-orchestrator",
        "agent": "Gwen",
        "models": ["model-a", "model-b"],
        "default_model": "model-a",
    }
    monkeypatch.setattr(server, "readiness", AsyncMock(return_value=record))

    response = client.get("/health")
    body = response.json()

    assert response.status_code == 200
    assert body["default_model"] == "model-a"
    assert body["models"] == 2
    assert body["worker"] is True
    assert body["api"] is True


def test_health_omits_default_model_when_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {"models": ["model-a"]}
    monkeypatch.setattr(server, "readiness", AsyncMock(return_value=record))

    body = client.get("/health").json()
    assert "default_model" not in body


def test_turn_unknown_model_id_is_400_listing_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {"models": ["model-a", "model-b"]}
    monkeypatch.setattr(server, "readiness", AsyncMock(return_value=record))
    monkeypatch.setitem(server._state, "client", MagicMock())

    response = client.post(
        "/sessions/chat-1/turns/stream",
        json={"prompt": "hi", "model_id": "nope"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "nope" in detail
    assert "model-a" in detail
    assert "model-b" in detail


def test_turn_rejects_unverified_reasoning_effort(client, monkeypatch):
    monkeypatch.setattr(server, "readiness", AsyncMock(return_value={"models": ["gemini-3.8-flash"]}))
    monkeypatch.setitem(server._state, "client", MagicMock())
    response = client.post("/sessions/chat-1/turns/stream", json={
        "prompt": "hi", "model_id": "gemini-3.8-flash", "reasoning_effort": "minimal",
    })
    assert response.status_code == 422


def test_turn_valid_model_id_is_accepted_and_forwarded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog model_id passes validation and rides the turn update's
    TurnInput; the stream still terminates with the reply frame."""
    record = {"models": ["model-a", "model-b"]}
    monkeypatch.setattr(server, "readiness", AsyncMock(return_value=record))

    update_handle = MagicMock()
    update_handle.result = AsyncMock(return_value="switched reply")
    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(return_value=update_handle)
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle = MagicMock(return_value=handle)
    monkeypatch.setitem(server._state, "client", temporal_client)

    async def _no_frames() -> Any:
        return
        yield  # unreachable; makes this an async generator

    stream_client = MagicMock()
    stream_client.get_offset = AsyncMock(return_value=0)
    stream_client.subscribe = MagicMock(return_value=_no_frames())
    monkeypatch.setattr(
        server.WorkflowStreamClient,
        "create",
        MagicMock(return_value=stream_client),
    )

    response = client.post(
        "/sessions/chat-1/turns/stream",
        json={"prompt": "hi", "model_id": "model-b"},
    )

    assert response.status_code == 200
    assert '"reply": "switched reply"' in response.text
    turn_input = handle.start_update.call_args.args[1]
    assert turn_input.model_id == "model-b"
    assert turn_input.prompt == "hi"


def test_turn_without_model_id_forwards_none(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {"models": ["model-a"]}
    monkeypatch.setattr(server, "readiness", AsyncMock(return_value=record))

    update_handle = MagicMock()
    update_handle.result = AsyncMock(return_value="ok")
    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(return_value=update_handle)
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle = MagicMock(return_value=handle)
    monkeypatch.setitem(server._state, "client", temporal_client)

    async def _no_frames() -> Any:
        return
        yield  # unreachable; makes this an async generator

    stream_client = MagicMock()
    stream_client.get_offset = AsyncMock(return_value=0)
    stream_client.subscribe = MagicMock(return_value=_no_frames())
    monkeypatch.setattr(
        server.WorkflowStreamClient,
        "create",
        MagicMock(return_value=stream_client),
    )

    response = client.post("/sessions/chat-1/turns/stream", json={"prompt": "hi"})

    assert response.status_code == 200
    turn_input = handle.start_update.call_args.args[1]
    assert turn_input.model_id is None


def test_start_session_unknown_model_lists_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {"models": ["model-a", "model-b", "model-c"]}
    monkeypatch.setattr(server, "readiness", AsyncMock(return_value=record))
    monkeypatch.setitem(server._state, "client", MagicMock())

    response = client.post("/sessions", json={"model_id": "nope"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "nope" in detail
    assert "model-a" in detail
    assert "model-b" in detail
