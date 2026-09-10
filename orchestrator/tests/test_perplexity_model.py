"""PerplexityModel as an OpenAIResponsesModel subclass.

Every fixture serves a canned SSE body through ``httpx.MockTransport``: the
parent's ``openai.AsyncOpenAI`` stream loop and this module's native-event tap
both read that one body, exactly as they do against the live API. There is no
fake client seam — the seam is the HTTP transport, so the parent parser under
test is the real one.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic import BaseModel
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.exceptions import (
    ContextWindowOverflowException,
    ModelThrottledException,
)
from temporalio.exceptions import ApplicationError

import perplexity_model
from config import NATIVE_OUTPUT_ITEM_TYPES, PERPLEXITY_API_BASE
from perplexity_model import PerplexityModel
from desktop_observation import resolve_observation, store_observation

# ---------------------------------------------------------------------------
# Canned SSE plumbing
# ---------------------------------------------------------------------------


def sse(*payloads: dict[str, Any] | str) -> bytes:
    """Serialize event dicts (or raw lines) as an SSE body."""
    parts: list[bytes] = []
    for payload in payloads:
        if isinstance(payload, str):
            parts.append(payload.encode())
            continue
        name = payload.get("type", "message")
        parts.append(f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode())
    return b"".join(parts)


def response_obj(*, status: str = "completed", usage: dict | None = None, **extra: Any) -> dict:
    body: dict[str, Any] = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "sonar/test",
        "status": status,
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        **extra,
    }
    if usage is not None:
        body["usage"] = usage
    return body


CREATED = {"type": "response.created", "response": response_obj(status="in_progress")}
COMPLETED = {
    "type": "response.completed",
    "response": response_obj(usage={"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}),
}


def text_delta(delta: str, *, index: int = 0) -> dict:
    return {
        "type": "response.output_text.delta",
        "delta": delta,
        "item_id": "msg_1",
        "output_index": index,
        "content_index": 0,
        "sequence_number": index,
        "logprobs": [],
    }


def function_call_item(
    *, call_id: str = "call_1", name: str = "lookup", arguments: str = '{"q": "x"}',
    item_id: str = "fc_1", status: str = "completed",
) -> dict:
    """A ``function_call`` output item as the Agent API emits it.

    Live capture (openai/gpt-6-astra, 2026-09-10): the complete JSON-string
    ``arguments`` rides on the item in BOTH ``response.output_item.added`` and
    ``.done``; the API defines no ``response.function_call_arguments.*`` events.
    """
    return {
        "type": "function_call",
        "id": item_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": status,
    }


def function_call_added(*, call_id: str = "call_1", name: str = "lookup",
                        arguments: str = '{"q": "x"}', item_id: str = "fc_1") -> dict:
    return {
        "type": "response.output_item.added",
        "output_index": 1,
        "sequence_number": 10,
        "item": function_call_item(call_id=call_id, name=name, arguments=arguments, item_id=item_id),
    }


def function_call_done(arguments: str = '{"q": "x"}', *, call_id: str = "call_1",
                       name: str = "lookup", item_id: str = "fc_1") -> dict:
    return {
        "type": "response.output_item.done",
        "output_index": 1,
        "sequence_number": 11,
        "item": function_call_item(call_id=call_id, name=name, arguments=arguments, item_id=item_id),
    }


def output_item_done(item: dict, *, index: int = 2) -> dict:
    return {
        "type": "response.output_item.done",
        "output_index": index,
        "sequence_number": 20 + index,
        "item": item,
    }


SHARE_FILE_ITEM = {
    "type": "share_file",
    "file_id": "file-1",
    "path": "/v1/responses/resp-1/files/file-1/content",
    "status": "completed",
}
SEARCH_RESULTS_EVENT = {
    "type": "response.reasoning.search_results",
    "results": [{"url": "https://example.com", "title": "Example"}],
}


class Recorder:
    """Captures the decoded request body of every POST the SDK makes."""

    def __init__(self, body: bytes, *, status: int = 200, chunk_size: int | None = None) -> None:
        self.body = body
        self.status = status
        self.chunk_size = chunk_size
        self.requests: list[dict[str, Any]] = []
        self.urls: list[str] = []
        self.headers: list[httpx.Headers] = []
        self.forwarded = bytearray()

    @property
    def request(self) -> dict[str, Any]:
        assert self.requests, "no request was sent"
        return self.requests[-1]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.headers.append(request.headers)
        self.requests.append(json.loads(request.content or b"{}"))
        if self.status >= 400:
            return httpx.Response(
                self.status,
                json={"error": {"message": "boom", "type": "invalid_request_error"}},
            )
        if self.chunk_size is None:
            return httpx.Response(
                self.status,
                content=self.body,
                headers={"content-type": "text/event-stream"},
            )
        body, size = self.body, self.chunk_size

        async def stream_body():
            for start in range(0, len(body), size):
                yield body[start : start + size]

        return httpx.Response(
            self.status,
            content=stream_body(),
            headers={"content-type": "text/event-stream"},
        )


def build(
    recorder: Recorder,
    *,
    model_id: str = "sonar/test",
    params: dict[str, Any] | None = None,
    api_key: str = "pplx-test",
    **config: Any,
) -> PerplexityModel:
    """A model whose HTTP transport is the recorder's MockTransport."""
    return PerplexityModel(
        model_id=model_id,
        params=params,
        api_key=api_key,
        transport=httpx.MockTransport(recorder.handler),
        **config,
    )


async def collect(model: PerplexityModel, messages=None, **kwargs) -> list[dict[str, Any]]:
    return [event async for event in model.stream(messages or [], **kwargs)]


@pytest.fixture
def desktop_image(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    (tmp_path / "runtime.json").write_text(json.dumps({
        "epoch": 7, "owner": {"namespace": "test", "workflow_id": "chat-1"}, "mode": "agent",
    }))
    ref = store_observation(
        Image.new("RGB", (16, 12), "blue"), namespace="test", workflow_id="chat-1",
        operation_id="capture-1", desktop_epoch=7,
    )
    monkeypatch.setattr(perplexity_model.activity, "info", lambda: SimpleNamespace(namespace="test", workflow_id="chat-1"))
    return ref, resolve_observation(ref, namespace="test", workflow_id="chat-1")


def desktop_messages(ref, name="browser"):
    return [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "capture-1", "name": name, "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "capture-1", "status": "success", "content": [
            {"text": json.dumps({"status": "success", "observation": ref.model_dump(mode="json")})},
        ]}}]},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["browser", "click", "take_screenshot"])
async def test_desktop_observation_becomes_separate_user_image(desktop_image, name):
    ref, png = desktop_image
    recorder = Recorder(sse(CREATED, SEARCH_RESULTS_EVENT, text_delta("seen"), COMPLETED))
    messages = desktop_messages(ref, name)
    before = copy.deepcopy(messages)
    model = build(recorder)
    frames = await collect(model, messages)
    inputs = recorder.request["input"]
    assert inputs[-2]["type"] == "function_call_output"
    assert isinstance(inputs[-2]["output"], str)
    assert inputs[-1]["role"] == "user"
    assert inputs[-1]["content"][0]["type"] == "input_text"
    part = inputs[-1]["content"][1]
    assert part["type"] == "input_image"
    assert base64.b64decode(part["image_url"].split(",", 1)[1]) == png
    assert messages == before
    assert {"perplexity": SEARCH_RESULTS_EVENT} in frames
    assert b"input_image" not in json.dumps(messages).encode()
    await collect(model, messages)
    assert recorder.request["input"] == inputs


@pytest.mark.asyncio
async def test_desktop_hydration_ignores_old_reference(desktop_image):
    ref, _ = desktop_image
    old = desktop_messages(ref.model_copy(update={"artifact_id": "missing-old-artifact"}))
    old[0]["content"][0]["toolUse"]["toolUseId"] = "old"
    old[1]["content"][0]["toolResult"]["toolUseId"] = "old"
    recorder = Recorder(sse(COMPLETED))
    await collect(build(recorder), old + desktop_messages(ref))
    images = [part for item in recorder.request["input"] for part in item.get("content", []) if isinstance(part, dict) and part["type"] == "input_image"]
    assert len(images) == 1


@pytest.mark.asyncio
async def test_desktop_hydration_fails_before_provider_on_missing_artifact(desktop_image):
    ref, _ = desktop_image
    from uuid import uuid4
    recorder = Recorder(sse(COMPLETED))
    with pytest.raises(ApplicationError) as error:
        await collect(build(recorder), desktop_messages(ref.model_copy(update={"artifact_id": str(uuid4())})))
    assert error.value.non_retryable
    assert not recorder.requests


@pytest.mark.asyncio
async def test_desktop_hydration_rejects_wrong_native_scope(desktop_image, monkeypatch):
    ref, _ = desktop_image
    monkeypatch.setattr(perplexity_model.activity, "info", lambda: SimpleNamespace(namespace="other", workflow_id="chat-1"))
    recorder = Recorder(sse(COMPLETED))
    with pytest.raises(ApplicationError) as error:
        await collect(build(recorder), desktop_messages(ref))
    assert error.value.non_retryable
    assert not recorder.requests


@pytest.mark.asyncio
async def test_unrelated_tool_reference_never_triggers_hydration(desktop_image):
    ref, _ = desktop_image
    recorder = Recorder(sse(COMPLETED))
    await collect(build(recorder), desktop_messages(ref, "lookup"))
    assert recorder.request["input"][-1]["type"] == "function_call_output"


@pytest.mark.asyncio
async def test_successful_desktop_result_requires_observation(desktop_image):
    ref, _ = desktop_image
    messages = desktop_messages(ref)
    messages[-1]["content"][0]["toolResult"]["content"] = [{"text": '{"status":"success"}'}]
    recorder = Recorder(sse(COMPLETED))
    with pytest.raises(ApplicationError) as error:
        await collect(build(recorder), messages)
    assert error.value.non_retryable
    assert not recorder.requests


@pytest.mark.asyncio
async def test_failed_desktop_result_can_report_error_without_image(desktop_image):
    ref, _ = desktop_image
    messages = desktop_messages(ref)
    messages[-1]["content"][0]["toolResult"].update(status="error", content=[{"text": "Action failed"}])
    recorder = Recorder(sse(COMPLETED))
    await collect(build(recorder), messages)
    assert recorder.request["input"][-1]["output"] == "Action failed"


@pytest.mark.asyncio
async def test_desktop_hydration_uses_keyword_messages(desktop_image):
    ref, _ = desktop_image
    recorder = Recorder(sse(COMPLETED))
    _ = [frame async for frame in build(recorder).stream(messages=desktop_messages(ref))]
    assert recorder.request["input"][-1]["content"][-1]["type"] == "input_image"


# ---------------------------------------------------------------------------
# Framework adherence
# ---------------------------------------------------------------------------


def test_perplexity_model_subclasses_the_concrete_strands_provider() -> None:
    assert issubclass(PerplexityModel, OpenAIResponsesModel)


def test_hand_rolled_parser_helpers_are_gone() -> None:
    """The parent owns message formatting, sequencing, and error mapping."""
    for removed in (
        "_message_items",
        "_message_item",
        "_image_part",
        "flush_ready",
        "_raise_sdk_error",
        "_raise_failed",
        "_native_event",
    ):
        assert not hasattr(PerplexityModel, removed), removed
    for removed in ("_value", "_application_error"):
        assert not hasattr(perplexity_model, removed), removed


def test_only_three_methods_are_overridden() -> None:
    """Exactly ``_resolve_client_args``, ``_format_request``, ``stream``."""
    inherited = set(dir(OpenAIResponsesModel))
    overrides = {
        name
        for name, value in vars(PerplexityModel).items()
        if name in inherited
        and not name.startswith("__")
        and name != "_abc_impl"  # ABCMeta machinery, not a method
        and callable(getattr(value, "__func__", value))
    }
    assert overrides == {"_resolve_client_args", "_format_request", "stream"}


def test_module_does_not_use_the_perplexity_sdk_client() -> None:
    source = (perplexity_model.__file__ or "").strip()
    assert source
    text = open(source, encoding="utf-8").read()
    assert "AsyncPerplexity" not in text
    assert '_format_chunk({' not in text


# ---------------------------------------------------------------------------
# (1) _resolve_client_args
# ---------------------------------------------------------------------------


def test_client_args_pin_the_agent_api_base_and_disable_retries() -> None:
    model = build(Recorder(sse(COMPLETED)))
    args = model._resolve_client_args()

    assert args["base_url"] == f"{PERPLEXITY_API_BASE}/v1"
    assert args["api_key"] == "pplx-test"
    assert args["max_retries"] == 0
    assert isinstance(args["http_client"], httpx.AsyncClient)


@pytest.mark.asyncio
async def test_request_goes_to_the_pinned_v1_responses_endpoint() -> None:
    recorder = Recorder(sse(COMPLETED))
    await collect(build(recorder))

    assert recorder.urls == [f"{PERPLEXITY_API_BASE}/v1/responses"]
    assert recorder.headers[0]["authorization"] == "Bearer pplx-test"


def test_api_key_never_enters_model_configuration() -> None:
    model = build(Recorder(sse(COMPLETED)), params={"temperature": 0.2})

    assert "api_key" not in model.get_config()
    assert model.get_config()["model_id"] == "sonar/test"
    assert model.get_config()["params"] == {"temperature": 0.2}


# ---------------------------------------------------------------------------
# (2) _format_request
# ---------------------------------------------------------------------------


def test_catalog_model_id_is_sent_as_model() -> None:
    request = build(Recorder(sse(COMPLETED)))._format_request([], None, "be exact")

    assert request["model"] == "sonar/test"
    assert "preset" not in request
    assert request["instructions"] == "be exact"
    assert request["stream"] is True


@pytest.mark.parametrize("name", ["fast", "low", "medium", "high", "xhigh", "wide-research"])
def test_preset_request_sends_preset_and_no_model(name: str) -> None:
    model = build(Recorder(sse(COMPLETED)), model_id=f"preset:{name}")

    request = model._format_request([], None, "be exact")

    # `preset` is not a named OpenAI SDK kwarg, so it rides in extra_body.
    assert request["extra_body"]["preset"] == name
    assert "model" not in request
    assert "preset" not in request
    assert model.get_config()["model_id"] == f"preset:{name}"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["fast", "high", "wide-research"])
async def test_preset_reaches_the_wire_and_model_does_not(name: str) -> None:
    recorder = Recorder(sse(COMPLETED))

    await collect(build(recorder, model_id=f"preset:{name}"))

    assert recorder.request["preset"] == name
    assert "model" not in recorder.request


def test_store_survives_the_parents_stateful_override() -> None:
    """The parent writes ``store`` AFTER **params, so stateful must be True."""
    model = build(Recorder(sse(COMPLETED)), params={"store": True, "background": True})

    request = model._format_request([], None, None)

    assert model.stateful is True
    assert request["store"] is True
    assert request["background"] is True


def test_object_schemas_without_properties_are_normalized() -> None:
    model = build(Recorder(sse(COMPLETED)))
    tools = [
        {
            "name": "lookup",
            "description": "Lookup",
            "inputSchema": {"json": {"type": "object", "properties": {"filter": {"type": "object"}}}},
        }
    ]

    request = model._format_request([], tools, None)

    assert request["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Lookup",
            "parameters": {
                "type": "object",
                "properties": {"filter": {"type": "object", "properties": {}}},
            },
        }
    ]


def test_native_tools_from_params_precede_function_tools_unmodified() -> None:
    native = [
        {"type": "web_search", "filters": {"recency": "week"}},
        {"type": "connector", "id": "connector_github", "server_label": "github"},
    ]
    params = {"tools": native, "temperature": 0.2}
    original = copy.deepcopy(params)
    model = build(Recorder(sse(COMPLETED)), params=params)
    tools = [
        {
            "name": "lookup",
            "description": "Lookup",
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
    ]

    request = model._format_request([], tools, None)

    assert request["tools"][:2] == native
    assert request["tools"][2]["type"] == "function"
    assert params == original, "params must not be mutated by request formatting"


@pytest.mark.asyncio
async def test_agent_api_params_pass_through_to_the_wire() -> None:
    params = {
        "skills": [{"type": "builtin", "name": "office"}],
        "reasoning": {"effort": "high"},
        "max_steps": 100,
        "background": True,
        "store": True,
        "max_output_tokens": 4096,
        "temperature": 0.2,
    }
    original = copy.deepcopy(params)
    recorder = Recorder(sse(COMPLETED))

    await collect(build(recorder, params=params))

    for key, value in original.items():
        assert recorder.request[key] == value, key
    assert recorder.request["stream"] is True
    assert params == original


@pytest.mark.asyncio
async def test_messages_and_tool_history_use_the_parent_formatter() -> None:
    recorder = Recorder(sse(COMPLETED))
    messages = [
        {"role": "user", "content": [{"text": "inspect"}]},
        {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "call-1", "name": "lookup", "input": {"q": "x"}}}
            ],
        },
        {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": "call-1", "content": [{"json": {"answer": 3}}]}}],
        },
    ]

    await collect(build(recorder), messages)

    assert recorder.request["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "inspect"}]},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "lookup",
            "arguments": '{"q": "x"}',
        },
        {"type": "function_call_output", "call_id": "call-1", "output": '{"answer": 3}'},
    ]


@pytest.mark.asyncio
async def test_images_are_encoded_by_the_parent_formatter() -> None:
    recorder = Recorder(sse(COMPLETED))
    messages = [
        {
            "role": "user",
            "content": [{"image": {"format": "png", "source": {"bytes": b"png"}}}],
        }
    ]

    await collect(build(recorder), messages)

    assert recorder.request["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_image", "image_url": "data:image/png;base64,cG5n"}],
        }
    ]


# ---------------------------------------------------------------------------
# (3) native-event tap + stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_frame_sequence() -> None:
    """Text, tool call, and exactly two native frames before messageStop."""
    recorder = Recorder(
        sse(
            CREATED,
            text_delta("Check", index=0),
            text_delta("ing", index=0),
            text_delta(" now", index=0),
            function_call_added(),
            function_call_done(),
            SEARCH_RESULTS_EVENT,
            output_item_done(SHARE_FILE_ITEM),
            COMPLETED,
        )
    )

    events = await collect(build(recorder))

    assert events[0] == {"messageStart": {"role": "assistant"}}
    assert {"contentBlockStart": {"start": {}}} in events
    assert [
        event["contentBlockDelta"]["delta"]["text"]
        for event in events
        if "contentBlockDelta" in event and "text" in event["contentBlockDelta"]["delta"]
    ] == ["Check", "ing", " now"]
    assert {
        "contentBlockStart": {"start": {"toolUse": {"name": "lookup", "toolUseId": "call_1"}}}
    } in events
    assert {
        "contentBlockDelta": {"delta": {"toolUse": {"input": '{"q": "x"}'}}}
    } in events

    native = [event for event in events if "perplexity" in event]
    assert native == [
        {"perplexity": SEARCH_RESULTS_EVENT},
        {"perplexity": output_item_done(SHARE_FILE_ITEM)},
    ]
    stop_index = next(i for i, event in enumerate(events) if "messageStop" in event)
    assert all(events.index(frame) < stop_index for frame in native)
    assert events[stop_index] == {"messageStop": {"stopReason": "tool_use"}}
    assert events[-1]["metadata"]["usage"] == {
        "inputTokens": 4,
        "outputTokens": 3,
        "totalTokens": 7,
    }


def tool_use_inputs(events: list[dict[str, Any]]) -> dict[str, str]:
    """``toolUseId`` -> the ``input`` string the agent will ``json.loads``."""
    inputs: dict[str, str] = {}
    current: str | None = None
    for event in events:
        if (start := event.get("contentBlockStart")) is not None:
            current = ((start.get("start") or {}).get("toolUse") or {}).get("toolUseId")
        elif "contentBlockStop" in event:
            current = None
        elif current and (delta := event.get("contentBlockDelta", {}).get("delta", {}).get("toolUse")):
            inputs[current] = inputs.get(current, "") + delta["input"]
    return inputs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire",
    [
        # The parent registers a call only on ``.added`` (openai_responses.py:388);
        # the live API sends ``.added`` AND ``.done`` for every call.
        pytest.param([function_call_added()], id="added-only"),
        pytest.param([function_call_added(), function_call_done()], id="added-then-done"),
        pytest.param([function_call_added(arguments=""), function_call_done()], id="empty-added-then-done"),
    ],
)
async def test_function_call_arguments_come_from_the_output_item(wire: list[dict]) -> None:
    """Regression: the Agent API has no ``function_call_arguments.*`` events.

    Before this, every tool call reached the agent with ``input: ""`` -> ``{}``
    -> ``missing a required argument: 'path'`` on ``load_tool``, in a loop
    until the workflow history cap terminated the run (chat-c8ad6bf3a395998e).
    """
    recorder = Recorder(sse(CREATED, *wire, COMPLETED))

    events = await collect(build(recorder))

    assert tool_use_inputs(events) == {"call_1": '{"q": "x"}'}
    assert {"messageStop": {"stopReason": "tool_use"}} in events


@pytest.mark.asyncio
async def test_done_item_arguments_win_over_added_item_arguments() -> None:
    recorder = Recorder(
        sse(CREATED, function_call_added(arguments='{"q": "par'), function_call_done('{"q": "final"}'), COMPLETED)
    )

    events = await collect(build(recorder))

    assert tool_use_inputs(events) == {"call_1": '{"q": "final"}'}


@pytest.mark.asyncio
async def test_parallel_function_calls_keep_their_own_arguments() -> None:
    """Two calls in one turn (ConcurrentToolExecutor fan-out) must not swap inputs."""
    recorder = Recorder(
        sse(
            CREATED,
            function_call_added(call_id="call_a", name="load_tool", item_id="fc_a",
                                arguments='{"path": "calculator", "name": "calculator"}'),
            function_call_added(call_id="call_b", name="think", item_id="fc_b",
                                arguments='{"thought": "plan", "cycle_count": 2}'),
            function_call_done(call_id="call_a", name="load_tool", item_id="fc_a",
                               arguments='{"path": "calculator", "name": "calculator"}'),
            function_call_done(call_id="call_b", name="think", item_id="fc_b",
                               arguments='{"thought": "plan", "cycle_count": 2}'),
            COMPLETED,
        )
    )

    events = await collect(build(recorder))

    inputs = tool_use_inputs(events)
    assert json.loads(inputs["call_a"]) == {"path": "calculator", "name": "calculator"}
    assert json.loads(inputs["call_b"]) == {"thought": "plan", "cycle_count": 2}
    names = [
        e["contentBlockStart"]["start"]["toolUse"]["name"]
        for e in events
        if "toolUse" in (e.get("contentBlockStart", {}).get("start") or {})
    ]
    assert names == ["load_tool", "think"]


@pytest.mark.asyncio
async def test_openai_style_argument_delta_events_still_win_when_present() -> None:
    """If a provider does emit ``function_call_arguments.done``, the parent's
    value is kept: the fill only replaces an EMPTY delta."""
    openai_done = {
        "type": "response.function_call_arguments.done",
        "item_id": "fc_1",
        "output_index": 1,
        "sequence_number": 11,
        "arguments": '{"q": "from-delta"}',
    }
    recorder = Recorder(sse(CREATED, function_call_added(arguments='{"q": "from-item"}'), openai_done, COMPLETED))

    events = await collect(build(recorder))

    assert tool_use_inputs(events) == {"call_1": '{"q": "from-delta"}'}


@pytest.mark.asyncio
async def test_function_call_arguments_do_not_leak_into_the_next_turn() -> None:
    recorder = Recorder(sse(CREATED, function_call_added(), function_call_done(), COMPLETED))
    model = build(recorder)
    await collect(model)
    assert model._call_arguments == {"call_1": '{"q": "x"}'}

    recorder.body = sse(CREATED, text_delta("plain"), COMPLETED)
    second = await collect(model)

    assert model._call_arguments == {}
    assert tool_use_inputs(second) == {}


def test_function_call_item_filter_is_total() -> None:
    """No payload shape can make the argument record raise or mis-key."""
    fc = perplexity_model._function_call_item
    assert fc(None) is None
    assert fc({"type": "response.output_item.done"}) is None
    assert fc({"type": "response.output_item.done", "item": "not-a-dict"}) is None
    assert fc({"type": "response.output_item.done", "item": {"type": "message"}}) is None
    assert fc({"type": "response.output_item.done", "item": {"type": "function_call"}}) is None
    assert fc({"type": "response.output_text.delta", "item": function_call_item()}) is None
    assert fc(function_call_added()) == function_call_item()
    assert fc(function_call_done()) == function_call_item()

    arguments: dict[str, str] = {}
    queue: asyncio.Queue = asyncio.Queue()
    perplexity_model._enqueue_native(queue, b"data: " + json.dumps(function_call_added(arguments="")).encode(), arguments)
    assert arguments == {}
    perplexity_model._enqueue_native(queue, b"data: " + json.dumps(function_call_done()).encode(), arguments)
    assert arguments == {"call_1": '{"q": "x"}'}
    assert queue.qsize() == 0  # function_call items are not native frames


@pytest.mark.asyncio
async def test_text_only_turn_stops_with_end_turn() -> None:
    recorder = Recorder(sse(CREATED, text_delta("hello"), COMPLETED))

    events = await collect(build(recorder))

    assert {"messageStop": {"stopReason": "end_turn"}} in events
    assert not any("perplexity" in event for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type", NATIVE_OUTPUT_ITEM_TYPES)
async def test_every_native_output_item_type_is_forwarded_verbatim(item_type: str) -> None:
    item = {"type": item_type, "status": "completed", "id": f"{item_type}-1"}
    recorder = Recorder(sse(CREATED, output_item_done(item), COMPLETED))

    events = await collect(build(recorder))

    assert {"perplexity": output_item_done(item)} in events


@pytest.mark.asyncio
async def test_message_output_items_are_not_forwarded() -> None:
    """A message item's text already streamed as output_text deltas."""
    item = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": []}
    recorder = Recorder(sse(CREATED, text_delta("hi"), output_item_done(item), COMPLETED))

    events = await collect(build(recorder))

    assert not any("perplexity" in event for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        "response.reasoning.search_queries",
        "response.reasoning.search_results",
        "response.reasoning.fetch_url_queries",
        "response.reasoning.fetch_url_results",
        "response.skill.loaded",
    ],
)
async def test_reasoning_and_skill_events_are_forwarded_verbatim(event_type: str) -> None:
    native = {"type": event_type, "thought": "Considering", "sequence_number": 5}
    recorder = Recorder(sse(CREATED, native, COMPLETED))

    events = await collect(build(recorder))

    assert {"perplexity": native} in events


@pytest.mark.asyncio
async def test_unknown_perplexity_only_event_types_are_not_forwarded() -> None:
    recorder = Recorder(
        sse(CREATED, {"type": "response.in_progress", "response": response_obj()}, COMPLETED)
    )

    events = await collect(build(recorder))

    assert not any("perplexity" in event for event in events)


@pytest.mark.asyncio
async def test_native_frames_are_ordered_before_the_next_parent_chunk() -> None:
    recorder = Recorder(
        sse(
            CREATED,
            SEARCH_RESULTS_EVENT,
            text_delta("answer"),
            output_item_done(SHARE_FILE_ITEM),
            COMPLETED,
        )
    )

    events = await collect(build(recorder))
    kinds = [next(iter(event)) for event in events]

    assert kinds.index("perplexity") < kinds.index("contentBlockDelta")
    assert kinds.count("perplexity") == 2


@pytest.mark.asyncio
async def test_tap_survives_arbitrary_chunk_boundaries() -> None:
    """Byte-level chunking must not split a data line out of existence."""
    body = sse(CREATED, text_delta("hello"), SEARCH_RESULTS_EVENT, output_item_done(SHARE_FILE_ITEM), COMPLETED)
    recorder = Recorder(body, chunk_size=7)

    events = await collect(build(recorder))

    assert len([event for event in events if "perplexity" in event]) == 2
    assert {"contentBlockDelta": {"delta": {"text": "hello"}}} in events


@pytest.mark.asyncio
async def test_truncated_trailing_line_and_comments_do_not_break_the_stream() -> None:
    """A dropped connection leaves a partial line; the tap must not react."""
    body = sse(
        CREATED,
        ": keep-alive comment\n\n",
        text_delta("still here"),
        SEARCH_RESULTS_EVENT,
        COMPLETED,
    ) + b'event: response.reasoning.search_queries\ndata: {"type": "response.rea'
    recorder = Recorder(body, chunk_size=11)

    events = await collect(build(recorder))

    assert {"contentBlockDelta": {"delta": {"text": "still here"}}} in events
    assert [event for event in events if "perplexity" in event] == [
        {"perplexity": SEARCH_RESULTS_EVENT}
    ]
    assert {"messageStop": {"stopReason": "end_turn"}} in events


def test_non_json_and_partial_data_lines_are_never_enqueued() -> None:
    """The tap's line filter is total: no input shape can make it raise."""
    queue: asyncio.Queue = asyncio.Queue()
    for raw in (
        b"data: not-json",
        b'data: {"type": "response.reasoning.trunc"',
        b"data: ",
        b"data: null",
        b"data: [1, 2]",
        b"data: [DONE]",
        b": comment",
        b"event: response.reasoning.search_results",
        b"",
        b"\xff\xfe binary noise",
        b'data: {"type": 7}',
        b'data: {"type": "response.output_item.done", "item": "not-a-dict"}',
        b'data: {"type": "response.output_item.done", "item": {"type": "message"}}',
    ):
        perplexity_model._enqueue_native(queue, raw)

    assert queue.qsize() == 0


def test_forwarded_bytes_are_identical_to_the_server_body() -> None:
    """The tap is read-only: the SDK sees exactly what the server sent."""
    body = sse(CREATED, text_delta("hi"), SEARCH_RESULTS_EVENT, COMPLETED)
    forwarded = bytearray()

    async def run() -> None:
        recorder = Recorder(body, chunk_size=5)
        model = build(recorder)
        original_tap = model._tap_sse

        async def tap(response: httpx.Response) -> None:
            await original_tap(response)
            wrapped = response.aiter_bytes

            def counting(chunk_size: int | None = None):
                async def gen():
                    source = wrapped() if chunk_size is None else wrapped(chunk_size)
                    async for chunk in source:
                        forwarded.extend(chunk)
                        yield chunk

                return gen()

            response.aiter_bytes = counting  # type: ignore[method-assign]

        model._tap_sse = tap  # type: ignore[method-assign]
        await collect(model)

    asyncio.run(run())

    assert bytes(forwarded) == body


@pytest.mark.asyncio
async def test_non_sse_responses_are_not_tapped() -> None:
    """A JSON error body must reach the SDK's own error path untouched."""
    recorder = Recorder(b"", status=400)

    with pytest.raises(Exception) as caught:
        await collect(build(recorder))

    assert not isinstance(caught.value, ApplicationError)


@pytest.mark.asyncio
async def test_re_running_the_same_model_does_not_leak_native_frames() -> None:
    """Stale queue state from turn one must not surface in turn two."""
    recorder = Recorder(sse(CREATED, SEARCH_RESULTS_EVENT, text_delta("one"), COMPLETED))
    model = build(recorder)

    first = await collect(model)
    assert len([event for event in first if "perplexity" in event]) == 1

    recorder.body = sse(CREATED, text_delta("two"), COMPLETED)
    second = await collect(model)

    assert not any("perplexity" in event for event in second)


# ---------------------------------------------------------------------------
# Failure mapping (parent's classify_openai_error)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_failed_with_rate_limit_raises_model_throttled() -> None:
    failed = {
        "type": "response.failed",
        "response": response_obj(
            status="failed",
            error={"code": "rate_limit_exceeded", "message": "rate limit reached"},
        ),
    }
    recorder = Recorder(sse(CREATED, failed))

    with pytest.raises(ModelThrottledException):
        await collect(build(recorder))


@pytest.mark.asyncio
async def test_response_failed_with_context_overflow_raises_context_window() -> None:
    failed = {
        "type": "response.failed",
        "response": response_obj(
            status="failed",
            error={"code": "context_length_exceeded", "message": "maximum context length"},
        ),
    }
    recorder = Recorder(sse(CREATED, failed))

    with pytest.raises(ContextWindowOverflowException):
        await collect(build(recorder))


@pytest.mark.asyncio
async def test_response_failed_otherwise_re_raises_and_is_not_a_bare_exception() -> None:
    failed = {
        "type": "response.failed",
        "response": response_obj(
            status="failed", error={"code": "invalid_request_error", "message": "bad tools"}
        ),
    }
    recorder = Recorder(sse(CREATED, failed))

    with pytest.raises(RuntimeError) as caught:
        await collect(build(recorder))

    assert type(caught.value) is not Exception
    assert "bad tools" in str(caught.value)


@pytest.mark.asyncio
async def test_http_429_raises_model_throttled_through_the_parent() -> None:
    recorder = Recorder(b"", status=429)

    with pytest.raises(ModelThrottledException):
        await collect(build(recorder))


@pytest.mark.asyncio
async def test_http_401_propagates_the_sdk_error() -> None:
    import openai

    recorder = Recorder(b"", status=401)

    with pytest.raises(openai.AuthenticationError):
        await collect(build(recorder))


# ---------------------------------------------------------------------------
# Config validation (kept helpers)
# ---------------------------------------------------------------------------


def test_invalid_preset_is_nonretryable() -> None:
    with pytest.raises(ApplicationError, match="Unknown Perplexity preset") as caught:
        build(Recorder(sse(COMPLETED)), model_id="preset:turbo")
    assert caught.value.non_retryable is True


def test_invalid_preset_from_update_config_is_rejected_on_the_next_request() -> None:
    """``update_config`` is the parent's; validation runs when the id is used."""
    model = build(Recorder(sse(COMPLETED)))
    model.update_config(model_id="preset:nope")

    with pytest.raises(ApplicationError, match="Unknown Perplexity preset") as caught:
        model._format_request([], None, None)
    assert caught.value.non_retryable is True


def test_update_config_preserves_minimal_model_config() -> None:
    model = build(Recorder(sse(COMPLETED)), params={"temperature": 0.1})
    model.update_config(model_id="two", params={"temperature": 0.3})

    config = model.get_config()
    assert config["model_id"] == "two"
    assert config["params"] == {"temperature": 0.3}
    assert "api_key" not in config


@pytest.mark.parametrize(
    "reserved",
    ["model", "preset", "input", "stream", "extra_headers", "extra_query", "extra_body", "timeout"],
)
def test_reserved_request_parameters_are_rejected(reserved: str) -> None:
    with pytest.raises(ApplicationError, match="Unsupported model parameters") as caught:
        build(Recorder(sse(COMPLETED)), params={reserved: "override"})
    assert caught.value.non_retryable is True


def test_ensure_object_properties_normalizes_nested_schemas() -> None:
    normalized = perplexity_model._ensure_object_properties(
        {"type": "object", "items": {"type": "object"}, "keep": [1, "two"]}
    )

    assert normalized == {
        "type": "object",
        "properties": {},
        "items": {"type": "object", "properties": {}},
        "keep": [1, "two"],
    }


# ---------------------------------------------------------------------------
# structured_output (inherited)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_output_uses_the_parent_parse_path() -> None:
    class Answer(BaseModel):
        value: int

    parsed_response = response_obj(
        output=[
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": '{"value": 7}', "annotations": []}
                ],
            }
        ]
    )
    recorder = Recorder(b"")
    recorder.handler = lambda request: httpx.Response(  # type: ignore[method-assign]
        200, json=parsed_response, headers={"content-type": "application/json"}
    )

    events = [
        event
        async for event in build(recorder).structured_output(Answer, [])
    ]

    assert events[-1] == {"output": Answer(value=7)}
