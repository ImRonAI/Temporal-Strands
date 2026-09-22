"""``use_agent`` as a durable, streaming Temporal activity.

The community ``strands_tools.use_agent`` is a sync tool that inherits the
live parent Agent in-process -- neither survives the activity boundary. This
registered activity keeps its contract (an isolated one-turn sub-agent with
its own system prompt) while resolving the model and tools the way every
other sub-agent activity here does:

- Model: the session's model by default (parent workflow ``model_id`` query
  against the worker's registered factories); ``model_id`` may name any other
  registered factory id explicitly. No provider zoo, no environment switching.
- Tools: skill Pattern 2 inherit. Rebuild a plain parent Agent (TemporalAgent
  cannot cross the activity boundary), then ``graph_tool._select_tools``
  filters ``parent.tool_registry`` by name. Omit ``tools`` to inherit all
  registry names. Unknown names log the official warning and are skipped.
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
import traceback
from typing import Any, Optional

from strands import Agent
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import GRAPH_QUIET_HEARTBEAT_INTERVAL, THINK_STREAM_BATCH_INTERVAL
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
        tools: Optional tools for the sub-agent: names that exist on the
            parent TemporalAgent registry after official load (the Temporal
            activity name). Omit to inherit every parent-registry name.
            Unknown names log the official warning and are skipped.
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
