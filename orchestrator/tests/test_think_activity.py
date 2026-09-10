"""Tests for the think activity: a thin streaming wrapper over strands_tools.think.

The activity owns no prompt text and no ThoughtProcessor of its own — prompt
construction is delegated to the installed ``strands_tools.think`` module, and
the persona/methodology prompts arrive as activity arguments (loaded from
``agent.json``'s ``think`` key by the workflow call site).

Runs the activity body under ``temporalio.testing.ActivityEnvironment`` with
the workflow-stream client and the parent-workflow query patched out, so the
cycle loop, streaming publishes, heartbeats, and return shape are exercised
without a Temporal server.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator
import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.models.model import Model
from temporalio.testing import ActivityEnvironment

import think_activity
from think_activity import think


def text_events(reply: str) -> list[dict[str, Any]]:
    """The frame sequence PerplexityModel.stream yields for a text-only turn."""
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": reply}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        },
    ]


class ScriptedModel(Model):
    """Yields one scripted event list per stream() call."""

    def __init__(self, scripts: list[list[dict[str, Any]]]) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    def update_config(self, **model_config: Any) -> None:  # pragma: no cover
        pass

    def get_config(self) -> Any:  # pragma: no cover
        return {}

    async def structured_output(
        self, output_model: Any, prompt: Any, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:  # pragma: no cover
        raise NotImplementedError
        yield

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append({"messages": copy.deepcopy(messages), "system_prompt": system_prompt,
                           "tool_specs": copy.deepcopy(tool_specs), "state": dict(kwargs.get("invocation_state") or {})})
        for event in self.scripts.pop(0):
            yield event


def prompt_text(call: dict[str, Any]) -> str:
    """The joined text of the user message a stream() call received."""
    return "\n".join(
        block["text"]
        for message in call["messages"]
        for block in message["content"]
        if "text" in block
    )


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
def reset_factories() -> None:
    think_activity.set_model_factories({})
    yield
    think_activity.set_model_factories({})


async def run_think(
    factories: dict[str, Any],
    stream: FakeStreamClient,
    *,
    model_id: str = "fake/text",
    heartbeats: list[Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Run the activity under ActivityEnvironment with infra patched out.

    Awaited INSIDE the patch context: env.run returns a coroutine, and the
    activity body only touches activity.client() / from_within_activity when
    it actually executes.
    """
    think_activity.set_model_factories(factories)
    env = ActivityEnvironment()
    if heartbeats is not None:
        env.on_heartbeat = lambda *args: heartbeats.append(args)

    handle = MagicMock()
    handle.query = AsyncMock(return_value=model_id)
    client = MagicMock()
    client.get_workflow_handle.return_value = handle

    with (
        patch.object(think_activity.activity, "client", return_value=client),
        patch.object(
            think_activity.WorkflowStreamClient,
            "from_within_activity",
            return_value=stream,
        ),
    ):
        return await env.run(think, **kwargs)


def test_prompt_construction_is_delegated_to_strands_tools() -> None:
    """No forked prompt text or processor lives in the module."""
    import strands_tools.think as upstream

    source = think_activity.__file__
    with open(source) as handle:
        text = handle.read()
    assert "strands_tools.think" in text
    assert "class ThoughtProcessor" not in text
    assert "DEFAULT_THINK_SYSTEM_PROMPT" not in text
    # The module reuses the installed processor rather than redefining it.
    assert think_activity.ThoughtProcessor is upstream.ThoughtProcessor


@pytest.mark.asyncio
async def test_cycles_chain_and_return_shape_matches_upstream() -> None:
    """N cycles run, each conclusion feeds the next, output is upstream's."""
    model = ScriptedModel([text_events("first insight"), text_events("second insight")])
    stream = FakeStreamClient()

    result = await run_think(
        {"fake/text": lambda: model},
        stream,
        thought="What are Saturn's largest moons?",
        cycle_count=2,
        system_prompt="You are an astronomer.",
    )

    assert result["status"] == "success"
    text = result["content"][0]["text"]
    assert "Cycle 1/2:\nfirst insight" in text
    assert "Cycle 2/2:\nsecond insight" in text

    # Cycle 2's prompt chains cycle 1's conclusion, verbatim per upstream.
    assert len(model.calls) == 2
    second_prompt = model.calls[1]["messages"]
    assert "Previous cycle concluded: first insight" in str(second_prompt)
    assert "Continue developing these ideas further." in str(second_prompt)
    # The per-cycle system prompt is the caller's; the module owns no default.
    assert model.calls[0]["system_prompt"] == "You are an astronomer."


@pytest.mark.asyncio
async def test_cycle_prompt_is_byte_identical_to_upstream_builder() -> None:
    """The cycle prompt comes from strands_tools' create_thinking_prompt."""
    import strands_tools.think as upstream
    from strands_tools.utils import console_util

    model = ScriptedModel([text_events("ok")])
    stream = FakeStreamClient()

    await run_think(
        {"fake/text": lambda: model},
        stream,
        thought="a thought",
        cycle_count=1,
        system_prompt="persona",
        thinking_system_prompt="METHOD X",
    )

    expected = upstream.ThoughtProcessor({}, console_util.create()).create_thinking_prompt(
        "a thought", 1, 1, "METHOD X"
    )
    assert prompt_text(model.calls[0]) == expected


@pytest.mark.asyncio
async def test_omitted_thinking_prompt_uses_upstream_default_instructions() -> None:
    """No thinking_system_prompt -> upstream's own default instructions."""
    import strands_tools.think as upstream
    from strands_tools.utils import console_util

    model = ScriptedModel([text_events("ok")])
    stream = FakeStreamClient()

    await run_think(
        {"fake/text": lambda: model},
        stream,
        thought="t",
        cycle_count=1,
        system_prompt="",
    )

    expected = upstream.ThoughtProcessor({}, console_util.create()).create_thinking_prompt(
        "t", 1, 1, None
    )
    assert prompt_text(model.calls[0]) == expected
    # An empty persona is passed through, not replaced by a forked default.
    assert model.calls[0]["system_prompt"] in (None, "")


@pytest.mark.asyncio
async def test_every_model_chunk_streams_to_the_thinking_topic() -> None:
    """Each raw StreamEvent is published live, unchanged, in order."""
    events = text_events("streamed reply")
    model = ScriptedModel([events])
    stream = FakeStreamClient()
    heartbeats: list[Any] = []

    await run_think(
        {"fake/text": lambda: model},
        stream,
        heartbeats=heartbeats,
        thought="stream me",
        cycle_count=1,
        system_prompt="prompt",
    )

    from workflow import THINKING_TOPIC

    assert stream.entered and stream.exited
    published = stream.topics[THINKING_TOPIC].published
    # Every raw model chunk streams live, in order, followed by exactly one
    # durable {"think_notes": ...} frame the think-first hook folds in.
    assert published[:len(events)] == events
    assert published[len(events):] == [
        {"think_notes": "Cycle 1/1:\nstreamed reply"}
    ]
    # One beat per streamed chunk keeps long cycles cancel/resume visible.
    assert len(heartbeats) == len(events)


@pytest.mark.asyncio
async def test_unregistered_model_returns_an_error_result() -> None:
    """A model id outside the worker catalog is a reported error, not a raise."""
    stream = FakeStreamClient()

    result = await run_think(
        {"fake/text": lambda: ScriptedModel([])},
        stream,
        model_id="not/registered",
        thought="anything",
        cycle_count=1,
        system_prompt="prompt",
    )

    assert result["status"] == "error"
    assert "not/registered" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_factory_failure_returns_an_error_result() -> None:
    """A factory that raises never escapes the activity."""

    def exploding_factory() -> Any:
        raise RuntimeError("factory exploded")

    stream = FakeStreamClient()

    result = await run_think(
        {"fake/text": exploding_factory},
        stream,
        thought="t",
        cycle_count=1,
        system_prompt="p",
    )

    assert result["status"] == "error"
    assert "factory exploded" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_empty_thought_still_returns_a_success_envelope() -> None:
    """Malformed/empty input degrades to an ordinary cycle, not a crash."""
    model = ScriptedModel([text_events("nothing to add")])
    stream = FakeStreamClient()

    result = await run_think(
        {"fake/text": lambda: model},
        stream,
        thought="",
        cycle_count=1,
        system_prompt="p",
    )

    assert result["status"] == "success"
    assert "Cycle 1/1:" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_model_failure_returns_error_result_with_content() -> None:
    """A mid-cycle exception becomes {"status": "error"} carrying the message."""

    class ExplodingModel(ScriptedModel):
        async def stream(
            self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
        ) -> AsyncGenerator[dict[str, Any], None]:
            raise RuntimeError("provider exploded")
            yield  # pragma: no cover

    stream = FakeStreamClient()

    result = await run_think(
        {"fake/text": lambda: ExplodingModel([])},
        stream,
        thought="t",
        cycle_count=1,
        system_prompt="p",
    )

    assert result["status"] == "error"
    assert result["content"][0]["text"]
    assert "provider exploded" in result["content"][0]["text"]


def test_module_stays_a_thin_wrapper() -> None:
    """Native cycle adaptation in this module, not a shared-tools framework."""
    import inspect
    import workflow
    assert not hasattr(workflow.ChatWorkflow, "_run_think")
    source = inspect.getsource(think_activity.think_async)
    assert "ThoughtProcessor({}, _CONSOLE).create_thinking_prompt" in source
    assert ".stream_async(" in source
    assert "tools=[]" not in source
    assert "agent.tool_registry.registry" in source


def call_events(name, call_id, arguments):
    import json
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {"toolUse": {"toolUseId": call_id, "name": name}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}},
        {"contentBlockStop": {}}, {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}},
    ]


@pytest.mark.asyncio
async def test_async_inherits_parent_tools_and_executes_them_between_cycles():
    from strands import Agent, tool
    from config import THINK_MODEL_ID
    calls = []
    @tool
    def lookup(value: str) -> str:
        """Retrieve test evidence."""
        calls.append(value)
        return "evidence-742"
    @tool
    def think() -> str:
        """Must never recurse."""
        raise AssertionError("recursive Think")
    model = ScriptedModel([call_events("lookup", "lookup-1", {"value": "question"}), text_events("first"), text_events("second")])
    factory = MagicMock(return_value=model)
    think_activity.configure({THINK_MODEL_ID: factory})
    parent = Agent(model=ScriptedModel([]), tools=[lookup, think], system_prompt="Parent persona",
                   messages=[{"role": "user", "content": [{"text": "prior context"}]}], callback_handler=None)
    original = copy.deepcopy(parent.messages)
    state = {"require_think": True, "reasoning_effort": "low", "session_id": "stable"}
    emitted = [event async for event in think_activity.think_async("Analyze", 2, "high", agent=parent, invocation_state=state)]
    final = emitted[-1]
    assert final["status"] == "success"
    assert final["content"][0]["text"] == "Cycle 1/2:\nfirst\n\nCycle 2/2:\nsecond"
    assert calls == ["question"]
    factory.assert_called_once()
    assert len(model.calls) == 3
    for request in model.calls:
        assert [spec["name"] for spec in request["tool_specs"]] == ["lookup"]
        assert request["system_prompt"] == "Parent persona"
        assert request["state"]["reasoning_effort"] == "high"
        assert "require_think" not in request["state"]
    assert "evidence-742" in str(model.calls[-1]["messages"])
    assert "prior context" in str(model.calls[-1]["messages"])
    assert "evidence-742" in str(final)
    assert parent.messages == original
    assert state == {"require_think": True, "reasoning_effort": "low", "session_id": "stable"}
    assert len([event for event in emitted if "event" in event]) > 3


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max"])
async def test_async_effort_and_prompt_are_caller_selected(effort):
    from strands import Agent
    from config import THINK_MODEL_ID
    model = ScriptedModel([text_events("insight")])
    think_activity.configure({THINK_MODEL_ID: lambda: model})
    parent = Agent(model=ScriptedModel([]), system_prompt="Parent", callback_handler=None)
    events = [event async for event in think_activity.think_async("request", 1, effort, agent=parent,
                                                                thinking_system_prompt="METHOD", system_prompt="Persona")]
    assert events[-1]["status"] == "success"
    assert model.calls[0]["state"]["reasoning_effort"] == effort
    expected = think_activity.ThoughtProcessor({}, think_activity._CONSOLE).create_thinking_prompt("request", 1, 1, "METHOD")
    assert prompt_text(model.calls[0]) == expected
    assert model.calls[0]["system_prompt"] == "Persona"


@pytest.mark.asyncio
async def test_async_zero_cycles_does_not_access_agent_factory_or_tools():
    events = [event async for event in think_activity.think_async("hello", 0, "minimal", agent=None)]
    assert events == [{"status": "success", "content": [{"text": ""}]}]


@pytest.mark.asyncio
@pytest.mark.parametrize("cycles,effort", [(-1, "minimal"), (11, "high"), (True, "high"), (1, "invented")])
async def test_async_invalid_arguments_return_upstream_error_without_execution(cycles, effort):
    events = [event async for event in think_activity.think_async("request", cycles, effort, agent=None)]
    assert events[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_async_no_astra_factory_returns_error_not_parent_model_fallback():
    from strands import Agent
    parent_model = ScriptedModel([text_events("must not execute")])
    parent = Agent(model=parent_model, callback_handler=None)
    result = [event async for event in think_activity.think_async("request", 1, "minimal", agent=parent)]
    assert result[-1]["status"] == "error"
    assert "no fallback" in result[-1]["content"][0]["text"]
    assert parent_model.calls == []


@pytest.mark.asyncio
async def test_async_explicit_empty_tools_keeps_native_override_and_no_recursion():
    from strands import Agent, tool
    from config import THINK_MODEL_ID
    @tool
    def lookup() -> str:
        """Test evidence."""
        return "result"
    model = ScriptedModel([text_events("answer")])
    think_activity.configure({THINK_MODEL_ID: lambda: model})
    parent = Agent(model=ScriptedModel([]), tools=[lookup], callback_handler=None)
    result = [event async for event in think_activity.think_async("request", 1, "low", agent=parent, tools=[])]
    assert result[-1]["status"] == "success"
    assert not model.calls[0]["tool_specs"]


@pytest.mark.asyncio
async def test_async_mid_cycle_failure_returns_evidence_without_retrying_completed_tool():
    from strands import Agent, tool
    from config import THINK_MODEL_ID
    calls = []
    @tool
    def lookup() -> str:
        """Return observable evidence."""
        calls.append("executed")
        return "evidence-before-error"
    class FailingAfterTool(ScriptedModel):
        async def stream(self, *args, **kwargs):
            if self.calls:
                raise RuntimeError("provider unavailable after action")
            async for event in super().stream(*args, **kwargs):
                yield event
    model = FailingAfterTool([call_events("lookup", "before-failure", {})])
    think_activity.configure({THINK_MODEL_ID: lambda: model})
    parent = Agent(model=ScriptedModel([]), tools=[lookup], callback_handler=None)
    events = [event async for event in think_activity.think_async("request", 3, "high", agent=parent)]
    assert events[-1]["status"] == "error"
    assert "provider unavailable after action" in events[-1]["content"][0]["text"]
    assert "evidence-before-error" in str(events[-1])
    assert calls == ["executed"]
