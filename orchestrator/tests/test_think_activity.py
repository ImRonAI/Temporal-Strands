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
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
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
    """Acceptance gate: the fork is gone and the module stays small.

    Raised from 120: the hook-side ``ThinkInput`` dataclass (activity_as_hook's
    ``activity_input`` payload) and the closing durable ``think_notes`` publish
    both live here now, not in workflow.py.
    """
    lines = open(think_activity.__file__).read().splitlines()
    assert len(lines) < 160, f"{len(lines)} lines; the wrapper must stay under 160"
