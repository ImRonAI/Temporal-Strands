"""Exercise the real SDK stream and request boundary, not a fake model loop."""

import asyncio
import copy
import json

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from config import CONNECTORS
from perplexity_model import PerplexityModel
from test_perplexity_model import CREATED, COMPLETED, collect, function_call_added, output_item_done, response_obj, sse, text_delta


TOOLS = [{"type": "sandbox"}, *[{"type": "connector", **item} for item in CONNECTORS]]


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-live")


def failed(connector_id):
    return {"type": "response.failed", "response": response_obj(status="failed", error={
        "code": "external_connector_error", "message": f'Managed connector "{connector_id}" is not connected.',
    })}


def test_documented_connector_ids_are_configured_without_allowlists():
    assert {item["id"] for item in CONNECTORS} == {"connector_googledrive", "connector_github", "connector_linear"}
    assert all("allowed_tools" not in item for item in CONNECTORS)


@pytest.mark.asyncio
@pytest.mark.parametrize("http_failure", [False, True])
async def test_initialization_failure_is_request_local_visible_and_recovers(http_failure):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            if http_failure:
                return httpx.Response(400, json={"error": failed("connector_googledrive")["response"]["error"]})
            return httpx.Response(200, content=sse(CREATED, failed("connector_googledrive")), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, content=sse(CREATED, text_delta("Independent work completed."), COMPLETED), headers={"content-type": "text/event-stream"})
    model = PerplexityModel(model_id="test", api_key="test", params={"tools": TOOLS, "store": True}, transport=httpx.MockTransport(handler))
    messages = [{"role": "user", "content": [{"text": "Use the desktop"}]}]
    original = copy.deepcopy((messages, model.get_config()))
    model_state = {"response_id": "previous-success"}
    events = await collect(model, messages, model_state=model_state)
    assert len(requests) == 2
    assert [r["previous_response_id"] for r in requests] == ["previous-success", "previous-success"]
    assert requests[0]["tools"] == TOOLS
    assert requests[1]["tools"] == [t for t in TOOLS if t.get("id") != "connector_googledrive"]
    assert "unavailable" in requests[1]["input"][-1]["content"][0]["text"]
    notice = next(e["perplexity"] for e in events if e.get("perplexity", {}).get("error"))
    assert notice["type"] == "mcp_list_tools" and notice["connector_id"] == "connector_googledrive"
    assert sum("messageStart" in e for e in events) == 1
    assert sum("messageStop" in e for e in events) == 1
    assert (messages, model.get_config()) == original
    assert model._invocation.get() is None
    await collect(model, messages)
    assert requests[2]["tools"] == TOOLS  # reconnect is discovered on the very next invocation


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [
    text_delta("Partial answer"), function_call_added(),
    {"type": "response.reasoning.started"},
    output_item_done({"type": "sandbox_results", "call_id": "mutation", "results": []}),
    output_item_done({"type": "mcp_call", "connector_id": "connector_linear", "name": "create_issue"}),
    {"type": "future.execution.event"},
])
async def test_failure_after_any_execution_never_replays(event):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, content=sse(CREATED, event, failed("connector_googledrive")), headers={"content-type": "text/event-stream"})
    model = PerplexityModel(model_id="test", params={"tools": TOOLS}, transport=httpx.MockTransport(handler))
    with pytest.raises(ApplicationError) as caught:
        await collect(model)
    assert caught.value.non_retryable and count == 1


@pytest.mark.asyncio
async def test_only_completed_catalog_and_inband_error_are_observed(caplog):
    initial = {"type": "mcp_list_tools", "id": "list", "connector_id": "connector_googledrive", "server_label": "google_drive", "tools": []}
    complete = {**initial, "tools": [{"name": "search"}]}
    error = {"type": "mcp_call", "connector_id": "connector_linear", "server_label": "linear", "id": "call", "name": "search", "error": "AUTH_REQUIRED"}
    body = sse(CREATED, {"type": "response.output_item.added", "item": initial}, output_item_done(complete), output_item_done(error), text_delta("Drive remains available."), COMPLETED)
    model = PerplexityModel(model_id="test", params={"tools": TOOLS}, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})))
    events = await collect(model)
    assert [e["perplexity"]["item"] for e in events if "perplexity" in e] == [complete, error]
    assert "AUTH_REQUIRED" in caplog.text and "google_drive" not in caplog.text


@pytest.mark.asyncio
async def test_recovery_on_shared_model_does_not_change_concurrent_request():
    calls = {}
    async def handler(request):
        body = json.loads(request.content)
        who = body["input"][0]["content"][0]["text"]
        calls.setdefault(who, []).append(body)
        await asyncio.sleep(0)
        payload = failed("connector_googledrive") if who == "A" and len(calls[who]) == 1 else COMPLETED
        return httpx.Response(200, content=sse(CREATED, payload), headers={"content-type": "text/event-stream"})
    model = PerplexityModel(model_id="test", params={"tools": TOOLS}, transport=httpx.MockTransport(handler))
    await asyncio.gather(*(collect(model, [{"role": "user", "content": [{"text": who}]}]) for who in ("A", "B")))
    assert len(calls["A"]) == 2 and len(calls["B"]) == 1
    assert calls["B"][0]["tools"] == TOOLS
    assert model.get_config()["params"]["tools"] == TOOLS


@pytest.mark.asyncio
async def test_each_unavailable_connector_is_isolated_once_with_finite_budget():
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        connector = next((t for t in body["tools"] if t["type"] == "connector"), None)
        return httpx.Response(200, content=sse(CREATED, failed(connector["id"]) if connector else COMPLETED), headers={"content-type": "text/event-stream"})
    model = PerplexityModel(model_id="test", params={"tools": TOOLS}, transport=httpx.MockTransport(handler))
    events = await collect(model)
    assert len(requests) == len(CONNECTORS) + 1
    assert requests[-1]["tools"] == [{"type": "sandbox"}]
    assert len([e for e in events if e.get("perplexity", {}).get("error")]) == len(CONNECTORS)
    assert model.get_config()["params"]["tools"] == TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_unrelated_request_errors_never_drop_connectors_or_loop(status):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(status, json={"error": {"message": "Invalid request", "type": "invalid_request_error"}})
    model = PerplexityModel(model_id="test", params={"tools": TOOLS}, transport=httpx.MockTransport(handler))
    with pytest.raises(ApplicationError) as error:
        await collect(model)
    assert error.value.non_retryable
    assert len(requests) == 1 and requests[0]["tools"] == TOOLS
