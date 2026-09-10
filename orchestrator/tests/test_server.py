"""FastAPI bridge: file proxy, health, session validation, topic contract.

No network and no Temporal server: the Temporal connect is patched to fail
(the lifespan degrades gracefully), the ModelCatalogWorkflow execution is
patched per test via ``seed_catalog``, and the pooled httpx client is replaced
with a fake for proxy tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import perplexity_operations
import server
import asyncio
import time
from types import SimpleNamespace

from config import PROVIDER_DISPLAY_NAMES

_MODEL_A = {"id": "model-a", "provider": "perplexity-agent-api", "label": "model-a"}
_MODEL_B = {"id": "model-b", "provider": "perplexity-agent-api", "label": "model-b"}
_MODEL_C = {"id": "model-c", "provider": "perplexity-agent-api", "label": "model-c"}
_MODEL_NEW = {
    "id": "model-new",
    "provider": "perplexity-agent-api",
    "label": "model-new",
}


def seed_catalog(
    monkeypatch: pytest.MonkeyPatch, entries: list[dict[str, str]]
) -> AsyncMock:
    """Patch the ModelCatalogWorkflow execution and clear the server cache.

    Returns the AsyncMock so a test can assert how many workflow executions a
    request path actually performed (cache hit vs. refresh-on-miss).
    """
    fetch = AsyncMock(return_value=entries)
    monkeypatch.setattr(server, "_execute_catalog_workflow", fetch)
    monkeypatch.setitem(server._catalog_cache, "models", None)
    monkeypatch.setitem(server._catalog_cache, "fetched_at", 0.0)
    return fetch


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> Any:
    yield
    server._catalog_cache["models"] = None
    server._catalog_cache["fetched_at"] = 0.0
    server._poller_cache["checked_at"] = 0.0
    server._poller_cache["ok"] = False


@pytest.mark.parametrize("failure", [False, True])
def test_turn_subscription_recovers_without_repeating_update(client, monkeypatch, failure):
    seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "SSE_SUBSCRIBE_RESTART_DELAY", 0)
    finished = asyncio.Event()
    async def result():
        await finished.wait()
        return "complete"
    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(return_value=SimpleNamespace(result=result))
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle.return_value = handle
    monkeypatch.setitem(server._state, "client", temporal_client)
    offsets = []
    async def subscribe(topics, from_offset):
        offsets.append(from_offset)
        if len(offsets) == 1:
            yield SimpleNamespace(data={"data": "first"}, topic="events", offset=0)
            if failure:
                raise RuntimeError("subscriber disconnected")
            return
        yield SimpleNamespace(data={"data": "second"}, topic="events", offset=1)
        finished.set()
        await asyncio.Event().wait()
    stream = SimpleNamespace(get_offset=AsyncMock(return_value=0), subscribe=subscribe)
    monkeypatch.setattr(server.WorkflowStreamClient, "create", MagicMock(return_value=stream))
    response = client.post("/sessions/chat-1/turns/stream", json={"prompt": "test", "model_id": "model-a"})
    assert response.status_code == 200
    assert offsets == [0, 1]
    assert "first" in response.text and "second" in response.text and '"done": true' in response.text
    handle.start_update.assert_awaited_once()


def test_turn_subscription_exhaustion_returns_clean_error(client, monkeypatch):
    seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "SSE_SUBSCRIBE_RESTART_DELAY", 0)
    async def result():
        await asyncio.Event().wait()
    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(return_value=SimpleNamespace(result=result))
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle.return_value = handle
    monkeypatch.setitem(server._state, "client", temporal_client)
    async def subscribe(*args, **kwargs):
        raise RuntimeError("synthetic disconnect")
        yield
    monkeypatch.setattr(server.WorkflowStreamClient, "create", MagicMock(return_value=SimpleNamespace(
        get_offset=AsyncMock(return_value=0), subscribe=subscribe,
    )))
    response = client.post("/sessions/chat-1/turns/stream", json={"prompt": "test", "model_id": "model-a"})
    assert response.status_code == 200
    assert "Live event connection lost" in response.text
    assert '"done": true' not in response.text
    handle.start_update.assert_awaited_once()


class TrackedSubscription:
    """A stand-in for ``WorkflowStreamClient.subscribe()``'s async iterator.

    Records whether the pump closed it, which is the contract the per-turn
    stream owes the SDK: the subscription is abandoned, the workflow is not.
    """

    def __init__(
        self,
        items: list[Any] | None = None,
        error: BaseException | None = None,
        block: bool = False,
    ) -> None:
        self._items = list(items or [])
        self._error = error
        self._block = block
        self.closed = False

    def __aiter__(self) -> "TrackedSubscription":
        return self

    async def __anext__(self) -> Any:
        if self._items:
            return self._items.pop(0)
        if self._error is not None:
            raise self._error
        if self._block:
            await asyncio.Event().wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


def _frame(text: str, offset: int) -> SimpleNamespace:
    return SimpleNamespace(data={"data": text}, topic="events", offset=offset)


def test_turn_subscribes_before_update_and_keeps_the_first_frame(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subscription is created BEFORE the update is accepted (otherwise
    early frames are lost), every frame precedes the terminal frame, and the
    subscription -- not the workflow -- is what gets closed at the end."""
    seed_catalog(monkeypatch, [_MODEL_A])
    calls: list[str] = []

    async def result() -> str:
        await asyncio.sleep(0.05)
        return "final reply"

    async def start_update(*args: Any, **kwargs: Any) -> Any:
        calls.append("start_update")
        return SimpleNamespace(result=result)

    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(side_effect=start_update)
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle.return_value = handle
    monkeypatch.setitem(server._state, "client", temporal_client)

    subscription = TrackedSubscription(items=[_frame("first", 0)], block=True)

    def subscribe(topics: Any, from_offset: int = 0, **kwargs: Any) -> TrackedSubscription:
        calls.append("subscribe")
        return subscription

    monkeypatch.setattr(
        server.WorkflowStreamClient,
        "create",
        MagicMock(return_value=SimpleNamespace(
            get_offset=AsyncMock(return_value=0), subscribe=subscribe
        )),
    )

    response = client.post(
        "/sessions/chat-1/turns/stream",
        json={"prompt": "hi", "model_id": "model-a"},
    )

    assert response.status_code == 200, response.text
    assert calls == ["subscribe", "start_update"]
    assert "first" in response.text
    assert response.text.index("first") < response.text.index('"done": true')
    assert '"reply": "final reply"' in response.text
    assert subscription.closed
    handle.cancel.assert_not_called()


def test_turn_update_failure_closes_subscription_not_the_workflow(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected update yields exactly one ``{"error"}`` frame, closes the
    subscription it opened, and never cancels the durable workflow."""
    seed_catalog(monkeypatch, [_MODEL_A])
    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(side_effect=RuntimeError("update rejected"))
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle.return_value = handle
    monkeypatch.setitem(server._state, "client", temporal_client)

    subscription = TrackedSubscription(block=True)
    monkeypatch.setattr(
        server.WorkflowStreamClient,
        "create",
        MagicMock(return_value=SimpleNamespace(
            get_offset=AsyncMock(return_value=0),
            subscribe=lambda *a, **k: subscription,
        )),
    )

    response = client.post(
        "/sessions/chat-1/turns/stream",
        json={"prompt": "hi", "model_id": "model-a"},
    )

    assert response.status_code == 200, response.text
    assert "update rejected" in response.text
    assert '"done": true' not in response.text
    assert subscription.closed, "the pump must aclose() the subscription it opened"
    handle.cancel.assert_not_called()


def test_turn_pump_iterates_the_subscription_directly() -> None:
    """No queue/consumer/drain indirection between subscribe() and the client:
    the SDK iterator is the buffer, and asyncio.wait selects against it."""
    source = server.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for banned in ("asyncio.Queue", "def consume", "asyncio.sleep(0.2)"):
        assert banned not in text, f"{banned} still present in server.py"


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


def test_health_lists_provider_catalog(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/health carries the worker-declared catalog: one object per model with
    its provider, the provider display-name map, and no id-list/count fields."""
    seed_catalog(
        monkeypatch,
        [
            {
                "id": "preset:high",
                "provider": "perplexity-agent-api",
                "label": "High (preset)",
            },
            {
                "id": "gemini-3.8-flash",
                "provider": "google-ai-studio",
                "label": "gemini-3.8-flash",
            },
        ],
    )
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    monkeypatch.setitem(server._state, "client", MagicMock())

    response = client.get("/health")
    body = response.json()

    assert response.status_code == 200
    assert set(body) == {
        "status",
        "temporal",
        "worker",
        "pollers",
        "models",
        "providers",
        "default_model",
    }
    assert "model_ids" not in body
    assert body["status"] == "ok"
    assert body["temporal"] is True
    assert body["worker"] is True
    assert body["pollers"] is True
    assert body["models"] == [
        {
            "id": "preset:high",
            "provider": "perplexity-agent-api",
            "label": "High (preset)",
        },
        {
            "id": "gemini-3.8-flash",
            "provider": "google-ai-studio",
            "label": "gemini-3.8-flash",
        },
    ]
    assert body["providers"] == PROVIDER_DISPLAY_NAMES
    assert body["default_model"] == "preset:high"


def test_health_is_degraded_without_pollers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liveness is Temporal's own DescribeTaskQueue probe: no pollers, no worker."""
    seed_catalog(monkeypatch, [])
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=False))
    monkeypatch.setitem(server._state, "client", MagicMock())

    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["worker"] is False
    assert body["pollers"] is False
    assert body["models"] == []
    assert body["default_model"] is None


def test_health_default_model_falls_back_to_first_catalog_entry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_catalog(
        monkeypatch,
        [
            {
                "id": "gemini-3.8-flash",
                "provider": "google-ai-studio",
                "label": "gemini-3.8-flash",
            }
        ],
    )
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    monkeypatch.setitem(server._state, "client", MagicMock())

    assert client.get("/health").json()["default_model"] == "gemini-3.8-flash"


def test_catalog_is_cached_then_refreshed_after_ttl(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One workflow execution per TTL window, then a fresh one."""
    fetch = seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    monkeypatch.setitem(server._state, "client", MagicMock())

    client.get("/health")
    client.get("/health")
    assert fetch.await_count == 1

    # Age the cache past its TTL; the next read must re-execute the workflow.
    server._catalog_cache["fetched_at"] = (
        time.monotonic() - server.CATALOG_CACHE_TTL.total_seconds() - 1
    )
    client.get("/health")
    assert fetch.await_count == 2


def test_start_session_refreshes_catalog_once_on_miss(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model the cache has not seen re-executes ModelCatalogWorkflow once;
    a worker that has since registered it is accepted, not rejected."""
    fetch = seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    temporal_client = MagicMock()
    temporal_client.start_workflow = AsyncMock()
    monkeypatch.setitem(server._state, "client", temporal_client)

    # Prime the cache with the stale single-model catalog.
    assert client.post("/sessions", json={"model_id": "model-a"}).status_code == 200
    assert fetch.await_count == 1

    fetch.return_value = [_MODEL_A, _MODEL_NEW]
    response = client.post("/sessions", json={"model_id": "model-new"})

    assert response.status_code == 200, response.text
    # Exactly one extra execution: the refresh on the miss, not a retry storm.
    assert fetch.await_count == 2
    assert temporal_client.start_workflow.await_count == 2


def test_start_session_503_when_no_worker_serves_a_catalog(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_catalog(monkeypatch, [])
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    monkeypatch.setitem(server._state, "client", MagicMock())

    response = client.post("/sessions", json={"model_id": "model-a"})
    assert response.status_code == 503


def test_turn_refreshes_catalog_once_on_miss(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = seed_catalog(monkeypatch, [_MODEL_A])
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
        server.WorkflowStreamClient, "create", MagicMock(return_value=stream_client)
    )

    fetch.return_value = [_MODEL_A, _MODEL_NEW]
    response = client.post(
        "/sessions/chat-1/turns/stream",
        json={"prompt": "hi", "model_id": "model-new"},
    )

    assert response.status_code == 200, response.text
    assert handle.start_update.call_args.args[1].model_id == "model-new"


def test_readiness_lease_helpers_are_gone() -> None:
    for removed in ("readiness", "validate_readiness_record", "models_of"):
        assert not hasattr(server, removed), (
            f"server.{removed} still exists; readiness is Temporal-native now"
        )


def test_turn_unknown_model_id_is_400_listing_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_catalog(monkeypatch, [_MODEL_A, _MODEL_B])
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
    seed_catalog(monkeypatch, [{"id": "gemini-3.8-flash", "provider": "google-ai-studio", "label": "gemini-3.8-flash"}])
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
    seed_catalog(monkeypatch, [_MODEL_A, _MODEL_B])

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
    seed_catalog(monkeypatch, [_MODEL_A])

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
    seed_catalog(monkeypatch, [_MODEL_A, _MODEL_B, _MODEL_C])
    monkeypatch.setitem(server._state, "client", MagicMock())

    response = client.post("/sessions", json={"model_id": "nope"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "nope" in detail
    assert "model-a" in detail
    assert "model-b" in detail
