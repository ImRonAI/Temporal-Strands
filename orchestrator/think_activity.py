"""Async Strands Think with upstream prompts and default parent-tool inheritance.

User-approved changes: fixed Astra, caller-chosen effort/0-10 cycles, full context
within a call, and conclusions plus evidence. Workflow-side execution preserves
native Temporal tool routing. The old activity below remains registered only for
pre-migration histories; it is not the new Think implementation.
"""

from __future__ import annotations

import logging
import asyncio
import copy
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.hooks import MessageAddedEvent
from strands.tools.executors import SequentialToolExecutor
from strands_tools.think import ThoughtProcessor
from strands_tools.utils import console_util
from temporalio import activity, workflow
from temporalio.contrib.strands import TemporalAgent
from temporalio.contrib.workflow_streams import WorkflowStreamClient

from config import (
    THINK_STREAM_BATCH_INTERVAL, THINK_MODEL_ID, THINK_START_TO_CLOSE,
    THINK_HEARTBEAT_TIMEOUT, THINK_RETRY_POLICY, THINK_REASONING_EFFORTS,
)


async def think_async(
    thought: str, cycle_count: int, reasoning_effort: str, *, agent: Agent,
    tools: list[str] | None = None, system_prompt: str | None = None,
    thinking_system_prompt: str | None = None, verbose: bool = False,
    invocation_state: dict[str, Any] | None = None, hooks: list[Any] | None = None,
    on_agent: Callable[[Any], None] | None = None, resolve_interrupts: Any = None,
):
    """Async streaming adaptation of strands_tools.think (0.8.5).

    Tool inheritance/exclusion, upstream prompt construction, cycle chaining and
    the error/summary envelope are unchanged. User-approved changes: fixed Astra,
    caller-chosen effort and 0-10 cycles, zero-call fast return, current parent
    context, a single native agent retaining all cycle messages, and tool evidence.
    Runtime hooks/callbacks are supplied by the workflow, not by model arguments.
    """
    generated = []
    conclusions = []
    thinker = None
    try:
        if type(cycle_count) is not int or not 0 <= cycle_count <= 10:
            raise ValueError("cycle_count must be an integer from 0 to 10")
        if cycle_count == 0:
            yield {"status": "success", "content": [{"text": ""}]}
            return
        # The API validates model-specific values; do not silently downgrade or
        # derive effort from task text. The caller explicitly supplies this value.
        if reasoning_effort not in THINK_REASONING_EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {THINK_REASONING_EFFORTS}")
        available = agent.tool_registry.registry
        selected = list(available) if tools is None else tools
        inherited = [available[name] for name in selected if name != "think" and name in available]
        context = copy.deepcopy(agent.messages)
        # A tool invocation happens with the parent's current tool batch open.
        # Supply only complete history to the child; never rewrite parent history.
        pending = set()
        boundary = len(context)
        for index, message in enumerate(context):
            for block in message.get("content", []):
                if "toolUse" in block:
                    if not pending:
                        boundary = index
                    pending.add(block["toolUse"]["toolUseId"])
                elif "toolResult" in block:
                    pending.discard(block["toolResult"]["toolUseId"])
        if pending:
            context = context[:boundary]
        options = dict(
            messages=context, tools=inherited, callback_handler=None,
            system_prompt=system_prompt or agent.system_prompt,
            tool_executor=SequentialToolExecutor(), hooks=hooks or [],
            conversation_manager=NullConversationManager(),
        )
        durable = workflow.in_workflow()
        if durable:
            from workflow import THINKING_TOPIC
            thinker = TemporalAgent(
                model=THINK_MODEL_ID, streaming_topic=THINKING_TOPIC,
                streaming_batch_interval=THINK_STREAM_BATCH_INTERVAL,
                start_to_close_timeout=THINK_START_TO_CLOSE,
                heartbeat_timeout=THINK_HEARTBEAT_TIMEOUT,
                retry_policy=THINK_RETRY_POLICY, **options,
            )
        else:
            factory = _MODEL_FACTORIES.get(THINK_MODEL_ID)
            if factory is None:
                raise RuntimeError(f"No registered model factory for {THINK_MODEL_ID}; no fallback is allowed")
            thinker = Agent(model=factory(), **options)
        thinker.hooks.add_callback(MessageAddedEvent, lambda event: generated.append(copy.deepcopy(event.message)))
        if on_agent:
            on_agent(thinker)
        state = {key: value for key, value in (invocation_state or {}).items()
                 if key not in {"agent", "request_state", "event_loop_cycle_id", "event_loop_cycle_span", "require_think"}}
        state["reasoning_effort"] = reasoning_effort
        prompt_for = ThoughtProcessor({}, _CONSOLE).create_thinking_prompt
        current = thought
        for cycle in range(1, cycle_count + 1):
            prompt = prompt_for(current, cycle, cycle_count, thinking_system_prompt)
            result = None
            async for event in thinker.stream_async(prompt, invocation_state=dict(state)):
                # Temporal's model activity already publishes raw model events;
                # publishing them again here would duplicate the UI stream.
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
            if result.stop_reason not in ("end_turn", "stop_sequence"):
                raise RuntimeError(f"Think did not complete its cycle: {result.stop_reason}")
            reply = str(result).strip()
            conclusions.append(f"Cycle {cycle}/{cycle_count}:\n{reply}")
            current = f"Previous cycle concluded: {reply}\nContinue developing these ideas further."
            if verbose:
                _CONSOLE.print(f"Cycle {cycle}/{cycle_count}: {reply}")
        content = [{"text": "\n\n".join(conclusions)}]
        if any("toolUse" in block for message in generated for block in message.get("content", [])):
            content.append({"json": {"messages": generated}})
        yield {"status": "success", "content": content}
    except asyncio.CancelledError:
        raise
    except Exception as error:
        yield {"status": "error", "content": [
            {"text": f"Error in think tool: {error}"},
            {"json": {"cycle_conclusions": conclusions, "messages": generated}},
        ]}
    finally:
        if thinker is not None:
            for name, binding in thinker.tool_registry.registry.items():
                if name != "think" and name not in agent.tool_registry.registry:
                    agent.tool_registry.register_tool(binding)
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
