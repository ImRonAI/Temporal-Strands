"""Tests for the think activity: strands_tools' think as a streaming activity.

Runs the activity body under ``temporalio.testing.ActivityEnvironment`` with
the workflow-stream client and workflow query patched out, so the cycle loop,
streaming publishes, and return shape are exercised without a Temporal server.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.models.model import Model
from temporalio.exceptions import ApplicationError
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
    think_activity._MODEL_FACTORIES.clear()
    yield
    think_activity._MODEL_FACTORIES.clear()


async def run_think(
    model: ScriptedModel,
    stream: FakeStreamClient,
    **kwargs: Any,
) -> Any:
    """Run the activity under ActivityEnvironment with infra patched out.

    Awaited INSIDE the patch context: env.run returns a coroutine, and the
    activity body only touches activity.client() / from_within_activity when
    it actually executes.
    """
    think_activity.configure({"fake/text": lambda: model})
    env = ActivityEnvironment()

    handle = MagicMock()
    handle.query = AsyncMock(return_value="fake/text")
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


@pytest.mark.asyncio
async def test_cycles_chain_and_return_shape_matches_original() -> None:
    """N cycles run, each conclusion feeds the next, output is the original's."""
    model = ScriptedModel([text_events("first insight"), text_events("second insight")])
    stream = FakeStreamClient()

    result = await run_think(
        model,
        stream,
        thought="What are Saturn's largest moons?",
        cycle_count=2,
        system_prompt="You are an astronomer.",
    )

    assert result["status"] == "success"
    text = result["content"][0]["text"]
    assert "Cycle 1/2:\nfirst insight" in text
    assert "Cycle 2/2:\nsecond insight" in text

    # Cycle 2's prompt chains cycle 1's conclusion, verbatim per the original.
    assert len(model.calls) == 2
    second_prompt = model.calls[1]["messages"]
    assert "Previous cycle concluded: first insight" in str(second_prompt)
    assert "Continue developing these ideas further." in str(second_prompt)
    # The per-cycle system prompt is the caller's, not the default.
    assert model.calls[0]["system_prompt"] == "You are an astronomer."


@pytest.mark.asyncio
async def test_every_model_chunk_streams_to_the_thinking_topic() -> None:
    """Each raw StreamEvent is published live, unchanged, in order."""
    events = text_events("streamed reply")
    model = ScriptedModel([events])
    stream = FakeStreamClient()

    await run_think(
        model,
        stream,
        thought="stream me",
        cycle_count=1,
        system_prompt="prompt",
    )

    from workflow import THINKING_TOPIC

    assert stream.entered and stream.exited
    assert stream.topics[THINKING_TOPIC].published == events


@pytest.mark.asyncio
async def test_unregistered_model_is_a_nonretryable_error() -> None:
    """A model id outside the worker catalog fails fast, not forever."""
    stream = FakeStreamClient()
    env = ActivityEnvironment()

    handle = MagicMock()
    handle.query = AsyncMock(return_value="not/registered")
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
        with pytest.raises(ApplicationError) as excinfo:
            await env.run(
                think,
                thought="anything",
                cycle_count=1,
                system_prompt="prompt",
            )
    assert excinfo.value.non_retryable


@pytest.mark.asyncio
async def test_empty_system_prompt_falls_back_to_the_default_persona() -> None:
    """No system_prompt -> DEFAULT_THINK_SYSTEM_PROMPT; no thinking_system_prompt
    -> DEFAULT_THINKING_SYSTEM_PROMPT injected into the cycle prompt."""
    model = ScriptedModel([text_events("ok")])
    stream = FakeStreamClient()

    await run_think(model, stream, thought="t", cycle_count=1, system_prompt="")

    from think_activity import (
        DEFAULT_THINK_SYSTEM_PROMPT,
        DEFAULT_THINKING_SYSTEM_PROMPT,
    )

    assert model.calls[0]["system_prompt"] == DEFAULT_THINK_SYSTEM_PROMPT
    prompt_text = str(model.calls[0]["messages"])
    assert "PARALLEL EVIDENCE GATHERING" in prompt_text
    assert DEFAULT_THINKING_SYSTEM_PROMPT.splitlines()[0] in prompt_text


@pytest.mark.asyncio
async def test_model_failure_returns_error_result_like_the_original() -> None:
    """A mid-cycle exception becomes {"status": "error"} per the original."""

    class ExplodingModel(ScriptedModel):
        async def stream(
            self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
        ) -> AsyncGenerator[dict[str, Any], None]:
            raise RuntimeError("provider exploded")
            yield  # pragma: no cover

    model = ExplodingModel([])
    stream = FakeStreamClient()

    result = await run_think(
        model, stream, thought="t", cycle_count=1, system_prompt="p"
    )

    assert result["status"] == "error"
    assert "provider exploded" in result["content"][0]["text"]
