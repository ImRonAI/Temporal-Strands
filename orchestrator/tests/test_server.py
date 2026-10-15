"""FastAPI bridge: file proxy, health, session validation, topic contract.

No network and no Temporal server: the Temporal connect is patched to fail
(the lifespan degrades gracefully), the ModelCatalogWorkflow execution is
patched per test via ``seed_catalog``, and the pooled httpx client is replaced
with a fake for proxy tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi.testclient import TestClient

import server
import asyncio
import json
import time
from types import SimpleNamespace
from datetime import datetime, timezone

from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream, WorkflowStreamClient
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError
from temporalio.client import WorkflowExecutionStatus, WorkflowUpdateFailedError
from temporalio.service import RPCError, RPCStatusCode

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


_NOVNC_URL = "http://localhost:51234/vnc.html?autoconnect=true"


def _desktop_handle():
    """A started-workflow handle. Its ``desktop`` update would claim a container,
    which ``POST /sessions`` must never do: the desktop is lazy."""
    handle = MagicMock()
    handle.execute_update = AsyncMock(return_value={
        "container": "gwen-desktop-chat-1", "queue": "desktop-browser-chat-1",
        "novnc_url": _NOVNC_URL,
    })
    return handle


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> Any:
    yield
    server._catalog_cache["models"] = None
    server._catalog_cache["fetched_at"] = 0.0
    server._poller_cache["checked_at"] = 0.0
    server._poller_cache["ok"] = False


def test_turn_subscription_exhaustion_returns_clean_error(client, monkeypatch):
    seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "SSE_SUBSCRIBE_RESTART_DELAY", 0)
    async def result():
        await asyncio.Event().wait()
    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(return_value=SimpleNamespace(workflow_run_id="synthetic-run", result=result))
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


class NativeTurnStream:
    """Real stream storage, paging and subscription; only Temporal RPCs are fake."""

    def __init__(self, monkeypatch, *, failure=False, fault=None, payload_size=0):
        self.id = "synthetic-turn"
        self.handlers = {}
        self.finished = asyncio.Event()
        self.poll_started = asyncio.Event()
        self.failure = failure
        self.fault = fault
        self.offset_queries = 0
        self.turn_calls = 0
        self.polls = []
        self.cancelled_polls = 0
        self.subscriptions = []
        self.labels = ["init_session", "navigate", "click:error", "browser:error", "take_screenshot:error"]

        def register(name, handler, **kwargs):
            self.handlers[name] = handler

        async def wait_condition(predicate, **kwargs):
            if not predicate():
                await asyncio.Event().wait()

        monkeypatch.setattr(workflow, "get_signal_handler", lambda name: None)
        monkeypatch.setattr(workflow, "set_signal_handler", register)
        monkeypatch.setattr(workflow, "set_update_handler", register)
        monkeypatch.setattr(workflow, "set_query_handler", register)
        monkeypatch.setattr(workflow, "payload_converter", lambda: DataConverter.default.payload_converter)
        monkeypatch.setattr(workflow, "wait_condition", wait_condition)
        self.stream = WorkflowStream()
        self.native = WorkflowStreamClient(self)
        self.payload_size = payload_size
        monkeypatch.setitem(server._state, "client", SimpleNamespace(get_workflow_handle=lambda _: self))
        monkeypatch.setattr(server.WorkflowStreamClient, "create", lambda *args: SimpleNamespace(
            get_offset=self.native.get_offset, subscribe=self.subscribe,
        ))

    def publish(self):
        for label in self.labels:
            self.stream.topic("events").publish({"label": label, "padding": "x" * self.payload_size})
        self.stream.topic("excluded-topic").publish({"label": "filtered tail"})

    def subscribe(self, topics, from_offset):
        iterator = self.native.subscribe(topics, from_offset=from_offset)
        self.subscriptions.append((topics, from_offset, iterator))
        return iterator

    async def query(self, name, **kwargs):
        if name == server.ChatWorkflow.turn_start_offset:
            return None
        self.offset_queries += 1
        if self.offset_queries == 2 and self.fault == "query-timeout":
            await asyncio.Event().wait()
        offset = self.handlers[name]()
        if self.offset_queries == 2 and self.fault == "append":
            self.stream.topic("events").publish({"label": "after completion snapshot"})
        return offset

    async def start_update(self, name, arg=None, *, args=None, **kwargs):
        if name == server.ChatWorkflow.turn:
            self.turn_calls += 1
            arg = arg if arg is not None else (args[0] if args else None)
            self.turn_args = list(args) if args is not None else [arg]

            async def result():
                await self.finished.wait()
                if isinstance(self.failure, Exception):
                    raise self.failure
                if self.failure:
                    raise RuntimeError("synthetic turn failure")
                return "synthetic reply"
        else:
            self.polls.append(arg)

            async def result():
                try:
                    if arg.topics and self.fault == "cancel-live":
                        self.poll_started.set()
                        await asyncio.Event().wait()
                    if not arg.topics:
                        if self.fault == "gap":
                            self.stream.truncate(arg.from_offset + 1)
                            self.fault = None
                        elif self.fault == "premature-end":
                            raise asyncio.CancelledError
                        elif self.fault == "read-timeout":
                            await asyncio.Event().wait()
                    try:
                        return await self.handlers[name](arg)
                    except ApplicationError as error:
                        raise WorkflowUpdateFailedError(error) from error
                except asyncio.CancelledError:
                    self.cancelled_polls += 1
                    raise

        return SimpleNamespace(workflow_run_id="synthetic-run", result=result)


async def native_turn_body(harness):
    response = await server.turn_stream(harness.id, server.TurnRequest(prompt="synthetic only"))
    harness.publish()
    return response.body_iterator


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


def test_agent_runs_topic_matches_workflow_module() -> None:
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
    pollers = AsyncMock(side_effect=lambda *, desktop=False: not desktop)
    monkeypatch.setattr(server, "task_queue_has_pollers", pollers)
    monkeypatch.setitem(server._state, "client", MagicMock())

    response = client.get("/health")
    body = response.json()

    assert response.status_code == 200
    assert set(body) == {
        "status",
        "temporal",
        "worker",
        "pollers",
        "desktop",
        "models",
        "providers",
        "default_model",
    }
    assert "model_ids" not in body
    # Desktops are per-session containers claimed on first use; health only
    # reports the host worker and never probes a desktop queue.
    assert body["status"] == "ok"
    assert body["temporal"] is True
    assert body["worker"] is True
    assert body["pollers"] is True
    assert body["desktop"] is True
    assert pollers.await_args_list == [call()]
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


def test_failed_refresh_serves_last_known_catalog_while_worker_polls(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ModelCatalogWorkflow timeout on refresh (busy worker) must not blank the
    catalog: /health keeps the declared models and /sessions does not 503."""
    fetch = seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    temporal_client = MagicMock()
    temporal_client.start_workflow = AsyncMock(return_value=_desktop_handle())
    monkeypatch.setitem(server._state, "client", temporal_client)

    assert client.get("/health").json()["models"] == [_MODEL_A]
    assert fetch.await_count == 1

    # Age the cache past its TTL and make the refresh fail (empty = timed out).
    server._catalog_cache["fetched_at"] = (
        time.monotonic() - server.CATALOG_CACHE_TTL.total_seconds() - 1
    )
    fetch.return_value = []

    health = client.get("/health").json()
    assert fetch.await_count == 2
    assert health["models"] == [_MODEL_A]
    assert health["status"] == "ok"

    response = client.post("/sessions", json={"model_id": "model-a"})
    assert response.status_code == 200, response.text
    temporal_client.start_workflow.assert_awaited_once()

    # The stale timestamp is preserved so the next call retries the refresh.
    fetch.return_value = [_MODEL_A, _MODEL_NEW]
    assert client.get("/health").json()["models"] == [_MODEL_A, _MODEL_NEW]


def test_failed_refresh_without_pollers_reports_no_catalog(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale catalog is NOT served when no worker polls the queue: that is a
    genuine outage and /health must report degraded."""
    fetch = seed_catalog(monkeypatch, [_MODEL_A])
    pollers = AsyncMock(return_value=True)
    monkeypatch.setattr(server, "task_queue_has_pollers", pollers)
    monkeypatch.setitem(server._state, "client", MagicMock())

    client.get("/health")
    server._catalog_cache["fetched_at"] = (
        time.monotonic() - server.CATALOG_CACHE_TTL.total_seconds() - 1
    )
    fetch.return_value = []
    pollers.return_value = False

    health = client.get("/health").json()
    assert health["models"] == []
    assert health["status"] == "degraded"


def test_start_session_refreshes_catalog_once_on_miss(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model the cache has not seen re-executes ModelCatalogWorkflow once;
    a worker that has since registered it is accepted, not rejected."""
    fetch = seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    temporal_client = MagicMock()
    temporal_client.start_workflow = AsyncMock(return_value=_desktop_handle())
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


def test_start_session_never_claims_a_desktop(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chat that never browses never pays for a container: POST /sessions
    only starts the workflow and answers immediately with no viewer URL."""
    seed_catalog(monkeypatch, [_MODEL_A])
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))
    handle = _desktop_handle()
    temporal_client = MagicMock()
    temporal_client.start_workflow = AsyncMock(return_value=handle)
    monkeypatch.setitem(server._state, "client", temporal_client)

    response = client.post("/sessions", json={"model_id": "model-a"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"].startswith("chat-")
    assert body["desktop_url"] is None
    handle.execute_update.assert_not_awaited()


def _control_client(monkeypatch: pytest.MonkeyPatch, desktop_url: str | None) -> MagicMock:
    handle = MagicMock()
    handle.query = AsyncMock(return_value=desktop_url)
    temporal_client = MagicMock(namespace="test")
    temporal_client.get_workflow_handle = MagicMock(return_value=handle)
    monkeypatch.setitem(server._state, "client", temporal_client)
    return handle


def test_desktop_control_status_without_a_desktop_is_a_normal_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """No container yet (the lazy default) answers 200 with mode recovery and
    desktop_url null, never a 503; nothing on disk is read and no VNC probe runs."""
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    handle = _control_client(monkeypatch, None)
    probe = AsyncMock()
    monkeypatch.setattr(server, "_vnc_input", probe)
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))

    response = client.get("/sessions/chat-1/desktop-control")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "run_id": None, "ready": False, "mode": "recovery", "epoch": "0", "desktop_url": None,
    }
    assert response.headers["cache-control"] == "no-store"
    handle.query.assert_awaited_once()
    probe.assert_not_awaited()


def test_desktop_control_status_reports_the_claimed_viewer_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Once the workflow holds a binding, status carries its noVNC URL next to
    the existing runtime.json-derived mode and epoch."""
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    desktop = tmp_path / "gwen-desktop-chat-1"
    desktop.mkdir()
    (desktop / "runtime.json").write_text(json.dumps({
        "epoch": 7, "owner": {"namespace": "test", "workflow_id": "chat-1"}, "mode": "agent",
    }))
    _control_client(monkeypatch, _NOVNC_URL)
    monkeypatch.setattr(server, "_vnc_input", AsyncMock())
    monkeypatch.setattr(server, "task_queue_has_pollers", AsyncMock(return_value=True))

    response = client.get("/sessions/chat-1/desktop-control")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "run_id": None, "ready": True, "mode": "agent", "epoch": "7", "desktop_url": _NOVNC_URL,
    }


def test_desktop_control_status_unknown_session_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = _control_client(monkeypatch, None)
    handle.query = AsyncMock(side_effect=RPCError("not found", RPCStatusCode.NOT_FOUND, b""))

    assert client.get("/sessions/missing/desktop-control").status_code == 404


def test_readiness_lease_helpers_are_gone() -> None:
    for removed in ("readiness", "validate_readiness_record", "models_of"):
        assert not hasattr(server, removed), (
            f"server.{removed} still exists; readiness is Temporal-native now"
        )


def _validating_turn_client(monkeypatch: pytest.MonkeyPatch, current_model: str) -> MagicMock:
    """A Temporal client whose session already holds ``current_model``.

    ``turn_stream`` queries the workflow's current model before deciding
    whether a switch needs catalog discovery, so the handle's ``query`` must be
    awaitable. ``start_update`` is recorded so a test can prove the rejected
    request never submitted a turn.
    """
    handle = MagicMock()
    handle.query = AsyncMock(return_value=current_model)
    handle.start_update = AsyncMock()
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle.return_value = handle
    monkeypatch.setitem(server._state, "client", temporal_client)
    monkeypatch.setattr(
        server.WorkflowStreamClient, "create",
        MagicMock(return_value=SimpleNamespace(get_offset=AsyncMock(return_value=0))),
    )
    return handle


def test_turn_unknown_model_id_is_400_listing_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_catalog(monkeypatch, [_MODEL_A, _MODEL_B])
    handle = _validating_turn_client(monkeypatch, "model-a")

    response = client.post(
        "/sessions/chat-1/turns/stream",
        json={"prompt": "hi", "model_id": "nope"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "nope" in detail
    assert "model-a" in detail
    assert "model-b" in detail
    handle.start_update.assert_not_called()


def test_turn_rejects_unverified_reasoning_effort(client, monkeypatch):
    seed_catalog(monkeypatch, [{"id": "gemini-3.8-flash", "provider": "google-ai-studio", "label": "gemini-3.8-flash"}])
    handle = _validating_turn_client(monkeypatch, "gemini-3.8-flash")
    response = client.post("/sessions/chat-1/turns/stream", json={
        "prompt": "hi", "model_id": "gemini-3.8-flash", "reasoning_effort": "minimal",
    })
    assert response.status_code == 422
    handle.start_update.assert_not_called()


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


def test_turn_request_defaults_tools_and_skills_to_empty_lists() -> None:
    body = server.TurnRequest(prompt="x")
    assert body.tools == []
    assert body.skills == []


@pytest.mark.asyncio
async def test_turn_stream_forwards_tools_and_skills_as_update_args(monkeypatch):
    seed_catalog(monkeypatch, [_MODEL_A])

    async def result():
        return "ok"

    update_handle = SimpleNamespace(result=result, workflow_run_id="synthetic-run")
    handle = MagicMock()
    handle.query = AsyncMock(return_value=None)
    handle.start_update = AsyncMock(return_value=update_handle)
    temporal_client = MagicMock()
    temporal_client.get_workflow_handle = MagicMock(return_value=handle)
    monkeypatch.setitem(server._state, "client", temporal_client)

    async def _no_frames() -> Any:
        return
        yield  # unreachable; makes this an async generator

    stream_client = SimpleNamespace(
        get_offset=AsyncMock(return_value=0),
        subscribe=MagicMock(return_value=_no_frames()),
    )
    monkeypatch.setattr(
        server.WorkflowStreamClient,
        "create",
        MagicMock(return_value=stream_client),
    )

    response = await server.turn_stream(
        "chat-args", server.TurnRequest(prompt="p", tools=["journal"], skills=["x"])
    )
    frames = [frame async for frame in response.body_iterator]
    assert any(b'"done": true' in frame for frame in frames)

    handle.start_update.assert_awaited_once()
    turn_args = handle.start_update.call_args.kwargs["args"]
    assert turn_args == ["p", ["journal"], ["x"]]


_AGENT_API_TYPE_NAMES = {
    "web_search",
    "fetch_url",
    "image_search",
    "people_search",
    "finance_search",
    "sandbox",
}


def test_catalog_lists_session_discoverable_and_skills_without_agent_api_types(
    client: TestClient,
) -> None:
    response = client.get("/catalog")
    assert response.status_code == 200
    body = response.json()
    assert "journal" in body["tools"]["session"]
    assert "forge" in body["tools"]["discoverable"]
    assert not _AGENT_API_TYPE_NAMES & set(body["tools"]["session"])
    assert not _AGENT_API_TYPE_NAMES & set(body["tools"]["discoverable"])
    for skill in body["skills"]:
        assert isinstance(skill["name"], str)
        assert isinstance(skill["description"], str)
