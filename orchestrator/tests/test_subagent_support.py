"""Activity-side parent Agent and official graph_tool._select_tools.

TemporalAgent cannot cross the activity boundary (skill Pattern 2).
``parent_agent`` rebuilds a plain Agent; ``_select_tools`` filters
``parent.tool_registry`` by name.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.models.model import Model

from graph_tool import _select_tools, _tool_name
from load_tool import STRANDS_TOOLS_DIR
from subagent_support import agent_workspace, parent_agent, workspace_task_prefix


def test_stream_projection_preserves_evidence_without_mutating_sdk_state():
    from subagent_support import publishable

    current = {"toolUseId": "t1", "name": "write", "input": '{"content":"large"}'}
    event = {"type": "tool_use_stream", "current_tool_use": current,
             "delta": {"toolUse": {"input": "large"}}, "request_state": {"prompt": "private"}}
    assert publishable(event, agent_event=True) == {
        "type": "tool_use_stream", "current_tool_use": {"toolUseId": "t1", "name": "write"}}
    assert event["current_tool_use"] is current
    assert current["input"] == '{"content":"large"}'
    assert event["delta"]["toolUse"]["input"] == "large"
    # Do not strip legitimate result fields just because their names match runtime state.
    result = {"type": "tool_result", "tool_result": {"content": [{"json": {
        "messages": ["evidence"], "request_state": "failed", "system_prompt": "generated"}}]}}
    assert publishable(result, agent_event=True) == result
    for frame in ({"reasoningText": "analysis", "reasoning": True},
                  {"event": {"perplexity": {"type": "response.reasoning.started", "thought": "searching"}}},
                  {"event": {"gemini": {"type": "google_search", "queries": ["q"]}}}):
        assert publishable(frame, agent_event=True) == frame


@pytest.mark.asyncio
async def test_native_tool_stream_storage_grows_linearly():
    from strands import Agent, tool
    from graph_activity import flatten_native_event
    from subagent_support import sanitize_stream_payload
    from test_graph_activity import ScriptedModel, text_events

    @tool
    def accept_content(content: str) -> str:
        """Accept generated content without writing it."""
        return str(len(content))

    class ContentModel(ScriptedModel):
        def __init__(self, chunks):
            super().__init__()
            self.chunks = chunks

        async def stream(self, *args, **kwargs):
            self.calls += 1
            if self.calls > 1:
                for event in text_events("finished"):
                    yield event
                return
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "call-1", "name": "accept_content"}}}}
            for part in ['{"content":"', *(["x" * 100] * self.chunks), '"}']:
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": part}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}

    sizes = []
    for chunks in (200, 400):
        graph_frames, agent_frames = [], []
        async for event in Agent(model=ContentModel(chunks), tools=[accept_content], callback_handler=None).stream_async("Generate content"):
            graph_frames.extend(flatten_native_event({"type": "multiagent_node_stream", "node_id": "writer", "event": event}))
            frame = sanitize_stream_payload({"agent_name": "writer", "event": event}, name_key="agent_name")
            if frame is not None:
                agent_frames.append(frame)
        for events in ([frame["event"] for frame in graph_frames], [frame["event"] for frame in agent_frames]):
            fragments = [event["event"]["contentBlockDelta"]["delta"]["toolUse"]["input"]
                         for event in events if "toolUse" in event.get("event", {}).get("contentBlockDelta", {}).get("delta", {})]
            assert json.loads("".join(fragments)) == {"content": "x" * (chunks * 100)}
            assert any(event.get("message", {}).get("role") == "user" for event in events)
            assert any(event.get("data") == "finished" for event in events)
            assert not any("input" in event.get("current_tool_use", {}) or "event_loop_cycle_span" in event for event in events)
        sizes.append(sum(len(json.dumps(frame).encode()) for frame in graph_frames + agent_frames))
    assert sizes[1] < sizes[0] * 2.1
    assert sizes[1] < 500_000

os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

CALCULATOR = str(STRANDS_TOOLS_DIR / "calculator.py")


class SilentModel(Model):
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
    ) -> AsyncGenerator[dict[str, Any], None]:  # pragma: no cover
        raise NotImplementedError
        yield


def _activity_mock(loaded: Any) -> MagicMock:
    info = MagicMock()
    info.workflow_id = "chat-test"

    async def query(name: str, *_: Any, **__: Any) -> Any:
        return {"model_id": "fake/text", "loaded_tools": loaded}[name]

    handle = MagicMock()
    handle.query = AsyncMock(side_effect=query)
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    return MagicMock(
        info=MagicMock(return_value=info),
        client=MagicMock(return_value=client),
        heartbeat=MagicMock(),
        logger=MagicMock(),
    )


@pytest.mark.asyncio
async def test_parent_agent_restores_official_load_tool_records() -> None:
    loaded = [{"path": CALCULATOR, "name": "calculator"}]
    with patch("subagent_support.activity", _activity_mock(loaded)):
        parent = await parent_agent(SilentModel())

    names = set(parent.tool_registry.registry)
    assert names == {"calculator"}


@pytest.mark.asyncio
async def test_select_tools_named_subset_unknown_is_official_warning() -> None:
    loaded = [{"path": CALCULATOR, "name": "calculator"}]
    with patch("subagent_support.activity", _activity_mock(loaded)):
        parent = await parent_agent(SilentModel())

    with patch("graph_tool.logger") as logger:
        selected = _select_tools(
            parent, ["file_read", "calculator", "strands_tools.current_time"]
        )

    assert {_tool_name(tool) for tool in selected} == {"calculator"}
    warned = {call.args[1] for call in logger.warning.call_args_list}
    assert warned == {"file_read", "strands_tools.current_time"}


@pytest.mark.asyncio
async def test_select_tools_omit_assigns_nothing() -> None:
    loaded = [{"path": CALCULATOR, "name": "calculator"}]
    with patch("subagent_support.activity", _activity_mock(loaded)):
        parent = await parent_agent(SilentModel())
    assert _select_tools(parent, None) == []
    assert _select_tools(parent, []) == []


@pytest.mark.asyncio
async def test_loaded_tools_query_failure_degrades_to_empty() -> None:
    """A run started before the query existed cannot answer it."""
    mock = _activity_mock([])
    mock.client.return_value.get_workflow_handle.return_value.query = AsyncMock(
        side_effect=RuntimeError("unknown query")
    )
    with patch("subagent_support.activity", mock):
        parent = await parent_agent(SilentModel())
    assert parent.tool_registry.registry == {}
    mock.logger.warning.assert_called()


def test_agent_workspace_is_not_host_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GWEN_AGENT_WORKSPACE_ROOT", str(tmp_path))
    import config

    monkeypatch.setattr(config, "AGENT_WORKSPACE_ROOT", tmp_path)
    root = agent_workspace("chat-abc")
    assert root == tmp_path / "chat-abc"
    assert (root / "workspace").is_dir()
    assert str(root) != str(Path.home())
    text = workspace_task_prefix("chat-abc")
    assert str(root / "workspace") in text
    assert "/home/user" in text
