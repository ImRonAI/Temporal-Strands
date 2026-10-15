"""``use_agent`` as a durable, streaming Temporal activity.

The community ``strands_tools.use_agent`` is a sync tool that inherits the
live parent Agent in-process -- neither survives the activity boundary. This
registered activity keeps its contract (an isolated one-turn sub-agent with
its own system prompt) while resolving the model and tools the way every
other sub-agent activity here does:

- Model: the session's model by default (parent workflow ``model_id`` query
  against the worker's registered factories); ``model_id`` may name any other
  registered factory id explicitly. No provider zoo, no environment switching.
- Tools: rebuild a plain parent Agent (TemporalAgent cannot cross the
  activity boundary), then ``graph_tool._select_tools``. Omit ``tools`` and
  the sub-agent receives every tool on that parent. A list selects those
  names. Unknown names are skipped.
- Skills: opt-in. ``skills`` attaches the official ``strands.AgentSkills``
  plugin scoped to the assigned names (progressive disclosure via its
  ``skills`` tool); omitted means no skill catalog on the sub-agent.
- Streaming: every ``Agent.stream_async`` event is sanitized and published on
  ``THINKING_TOPIC`` as ``{"tool_use": {"name": "use_agent", "toolUseId"},
  "data": {"agent_name", "text", "event"}}`` -- the same standalone frame
  shape ``use_skill`` publishes (with ``agent_name`` in place of
  ``skill_name``), so the frontend's generic tool stream path applies.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing, suppress
from typing import Any, Optional

from strands import Agent
from strands.types._events import ToolResultEvent, ToolStreamEvent
from strands.types.tools import ToolResult, ToolUse
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import GRAPH_QUIET_HEARTBEAT_INTERVAL, SUBAGENT_STREAM_BATCH_INTERVAL
from graph_tool import _select_tools, _tool_name
from subagent_support import (
    heartbeat,
    in_process_model,
    model_factories,
    parent_agent,
    quiet_heartbeat_ticker,
    sanitize_stream_payload,
    session_model,
    workspace_task_prefix,
)


@activity.defn(name="use_agent")
async def use_agent_activity(
    prompt: str,
    system_prompt: str,
    agent_name: Optional[str] = None,
    tools: Optional[list[str]] = None,
    model_id: Optional[str] = None,
    skills: Optional[list[str]] = None,
) -> ToolResult:
    """Run one isolated sub-agent turn with its own system prompt, streaming live.

    Creates a fresh agent with the given persona, runs it once on the prompt,
    and returns its reply. The sub-agent's context is completely separate from
    yours; put everything it needs into the prompt.

    Args:
        prompt: The task or question for the sub-agent to process.
        system_prompt: The sub-agent's persona and instructions.
        agent_name: Optional display label for the sub-agent (defaults to
            "sub-agent"); shown in the live activity stream.
        tools: Registry names to assign. Omit this and the sub-agent receives
            every community tool loaded on this session. Name the tools it
            needs to give it only those. An empty list gives it no parent
            tools. Unknown names are skipped.
        model_id: Leave blank to inherit the parent TemporalAgent's model.
            A registered factory name (the same string TemporalAgent(model=)
            takes) runs the sub-agent on that model instead.
        skills: Optional registered skill names to assign to the sub-agent:
            its skills tool is scoped to exactly these names and only they
            are listed in its available_skills. Unknown names fail the call.

    Returns:
        {"status": "success" | "error", "content": [{"text": ...}]}
    """
    from workflow import THINKING_TOPIC

    prompt = workspace_task_prefix(activity.info().workflow_id) + prompt

    activity_id = activity.info().activity_id or "use_agent_run"
    tool_use = {"name": "use_agent", "toolUseId": activity_id}
    label = agent_name or "sub-agent"

    try:
        # Skill Rule 1: model= is a registered factory name. Blank means
        # omit — inherit the parent TemporalAgent's model.
        if not model_id:
            model = await session_model("use_agent")
        else:
            factory = model_factories().get(model_id)
            if factory is None:
                raise ApplicationError(
                    f"use_agent: no registered model factory for {model_id!r}",
                    type="UseAgentActivityError",
                    non_retryable=True,
                )
            model = factory()
        model = in_process_model(model)

        # Official use_agent: filter parent.tool_registry, then Agent(model,
        # messages=[], tools=filtered). The registry parent is a Temporal
        # rebuild (skill Pattern 2) and must not share this streaming model —
        # PerplexityModel is stateful (store=True).
        parent = await parent_agent(await session_model("use_agent"))
        agent_tools = _select_tools(parent, tools)

        # Skills are opt-in for the nested agent. When the caller assigns
        # names, the official strands.AgentSkills plugin is scoped to exactly
        # those skills (name/description injected per invocation; SKILL.md
        # loaded only through its ``skills`` tool) and replaces the parent's
        # catalog-wide inline ``skill`` tool. With no assignment the nested
        # agent carries no skill catalog at all -- the full ~700-skill listing
        # would be ~300KB per model call.
        plugins: list[Any] = []
        if skills:
            from skills_config import agent_skills_plugin

            agent_tools = [t for t in agent_tools if _tool_name(t) != "skill"]
            plugins.append(agent_skills_plugin(skills))

        agent = Agent(
            model=model,
            messages=[],
            tools=agent_tools,
            system_prompt=system_prompt,
            name=label,
            callback_handler=None,
            plugins=plugins,
        )

        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=SUBAGENT_STREAM_BATCH_INTERVAL,
        )
        topic = stream_client.topic(THINKING_TOPIC)

        result: ToolResult | None = None
        nested_use: ToolUse = {
            "name": "use_agent", "toolUseId": activity_id, "input": {"input": prompt},
        }
        stream = agent.as_tool(name="use_agent").stream(nested_use, {})
        # The sub-agent goes quiet while its own tool calls run (use_skill,
        # file ops), so a per-chunk heartbeat alone can outlast the heartbeat
        # timeout; the ticker keeps Temporal aware between events.
        async with stream_client, aclosing(stream):
            ticker = asyncio.create_task(
                quiet_heartbeat_ticker(GRAPH_QUIET_HEARTBEAT_INTERVAL.total_seconds())
            )
            try:
                async for event in stream:
                    heartbeat()
                    if isinstance(event, ToolResultEvent):
                        result = event.tool_result
                        continue
                    if not isinstance(event, ToolStreamEvent):
                        raise ApplicationError(
                            "use_agent cannot resume an interrupted sub-agent inside an activity",
                            type="UseAgentActivityError", non_retryable=True,
                        )
                    topic.publish(
                        {
                            "tool_use": tool_use,
                            "data": sanitize_stream_payload(
                                {"agent_name": label, "event": event["tool_stream_event"]["data"]},
                                name_key="agent_name",
                            ),
                        }
                    )
            finally:
                ticker.cancel()
                with suppress(asyncio.CancelledError):
                    await ticker

        if result is None:
            raise ApplicationError("use_agent produced no tool result", non_retryable=True)
        return result
    except ApplicationError:
        raise
    except Exception as error:  # noqa: BLE001 - tool boundary
        # Raising (not returning) is the framework contract: ``activity_as_tool``
        # turns a returned value into a success tool result, so an error dict
        # would reach the model as a successful call (graph_activity._fail).
        activity.logger.error("use_agent activity failed: %s", error)
        raise ApplicationError(
            f"Error in use_agent: {error}",
            type="UseAgentActivityError",
            non_retryable=True,
        ) from error
