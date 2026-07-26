"""Temporal glue for the vendored `think` tool (think.py — the verbatim
strands-agents-tools implementation with only async streaming added).

Everything about how thinking is PERFORMED lives in think.py, unmodified.
This module only supplies what running it under Temporal requires:

- An activity boundary. think()'s nested agent(prompt) call does real
  model I/O and bridges sync-to-async in a separate thread (confirmed in
  strands.agent.agent source) — both incompatible with a workflow's
  single-threaded deterministic event loop, and both fine in an activity.
- A stand-in parent agent. think()'s model_provider=None behavior is "use
  the parent agent's model", but the live TemporalAgent can't cross the
  workflow/activity boundary (it isn't serializable) — so the parent is
  reconstructed here from model_id, with the same tools as the real agent
  (workflow.py) so think()'s own tool filtering has the same registry to
  work with. model_settings' {"model_id", "params"} keys are honored in
  that reconstruction — this is how the calling agent caps thinking length
  via {"params": {"max_output_tokens": N}}.
- Live publishing: think()'s on_cycle streaming hook publishes each
  cycle's response to the workflow's "thinking" stream topic as it
  completes (rendered as ChainOfThought steps in the UI).
- A periodic heartbeat while cycles run — one cycle is a full nested-agent
  model call and routinely exceeds the 30s heartbeat_timeout, so
  heartbeating only between cycles would get the activity killed and
  retried mid-think.

One real, load-bearing caveat: AGENT_API_FUNCTION_TOOLS are
Temporal-activity-wrapped — their .stream() dispatches through
workflow.execute_activity, which only works inside a live workflow
context. They're passed to the stand-in parent so the thinking agent's
tool set matches the main agent's; if the thinking agent actually invokes
one from inside this activity it errors, and Strands surfaces that as an
error tool-result the thinking agent can recover from, not a crash.
"""

import os
from typing import Any, Optional

from temporalio import activity


@activity.defn
async def run_thinking_cycles(
    thought: str,
    cycle_count: int,
    system_prompt: str,
    model_id: str,
    stream_topic_name: str,
    tools: Optional[list[str]] = None,
    model_provider: Optional[str] = None,
    model_settings: Optional[dict[str, Any]] = None,
    thinking_system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """think()'s own parameters and return contract ({"status":
    "success|error", "content": [{"text": ...}]}), carried through
    unchanged, plus model_id / stream_topic_name — see module docstring."""
    import asyncio

    from strands import Agent
    from temporalio.contrib.workflow_streams import WorkflowStreamClient

    from perplexity_model import MAX_OUTPUT_TOKENS_CEILING, PerplexityModel
    from perplexity_tools import AGENT_API_FUNCTION_TOOLS
    from think import think

    api_key = os.environ["PERPLEXITY_API_KEY"]
    settings = model_settings or {}

    # max_output_tokens defaults to the platform maximum, exactly as it does
    # for the main agent's models — the calling agent can still narrow it per
    # cycle via model_settings={"params": {"max_output_tokens": N}}, which is
    # what that documented knob is for. Previously nothing set it here at all,
    # so thinking cycles on anthropic/* models 400'd outright.
    params = {"max_output_tokens": MAX_OUTPUT_TOKENS_CEILING}
    params.update(settings.get("params") or {})

    parent_agent = Agent(
        model=PerplexityModel(
            api_key=api_key,
            model_id=str(settings.get("model_id") or model_id),
            params=params,
        ),
        tools=AGENT_API_FUNCTION_TOOLS,
    )

    async def heartbeat_loop() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(10)

    pinger = asyncio.ensure_future(heartbeat_loop())
    try:
        stream_client = WorkflowStreamClient.from_within_activity()
        async with stream_client:
            topic = stream_client.topic(stream_topic_name)
            return await think(
                thought=thought,
                cycle_count=cycle_count,
                system_prompt=system_prompt,
                tools=tools,
                model_provider=model_provider,
                model_settings=model_settings,
                thinking_system_prompt=thinking_system_prompt,
                agent=parent_agent,
                on_cycle=topic.publish,
            )
    finally:
        pinger.cancel()
