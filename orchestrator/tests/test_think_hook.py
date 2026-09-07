"""Unit tests for workflow._ThinkFirstHook.

The hook is exercised directly with an injected executor callable (the
testability seam its constructor provides), so no Temporal server, worker, or
live agent is needed. The executor stands in for ``workflow.execute_activity``
and receives exactly the arguments the hook would pass it.
"""

from __future__ import annotations

from typing import Any

import pytest
from strands.hooks import HookRegistry
from strands.hooks.events import BeforeInvocationEvent

import think_activity
from workflow import (
    AGENT_API_TOOLS,
    THINK_TOOL,
    _ThinkFirstHook,
    _prompt_text_from_message,
    _think_notes_text,
)


def think_result(text: str) -> dict[str, Any]:
    return {"status": "success", "content": [{"text": text}]}


class RecordingExecutor:
    """Awaitable executor that records calls and returns a scripted result."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else think_result("notes")
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, activity_fn: Any, *, args: list[Any], **options: Any) -> Any:
        self.calls.append({"activity_fn": activity_fn, "args": args, "options": options})
        if self.error is not None:
            raise self.error
        return self.result


def user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"text": text}]}


def event_for(messages: Any) -> BeforeInvocationEvent:
    # agent is unused by the hook; a sentinel object suffices.
    return BeforeInvocationEvent(agent=object(), messages=messages)


def make_hook(executor: RecordingExecutor, system_prompt: str = "sys") -> _ThinkFirstHook:
    hook = _ThinkFirstHook(system_prompt, executor=executor)
    hook.mark_turn_start()
    return hook


@pytest.mark.asyncio
async def test_notes_are_folded_into_the_user_message() -> None:
    executor = RecordingExecutor(think_result("insight one\ninsight two"))
    hook = make_hook(executor, system_prompt="the session prompt")
    messages = [user_message("what is the plan?")]

    await hook._think_first(event_for(messages))

    # The activity was called with the prompt, one cycle, and the session
    # system prompt, under the think activity options.
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["activity_fn"] is think_activity.think
    assert call["args"] == ["what is the plan?", 1, "the session prompt"]
    assert call["options"]["retry_policy"].maximum_attempts == 1

    # The notes block was appended to the SAME message content list.
    content = messages[-1]["content"]
    assert content[0] == {"text": "what is the plan?"}
    assert content[-1]["text"] == (
        "\n\n<think_notes>\ninsight one\ninsight two\n</think_notes>"
    )


@pytest.mark.asyncio
async def test_interrupt_response_resume_is_skipped() -> None:
    executor = RecordingExecutor()
    hook = make_hook(executor)
    messages = [
        {
            "role": "user",
            "content": [
                {"interruptResponse": {"interruptId": "i-1", "response": "yes"}}
            ],
        }
    ]

    await hook._think_first(event_for(messages))

    assert executor.calls == []
    assert messages[0]["content"] == [
        {"interruptResponse": {"interruptId": "i-1", "response": "yes"}}
    ]


@pytest.mark.asyncio
async def test_non_user_or_textless_messages_are_skipped() -> None:
    executor = RecordingExecutor()
    hook = make_hook(executor)

    await hook._think_first(event_for(None))  # messages may be None
    await hook._think_first(event_for([]))
    await hook._think_first(
        event_for([{"role": "assistant", "content": [{"text": "hi"}]}])
    )
    await hook._think_first(
        event_for([{"role": "user", "content": [{"image": {"format": "png"}}]}])
    )

    assert executor.calls == []


@pytest.mark.asyncio
async def test_runs_at_most_once_per_turn() -> None:
    executor = RecordingExecutor()
    hook = make_hook(executor)
    first = [user_message("first prompt")]
    second = [user_message("second invocation same turn")]

    await hook._think_first(event_for(first))
    await hook._think_first(event_for(second))

    assert len(executor.calls) == 1
    # Second message untouched.
    assert second[0]["content"] == [{"text": "second invocation same turn"}]

    # A new turn resets the flag.
    hook.mark_turn_start()
    third = [user_message("next turn prompt")]
    await hook._think_first(event_for(third))
    assert len(executor.calls) == 2
    assert executor.calls[1]["args"][0] == "next turn prompt"


@pytest.mark.asyncio
async def test_activity_failure_is_swallowed() -> None:
    executor = RecordingExecutor(error=RuntimeError("activity exploded"))
    hook = make_hook(executor)
    messages = [user_message("carry on regardless")]

    # Does not raise; the turn proceeds without notes.
    await hook._think_first(event_for(messages))

    assert len(executor.calls) == 1
    assert messages[0]["content"] == [{"text": "carry on regardless"}]

    # And the once-per-turn flag stays consumed: no retry storm within a turn.
    await hook._think_first(event_for(messages))
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_error_result_and_empty_notes_leave_the_message_unchanged() -> None:
    executor = RecordingExecutor({"status": "error", "content": []})
    hook = make_hook(executor)
    messages = [user_message("prompt")]

    await hook._think_first(event_for(messages))

    assert messages[0]["content"] == [{"text": "prompt"}]


def test_hook_registers_on_before_invocation() -> None:
    registry = HookRegistry()
    hook = _ThinkFirstHook("sys", executor=RecordingExecutor())
    registry.add_hook(hook)
    event = BeforeInvocationEvent(agent=object(), messages=None)
    callbacks = list(registry.get_callbacks_for(event))
    assert hook._think_first in callbacks


def test_prompt_text_extraction_joins_text_blocks() -> None:
    message = {
        "role": "user",
        "content": [{"text": "line one"}, {"image": {}}, {"text": "line two"}],
    }
    assert _prompt_text_from_message(message) == "line one\nline two"
    assert _prompt_text_from_message({"role": "user", "content": "nope"}) is None
    assert _prompt_text_from_message("not a dict") is None


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


def test_think_notes_text_joins_content_blocks() -> None:
    assert _think_notes_text(think_result("a")) == "a"
    assert (
        _think_notes_text(
            {"status": "success", "content": [{"text": "a"}, {"text": "b"}]}
        )
        == "a\nb"
    )
    assert _think_notes_text(None) == ""
    assert _think_notes_text({"status": "success", "content": None}) == ""
