"""Pattern 3: ``use_skill`` as a durable Temporal activity with stream publish.

The async-generator skill tool runs inside the activity; each stream frame is
sanitized and published on ``THINKING_TOPIC`` for the SSE bridge. Model and
publish plumbing is shared with ``graph_activity`` / ``use_agent_activity``
via ``subagent_support``.
Reference: aws-samples/sample-strands-agents-agentskills ``create_skill_agent_tool``.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable, Mapping
from typing import Any, cast

from strands import Agent
from strands.types._events import ToolResultEvent, ToolStreamEvent
from strands.types.tools import ToolUse
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import THINK_STREAM_BATCH_INTERVAL
from skills_config import create_use_skill_tool
from subagent_support import heartbeat, sanitize_stream_payload, session_model
import subagent_support


def configure(model_factories: Mapping[str, Callable[[], Any]]) -> None:
    """Kept for run_worker compatibility: installs the shared registry."""
    subagent_support.configure(model_factories)


async def _session_model() -> Any:
    return await session_model("use_skill")


def _sanitize_stream_payload(raw: Any) -> dict[str, Any]:
    return sanitize_stream_payload(raw, name_key="skill_name")


@activity.defn(name="use_skill")
async def use_skill_activity(skill_name: str, request: str) -> dict[str, Any]:
    """Execute a registered skill in an isolated sub-agent (Agent-as-Tool meta-tool)."""
    from workflow import THINKING_TOPIC

    tool_use: ToolUse = {
        "toolUseId": activity.info().activity_id,
        "name": "use_skill",
        "input": {"skill_name": skill_name, "request": request},
    }

    try:
        model = await _session_model()
        parent = Agent(model=model, tools=[], system_prompt="", callback_handler=None)
        use_skill_tool = create_use_skill_tool(model)
        invocation_state: dict[str, Any] = {"agent": parent, "model": model}

        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=THINK_STREAM_BATCH_INTERVAL,
        )
        topic = stream_client.topic(THINKING_TOPIC)
        result: dict[str, Any] = {}

        async with stream_client:
            async for event in use_skill_tool.stream(tool_use, invocation_state):
                heartbeat()
                if isinstance(event, ToolStreamEvent):
                    envelope = cast(dict[str, Any], event["tool_stream_event"])
                    data = envelope.get("data")
                    topic.publish(
                        {
                            "tool_use": {
                                "name": "use_skill",
                                "toolUseId": tool_use["toolUseId"],
                            },
                            "data": _sanitize_stream_payload(data),
                        }
                    )
                elif isinstance(event, ToolResultEvent):
                    result = cast(dict[str, Any], event.tool_result)

        if not result:
            result = {
                "status": "success",
                "content": [{"text": f"Skill {skill_name!r} completed."}],
                "toolUseId": tool_use["toolUseId"],
            }
        return result
    except ApplicationError:
        raise
    except Exception as error:
        message = f"Error in use_skill: {error}\n{traceback.format_exc()}"
        activity.logger.error("use_skill activity failed: %s", error)
        return {
            "status": "error",
            "content": [{"text": message}],
            "toolUseId": tool_use["toolUseId"],
        }
