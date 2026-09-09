"""``use_agent`` as a durable, streaming Temporal activity.

The community ``strands_tools.use_agent`` is a sync tool that inherits the
live parent Agent in-process -- neither survives the activity boundary. This
registered activity keeps its contract (an isolated one-turn sub-agent with
its own system prompt) while resolving the model and tools the way every
other sub-agent activity here does:

- Model: the session's model by default (parent workflow ``model_id`` query
  against the worker's registered factories); ``model_id`` may name any other
  registered factory id explicitly. No provider zoo, no environment switching.
- Tools: the safe sub-agent set (``use_skill``, ``file_read``,
  ``file_write``) plus community modules resolved through the existing
  ``load_tool`` search roots. Unknown names fail the call with the available
  list rather than silently running toolless.
- Streaming: every ``Agent.stream_async`` event is sanitized and published on
  ``THINKING_TOPIC`` as ``{"tool_use": {"name": "use_agent", "toolUseId"},
  "data": {"agent_name", "text", "event"}}`` -- the same standalone frame
  shape ``use_skill`` publishes (with ``agent_name`` in place of
  ``skill_name``), so the frontend's generic tool stream path applies.
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any, Optional

from strands import Agent
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import GRAPH_QUIET_HEARTBEAT_INTERVAL, THINK_STREAM_BATCH_INTERVAL
from subagent_support import (
    UnknownToolError,
    heartbeat,
    model_factories,
    quiet_heartbeat_ticker,
    resolve_tools,
    sanitize_stream_payload,
    session_model,
)


@activity.defn(name="use_agent")
async def use_agent_activity(
    prompt: str,
    system_prompt: str,
    agent_name: Optional[str] = None,
    tools: Optional[list[str]] = None,
    model_id: Optional[str] = None,
) -> dict[str, Any]:
    """Run one isolated sub-agent turn with its own system prompt, streaming live.

    Creates a fresh agent with the given persona, runs it once on the prompt,
    and returns its reply. The sub-agent's context is completely separate from
    yours; put everything it needs into the prompt.

    Args:
        prompt: The task or question for the sub-agent to process.
        system_prompt: The sub-agent's persona and instructions.
        agent_name: Optional display label for the sub-agent (defaults to
            "sub-agent"); shown in the live activity stream.
        tools: Optional tool names for the sub-agent, resolved from the
            built-ins (use_skill, file_read, file_write) plus community
            modules under orchestrator/tools/. Omit for the built-ins only.
        model_id: Optional registered model id to run the sub-agent on;
            omit to inherit the session's current model.

    Returns:
        {"status": "success" | "error", "content": [{"text": ...}]}
    """
    from workflow import THINKING_TOPIC

    activity_id = activity.info().activity_id or "use_agent_run"
    tool_use = {"name": "use_agent", "toolUseId": activity_id}
    label = agent_name or "sub-agent"

    try:
        if model_id is not None:
            factory = model_factories().get(model_id)
            if factory is None:
                raise ApplicationError(
                    f"use_agent: no registered model factory for {model_id!r}",
                    type="UseAgentActivityError",
                    non_retryable=True,
                )
            model = factory()
        else:
            model = await session_model("use_agent")

        try:
            agent_tools = resolve_tools(tools, model)
        except UnknownToolError as error:
            return {
                "status": "error",
                "content": [{"text": f"use_agent tools: {error}"}],
                "toolUseId": activity_id,
            }

        # Reference example-3 wiring: the agent that carries use_skill gets
        # the skills catalog prompt appended so it can only name skills that
        # exist (agentskills.generate_skills_prompt).
        from skills_config import augmented_system_prompt

        agent = Agent(
            model=model,
            tools=agent_tools,
            system_prompt=augmented_system_prompt(system_prompt),
            name=label,
            callback_handler=None,
        )

        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=THINK_STREAM_BATCH_INTERVAL,
        )
        topic = stream_client.topic(THINKING_TOPIC)

        result: Any = None
        stream = agent.stream_async(prompt)
        # The sub-agent goes quiet while its own tool calls run (use_skill,
        # file ops), so a per-chunk heartbeat alone can outlast the heartbeat
        # timeout; the ticker keeps Temporal aware between events.
        ticker = asyncio.create_task(
            quiet_heartbeat_ticker(GRAPH_QUIET_HEARTBEAT_INTERVAL.total_seconds())
        )
        async with stream_client:
            try:
                async for event in stream:
                    heartbeat()
                    topic.publish(
                        {
                            "tool_use": tool_use,
                            "data": sanitize_stream_payload(
                                {"agent_name": label, "event": event},
                                name_key="agent_name",
                            ),
                        }
                    )
                    if "result" in event:
                        result = event["result"]
            finally:
                ticker.cancel()
                await stream.aclose()

        reply = str(result).strip() if result is not None else ""
        return {
            "status": "success",
            "content": [{"text": reply or f"Agent {label!r} completed."}],
            "toolUseId": activity_id,
        }
    except ApplicationError:
        raise
    except Exception as error:  # noqa: BLE001 - tool boundary
        message = f"Error in use_agent: {error}\n{traceback.format_exc()}"
        activity.logger.error("use_agent activity failed: %s", error)
        return {
            "status": "error",
            "content": [{"text": message}],
            "toolUseId": activity_id,
        }
