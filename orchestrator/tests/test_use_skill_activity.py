"""use_skill Temporal activity streaming publish."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
                "agent": object(),
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
    assert len(published) == 1
    assert published[0]["tool_use"]["name"] == "use_skill"
    assert published[0]["data"]["skill_name"] == "demo-skill"
