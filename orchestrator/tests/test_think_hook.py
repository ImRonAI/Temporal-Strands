"""Unit tests for workflow._ToolResultHook and the THINK_TOOL activity binding.

Think runs as the ``think`` activity (Pattern 2, surface 3): the model decides
think cycles itself, and the hook keeps no think state — it only publishes
clamped tool results on ``tool_results`` and rewrites failed-activity errors.
"""

from __future__ import annotations

from typing import Any

from strands.hooks import HookRegistry
from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent, MessageAddedEvent
from temporalio.exceptions import ActivityError, ApplicationError

from workflow import PERMANENT_COMMUNITY_TOOLS, THINK_TOOL, _ToolResultHook


def before_call(name: str, tool_use_id: str = "call-1") -> BeforeToolCallEvent:
    return BeforeToolCallEvent(
        agent=object(),  # type: ignore[arg-type]
        selected_tool=None,
        invocation_state={},
        tool_use={"toolUseId": tool_use_id, "name": name, "input": {}},
    )


def after_call(
    name: str,
    result: dict[str, Any] | None,
    *,
    tool_use_id: str = "call-1",
    exception: Exception | None = None,
) -> AfterToolCallEvent:
    return AfterToolCallEvent(
        agent=object(),  # type: ignore[arg-type]
        selected_tool=None,
        invocation_state={},
        tool_use={"toolUseId": tool_use_id, "name": name, "input": {}},
        result=result,  # type: ignore[arg-type]
        exception=exception,
    )


def message_added(content: list[dict[str, Any]]) -> MessageAddedEvent:
    return MessageAddedEvent(
        agent=object(),  # type: ignore[arg-type]
        message={"role": "user", "content": content},  # type: ignore[typeddict-item]
    )


def registered(registry: HookRegistry, event: Any) -> list[Any]:
    return list(registry.get_callbacks_for(event))


def test_tool_composition() -> None:
    assert THINK_TOOL.tool_name == "think"
    assert [tool.tool_name for tool in PERMANENT_COMMUNITY_TOOLS] == [
        "load_tool",
        "browser",
        "mcp_client",
        "graph",
        "use_agent",
        "use_skill",
    ]


def test_think_tool_wraps_the_think_activity_with_the_config_envelope() -> None:
    """THINK_TOOL is a TemporalActivityTool dispatching the ``think`` activity
    with the config THINK_* timeouts/retry — one automatic attempt, since a
    replayed think is a duplicate inference."""
    import config
    from temporalio.contrib.strands._temporal_activity_tool import TemporalActivityTool

    assert isinstance(THINK_TOOL, TemporalActivityTool)
    assert THINK_TOOL._options["start_to_close_timeout"] == config.THINK_START_TO_CLOSE
    assert THINK_TOOL._options["heartbeat_timeout"] == config.THINK_HEARTBEAT_TIMEOUT
    assert THINK_TOOL._options["retry_policy"] == config.THINK_RETRY_POLICY


def test_hook_registers_tool_call_and_message_callbacks() -> None:
    hook = _ToolResultHook(lambda _: None)
    registry = HookRegistry()
    hook.register_hooks(registry)
    assert hook._bind_skill_activity in registered(registry, before_call("think"))
    assert hook._record in registered(
        registry, after_call("think", {"toolUseId": "call-1", "status": "success", "content": []})
    )
    assert hook._reconcile in registered(registry, message_added([]))


def test_record_publishes_clamped_tool_result() -> None:
    published: list[dict[str, Any]] = []
    hook = _ToolResultHook(published.append)
    result = {
        "toolUseId": "shot-1",
        "status": "success",
        "content": [
            {"text": '{"screenshot": "aGV4", "mediaType": "image/png", "url": "https://x"}'},
            {"image": {"format": "png", "source": {"bytes": "aGV4"}}},
            {"text": "plain text"},
        ],
    }
    hook._record(after_call("take_screenshot", result, tool_use_id="shot-1"))

    assert len(published) == 1
    frame = published[0]
    assert frame["tool_use_id"] == "shot-1"
    assert frame["status"] == "success"
    # Screenshot bytes never ride durable published frames; text survives.
    assert frame["content"] == [
        {"text": '{"url": "https://x"}'},
        {"text": "plain text"},
    ]
    # The model keeps the full result; only the publish is stripped.
    assert len(result["content"]) == 3


def test_record_rewrites_failed_activity_error_with_inner_message() -> None:
    published: list[dict[str, Any]] = []
    hook = _ToolResultHook(published.append)
    inner = ApplicationError("graph exploded", non_retryable=True)
    error = ActivityError(
        "Activity task failed", scheduled_event_id=1, started_event_id=2,
        identity="w", activity_type="graph", activity_id="a1", retry_state=None,
    )
    error.__cause__ = inner
    event = after_call(
        "graph",
        {"toolUseId": "graph-1", "status": "error", "content": [{"text": "Activity task failed"}]},
        tool_use_id="graph-1",
        exception=error,
    )
    hook._record(event)

    assert published[0]["content"] == [{"text": "Error: graph exploded"}]
    # The rewrite enters the conversation, not just the publish.
    assert event.result["content"] == [{"text": "Error: graph exploded"}]


def test_reconcile_publishes_unrecorded_results_once_and_clears() -> None:
    published: list[dict[str, Any]] = []
    hook = _ToolResultHook(published.append)
    hook._record(after_call("browser", {"toolUseId": "a", "status": "success", "content": []}, tool_use_id="a"))
    assert [frame["tool_use_id"] for frame in published] == ["a"]

    hook._reconcile(message_added([
        {"toolResult": {"toolUseId": "a", "status": "success", "content": []}},
        {"toolResult": {"toolUseId": "b", "status": "error", "content": [{"text": "cancelled"}]}},
    ]))
    # "a" was already recorded by _record; only the cancelled "b" is published.
    assert [frame["tool_use_id"] for frame in published] == ["a", "b"]
    assert published[1]["status"] == "error"

    # _recorded is cleared, so a later identical result publishes again.
    hook._reconcile(message_added([
        {"toolResult": {"toolUseId": "a", "status": "success", "content": []}},
    ]))
    assert [frame["tool_use_id"] for frame in published] == ["a", "b", "a"]
