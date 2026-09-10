"""``strands_tools.think`` as a streaming Temporal activity — a thin wrapper.

Prompts, cycle chaining, and the return envelope stay upstream's
(``create_thinking_prompt`` is called, never copied); persona/methodology text
lives in ``agent.json``'s ``think`` key. Two Temporal deltas: upstream's
``process_cycle`` is blocking, so the loop alone is re-expressed over
``stream_async()`` (raw ``StreamEvent``s on ``THINKING_TOPIC``, heartbeat per
chunk); and its parent-agent/``model_provider`` choice can neither cross an
activity boundary nor name a Temporal factory, so the model comes from the
parent workflow's ``model_id`` query against :func:`set_model_factories`. Keep
the name ``think``: ``activity_as_tool`` and the UI's CoT suppression key off it."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from strands import Agent
from strands_tools.think import ThoughtProcessor
from strands_tools.utils import console_util
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient

from config import THINK_STREAM_BATCH_INTERVAL


@dataclass(frozen=True)
class ThinkInput:
    """Hook-side dispatch payload (``activity_as_hook`` ``activity_input``).

    The model-callable ``think`` tool keeps its multi-argument signature;
    the think-first hook dispatches through this single frozen dataclass,
    which the JSON converter reconstructs from ``arg_types`` type hints.
    """

    thought: str
    cycle_count: int = 1
    system_prompt: str = ""
    thinking_system_prompt: str | None = None


logger = logging.getLogger(__name__)
# Activities have no console; outside STRANDS_TOOL_CONSOLE_MODE console_util
# returns the discarding one upstream's processor accepts.
_CONSOLE = console_util.create()
# Worker model factories by live catalog id -- the mapping StrandsPlugin gets.
# API keys stay in the closures, never in workflow state or activity payloads.
_MODEL_FACTORIES: dict[str, Callable[[], Any]] = {}


def set_model_factories(model_factories: Mapping[str, Callable[[], Any]]) -> None:
    """Install the worker's model factories for the think activity."""
    _MODEL_FACTORIES.clear()
    _MODEL_FACTORIES.update(model_factories)


configure = set_model_factories  # historical name, kept for existing callers


async def _session_model() -> Any:
    """The session's model: parent workflow's ``model_id`` query -> factory."""
    info = activity.info()
    if not info.workflow_id:
        raise RuntimeError("think must be scheduled by a workflow")
    handle = activity.client().get_workflow_handle(info.workflow_id)
    model_id = await handle.query("model_id")
    if (factory := _MODEL_FACTORIES.get(model_id)) is None:
        raise RuntimeError(f"no registered model factory for {model_id!r}")
    return factory()


async def _run_cycle(prompt: str, system_prompt: str, model: Any,
                     publish: Callable[..., None]) -> str:
    """One streamed cycle. Plain ``strands.Agent``, never TemporalAgent: the
    durable unit is THIS activity. ``tools=[]`` leaves only the model's own
    natives — upstream's recursion guard, structurally."""
    agent = Agent(model=model, messages=[], tools=[], callback_handler=None,
                  system_prompt=system_prompt)
    result: Any = None
    async for event in agent.stream_async(prompt):
        if "event" in event:
            publish(event["event"])
            try:
                activity.heartbeat()  # keeps long cycles cancel/resume-visible
            except RuntimeError:  # pragma: no cover - no activity context
                pass
        result = event.get("result", result)
    return str(result).strip() if result is not None else ""


@activity.defn(name="think")
async def think(thought: str | ThinkInput, cycle_count: int = 1,
                system_prompt: str = "",
                thinking_system_prompt: str | None = None) -> dict[str, Any]:
    """Recursive thinking tool for sophisticated thought generation.

    Multi-cycle cognitive analysis: each cycle builds on the previous cycle's
    conclusion, reaching a depth a single pass cannot.

    Args:
        thought: The detailed thought, question, or problem to process — a
            plain string from the model-callable tool, or a ThinkInput from
            the think-first hook's ``activity_as_hook`` dispatch.
        cycle_count: Number of cycles (1-10); 3-5 balances depth against time.
        system_prompt: WHO the thinker is — persona and expertise domain,
            applied to every cycle.
        thinking_system_prompt: Optional — HOW the thinker works each cycle,
            overriding the default methodology. Prefer omitting it unless the
            task needs a specialized one.

    Returns:
        ``{"status": "success"|"error", "content": [{"text": ...}]}`` — the
        concatenated cycles, or the failure detail.
    """
    from workflow import THINKING_TOPIC  # lazy: workflow imports this module

    if isinstance(thought, dict):  # decoded without type hints
        thought = ThinkInput(**thought)
    if isinstance(thought, ThinkInput):  # hook dispatch unpacks to locals
        thought, cycle_count, system_prompt, thinking_system_prompt = (
            thought.thought, thought.cycle_count,
            thought.system_prompt or system_prompt, thought.thinking_system_prompt)

    try:
        model = await _session_model()
        prompt_for = ThoughtProcessor({}, _CONSOLE).create_thinking_prompt
        current, out = thought, []
        client = WorkflowStreamClient.from_within_activity(
            batch_interval=THINK_STREAM_BATCH_INTERVAL)
        topic = client.topic(THINKING_TOPIC)
        async with client:
            for cycle in range(1, cycle_count + 1):
                prompt = prompt_for(current, cycle, cycle_count, thinking_system_prompt)
                reply = await _run_cycle(prompt, system_prompt, model, topic.publish)
                out.append(f"Cycle {cycle}/{cycle_count}:\n{reply}")
                current = (f"Previous cycle concluded: {reply}\n"
                           "Continue developing these ideas further.")
            # Hook dispatches discard the return value; deliver the notes as
            # one durable frame the workflow folds into the prompt.
            topic.publish({"think_notes": "\n\n".join(out)}, force_flush=True)
        return {"status": "success", "content": [{"text": "\n\n".join(out)}]}
    except Exception as error:  # noqa: BLE001 - upstream's error envelope
        logger.error("Error in think tool: %s", error)
        text = f"Error in think tool: {error}\n{traceback.format_exc()}"
        return {"status": "error", "content": [{"text": text}]}
