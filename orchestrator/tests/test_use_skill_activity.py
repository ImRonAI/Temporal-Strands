"""use_skill Temporal activity streaming publish."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest
from strands.types._events import ToolResultEvent, ToolStreamEvent

from use_skill_activity import _sanitize_stream_payload, use_skill_activity


def test_sanitize_stream_payload_strips_agent() -> None:
    out = _sanitize_stream_payload(
        {
            "skill_name": "demo",
            "agent": object(),
            "event": {"data": "hello"},
        }
    )
    assert out["skill_name"] == "demo"
    assert out["text"] == "hello"
    assert "agent" not in out


def test_skill_activity_uses_caller_tool_id_with_replay_guard() -> None:
    from workflow import _ToolResultHook

    event = SimpleNamespace(tool_use={"name": "use_skill", "toolUseId": "caller-skill-42"}, selected_tool=None)
    hook = _ToolResultHook(lambda _: None)
    with patch("workflow.workflow.patched", return_value=True) as patched:
        hook._bind_skill_activity(event)
    patched.assert_called_once_with("skill-agent-stream-correlation-v1")
    assert event.selected_tool.tool_name == "use_skill"
    assert event.selected_tool._options["activity_id"] == "caller-skill-42"
    event.selected_tool = None
    with patch("workflow.workflow.patched", return_value=False):
        hook._bind_skill_activity(event)
    assert event.selected_tool is None


@pytest.mark.asyncio
async def test_use_skill_activity_publishes_thinking_frames() -> None:
    tool_use_id = "act-skill-1"
    terminal = {
        "status": "success",
        "content": [{"text": "done"}],
        "toolUseId": tool_use_id,
    }

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        yield ToolStreamEvent(
            tool_use,
            {
                "skill_name": "demo-skill",
                "agent": SimpleNamespace(
                    system_prompt="Exact runtime system prompt",
                    tool_registry=SimpleNamespace(registry={"read": SimpleNamespace(tool_spec={
                        "name": "file_read", "description": "Read a file",
                        "inputSchema": {"json": {"type": "object"}},
                    })}),
                    model=SimpleNamespace(api_key="never-publish-this"),
                ),
                "event": {"data": "chunk"},
            },
        )
        yield ToolResultEvent(terminal)

    published: list[dict] = []

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
    activity_info.attempt = 1

    handle = AsyncMock()
    handle.query = AsyncMock(return_value="gemini-3.7-flash")

    mock_tool = MagicMock()
    mock_tool.stream = fake_stream

    with (
        patch("use_skill_activity.activity.info", return_value=activity_info),
        patch(
            "use_skill_activity.activity.client",
            return_value=MagicMock(get_workflow_handle=lambda _id: handle),
        ),
        patch("use_skill_activity._session_model", new=AsyncMock(return_value=object())),
        patch("use_skill_activity.Agent"),
        patch(
            "use_skill_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("use_skill_activity.create_use_skill_tool", return_value=mock_tool),
    ):
        result = await use_skill_activity("demo-skill", "do something")

    assert result == terminal
    assert len(published) == 4
    assert published[0]["tool_use"]["name"] == "use_skill"
    assert published[0]["data"]["skill_name"] == "demo-skill"
    assert published[0]["data"]["type"] == "skill_start"
    assert published[1]["data"]["configuration"] == {
        "systemPrompt": "Exact runtime system prompt", "userPrompt": "do something",
        "model": "gemini-3.7-flash", "skills": [],
        "tools": [{"name": "file_read", "description": "Read a file", "inputSchema": {"json": {"type": "object"}}}],
    }
    assert published[2]["data"]["event"] == {"data": "chunk"}
    assert published[3]["data"]["type"] == "skill_complete"
    assert "never-publish-this" not in str(published)


@pytest.mark.asyncio
async def test_use_skill_activity_assigns_inline_skills() -> None:
    """``skills`` reaches the factory as ``assigned_skills`` and the scoped
    catalog rides with the request."""
    tool_use_id = "act-skill-2"
    terminal = {
        "status": "success",
        "content": [{"text": "done"}],
        "toolUseId": tool_use_id,
    }
    seen_requests: list[str] = []

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        seen_requests.append(tool_use["input"]["request"])
        yield ToolResultEvent(terminal)

    fake_client = MagicMock()
    fake_client.topic.return_value = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    mock_tool = MagicMock()
    mock_tool.stream = fake_stream
    factory = MagicMock(return_value=mock_tool)

    with (
        patch("use_skill_activity.activity.info", return_value=activity_info),
        patch(
            "use_skill_activity.activity.client",
            return_value=MagicMock(get_workflow_handle=lambda _id: AsyncMock()),
        ),
        patch("use_skill_activity._session_model", new=AsyncMock(return_value=object())),
        patch("use_skill_activity.Agent"),
        patch(
            "use_skill_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("use_skill_activity.create_use_skill_tool", factory),
        patch(
            "skills_config.skills_prompt",
            return_value="<available_skills>scoped</available_skills>",
        ),
    ):
        result = await use_skill_activity(
            "demo-skill", "do something", skills=["helper-a", "helper-b"]
        )

    assert result == terminal
    assert factory.call_args.kwargs["assigned_skills"] == ["helper-a", "helper-b"]
    assert seen_requests == [
        "do something\n\n<available_skills>scoped</available_skills>"
    ]


@pytest.mark.asyncio
async def test_use_skill_activity_without_skills_keeps_request_verbatim() -> None:
    tool_use_id = "act-skill-3"
    terminal = {"status": "success", "content": [{"text": "ok"}], "toolUseId": tool_use_id}
    seen_requests: list[str] = []

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        seen_requests.append(tool_use["input"]["request"])
        yield ToolResultEvent(terminal)

    fake_client = MagicMock()
    fake_client.topic.return_value = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    mock_tool = MagicMock()
    mock_tool.stream = fake_stream
    factory = MagicMock(return_value=mock_tool)

    with (
        patch("use_skill_activity.activity.info", return_value=activity_info),
        patch(
            "use_skill_activity.activity.client",
            return_value=MagicMock(get_workflow_handle=lambda _id: AsyncMock()),
        ),
        patch("use_skill_activity._session_model", new=AsyncMock(return_value=object())),
        patch("use_skill_activity.Agent"),
        patch(
            "use_skill_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("use_skill_activity.create_use_skill_tool", factory),
    ):
        await use_skill_activity("demo-skill", "do something")

    assert factory.call_args.kwargs["assigned_skills"] is None
    assert seen_requests == ["do something"]
