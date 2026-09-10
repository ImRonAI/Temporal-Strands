"""Unit tests for workflow._ThinkFirstHook on ``activity_as_hook``.

The hook is exercised through a real ``HookRegistry`` with
``temporalio.contrib.strands.workflow.activity_as_hook`` patched out, so no
Temporal server, worker, or live agent is needed. The fake records the wiring
(activity fn, activity_input producer, activity options) and returns a
recorded callback; the deterministic ``BeforeModelCallEvent`` callback reads
notes frames from a fake ``WorkflowStream`` state log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from strands.hooks import HookRegistry
from strands.hooks.events import BeforeInvocationEvent, BeforeModelCallEvent
from temporalio.api.common.v1 import Payload
from temporalio.converter import DataConverter

import think_activity
import workflow as chat_workflow
from workflow import (
    AGENT_API_TOOLS,
    THINK_TOOL,
    THINKING_TOPIC,
    _ThinkFirstHook,
)
from think_activity import ThinkInput


def user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"text": text}]}


def invocation_event(messages: Any) -> BeforeInvocationEvent:
    # agent is unused by the hook; a sentinel object suffices.
    return BeforeInvocationEvent(agent=object(), messages=messages)


def model_event(messages: Any) -> BeforeModelCallEvent:
    # The notes-folding callback reads the conversation through event.agent.
    return BeforeModelCallEvent(agent=type("A", (), {"messages": messages})())


def json_payload(value: Any) -> Payload:
    """Encode exactly as WorkflowStream._publish_to_topic does."""
    return DataConverter.default.payload_converter.to_payload(value)


@dataclass
class WireItem:
    topic: str
    data: str
    offset: int = 0


@dataclass
class StreamState:
    log: list[WireItem]
    base_offset: int = 0


class FakeStream:
    """Minimal stand-in for WorkflowStream exposing get_state()."""

    def __init__(self, items: list[tuple[int, str, Any]] | None = None,
                 base_offset: int = 0) -> None:
        self._items = list(items or [])
        self._base_offset = base_offset

    def get_state(self, **_: Any) -> StreamState:
        # get_state() yields the wire representation: base64 of the
        # serialized per-item Payload (_WorkflowStreamWireItem).
        import base64

        return StreamState(
            log=[
                WireItem(
                    topic=topic,
                    data=base64.b64encode(
                        json_payload(payload).SerializeToString()
                    ).decode(),
                    offset=offset,
                )
                for offset, topic, payload in self._items
            ],
            base_offset=self._base_offset,
        )


class FakeHookWiring:
    """Fake ``activity_as_hook`` that records wiring and returns a recorder."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.activity_fn: Any = None
        self.dispatched: list[Any] = []

    def __call__(self, activity_fn: Any, **kwargs: Any):
        self.activity_fn = activity_fn
        self.kwargs = kwargs

        async def callback(event: Any) -> None:
            self.dispatched.append(kwargs["activity_input"](event))

        return callback


@pytest.fixture(autouse=True)
def default_payload_converter():
    """Fold-decoding runs outside a workflow event loop in these unit tests;
    the repo's real runtime uses the default data converter everywhere."""
    converter = DataConverter.default.payload_converter
    with patch.object(
        chat_workflow.workflow, "payload_converter", lambda: converter
    ):
        yield


def make_hook(
    stream: FakeStream,
    wiring: FakeHookWiring | None = None,
    system_prompt: str = "sys",
    think_persona: str | None = None,
    think_methodology: str | None = None,
) -> tuple[_ThinkFirstHook, FakeHookWiring, HookRegistry]:
    """Register _ThinkFirstHook with activity_as_hook faked out."""
    wiring = wiring or FakeHookWiring()
    registry = HookRegistry()
    with (
        patch.object(chat_workflow, "activity_as_hook", wiring),
        patch.object(chat_workflow, "THINK_SYSTEM_PROMPT", think_persona),
        patch.object(chat_workflow, "THINK_METHODOLOGY_PROMPT", think_methodology),
    ):
        hook = _ThinkFirstHook(system_prompt, stream)
        hook.register_hooks(registry)
    return hook, wiring, registry


def registered(registry: HookRegistry, event: Any) -> list[Any]:
    return list(registry.get_callbacks_for(event))


@pytest.mark.asyncio
async def test_before_invocation_wiring_uses_config_timeouts() -> None:
    hook, wiring, registry = make_hook(FakeStream())
    callbacks = registered(registry, invocation_event([user_message("q")]))
    assert len(callbacks) == 1

    assert wiring.activity_fn is think_activity.think
    options = wiring.kwargs
    assert options["start_to_close_timeout"] is chat_workflow.THINK_START_TO_CLOSE
    assert options["heartbeat_timeout"] is chat_workflow.THINK_HEARTBEAT_TIMEOUT
    assert options["retry_policy"] is chat_workflow.THINK_RETRY_POLICY


@pytest.mark.asyncio
async def test_activity_input_builds_think_input_from_last_message() -> None:
    # Empty agent.json persona -> the session's own system prompt is used.
    hook, wiring, registry = make_hook(
        FakeStream(), system_prompt="the persona", think_persona=""
    )
    event = invocation_event([user_message("what is the plan?")])
    think_input = wiring.kwargs["activity_input"](event)

    assert isinstance(think_input, ThinkInput)
    assert think_input.thought == "what is the plan?"
    assert think_input.cycle_count == 1
    assert think_input.system_prompt == "the persona"

    await registered(registry, event)[0](event)
    assert wiring.dispatched == [think_input]


@pytest.mark.asyncio
async def test_before_invocation_skips_non_text_and_resume_messages() -> None:
    hook, wiring, registry = make_hook(FakeStream())
    for messages in (
        None,
        [],
        [{"role": "assistant", "content": [{"text": "hi"}]}],
        [{"role": "user", "content": [{"image": {"format": "png"}}]}],
        [{"role": "user",
          "content": [{"interruptResponse": {"interruptId": "i-1", "response": "yes"}}]}],
    ):
        event = invocation_event(messages)
        assert wiring.kwargs["activity_input"](event) is None

    event = invocation_event([user_message("ok")])
    assert wiring.kwargs["activity_input"](event) is not None


def test_notes_folded_once_from_stream_state() -> None:
    """The deterministic callback folds the last think_notes frame once."""
    stream = FakeStream([(5, THINKING_TOPIC, {"think_notes": "insight A"})], base_offset=4)
    hook, _, _ = make_hook(stream)
    hook.begin_turn(turn_start_offset=4)

    messages = [user_message("prompt")]
    hook._fold_think_notes(model_event(messages))
    assert messages[-1]["content"][-1] == {
        "text": "\n\n<think_notes>\ninsight A\n</think_notes>"
    }

    # Same turn's second model call must not re-append.
    hook._fold_think_notes(model_event(messages))
    assert len(messages[-1]["content"]) == 2


def test_notes_skipped_when_applied_offset_matches() -> None:
    """A frame at an offset already applied is stale (cancel/replay)."""
    stream = FakeStream([(5, THINKING_TOPIC, {"think_notes": "stale"})])
    hook, _, _ = make_hook(stream)
    hook.begin_turn(turn_start_offset=0)
    hook._applied_offset = 5

    messages = [user_message("prompt")]
    hook._fold_think_notes(model_event(messages))
    assert messages[-1]["content"] == [{"text": "prompt"}]


def test_notes_ignored_before_turn_start() -> None:
    """Frames older than the turn's start offset belong to earlier turns."""
    stream = FakeStream([(2, THINKING_TOPIC, {"think_notes": "old"})], base_offset=0)
    hook, _, _ = make_hook(stream)
    hook.begin_turn(turn_start_offset=3)

    messages = [user_message("prompt")]
    hook._fold_think_notes(model_event(messages))
    assert messages[-1]["content"] == [{"text": "prompt"}]


def test_notes_skipped_on_non_text_user_message() -> None:
    """interruptResponse resumes and non-text messages get no folding."""
    stream = FakeStream([(1, THINKING_TOPIC, {"think_notes": "N"})])
    hook, _, _ = make_hook(stream)
    hook.begin_turn(turn_start_offset=0)

    messages = [
        {"role": "user",
         "content": [{"interruptResponse": {"interruptId": "i", "response": "y"}}]}
    ]
    hook._fold_think_notes(model_event(messages))
    assert "think_notes" not in str(messages[-1]["content"])

    messages = [{"role": "user", "content": [{"image": {"format": "png"}}]}]
    hook._fold_think_notes(model_event(messages))
    assert messages[-1]["content"] == [{"image": {"format": "png"}}]


def test_no_notes_frame_leaves_message_unchanged() -> None:
    """think status:\"error\" publishes no notes -> bare prompt proceeds."""
    stream = FakeStream([(1, THINKING_TOPIC, {"event": {"contentBlockDelta": {}}})])
    hook, _, _ = make_hook(stream)
    hook.begin_turn(turn_start_offset=0)

    messages = [user_message("prompt")]
    hook._fold_think_notes(model_event(messages))
    assert messages[-1]["content"] == [{"text": "prompt"}]


def test_begin_turn_resets_applied_offset() -> None:
    hook, _, _ = make_hook(FakeStream())
    hook._applied_offset = 9
    hook.begin_turn(turn_start_offset=10)
    assert hook._applied_offset is None


def test_registers_both_callbacks() -> None:
    hook, _, registry = make_hook(FakeStream())
    assert len(registered(registry, invocation_event([user_message("x")]))) == 1
    model_callbacks = registered(registry, model_event([user_message("x")]))
    assert hook._fold_think_notes in model_callbacks


def test_tool_composition() -> None:
    """THINK_TOOL keys the UI's chain-of-thought suppression; the Agent API
    tools expose all ten perplexity_operations activities."""
    assert THINK_TOOL.tool_name == "think"
    assert [tool.tool_name for tool in AGENT_API_TOOLS] == [
        "create_fast_agent_response",
        "create_low_agent_response",
        "create_medium_agent_response",
        "create_high_agent_response",
        "create_xhigh_agent_response",
        "create_wide_research_agent_response",
        "retrieve_agent_response",
        "list_agent_response_files",
        "download_agent_response_file",
        "list_agent_models",
        "cancel_agent_response",
    ]
