import copy
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel
from perplexity import (
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from temporalio.exceptions import ApplicationError

import perplexity_model
from perplexity_model import PerplexityModel


def event(type_: str, **values: object) -> SimpleNamespace:
    return SimpleNamespace(type=type_, **values)


class FakeResponses:
    def __init__(self, events=None, error=None) -> None:
        self.events = events or []
        self.error = error
        self.request = None

    async def create(self, **request):
        self.request = request
        if self.error:
            raise self.error

        async def stream():
            for item in self.events:
                yield item

        return stream()


class FakeClient:
    def __init__(self, events=None, error=None) -> None:
        self.responses = FakeResponses(events, error)


def completed(*, status="completed", output=None, usage=None):
    return event(
        "response.completed",
        response=SimpleNamespace(status=status, output=output or [], usage=usage),
    )


async def collect(model, messages=None, **kwargs):
    return [item async for item in model.stream(messages or [], **kwargs)]


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max"])
async def test_per_turn_reasoning_does_not_change_shared_model_defaults(effort):
    client = FakeClient([completed()])
    model = PerplexityModel(model_id="perplexity/kimi-k3", client=client)
    await collect(model, invocation_state={"reasoning_effort": effort})
    assert client.responses.request["reasoning"] == {"effort": effort}
    await collect(model)
    assert "reasoning" not in client.responses.request
    assert model.get_config()["params"] == {}


@pytest.mark.asyncio
async def test_message_start_has_no_attempt_outside_activity_context() -> None:
    """Outside a Temporal activity the frame stays byte-identical to before."""
    events = await collect(
        PerplexityModel(model_id="sonar/test", client=FakeClient([completed()]))
    )
    assert events[0] == {"messageStart": {"role": "assistant"}}


@pytest.mark.asyncio
async def test_message_start_carries_attempt_inside_activity_context(monkeypatch) -> None:
    """Inside an activity, messageStart carries activity.info().attempt (GWEN-6)."""
    monkeypatch.setattr(perplexity_model.activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        perplexity_model.activity, "info", lambda: SimpleNamespace(attempt=3)
    )
    events = await collect(
        PerplexityModel(model_id="sonar/test", client=FakeClient([completed()]))
    )
    assert events[0] == {"messageStart": {"role": "assistant", "attempt": 3}}


@pytest.mark.asyncio
async def test_request_maps_messages_images_tools_and_exact_params_without_mutation() -> None:
    native_tools = [{"type": "web_search", "filters": {"recency": "week"}}]
    params = {"temperature": 0.2, "tools": native_tools, "reasoning": {"effort": "high"}}
    original = copy.deepcopy(params)
    client = FakeClient([completed()])
    model = PerplexityModel(model_id="sonar/test", params=params, client=client)
    messages = [
        {
            "role": "user",
            "content": [
                {"text": "inspect"},
                {"image": {"format": "png", "source": {"bytes": b"png"}}},
            ],
        }
    ]
    tools = [
        {
            "name": "lookup",
            "description": "Lookup",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"filter": {"type": "object"}},
                }
            },
        }
    ]

    await collect(model, messages, tool_specs=tools, system_prompt="be exact")

    request = client.responses.request
    assert request == {
        "model": "sonar/test",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "inspect"},
                    {"type": "input_image", "image_url": "data:image/png;base64,cG5n"},
                ],
            }
        ],
        "stream": True,
        "temperature": 0.2,
        "reasoning": {"effort": "high"},
        "tools": [
            {"type": "web_search", "filters": {"recency": "week"}},
            {
                "type": "function",
                "name": "lookup",
                "description": "Lookup",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "object", "properties": {}},
                    },
                },
            },
        ],
        "instructions": "be exact",
    }
    assert params == original
    assert model.get_config() == {"model_id": "sonar/test", "params": original}


def test_format_request_exposes_the_provider_request_contract() -> None:
    model = PerplexityModel(model_id="sonar/test", client=FakeClient())

    assert model._format_request([], None, "be exact") == {
        "model": "sonar/test",
        "input": [],
        "stream": True,
        "tools": [],
        "instructions": "be exact",
    }


def test_format_request_normalizes_null_native_tools() -> None:
    model = PerplexityModel(model_id="sonar/test", params={"tools": None}, client=FakeClient())

    assert model._format_request([], None, None)["tools"] == []


@pytest.mark.asyncio
async def test_request_replays_function_calls_outputs_and_thought_signature() -> None:
    client = FakeClient([completed()])
    model = PerplexityModel(model_id="sonar/test", client=client)
    messages = [
        {
            "role": "assistant",
            "content": [
                {"text": "checking"},
                {
                    "toolUse": {
                        "toolUseId": "call-1",
                        "name": "lookup",
                        "input": {"q": "x"},
                        "reasoningSignature": "opaque",
                    }
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "call-1",
                        "content": [{"json": {"answer": 3}}],
                    }
                }
            ],
        },
    ]

    await collect(model, messages)

    assert client.responses.request["input"] == [
        {"type": "message", "role": "assistant", "content": "checking"},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "lookup",
            "arguments": '{"q": "x"}',
            "thought_signature": "opaque",
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"answer": 3}',
        },
    ]
    assert client.responses.request["tools"] == []


@pytest.mark.asyncio
async def test_stream_maps_fragmented_text_usage_and_terminal_stop() -> None:
    usage = SimpleNamespace(input_tokens=4, output_tokens=3, total_tokens=7)
    client = FakeClient(
        [
            event("response.output_text.delta", delta="hel"),
            event("response.output_text.delta", delta="lo"),
            event("response.output_text.done", text="hello"),
            completed(usage=usage),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert events == [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "hel"}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "lo"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {"inputTokens": 4, "outputTokens": 3, "totalTokens": 7},
            }
        },
    ]


@pytest.mark.asyncio
async def test_text_delta_is_emitted_before_provider_stream_completes() -> None:
    released = False

    class LiveResponses:
        async def create(self, **request):
            async def stream():
                nonlocal released
                yield event("response.output_text.delta", delta="live", output_index=0, content_index=0)
                released = True
                yield event("response.output_text.done", text="live", output_index=0, content_index=0)
                yield completed()

            return stream()

    model = PerplexityModel(model_id="sonar/test", client=SimpleNamespace(responses=LiveResponses()))
    events = model.stream([]).__aiter__()

    assert await anext(events) == {"messageStart": {"role": "assistant"}}
    assert await anext(events) == {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
    assert await anext(events) == {
        "contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "live"}}
    }
    assert released is False


@pytest.mark.asyncio
async def test_text_done_supplies_text_when_delta_is_missing() -> None:
    client = FakeClient(
        [
            event("response.output_text.done", text="complete", output_index=0, content_index=0),
            completed(),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "complete"}}} in events


@pytest.mark.asyncio
async def test_stream_maps_separate_text_output_items_to_separate_blocks() -> None:
    client = FakeClient(
        [
            event("response.output_text.delta", delta="first", output_index=0, content_index=0),
            event("response.output_text.done", text="first", output_index=0, content_index=0),
            event("response.output_text.delta", delta="second", output_index=1, content_index=0),
            event("response.output_text.done", text="second", output_index=1, content_index=0),
            completed(),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert events[:7] == [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "first"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "second"}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
    ]


@pytest.mark.asyncio
async def test_stream_maps_multiple_fragmented_calls_and_signatures() -> None:
    calls = [
        SimpleNamespace(
            type="function_call",
            call_id="a",
            name="one",
            arguments='{"x":',
            thought_signature=None,
            status="in_progress",
        ),
        SimpleNamespace(
            type="function_call",
            call_id="b",
            name="two",
            arguments="{",
            thought_signature=None,
            status="in_progress",
        ),
    ]
    done_calls = [
        SimpleNamespace(
            type="function_call",
            call_id="a",
            name="one",
            arguments='{"x": 1}',
            thought_signature="s1",
            status="completed",
        ),
        SimpleNamespace(
            type="function_call",
            call_id="b",
            name="two",
            arguments='{"y": 2}',
            thought_signature=None,
            status="completed",
        ),
    ]
    client = FakeClient(
        [
            event("response.output_item.added", item=calls[0], output_index=0),
            event("response.output_item.added", item=calls[1], output_index=1),
            event("response.output_item.done", item=done_calls[0], output_index=0),
            event("response.output_item.done", item=done_calls[1], output_index=1),
            completed(output=done_calls),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    starts = [item for item in events if "contentBlockStart" in item]
    deltas = [item for item in events if "contentBlockDelta" in item]
    assert starts == [
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "a", "name": "one", "reasoningSignature": "s1"}},
            }
        },
        {
            "contentBlockStart": {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"toolUseId": "b", "name": "two"}},
            }
        },
    ]
    assert [item["contentBlockDelta"]["delta"]["toolUse"]["input"] for item in deltas] == [
        '{"x":',
        " 1}",
        "{",
        '"y": 2}',
    ]
    assert {item["contentBlockStop"]["contentBlockIndex"] for item in events if "contentBlockStop" in item} == {0, 1}
    assert {"messageStop": {"stopReason": "tool_use"}} in events


@pytest.mark.asyncio
async def test_stream_emits_completed_calls_in_output_index_order() -> None:
    first = SimpleNamespace(
        type="function_call", call_id="a", name="one", arguments="{}", status="completed"
    )
    second = SimpleNamespace(
        type="function_call", call_id="b", name="two", arguments="{}", status="completed"
    )
    client = FakeClient(
        [
            event("response.output_item.done", item=second, output_index=1),
            event("response.output_item.done", item=first, output_index=0),
            completed(output=[first, second]),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))
    starts = [item["contentBlockStart"] for item in events if "contentBlockStart" in item]

    assert [item["start"]["toolUse"]["name"] for item in starts] == ["one", "two"]


@pytest.mark.asyncio
async def test_stream_preserves_output_order_across_calls_and_text() -> None:
    call = SimpleNamespace(
        type="function_call", call_id="a", name="one", arguments="{}", status="completed"
    )
    client = FakeClient(
        [
            event("response.output_item.done", item=call, output_index=0),
            event("response.output_text.delta", delta="after", output_index=1, content_index=0),
            event("response.output_text.done", text="after", output_index=1, content_index=0),
            completed(output=[call]),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))
    starts = [item["contentBlockStart"]["start"] for item in events if "contentBlockStart" in item]

    assert starts == [{"toolUse": {"toolUseId": "a", "name": "one"}}, {}]


@pytest.mark.asyncio
async def test_native_output_item_does_not_block_later_text() -> None:
    native_item = SimpleNamespace(type="search_results", status="completed")
    client = FakeClient(
        [
            event("response.output_item.done", item=native_item, output_index=0),
            event("response.output_text.delta", delta="answer", output_index=1, content_index=0),
            event("response.output_text.done", text="answer", output_index=1, content_index=0),
            completed(output=[native_item]),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "answer"}}} in events


@pytest.mark.asyncio
async def test_stream_closes_unfinished_text_blocks_in_start_order() -> None:
    client = FakeClient(
        [
            event("response.output_text.delta", delta="first", output_index=0, content_index=0),
            event("response.output_text.delta", delta="second", output_index=1, content_index=0),
            completed(),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))
    stops = [item["contentBlockStop"]["contentBlockIndex"] for item in events if "contentBlockStop" in item]

    assert stops == [0, 1]


@pytest.mark.asyncio
async def test_malformed_function_arguments_are_nonretryable() -> None:
    call = SimpleNamespace(
        type="function_call",
        call_id="a",
        name="one",
        arguments="not-json",
        status="completed",
    )
    client = FakeClient(
        [
            event("response.output_item.added", item=call, output_index=0),
            event("response.output_item.done", item=call, output_index=0),
        ]
    )
    with pytest.raises(ApplicationError) as caught:
        await collect(PerplexityModel(model_id="sonar/test", client=client))
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "status_code", "non_retryable"),
    [
        (AuthenticationError, 401, True),
        (BadRequestError, 400, True),
        (RateLimitError, 429, False),
        (InternalServerError, 500, False),
    ],
)
async def test_http_failure_classification(error_type, status_code, non_retryable) -> None:
    request = httpx.Request("POST", "https://api.perplexity.ai/v1/agent")
    response = httpx.Response(status_code, request=request)
    error = error_type("failure", response=response, body=None)
    with pytest.raises(ApplicationError) as caught:
        await collect(PerplexityModel(model_id="sonar/test", client=FakeClient(error=error)))
    assert caught.value.non_retryable is non_retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("code,non_retryable", [("invalid_request_error", True), ("server_error", False)])
async def test_response_failed_classification(code, non_retryable) -> None:
    failure = event("response.failed", error=SimpleNamespace(message="failed", code=code, type=code))
    with pytest.raises(ApplicationError) as caught:
        await collect(PerplexityModel(model_id="sonar/test", client=FakeClient([failure])))
    assert caught.value.non_retryable is non_retryable


@pytest.mark.asyncio
async def test_stream_without_authoritative_terminal_event_raises() -> None:
    client = FakeClient([event("response.output_text.delta", delta="partial")])
    with pytest.raises(ApplicationError, match="authoritative terminal"):
        await collect(PerplexityModel(model_id="sonar/test", client=client))


@pytest.mark.asyncio
async def test_structured_output_collects_text_and_validates_json() -> None:
    class Answer(BaseModel):
        value: int

    client = FakeClient(
        [
            event("response.output_text.delta", delta='{"value":'),
            event("response.output_text.delta", delta=" 7}"),
            completed(),
        ]
    )
    events = [
        item
        async for item in PerplexityModel(model_id="sonar/test", client=client).structured_output(Answer, [])
    ]
    assert events[-1] == {"output": Answer(value=7)}


def test_update_config_preserves_minimal_model_config() -> None:
    model = PerplexityModel(model_id="one", client=FakeClient(), params={"temperature": 0.1})
    model.update_config(model_id="two", params={"temperature": 0.3})
    assert model.get_config() == {"model_id": "two", "params": {"temperature": 0.3}}
    assert not {"api_key", "background", "store", "max_output_tokens", "previous_response_id"} & model.get_config().keys()


@pytest.mark.parametrize(
    "reserved",
    [
        "model",
        "preset",
        "input",
        "stream",
        "extra_headers",
        "extra_query",
        "extra_body",
        "timeout",
    ],
)
def test_reserved_request_parameters_are_rejected(reserved) -> None:
    with pytest.raises(ApplicationError, match="Unsupported model parameters") as caught:
        PerplexityModel(model_id="sonar/test", params={reserved: "override"}, client=FakeClient())

    assert caught.value.non_retryable is True


def test_default_client_keeps_credentials_out_of_config_and_disables_retries(monkeypatch) -> None:
    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(perplexity_model, "AsyncPerplexity", fake_client)

    model = PerplexityModel(api_key="secret", model_id="sonar/test", params={"temperature": 0.2})

    assert created == {"api_key": "secret", "max_retries": 0}
    assert model.get_config() == {"model_id": "sonar/test", "params": {"temperature": 0.2}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsupported",
    [
        {"tool_choice": {"any": {}}},
        {"unknown_option": True},
    ],
)
async def test_unsupported_strands_options_are_nonretryable(unsupported) -> None:
    with pytest.raises(ApplicationError, match="Unsupported Strands options") as caught:
        await collect(PerplexityModel(model_id="sonar/test", client=FakeClient()), **unsupported)
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
async def test_plain_system_prompt_accepts_equivalent_structured_content() -> None:
    client = FakeClient([completed()])

    await collect(
        PerplexityModel(model_id="sonar/test", client=client),
        system_prompt="plain",
        system_prompt_content=[{"text": "plain"}],
    )

    assert client.responses.request["instructions"] == "plain"


@pytest.mark.asyncio
async def test_structured_system_prompt_text_is_authoritative() -> None:
    client = FakeClient([completed()])

    await collect(
        PerplexityModel(model_id="sonar/test", client=client),
        system_prompt="plain",
        system_prompt_content=[{"text": "first"}, {"cachePoint": {"type": "default"}}, {"text": "second"}],
        model_state={},
    )

    assert client.responses.request["instructions"] == "first\nsecond"


@pytest.mark.asyncio
async def test_incomplete_function_call_is_retryable() -> None:
    call = SimpleNamespace(
        type="function_call",
        call_id="a",
        name="one",
        arguments='{"x":',
        status="in_progress",
    )
    client = FakeClient(
        [
            event("response.output_item.added", item=call, output_index=0),
            completed(output=[]),
        ]
    )

    with pytest.raises(ApplicationError, match="Incomplete function call") as caught:
        await collect(PerplexityModel(model_id="sonar/test", client=client))
    assert caught.value.non_retryable is False


@pytest.mark.asyncio
async def test_terminal_output_is_authoritative_for_stop_reason() -> None:
    call = SimpleNamespace(
        type="function_call",
        call_id="a",
        name="one",
        arguments="{}",
        status="completed",
    )
    client = FakeClient(
        [
            event("response.output_item.done", item=call, output_index=0),
            completed(output=[]),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert {"messageStop": {"stopReason": "end_turn"}} in events


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "in_progress", "queued", "requires_action"])
async def test_noncompleted_function_call_is_retryable(status) -> None:
    call = SimpleNamespace(
        type="function_call",
        call_id="a",
        name="one",
        arguments="{}",
        status=status,
    )
    client = FakeClient([event("response.output_item.done", item=call, output_index=0)])

    with pytest.raises(ApplicationError, match="Non-completed function call") as caught:
        await collect(PerplexityModel(model_id="sonar/test", client=client))
    assert caught.value.non_retryable is False


@pytest.mark.asyncio
async def test_function_call_without_status_is_retryable() -> None:
    call = SimpleNamespace(type="function_call", call_id="a", name="one", arguments="{}")
    client = FakeClient([event("response.output_item.done", item=call, output_index=0)])

    with pytest.raises(ApplicationError, match="Non-completed function call: missing") as caught:
        await collect(PerplexityModel(model_id="sonar/test", client=client))
    assert caught.value.non_retryable is False


@pytest.mark.asyncio
async def test_missing_terminal_usage_does_not_emit_metadata() -> None:
    events = await collect(PerplexityModel(model_id="sonar/test", client=FakeClient([completed()])))

    assert not any("metadata" in item for item in events)


@pytest.mark.parametrize("name", ["fast", "low", "medium", "high", "xhigh", "wide-research"])
def test_preset_request_sends_preset_and_no_model(name) -> None:
    model = PerplexityModel(model_id=f"preset:{name}", client=FakeClient())

    request = model._format_request([], None, "be exact")

    assert request["preset"] == name
    assert "model" not in request
    assert request == {
        "preset": name,
        "input": [],
        "stream": True,
        "tools": [],
        "instructions": "be exact",
    }
    assert model.get_config()["model_id"] == f"preset:{name}"


@pytest.mark.asyncio
async def test_preset_request_still_sends_tools_instructions_and_input() -> None:
    client = FakeClient([completed()])
    model = PerplexityModel(model_id="preset:high", client=client)
    tools = [
        {
            "name": "lookup",
            "description": "Lookup",
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
    ]

    await collect(
        model,
        [{"role": "user", "content": [{"text": "hi"}]}],
        tool_specs=tools,
        system_prompt="be exact",
    )

    request = client.responses.request
    assert request["preset"] == "high"
    assert "model" not in request
    assert request["instructions"] == "be exact"
    assert request["input"] == [{"type": "message", "role": "user", "content": "hi"}]
    assert request["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Lookup",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


def test_invalid_preset_is_nonretryable() -> None:
    with pytest.raises(ApplicationError, match="Unknown Perplexity preset") as caught:
        PerplexityModel(model_id="preset:turbo", client=FakeClient())
    assert caught.value.non_retryable is True


def test_update_config_rejects_invalid_preset() -> None:
    model = PerplexityModel(model_id="sonar/test", client=FakeClient())
    with pytest.raises(ApplicationError, match="Unknown Perplexity preset"):
        model.update_config(model_id="preset:nope")


@pytest.mark.asyncio
async def test_agent_api_params_pass_through_unmodified() -> None:
    params = {
        "skills": [
            {"type": "builtin", "name": "web_search"},
            {"type": "custom", "id": "skill-1"},
        ],
        "reasoning": {"effort": "high"},
        "models": ["anthropic/claude-fable-5", "google/gemini-3.8-flash"],
        "response_format": {"type": "json_schema", "json_schema": {"name": "out"}},
        "language_preference": "en",
        "max_steps": 12,
        "background": True,
        "store": True,
        "max_output_tokens": 4096,
    }
    original = copy.deepcopy(params)
    client = FakeClient([completed()])

    await collect(PerplexityModel(model_id="sonar/test", params=params, client=client))

    request = client.responses.request
    for key, value in original.items():
        assert request[key] == value
    assert request["stream"] is True
    assert params == original


@pytest.mark.asyncio
async def test_models_fallback_chain_replaces_model_key() -> None:
    client = FakeClient([completed()])
    model = PerplexityModel(
        model_id="sonar/test",
        params={"models": ["anthropic/claude-fable-5", "google/gemini-3.8-flash"]},
        client=client,
    )

    await collect(model)

    request = client.responses.request
    assert request["models"] == ["anthropic/claude-fable-5", "google/gemini-3.8-flash"]
    assert "model" not in request
    assert "preset" not in request


class ModelDumpEvent(SimpleNamespace):
    def model_dump(self, mode="json"):
        return {key: value for key, value in vars(self).items()}


@pytest.mark.asyncio
async def test_reasoning_thought_emits_reasoning_content_and_raw_envelope() -> None:
    reasoning = ModelDumpEvent(
        type="response.reasoning.started", thought="Considering the question"
    )
    client = FakeClient([reasoning, completed()])

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert {"perplexity": {"type": "response.reasoning.started", "thought": "Considering the question"}} in events
    reasoning_deltas = [
        item
        for item in events
        if "contentBlockDelta" in item
        and "reasoningContent" in item["contentBlockDelta"]["delta"]
    ]
    assert reasoning_deltas == [
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"text": "Considering the question\n"}},
            }
        }
    ]
    assert {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}} in events
    assert {"contentBlockStop": {"contentBlockIndex": 0}} in events


@pytest.mark.asyncio
async def test_reasoning_event_without_thought_emits_only_raw_envelope() -> None:
    reasoning = ModelDumpEvent(type="response.reasoning.stopped", thought=None)
    client = FakeClient([reasoning, completed()])

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert {"perplexity": {"type": "response.reasoning.stopped", "thought": None}} in events
    assert not any(
        "contentBlockDelta" in item
        and "reasoningContent" in item["contentBlockDelta"]["delta"]
        for item in events
    )


@pytest.mark.asyncio
async def test_reasoning_block_does_not_collide_with_text_block_indices() -> None:
    reasoning = ModelDumpEvent(type="response.reasoning.started", thought="thinking")
    client = FakeClient(
        [
            reasoning,
            event("response.output_text.delta", delta="answer", output_index=0, content_index=0),
            event("response.output_text.done", text="answer", output_index=0, content_index=0),
            completed(),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    indices = [
        item["contentBlockDelta"]["contentBlockIndex"]
        for item in events
        if "contentBlockDelta" in item
    ]
    assert len(set(indices)) == len(indices) or indices == sorted(indices)
    reasoning_index = next(
        item["contentBlockDelta"]["contentBlockIndex"]
        for item in events
        if "contentBlockDelta" in item
        and "reasoningContent" in item["contentBlockDelta"]["delta"]
    )
    text_index = next(
        item["contentBlockDelta"]["contentBlockIndex"]
        for item in events
        if "contentBlockDelta" in item and "text" in item["contentBlockDelta"]["delta"]
    )
    assert reasoning_index != text_index


@pytest.mark.asyncio
async def test_share_file_output_item_is_forwarded_verbatim() -> None:
    share_file = ModelDumpEvent(
        type="share_file",
        file_id="file-1",
        path="/v1/responses/resp-1/files/file-1/content",
        status="completed",
    )
    client = FakeClient(
        [
            event("response.output_item.done", item=share_file, output_index=0),
            completed(),
        ]
    )

    events = await collect(PerplexityModel(model_id="sonar/test", client=client))

    assert {
        "perplexity": {
            "type": "share_file",
            "file_id": "file-1",
            "path": "/v1/responses/resp-1/files/file-1/content",
            "status": "completed",
        }
    } in events
