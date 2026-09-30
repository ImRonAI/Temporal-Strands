"""Temporal activity for the installed ``strands_tools.think`` tool.

Official integration pattern (docs.temporal.io/develop/python/integrations/
strands-agents): "If you are using built-in ``strands_tools``, wrap them in a
thin async function decorated with ``@activity.defn`` so they run as Temporal
Activities." The tool itself (``orchestrator/strands-tools/think.py``) is
unmodified; this activity only carries the call across the activity boundary,
where a synchronous, nested-agent tool can legally execute (workflows block
threads; agent objects cannot cross the boundary).

``model_provider="perplexity"`` resolves through the tool's own
custom-provider mechanism (``create_model`` → ``.models/perplexity.py``), so
cycles run on the session's Perplexity Agent API model with its native
server-side tools. ``model_settings={}`` is required for custom providers:
``create_model`` consults the built-in-only ``get_provider_config`` whenever
config is None. With no ``agent`` kwarg the nested cycles get an empty tool
registry and no parent callback handler. The custom provider forwards its native
stream events to the existing thinking topic; the installed cycle loop is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from strands_tools.think import think as native_think
from config import THINK_STREAM_BATCH_INTERVAL

logger = logging.getLogger(__name__)


@activity.defn(name="think")
async def think(
    thought: str,
    cycle_count: int = 3,
    system_prompt: str = "",
    tools: Optional[List[str]] = None,
    model_provider: Optional[str] = "perplexity",
    model_settings: Optional[Dict[str, Any]] = None,
    thinking_system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Process a thought through multiple recursive thinking cycles for deep analysis.

    Each cycle builds on the previous cycle's output, producing depth of analysis that is
    difficult to reach in a single pass. Use this for problems that benefit from sustained
    reasoning: architecture decisions, tradeoff analysis, ethical implications, or open-ended
    ideation. The nested agent can call other tools during its cycles, but never itself.

    Args:
        thought: The thought or idea to process. Can be a question, statement, problem
            description, or creative prompt.
        cycle_count: Number of thinking cycles to perform (1-10). More cycles give deeper
            analysis at higher latency and cost; 3-5 is a good default. You decide how many
            cycles the problem needs.
        system_prompt: System prompt for the thinking agent. Specifies WHO the agent is -
            its persona, role, and expertise domain. For example, "You are a creative AI
            researcher specializing in educational technology."
        tools: Tool names to make available to the nested agent. Defaults to the nested
            agent's own native tools.
        model_provider: Provider for the thinking cycles. Defaults to "perplexity" — the
            session's Perplexity Agent API model with native server-side tools. Pass
            "bedrock", "anthropic", "litellm", "llamaapi", "ollama", "openai", "github",
            or "env" only to override.
        model_settings: Optional model configuration, e.g.
            {"model_id": "claude-sonnet-4-20250514", "params": {"temperature": 1}}.
            Defaults to the provider's standard configuration.
        thinking_system_prompt: Optional instructions controlling HOW the agent thinks, as
            opposed to system_prompt which controls who it is. For example, "Use first
            principles reasoning. Break down complex problems into fundamental components."

    Returns:
        Dict with "status" ("success" or "error") and "content", a list containing the
        concatenated output of all thinking cycles, or error details on failure.
    """
    tool_input: Dict[str, Any] = {
        "thought": thought,
        "cycle_count": cycle_count,
        "system_prompt": system_prompt,
        "model_provider": model_provider,
        "model_settings": model_settings if model_settings is not None else {},
    }
    if tools is not None:
        tool_input["tools"] = tools
    if thinking_system_prompt is not None:
        tool_input["thinking_system_prompt"] = thinking_system_prompt
    try:
        activity.heartbeat({"cycle_count": cycle_count})
    except RuntimeError:  # pragma: no cover - direct invocation outside Temporal
        logger.debug("Think heartbeat skipped outside activity context")
    # Direct keyword invocation of the installed callable — the same call
    # shape as ``agent.tool.think(thought=..., ...)`` (decorator.py __call__
    # forwards *args/**kwargs to the tool function; a ToolUse positional
    # would land in ``thought``).
    if model_provider != "perplexity":
        return await asyncio.to_thread(native_think, **tool_input)

    from workflow import THINKING_TOPIC

    loop = asyncio.get_running_loop()
    stream = WorkflowStreamClient.from_within_activity(batch_interval=THINK_STREAM_BATCH_INTERVAL)
    topic = stream.topic(THINKING_TOPIC)

    def publish(event: dict[str, Any]) -> None:
        # The stock synchronous tool runs its model in a separate thread/loop.
        # Publish on the activity's loop, where the Temporal client lives.
        loop.call_soon_threadsafe(topic.publish, event)

    tool_input["model_settings"] = {**tool_input["model_settings"], "stream_callback": publish}
    async with stream:
        return await asyncio.to_thread(native_think, **tool_input)
