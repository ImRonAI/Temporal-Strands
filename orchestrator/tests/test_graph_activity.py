"""Unit tests for graph_activity streaming envelopes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.agent.agent_result import AgentResult
from strands.multiagent.base import NodeResult, Status
from strands.telemetry.metrics import EventLoopMetrics
from strands.types._events import ToolResultEvent, ToolStreamEvent

from graph_activity import _publishable, graph_activity, configure


@pytest.fixture(autouse=True)
def model_factory() -> None:
    configure({"gemini-3.7-flash": lambda: object()})


@pytest.mark.asyncio
async def test_graph_activity_publishes_tool_stream_events() -> None:
    tool_use_id = "activity-graph-1"
    stream_event = {
        "type": "multiagent_node_start",
        "node_id": "research",
        "node_type": "agent",
    }
    terminal = {
        "status": "success",
        "content": [{"text": "done"}],
        "toolUseId": tool_use_id,
    }

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        yield ToolStreamEvent(tool_use, stream_event)
        yield ToolResultEvent(terminal)

    published: list[dict[str, Any]] = []

    class FakeTopic:
        def publish(self, payload: dict[str, Any]) -> None:
            published.append(payload)

    fake_client = MagicMock()
    fake_client.topic.return_value = FakeTopic()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    handle = AsyncMock()
    handle.query = AsyncMock(return_value="gemini-3.7-flash")

    with (
        patch("graph_activity.activity.info", return_value=activity_info),
        patch(
            "graph_activity.activity.client",
            return_value=MagicMock(get_workflow_handle=lambda _id: handle),
        ),
        patch("graph_activity._session_model", new=AsyncMock(return_value=object())),
        patch("graph_activity.Agent"),
        patch(
            "graph_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("graph_activity.official_graph.stream", side_effect=fake_stream),
    ):
        result = await graph_activity(action="execute", graph_id="g1", task="run")

    assert result == terminal
    assert len(published) == 1
    assert published[0]["tool_use"]["name"] == "graph"
    assert published[0]["tool_use"]["toolUseId"] == tool_use_id
    assert published[0]["data"] == stream_event


class _Handle:
    """Stands in for the live Agent/model handles Strands merges into deltas."""


def test_publishable_drops_runtime_handles_and_structures_results() -> None:
    agent_result = AgentResult(
        stop_reason="end_turn",
        message={"role": "assistant", "content": [{"text": "final report"}]},
        metrics=EventLoopMetrics(),
        state={},
    )
    node_stop = {
        "type": "multiagent_node_stop",
        "node_id": "research",
        "node_result": NodeResult(
            result=agent_result, execution_time=12, status=Status.COMPLETED
        ),
    }
    text_delta = {
        "type": "multiagent_node_stream",
        "node_id": "research",
        "event": {"data": "hi", "delta": {"text": "hi"}, "agent": _Handle(), "model": _Handle()},
    }
    failed = {
        "type": "multiagent_node_stop",
        "node_id": "expert",
        "node_result": NodeResult(
            result=RuntimeError("503 UNAVAILABLE"), execution_time=1, status=Status.FAILED
        ),
    }

    out = [_publishable(frame) for frame in (node_stop, text_delta, failed)]

    # The whole point: every frame survives the Temporal JSON payload converter.
    json.dumps(out)
    assert out[0]["node_result"] == {
        "__type__": "NodeResult",
        "status": "completed",
        "result": {
            "__type__": "AgentResult",
            "stop_reason": "end_turn",
            "message": {"role": "assistant", "content": [{"text": "final report"}]},
        },
        "execution_time": 12,
        "execution_count": 0,
    }
    assert out[1]["event"] == {"data": "hi", "delta": {"text": "hi"}}
    assert out[2]["node_result"]["status"] == "failed"
    assert out[2]["node_result"]["result"] == {
        "__type__": "RuntimeError",
        "error": "503 UNAVAILABLE",
    }


@pytest.mark.asyncio
async def test_graph_activity_publishes_terminal_error_frame_on_failure() -> None:
    tool_use_id = "activity-graph-2"

    async def failing_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        yield ToolStreamEvent(
            tool_use, {"type": "multiagent_node_start", "node_id": "plan", "node_type": "agent"}
        )
        raise RuntimeError("Unable to serialize unknown type")

    published: list[dict[str, Any]] = []

    class FakeTopic:
        def publish(self, payload: dict[str, Any]) -> None:
            published.append(payload)

    fake_client = MagicMock()
    fake_client.topic.return_value = FakeTopic()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    with (
        patch("graph_activity.activity.info", return_value=activity_info),
        patch("graph_activity._session_model", new=AsyncMock(return_value=object())),
        patch("graph_activity.Agent"),
        patch(
            "graph_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("graph_activity.official_graph.stream", side_effect=failing_stream),
    ):
        result = await graph_activity(action="execute", graph_id="g1", task="run")

    assert result["status"] == "error"
    assert result["toolUseId"] == tool_use_id
    # The route folds this terminal frame into the snapshot as status "failed".
    assert published[-1]["data"]["status"] == "error"
    assert published[-1]["tool_use"]["toolUseId"] == tool_use_id
    assert "Unable to serialize" in published[-1]["data"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_graph_activity_validation_errors() -> None:
    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = "val-1"

    with patch("graph_activity.activity.info", return_value=activity_info):
        # Create without topology
        res1 = await graph_activity(action="create", graph_id="g1")
        assert res1["status"] == "error"
        assert "topology with a non-empty 'nodes' list is required" in res1["content"][0]["text"]

        # Create with empty topology
        res2 = await graph_activity(action="create", graph_id="g1", topology={"nodes": []})
        assert res2["status"] == "error"
        assert "topology with a non-empty 'nodes' list is required" in res2["content"][0]["text"]

        # Execute without task
        res3 = await graph_activity(action="execute", graph_id="g1")
        assert res3["status"] == "error"
        assert "task prompt is required" in res3["content"][0]["text"]


@pytest.mark.asyncio
async def test_graph_activity_single_turn_create_and_execute() -> None:
    tool_use_id = "act-single-1"
    calls: list[str] = []

    async def mock_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        action = tool_use["input"]["action"]
        calls.append(action)
        if action == "create":
            yield ToolResultEvent({"status": "success", "content": [{"text": "created"}]})
        elif action == "execute":
            yield ToolResultEvent({"status": "success", "content": [{"text": "executed"}]})

    fake_client = MagicMock()
    fake_client.topic.return_value = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    with (
        patch("graph_activity.activity.info", return_value=activity_info),
        patch("graph_activity._session_model", new=AsyncMock(return_value=object())),
        patch("graph_activity.Agent"),
        patch(
            "graph_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("graph_activity.official_graph.stream", side_effect=mock_stream),
    ):
        result = await graph_activity(
            action="execute",
            task="do work",
            topology={"nodes": [{"id": "n1", "type": "agent", "system_prompt": "test"}]},
        )

    assert result["status"] == "success"
    assert result["content"] == [{"text": "executed"}]
    # Both create and execute were invoked in sequence
    assert calls == ["create", "execute"]


def test_after_tool_call_unwraps_serialized_error() -> None:
    from strands.hooks.events import AfterToolCallEvent
    from workflow import _ToolResultHook

    published: list[dict[str, Any]] = []
    hook = _ToolResultHook(publish=published.append)

    event = AfterToolCallEvent(
        agent=MagicMock(),
        selected_tool=None,
        tool_use={"name": "graph", "toolUseId": "u1", "input": {}},
        invocation_state={},
        result={
            "toolUseId": "u1",
            "status": "success",
            "content": [
                {
                    "text": json.dumps(
                        {
                            "status": "error",
                            "content": [{"text": "graph_id and topology are required"}],
                        }
                    )
                }
            ],
        },
    )

    hook._record(event)

    # event.result is updated with real error status and clean content
    assert event.result["status"] == "error"
    assert event.result["content"] == [{"text": "graph_id and topology are required"}]
    # The published stream also receives the error status
    assert len(published) == 1
    assert published[0]["status"] == "error"
    assert published[0]["content"] == [{"text": "graph_id and topology are required"}]
