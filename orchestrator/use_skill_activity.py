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
from subagent_support import heartbeat, publishable, sanitize_stream_payload, session_model
import subagent_support


def configure(model_factories: Mapping[str, Callable[[], Any]]) -> None:
    """Kept for run_worker compatibility: installs the shared registry."""
    subagent_support.configure(model_factories)


async def _session_model() -> Any:
    return await session_model("use_skill")


def _sanitize_stream_payload(raw: Any) -> dict[str, Any]:
    return sanitize_stream_payload(raw, name_key="skill_name")


@activity.defn(name="use_skill")
async def use_skill_activity(
    skill_name: str, request: str, skills: list[str] | None = None
) -> dict[str, Any]:
    """Execute a registered skill in an isolated sub-agent (Agent-as-Tool meta-tool).

    Args:
        skill_name: Registered skill that BECOMES the sub-agent (its SKILL.md
            is the sub-agent's system prompt).
        request: The task for the skill sub-agent.
        skills: Optional registered skill names to assign to the sub-agent as
            inline tools: it gets a ``skill(skill_name)`` tool scoped to
            exactly these names and loads their instructions into its own
            context (Pattern 2) — traditional skill use, never a nested
            sub-agent. Unknown names fail the call with the available list.
    """
    from workflow import THINKING_TOPIC

    tool_use: ToolUse = {
        "toolUseId": activity.info().activity_id,
        "name": "use_skill",
        "input": {"skill_name": skill_name, "request": request},
    }

    try:
        model = await _session_model()
        parent = Agent(model=model, tools=[], system_prompt="", callback_handler=None)
        use_skill_tool = create_use_skill_tool(model, assigned_skills=skills)
        if skills:
            # The sub-agent's system prompt is the skill's own SKILL.md
            # (reference _create_skill_agent), so the assigned-skills catalog
            # rides with the request: the scoped <available_skills> block
            # tells the sub-agent what its skill(skill_name) tool can load.
            from skills_config import skills_prompt

            catalog = skills_prompt(skills)
            if catalog:
                request = f"{request}\n\n{catalog}"
                tool_use["input"]["request"] = request
        invocation_state: dict[str, Any] = {"agent": parent, "model": model}

        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=THINK_STREAM_BATCH_INTERVAL,
        )
        topic = stream_client.topic(THINKING_TOPIC)
        result: dict[str, Any] = {}
        configuration_sent = False

        async with stream_client:
            topic.publish({
                "tool_use": {"name": "use_skill", "toolUseId": tool_use["toolUseId"]},
                "data": {"skill_name": skill_name, "type": "skill_start",
                         "attempt": activity.info().attempt, "request": request,
                         "skills": skills or []},
            })
            async for event in use_skill_tool.stream(tool_use, invocation_state):
                heartbeat()
                if isinstance(event, ToolStreamEvent):
                    envelope = cast(dict[str, Any], event["tool_stream_event"])
                    data = envelope.get("data")
                    # The reference tool exposes the actual sub-agent on each
                    # frame. Publish only its display configuration, never the
                    # model client, credentials, or runtime handles.
                    agent = data.get("agent") if isinstance(data, dict) else None
                    if not configuration_sent and isinstance(getattr(agent, "system_prompt", None), str):
                        topic.publish({
                            "tool_use": {"name": "use_skill", "toolUseId": tool_use["toolUseId"]},
                            "data": {"skill_name": skill_name, "configuration": {
                                "systemPrompt": agent.system_prompt,
                                "userPrompt": request,
                                "model": await subagent_support.session_model_id(),
                                "tools": publishable([tool.tool_spec for tool in agent.tool_registry.registry.values()]),
                                "skills": skills or [],
                            }},
                        })
                        configuration_sent = True
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
            topic.publish({
                "tool_use": {"name": "use_skill", "toolUseId": tool_use["toolUseId"]},
                "data": {"skill_name": skill_name, "type": "skill_complete",
                         "status": result.get("status", "success"), "result": publishable(result)},
            })

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
