"""Tests for the Perplexity Agent API operation activities.

Runs each activity body under ``temporalio.testing.ActivityEnvironment`` with
the workflow-stream client patched out and a fake ``AsyncPerplexity`` client
installed through :func:`perplexity_operations.configure`, mirroring the fake
patterns in test_think_activity.py / test_perplexity_model.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from perplexity import (
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from unittest.mock import patch

import agent_api_tools
import config
import perplexity_operations
from perplexity_operations import (
    AGENT_RUNS_TOPIC,
    create_fast_agent_response,
    create_low_agent_response,
    create_medium_agent_response,
    create_high_agent_response,
    create_xhigh_agent_response,
    create_wide_research_agent_response,
    download_agent_response_file,
    list_agent_models,
    list_agent_response_files,
    retrieve_agent_response,
    cancel_agent_response,
)


def event(type_: str, **values: object) -> SimpleNamespace:
    return SimpleNamespace(type=type_, **values)


def response_obj(
    *,
    id: str = "resp_1",
    status: str = "completed",
    model: str = "openai/gpt-5.6-luna",
    output: list[Any] | None = None,
    usage: Any = None,
    error: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id, status=status, model=model, output=output or [], usage=usage, error=error
    )


def message_item(text: str, *, id: str = "msg_1") -> SimpleNamespace:
    return SimpleNamespace(
        type="message",
        id=id,
        status="completed",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text=text, annotations=[])],
    )


def completed(response: SimpleNamespace | None = None, *, sequence_number: int = 9) -> SimpleNamespace:
    return event(
        "response.completed",
        response=response or response_obj(),
        sequence_number=sequence_number,
    )


class FakeFiles:
    def __init__(self, files: Any = None, content: Any = None, error: Exception | None = None) -> None:
        self.files = files
        self.content_response = content
        self.error = error
        self.list_calls: list[str] = []
        self.content_calls: list[dict[str, str]] = []

    async def list(self, response_id: str) -> Any:
        self.list_calls.append(response_id)
        if self.error:
            raise self.error
        return self.files

    async def content(self, file_id: str, *, response_id: str) -> Any:
        self.content_calls.append({"file_id": file_id, "response_id": response_id})
        if self.error:
            raise self.error
        return self.content_response


class FakeResponses:
    def __init__(
        self,
        events: list[Any] | None = None,
        error: Exception | None = None,
        retrieved: Any = None,
        cancelled: Any = None,
        files: FakeFiles | None = None,
    ) -> None:
        self.events = events or []
        self.error = error
        self.retrieved = retrieved
        self.cancelled = cancelled
        self.files = files or FakeFiles()
        self.request: dict[str, Any] | None = None
        self.retrieve_calls: list[str] = []
        self.cancel_calls: list[str] = []

    async def create(self, **request: Any) -> Any:
        self.request = request
        if self.error:
            raise self.error

        async def stream() -> Any:
            for item in self.events:
                yield item

        return stream()

    async def retrieve(self, response_id: str) -> Any:
        self.retrieve_calls.append(response_id)
        if self.error:
            raise self.error
        return self.retrieved

    async def cancel(self, response_id: str) -> Any:
        self.cancel_calls.append(response_id)
        if self.error:
            raise self.error
        return self.cancelled or SimpleNamespace(response_id=response_id, status="cancelling")


class FakeClient:
    def __init__(self, *, models: Any = None, get_error: Exception | None = None, **kwargs: Any) -> None:
        self.responses = FakeResponses(**kwargs)
        self.models_payload = models
        self.get_error = get_error
        self.get_calls: list[dict[str, Any]] = []

    async def get(self, path: str, *, cast_to: Any) -> Any:
        """Stands in for AsyncPerplexity's public base-client GET surface."""
        self.get_calls.append({"path": path, "cast_to": cast_to})
        if self.get_error:
            raise self.get_error
        return self.models_payload


class FakeBinaryResponse:
    """Stands in for the SDK's AsyncBinaryAPIResponse (headers + iter_bytes)."""

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None) -> None:
        self.chunks = chunks
        self.headers = httpx.Headers(headers or {})

    async def iter_bytes(self, chunk_size: int | None = None) -> Any:
        for chunk in self.chunks:
            yield chunk


class FakeTopic:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, value: Any, *, force_flush: bool = False) -> None:
        self.published.append(value)


class FakeStreamClient:
    """Stands in for WorkflowStreamClient.from_within_activity()."""

    def __init__(self) -> None:
        self.topics: dict[str, FakeTopic] = {}
        self.entered = False
        self.exited = False

    def topic(self, name: str, **_: Any) -> FakeTopic:
        return self.topics.setdefault(name, FakeTopic())

    async def __aenter__(self) -> "FakeStreamClient":
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True


@pytest.fixture(autouse=True)
def reset_client_factory() -> Any:
    perplexity_operations.configure(None)
    yield
    perplexity_operations.configure(None)


async def run_activity(activity_fn: Any, client: FakeClient, /, **kwargs: Any) -> Any:
    """Run an operation activity under ActivityEnvironment with fakes installed."""
    perplexity_operations.configure(lambda: client)
    env = ActivityEnvironment()
    stream = FakeStreamClient()
    heartbeats: list[Any] = []
    env.on_heartbeat = lambda *details: heartbeats.append(details)
    with patch.object(
        perplexity_operations.WorkflowStreamClient,
        "from_within_activity",
        return_value=stream,
    ):
        result = await env.run(activity_fn, **kwargs)
    return result, stream, heartbeats


PRESET_ACTIVITIES = [
    (create_fast_agent_response, "fast"),
    (create_low_agent_response, "low"),
    (create_medium_agent_response, "medium"),
    (create_high_agent_response, "high"),
    (create_xhigh_agent_response, "xhigh"),
    (create_wide_research_agent_response, "wide-research"),
]


# --- preset create request shape ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("activity_fn", "preset"), PRESET_ACTIVITIES)
async def test_each_preset_sends_its_fixed_preset_and_forced_flags(activity_fn, preset) -> None:
    """Each of the six activities sends exactly its documented preset name and
    forces background=True / stream=True internally."""
    client = FakeClient(events=[completed()])

    await run_activity(activity_fn, client, input="what is up")

    request = client.responses.request
    assert request is not None
    assert request["preset"] == preset
    assert request["background"] is True
    assert request["stream"] is True
    assert request["input"] == "what is up"
    # Omitted optional fields are not sent at all -- preset internals stay live.
    for absent in ("model", "models", "instructions", "extra_body"):
        assert absent not in request
    # skills defaults to the builtin office suite when the caller passes none.
    assert request["skills"] == [dict(skill) for skill in config.BUILTIN_SKILLS]
    # tools defaults to the shared native array, connectors included.
    assert request["tools"] == agent_api_tools.native_tools()


@pytest.mark.asyncio
async def test_full_request_field_passthrough_is_exact() -> None:
    """Every supported ResponsesRequest field beside the preset reaches the SDK
    verbatim; temperature/top_p travel through extra_body (not in the installed
    SDK signature yet)."""
    client = FakeClient(events=[completed()])
    tools = [
        {"type": "web_search", "max_results": 10, "search_context_size": "high"},
        {"type": "finance_search"},
        {"type": "people_search"},
        {"type": "fetch_url", "max_urls": 3},
        {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
        {"type": "sandbox"},
        {
            "type": "mcp",
            "server_label": "corp_tools",
            "server_url": "https://mcp.example.com/http",
        },
    ]
    skills = [
        {"type": "builtin", "name": "office/pdf"},
        {
            "type": "inline",
            "name": "release-notes",
            "description": "Writes release notes",
            "instructions": "Write terse notes.",
        },
    ]

    await run_activity(
        create_medium_agent_response,
        client,
        input="hi",
        instructions="be exact",
        language_preference="de",
        max_output_tokens=4096,
        max_steps=12,
        model="anthropic/claude-sonnet-4-6",
        models=["anthropic/claude-sonnet-4-6", "openai/gpt-5.6-luna"],
        previous_response_id="resp_0",
        reasoning_effort="high",
        response_format_json=json.dumps(
            {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": {"type": "object"}},
            }
        ),
        store=False,
        temperature=0.4,
        top_p=0.9,
        tools_json=json.dumps(tools),
        skills_json=json.dumps(skills),
    )

    request = client.responses.request
    assert request["preset"] == "medium"
    assert request["background"] is True
    assert request["stream"] is True
    assert request["input"] == "hi"
    assert request["instructions"] == "be exact"
    assert request["language_preference"] == "de"
    assert request["max_output_tokens"] == 4096
    assert request["max_steps"] == 12
    assert request["model"] == "anthropic/claude-sonnet-4-6"
    assert request["models"] == ["anthropic/claude-sonnet-4-6", "openai/gpt-5.6-luna"]
    assert request["previous_response_id"] == "resp_0"
    assert request["reasoning"] == {"effort": "high"}
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": {"type": "object"}},
    }
    assert request["store"] is False
    assert request["tools"] == tools
    assert request["skills"] == skills
    assert request["extra_body"] == {"temperature": 0.4, "top_p": 0.9}


# --- validation ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("models", [[], ["a"] * 6])
async def test_models_fallback_length_is_1_to_5(models) -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="q", models=models)
    assert caught.value.non_retryable is True
    assert client.responses.request is None


@pytest.mark.asyncio
@pytest.mark.parametrize("max_steps", [0, 101])
async def test_max_steps_must_be_1_to_100(max_steps) -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_low_agent_response, client, input="q", max_steps=max_steps)
    assert caught.value.non_retryable is True
    assert client.responses.request is None


@pytest.mark.asyncio
async def test_max_output_tokens_must_be_positive() -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="q", max_output_tokens=0)
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"model": "anthropic/claude-sonnet-4-6"},
        {"models": ["openai/gpt-5.6-luna", "anthropic/claude-sonnet-4-6"]},
    ],
)
async def test_anthropic_models_require_max_output_tokens(override) -> None:
    """The API returns 400 when max_output_tokens is omitted for anthropic/*
    models; validated locally before network I/O."""
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError, match="max_output_tokens") as caught:
        await run_activity(create_high_agent_response, client, input="q", **override)
    assert caught.value.non_retryable is True
    assert client.responses.request is None

    # Providing max_output_tokens satisfies the constraint.
    client = FakeClient(events=[completed()])
    await run_activity(
        create_high_agent_response, client, input="q", max_output_tokens=16384, **override
    )
    assert client.responses.request is not None


@pytest.mark.asyncio
async def test_reasoning_effort_enum_is_validated() -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(
            create_fast_agent_response, client, input="q", reasoning_effort="extreme"
        )
    assert caught.value.non_retryable is True

    client = FakeClient(events=[completed()])
    await run_activity(
        create_fast_agent_response, client, input="q", reasoning_effort="max"
    )
    assert client.responses.request["reasoning"] == {"effort": "max"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [-0.1, 2.1],
)
async def test_temperature_range_is_validated(value) -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="q", temperature=value)
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
async def test_top_p_range_is_validated() -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="q", top_p=1.5)
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_format",
    [
        {"type": "text"},
        {"type": "json_schema", "json_schema": {"schema": {}}},
        {"type": "json_schema", "json_schema": {"name": "x" * 65, "schema": {}}},
    ],
)
async def test_response_format_constraints(response_format) -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(
            create_fast_agent_response,
            client,
            input="q",
            response_format_json=json.dumps(response_format),
        )
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skills",
    [
        [{"type": "builtin", "name": "office"}] * 17,  # maxItems 16
        [{"type": "builtin", "name": "excel"}],  # unknown builtin
        [{"type": "inline", "name": "Bad_Name", "description": "d", "instructions": "i"}],
        [{"type": "inline", "name": "ok", "description": "d", "instructions": "x" * 65_537}],
        [{"type": "inline", "name": "ok", "description": "d" * 1025, "instructions": "i"}],
        # 1,024 UTF-8 BYTES, not characters: 513 two-byte chars = 1,026 bytes.
        [{"type": "inline", "name": "ok", "description": "é" * 513, "instructions": "i"}],
        [{"type": "magic", "name": "office"}],
        # additionalProperties: false on both skill schemas.
        [{"type": "builtin", "name": "office", "extra": True}],
        [
            {
                "type": "inline",
                "name": "ok",
                "description": "d",
                "instructions": "i",
                "extra": True,
            }
        ],
    ],
)
async def test_skill_constraints_are_enforced(skills) -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_xhigh_agent_response, client, input="q", skills_json=json.dumps(skills))
    assert caught.value.non_retryable is True
    assert client.responses.request is None


@pytest.mark.asyncio
async def test_inline_skill_total_instruction_budget_is_enforced() -> None:
    """Per-skill instructions fit, but the 262,144-byte request total does not."""
    skills = [
        {"type": "inline", "name": f"skill-{i}", "description": "d", "instructions": "x" * 60_000}
        for i in range(5)
    ]
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_xhigh_agent_response, client, input="q", skills_json=json.dumps(skills))
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tools",
    [
        [{"type": "teleport"}],
        [{"type": "web_search", "max_results": 0}],
        [{"type": "web_search", "max_results": 51}],
        [{"type": "web_search", "search_context_size": "extreme"}],
        [{"type": "fetch_url", "max_urls": 11}],
        [{"type": "function"}],  # name required
        [{"type": "mcp", "server_label": "ok", "server_url": "http://insecure.example"}],
        [{"type": "mcp", "server_label": "bad label!", "server_url": "https://ok.example"}],
        # server_label is capped at 64 characters (^[a-zA-Z0-9_-]{1,64}$).
        [{"type": "mcp", "server_label": "x" * 65, "server_url": "https://ok.example"}],
        # server_label must be unique per request.
        [
            {"type": "mcp", "server_label": "dup", "server_url": "https://a.example"},
            {"type": "mcp", "server_label": "dup", "server_url": "https://b.example"},
        ],
        # connector: id required, label pattern + uniqueness enforced.
        [{"type": "connector", "server_label": "github"}],
        [{"type": "connector", "id": "connector_x", "server_label": "bad label!"}],
        [
            {"type": "connector", "id": "connector_a", "server_label": "dup"},
            {"type": "connector", "id": "connector_b", "server_label": "dup"},
        ],
    ],
)
async def test_native_tool_constraints_are_enforced(tools) -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_medium_agent_response, client, input="q", tools_json=json.dumps(tools))
    assert caught.value.non_retryable is True
    assert client.responses.request is None


@pytest.mark.asyncio
async def test_connector_tool_type_is_accepted() -> None:
    """A valid {"type": "connector"} tool passes validation and reaches the SDK."""
    client = FakeClient(events=[completed()])
    tools = [
        {"type": "web_search"},
        {
            "type": "connector",
            "id": "connector_googledrive",
            "server_label": "google_drive",
            "server_description": "Drive files",
        },
    ]

    await run_activity(
        create_fast_agent_response, client, input="q", tools_json=json.dumps(tools)
    )

    assert client.responses.request is not None
    assert client.responses.request["tools"] == tools


@pytest.mark.asyncio
async def test_default_tools_include_dashboard_connectors() -> None:
    """When the caller sends no tools, the request carries the shared native
    array, dashboard connectors included."""
    client = FakeClient(events=[completed()])

    await run_activity(create_fast_agent_response, client, input="q")

    request = client.responses.request
    assert request is not None
    assert request["tools"] == agent_api_tools.native_tools()
    connector_ids = [
        tool["id"] for tool in request["tools"] if tool.get("type") == "connector"
    ]
    assert connector_ids == ["connector_googledrive", "connector_github"]


@pytest.mark.asyncio
async def test_input_is_required_and_nonempty() -> None:
    client = FakeClient(events=[completed()])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="")
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
async def test_create_maps_images_json_to_multimodal_input() -> None:
    client = FakeClient(events=[completed()])
    images_json = json.dumps([
        "https://example.com/chart.png",
        {"image_url": "data:image/png;base64,abc"},
        {"source": {"url": "https://example.com/photo.jpg"}},
        {"source": {"bytes": "fake_bytes"}},
    ])
    await run_activity(
        create_fast_agent_response,
        client,
        input="Analyze these images",
        images_json=images_json,
    )
    request = client.responses.request
    assert request is not None
    assert request["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Analyze these images"},
                {"type": "input_image", "image_url": "https://example.com/chart.png"},
                {"type": "input_image", "image_url": "data:image/png;base64,abc"},
                {"type": "input_image", "image_url": "https://example.com/photo.jpg"},
                {"type": "input_image", "image_url": "data:application/octet-stream;base64,fake_bytes"},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_create_rejects_invalid_images_json() -> None:
    client = FakeClient()
    with pytest.raises(ApplicationError) as caught:
        await run_activity(
            create_fast_agent_response,
            client,
            input="Analyze",
            images_json="not-json",
        )
    assert caught.value.non_retryable is True


# --- streaming, envelopes, heartbeats --------------------------------------


@pytest.mark.asyncio
async def test_events_publish_ordered_envelopes_on_agent_runs_topic() -> None:
    """Every SDK stream event is published verbatim inside an envelope carrying
    activity name/id, preset, attempt, sequence number, and the response id
    once response.created has arrived."""
    events = [
        event("response.created", response=response_obj(status="queued"), sequence_number=0),
        event(
            "response.reasoning.search_queries",
            queries=["saturn moons"],
            sequence_number=1,
        ),
        event(
            "response.output_text.delta",
            delta="Titan",
            item_id="msg_1",
            output_index=0,
            content_index=0,
            sequence_number=2,
        ),
        completed(response_obj(output=[message_item("Titan")]), sequence_number=3),
    ]
    client = FakeClient(events=events)

    _, stream, heartbeats = await run_activity(
        create_fast_agent_response, client, input="saturn?"
    )

    assert stream.entered and stream.exited
    published = stream.topics[AGENT_RUNS_TOPIC].published
    assert len(published) == 4
    assert [env_["sequence_number"] for env_ in published] == [0, 1, 2, 3]
    assert all(env_["activity"] == "create_fast_agent_response" for env_ in published)
    assert all(env_["preset"] == "fast" for env_ in published)
    assert all(isinstance(env_["activity_id"], str) for env_ in published)
    assert all(env_["attempt"] == 1 for env_ in published)
    # response id is unknown until response.created is processed.
    assert published[0]["response_id"] == "resp_1"
    assert published[1]["response_id"] == "resp_1"
    # The event itself is verbatim, serializable data.
    assert published[1]["event"]["type"] == "response.reasoning.search_queries"
    assert published[1]["event"]["queries"] == ["saturn moons"]
    assert published[2]["event"]["delta"] == "Titan"
    json.dumps(published)  # nothing non-serializable leaked in

    # Heartbeats carried bounded progress (response id + last sequence number).
    assert heartbeats
    assert heartbeats[-1][0] == {"response_id": "resp_1", "sequence_number": 3}


@pytest.mark.asyncio
async def test_terminal_projection_is_serializable() -> None:
    """response.completed.response projects to the toolResult shape,
    preserving unknown output items as data."""
    unknown_item = SimpleNamespace(type="hologram_results", id="h1", payload={"a": 1})
    search_item = SimpleNamespace(
        type="search_results",
        queries=["q"],
        results=[SimpleNamespace(id=1, url="https://x", title="t", snippet="s")],
    )
    usage = SimpleNamespace(input_tokens=10, output_tokens=4, total_tokens=14)
    terminal = response_obj(
        id="resp_9",
        model="openai/gpt-5.6-luna",
        output=[message_item("final answer"), search_item, unknown_item],
        usage=usage,
    )
    client = FakeClient(events=[completed(terminal)])

    result, _, _ = await run_activity(create_medium_agent_response, client, input="q")

    assert result["response_id"] == "resp_9"
    assert result["model"] == "openai/gpt-5.6-luna"
    assert result["status"] == "completed"
    assert result["output_text"] == "final answer"
    assert result["error"] is None
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    types = [item["type"] for item in result["output"]]
    assert types == ["message", "search_results", "hologram_results"]
    assert result["output"][2]["payload"] == {"a": 1}
    json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "non_retryable"),
    [("invalid_request_error", True), ("server_error", False)],
)
async def test_response_failed_classification(code, non_retryable) -> None:
    failure = event(
        "response.failed",
        error=SimpleNamespace(message="failed", code=code, type=code),
        sequence_number=1,
    )
    client = FakeClient(events=[failure])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="q")
    assert caught.value.non_retryable is non_retryable


@pytest.mark.asyncio
async def test_stream_without_authoritative_completion_is_retryable() -> None:
    client = FakeClient(
        events=[event("response.created", response=response_obj(status="queued"), sequence_number=0)]
    )
    with pytest.raises(ApplicationError, match="authoritative") as caught:
        await run_activity(create_fast_agent_response, client, input="q")
    assert caught.value.non_retryable is False


@pytest.mark.asyncio
async def test_non_completed_terminal_status_is_retryable() -> None:
    client = FakeClient(events=[completed(response_obj(status="incomplete"))])
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="q")
    assert caught.value.non_retryable is False


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
async def test_create_http_failure_classification(error_type, status_code, non_retryable) -> None:
    request = httpx.Request("POST", "https://api.perplexity.ai/v1/agent")
    response = httpx.Response(status_code, request=request)
    error = error_type("failure", response=response, body=None)
    client = FakeClient(error=error)
    with pytest.raises(ApplicationError) as caught:
        await run_activity(create_fast_agent_response, client, input="q")
    assert caught.value.non_retryable is non_retryable


# --- retrieve / files -------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_calls_sdk_and_returns_projection() -> None:
    retrieved = response_obj(id="resp_5", output=[message_item("stored answer")])
    client = FakeClient(retrieved=retrieved)

    result, _, _ = await run_activity(retrieve_agent_response, client, response_id="resp_5")

    assert client.responses.retrieve_calls == ["resp_5"]
    assert result["response_id"] == "resp_5"
    assert result["output_text"] == "stored answer"
    json.dumps(result)


@pytest.mark.asyncio
async def test_retrieve_not_found_is_nonretryable() -> None:
    """Unknown, cross-account, or store:false responses 404; preserved as
    non-retryable failures."""
    request = httpx.Request("GET", "https://api.perplexity.ai/v1/responses/x")
    response = httpx.Response(404, request=request)
    from perplexity import NotFoundError

    client = FakeClient(error=NotFoundError("nope", response=response, body=None))
    with pytest.raises(ApplicationError) as caught:
        await run_activity(retrieve_agent_response, client, response_id="resp_x")
    assert caught.value.non_retryable is True


@pytest.mark.asyncio
async def test_cancel_calls_sdk_and_returns_data() -> None:
    cancelled = SimpleNamespace(response_id="resp_cancel", status="cancelling")
    client = FakeClient(cancelled=cancelled)

    result, _, _ = await run_activity(cancel_agent_response, client, response_id="resp_cancel")

    assert client.responses.cancel_calls == ["resp_cancel"]
    assert result == {"response_id": "resp_cancel", "status": "cancelling"}
    json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["../etc", "resp/1", "", "../../bad"])
async def test_cancel_rejects_unsafe_identifiers(bad_id: str) -> None:
    client = FakeClient()
    with pytest.raises(ApplicationError) as caught:
        await run_activity(cancel_agent_response, client, response_id=bad_id)
    assert caught.value.non_retryable is True
    assert client.responses.cancel_calls == []


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
async def test_cancel_error_classification(error_type, status_code, non_retryable) -> None:
    request = httpx.Request("POST", "https://api.perplexity.ai/v1/agent/resp_x/cancel")
    response = httpx.Response(status_code, request=request)
    client = FakeClient(error=error_type("failure", response=response, body=None))
    with pytest.raises(ApplicationError) as caught:
        await run_activity(cancel_agent_response, client, response_id="resp_x")
    assert caught.value.non_retryable is non_retryable


@pytest.mark.asyncio
async def test_list_files_returns_supplied_object_data_shape() -> None:
    files = SimpleNamespace(
        object="list",
        data=[
            SimpleNamespace(
                id="file_1", object="file", filename="report.pdf", bytes=1234, created_at=1
            )
        ],
    )
    client = FakeClient(files=FakeFiles(files=files))

    result, _, _ = await run_activity(list_agent_response_files, client, response_id="resp_1")

    assert client.responses.files.list_calls == ["resp_1"]
    assert result["object"] == "list"
    assert result["data"] == [
        {"id": "file_1", "object": "file", "filename": "report.pdf", "bytes": 1234, "created_at": 1}
    ]
    json.dumps(result)


@pytest.mark.asyncio
async def test_download_streams_bytes_hashes_and_returns_proxy_reference(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "AGENT_FILE_STORE_DIR", tmp_path)
    payload = b"%PDF-1.7 fake bytes"
    binary = FakeBinaryResponse(
        [payload[:5], payload[5:]],
        headers={
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="report.pdf"',
        },
    )
    client = FakeClient(files=FakeFiles(content=binary))

    result, _, _ = await run_activity(
        download_agent_response_file, client, response_id="resp_1", file_id="file_1"
    )

    assert client.responses.files.content_calls == [
        {"file_id": "file_1", "response_id": "resp_1"}
    ]
    stored = tmp_path / "resp_1" / "file_1"
    assert stored.read_bytes() == payload
    assert result == {
        "response_id": "resp_1",
        "file_id": "file_1",
        "filename": "report.pdf",
        "content_type": "application/pdf",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "path": "/v1/agent/resp_1/files/file_1/content",
        "url": "/api/orchestrator/file?path=%2Fv1%2Fagent%2Fresp_1%2Ffiles%2Ffile_1%2Fcontent",
    }
    # No raw bytes, base64 payloads, or internal filesystem paths in the result.
    assert str(tmp_path) not in json.dumps(result)
    json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_id", "file_id"),
    [
        ("../etc", "file_1"),
        ("resp_1", "../../secret"),
        ("resp/1", "file_1"),
        ("", "file_1"),
        ("resp_1", ""),
    ],
)
async def test_download_rejects_unsafe_identifiers(response_id, file_id) -> None:
    client = FakeClient(files=FakeFiles(content=FakeBinaryResponse([b"x"])))
    with pytest.raises(ApplicationError) as caught:
        await run_activity(
            download_agent_response_file, client, response_id=response_id, file_id=file_id
        )
    assert caught.value.non_retryable is True
    assert client.responses.files.content_calls == []


# --- model catalog -----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agent_models_gets_v1_models_and_returns_openapi_shape() -> None:
    """list_agent_models GETs exactly /v1/models through the client's base
    request surface and returns ids verbatim in the OpenAPI list shape,
    dropping only malformed records."""
    client = FakeClient(
        models={
            "object": "list",
            "data": [
                {"id": "openai/gpt-5.6-sol", "object": "model", "created": 1, "owned_by": "openai"},
                {"id": "anthropic/claude-opus-5", "object": "model", "created": 2, "owned_by": "anthropic"},
                {"object": "model", "created": 3, "owned_by": "nobody"},  # no id: dropped
                "not-a-record",  # malformed: dropped
            ],
        }
    )

    result, _, _ = await run_activity(list_agent_models, client)

    assert client.get_calls == [{"path": "/v1/models", "cast_to": object}]
    assert result == {
        "object": "list",
        "data": [
            {"id": "openai/gpt-5.6-sol", "object": "model", "created": 1, "owned_by": "openai"},
            {"id": "anthropic/claude-opus-5", "object": "model", "created": 2, "owned_by": "anthropic"},
        ],
    }
    json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "status_code", "non_retryable"),
    [
        (AuthenticationError, 401, True),
        (RateLimitError, 429, False),
        (InternalServerError, 500, False),
    ],
)
async def test_list_agent_models_error_classification(error_type, status_code, non_retryable) -> None:
    request = httpx.Request("GET", "https://api.perplexity.ai/v1/models")
    response = httpx.Response(status_code, request=request)
    client = FakeClient(get_error=error_type("failure", response=response, body=None))
    with pytest.raises(ApplicationError) as caught:
        await run_activity(list_agent_models, client)
    assert caught.value.non_retryable is non_retryable


# --- worker client wiring ----------------------------------------------------


def test_default_client_keeps_retries_at_zero(monkeypatch) -> None:
    created: dict[str, Any] = {}

    def fake_client(**kwargs: Any) -> FakeClient:
        created.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(perplexity_operations, "AsyncPerplexity", fake_client)
    perplexity_operations.configure(None)
    perplexity_operations._get_client()
    assert created["max_retries"] == 0
