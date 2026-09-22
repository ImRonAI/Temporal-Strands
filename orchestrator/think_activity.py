"""Streaming port of the installed ``strands_tools.think`` community tool.

Source: https://github.com/strands-agents/tools/blob/main/src/strands_tools/think.py

``think_async`` follows the same cycle loop, prompt builder, tool inheritance,
and result envelope. It uses ``stream_async`` instead of ``agent(prompt)``, so
reasoning traces and in-cycle tool calls reach Chain of Thought. The old ``think`` activity below
remains registered only for pre-migration histories. Temporal uses named worker
model factories and retains approval hooks; provider overrides are unsupported
in that path rather than constructing live provider models inside a workflow.
"""

from __future__ import annotations

import logging
import asyncio
import os
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from strands import Agent
from strands_tools.think import ThoughtProcessor
from strands_tools.utils.models.model import create_model
from strands_tools.utils import console_util
from temporalio import activity, workflow
from temporalio.contrib.strands import TemporalAgent
from temporalio.contrib.workflow_streams import WorkflowStreamClient

from config import (
    THINK_STREAM_BATCH_INTERVAL, THINK_START_TO_CLOSE,
    THINK_HEARTBEAT_TIMEOUT, THINK_RETRY_POLICY,
)

logger = logging.getLogger(__name__)


def _inherit_tools(parent: Agent | None, specified_tools: list[str] | None) -> list[Any]:
    """Same tool filter as strands_tools.think.ThoughtProcessor.process_cycle."""
    if parent is None:
        return []
    registry = parent.tool_registry.registry
    if specified_tools is not None:
        inherited = []
        for name in specified_tools:
            if name == "think":
                logger.warning("Excluding 'think' tool from nested agent to prevent recursion")
                continue
            if name in registry:
                inherited.append(registry[name])
            else:
                logger.warning("Tool '%s' not found in parent agent's tool registry", name)
        return inherited
    return [tool for name, tool in registry.items() if name != "think"]


def _cycle_agent(
    parent: Agent | None,
    inherited: list[Any],
    system_prompt: str,
    *,
    durable: bool,
    hooks: list[Any] | None,
    model_name: str | None,
    model_provider: str | None,
    model_settings: dict[str, Any] | None,
) -> Agent:
    """Fresh Agent per cycle, matching upstream ``messages=[]`` + inherited tools.

    Temporal uses the parent's selected factory name and publishes the nested
    agent's StreamEvents on THINKING_TOPIC. Inherited tools are the parent's
    actual registered bindings, preserving their activity routing and options.
    """
    extra: dict[str, Any] = {"hooks": hooks or []}
    if parent is not None:
        extra["callback_handler"] = parent.callback_handler
        extra["trace_attributes"] = getattr(parent, "trace_attributes", None) or {}
    if durable:
        from workflow import THINKING_TOPIC
        # Resolve the same worker factory as the parent, never a substitute model.
        if model_provider is not None:
            raise ValueError("Think provider overrides require a worker-registered model; select the session model instead")
        if model_name is None:
            raise ValueError("Temporal Think requires the parent model factory name")
        return TemporalAgent(
            model=model_name, messages=[], tools=inherited, system_prompt=system_prompt,
            streaming_topic=THINKING_TOPIC,
            streaming_batch_interval=THINK_STREAM_BATCH_INTERVAL,
            start_to_close_timeout=THINK_START_TO_CLOSE,
            heartbeat_timeout=THINK_HEARTBEAT_TIMEOUT,
            retry_policy=THINK_RETRY_POLICY, **extra,
        )
    selected = parent.model if parent is not None else None
    if model_provider is not None:
        provider = os.getenv("STRANDS_PROVIDER", "bedrock") if model_provider == "env" else model_provider
        try:
            selected = create_model(provider=provider, config=model_settings)
        except Exception as error:
            # This fallback is the upstream tool's behavior, not retry logic.
            logger.warning("Failed to create %s model: %s; using parent's model", provider, error)
    return Agent(model=selected, messages=[], tools=inherited, system_prompt=system_prompt, **extra)


async def think_async(
    thought: str, cycle_count: int, system_prompt: str,
    tools: list[str] | None = None, model_provider: str | None = None,
    model_settings: dict[str, Any] | None = None,
    thinking_system_prompt: str | None = None, agent: Agent | None = None, *,
    model_name: str | None = None,
    invocation_state: dict[str, Any] | None = None, hooks: list[Any] | None = None,
    on_agent: Callable[[Any], None] | None = None, resolve_interrupts: Any = None,
):
    """Streaming port of ``strands_tools.think.think``.

    Same cycle loop, prompt builder, tool inheritance, and success/error
    envelope as the installed community tool. ``stream_async`` replaces
    ``agent(prompt)`` so reasoning deltas and in-cycle tool calls reach the UI.
    """
    conclusions: list[str] = []
    thinker = None
    try:
        custom_system_prompt = system_prompt
        if not custom_system_prompt:
            custom_system_prompt = (
                "You are an expert analytical thinker. Process the thought deeply and provide clear insights."
            )
        durable = workflow.in_workflow()
        state = {key: value for key, value in (invocation_state or {}).items()
                 if key not in {"agent", "request_state", "event_loop_cycle_id", "event_loop_cycle_span", "require_think"}}
        prompt_for = ThoughtProcessor({}, _CONSOLE).create_thinking_prompt
        current = thought
        for cycle in range(1, cycle_count + 1):
            prompt = prompt_for(current, cycle, cycle_count, thinking_system_prompt)
            thinker = _cycle_agent(
                agent, _inherit_tools(agent, tools), custom_system_prompt,
                durable=durable, hooks=hooks, model_name=model_name,
                model_provider=model_provider, model_settings=model_settings,
            )
            if on_agent:
                on_agent(thinker)
            result = None
            async for event in thinker.stream_async(prompt, invocation_state=dict(state)):
                # TemporalAgent already publishes raw model events on THINKING_TOPIC.
                if not durable and "event" in event:
                    yield event
                if "result" in event:
                    result = event["result"]
            while result is not None and result.stop_reason == "interrupt":
                if resolve_interrupts is None:
                    raise RuntimeError("Think requires approval; no approval handler is available")
                responses = await resolve_interrupts(result.interrupts or [])
                async for event in thinker.stream_async(responses, invocation_state=dict(state)):
                    if not durable and "event" in event:
                        yield event
                    if "result" in event:
                        result = event["result"]
            if result is None:
                raise RuntimeError("Think stream produced no result")
            reply = str(result).strip()
            conclusions.append(f"Cycle {cycle}/{cycle_count}:\n{reply}")
            current = f"Previous cycle concluded: {reply}\nContinue developing these ideas further."
            if on_agent:
                on_agent(None)
                thinker = None
        yield {
            "status": "success",
            "content": [{"text": "\n\n".join(conclusions)}],
        }
    except asyncio.CancelledError:
        raise
    except Exception as error:
        yield {
            "status": "error",
            "content": [{"text": f"Error in think tool: {error}\n{traceback.format_exc()}"}],
        }
    finally:
        if on_agent:
            on_agent(None)


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
    """Legacy activity behavior, retained only for replay of shipped histories.

    Its empty registry was the original defect; new Think uses think_async.
    """
    agent = Agent(model=model, messages=[], tools=[], callback_handler=None,
                  system_prompt=system_prompt)
    result: Any = None
    async for event in agent.stream_async(prompt):
        if "event" in event:
            publish(event["event"])
            try:
                activity.heartbeat()  # keeps long cycles cancel/resume-visible
            except RuntimeError:  # pragma: no cover - no activity context
                logger.debug("Think heartbeat skipped outside activity context")
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
