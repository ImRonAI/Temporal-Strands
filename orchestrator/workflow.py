"""Durable chat session workflow.

Composed from three patterns in the strands-temporal guide
(/Applications/strands-temporal/references/strands_temporal_agent_guide.md),
verified against temporalio 1.31.0 / strands-agents 1.50.2:

- Pattern 9 (continue-as-new): each turn is a ``@workflow.update`` so the caller
  gets its reply from the same call, and the agent is rebuilt inside ``run`` from
  carried messages. This is the one documented exception to "build the agent in
  ``__init__``" (guide R7).
- Pattern 3 (HITL): the turn loop answers ``result.stop_reason == "interrupt"``;
  ``approve`` signals the answer back. Every interrupt in ``result.interrupts``
  is answered and the full list handed back (guide R9).
- Pattern 8 (streaming): ``WorkflowStream`` hosts the topic named by
  ``TemporalAgent(streaming_topic=...)``; ``server.py`` subscribes to the same
  name. The names must match exactly (guide R10).

The stream topics are this repository's own SSE protocol, consumed by the
protected route ``app/api/orchestrator/route.ts``:

- ``events``      -- raw Strands ``StreamEvent`` dicts published by the model
                     activity itself (``_model_activity.invoke_model_streaming``).
- ``approval``    -- ``{"reason": str | None}``, published here when an
                     interrupt is pending and again with ``None`` once answered.
- ``tool_results`` -- ``{"tool_use_id", "status", "content"}``, published from an
  ``AfterToolCallEvent`` hook.
- ``thinking``    -- raw model ``StreamEvent`` chunks published live from the
                     ``think`` activity's nested-agent cycles, plus graph tool
                     ``ToolStreamEvent`` envelopes (``tool_use`` + ``data``
                     with native ``multiagent_*`` events) published from
                     ``graph_activity``.

The ``think`` tool (strands-agents-tools semantics, ``think_activity.py``) is
both a model-callable tool (``THINK_TOOL``) and forced ahead of the model on
every new user prompt by ``_ThinkFirstHook`` (``BeforeInvocationEvent``): its
notes are folded into the user message as a ``<think_notes>`` text block.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import timedelta
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
)
from strands.types.content import Messages
from strands.types.exceptions import EventLoopException
from strands.types.interrupt import InterruptResponseContent
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.contrib.strands import TemporalAgent, TemporalMCPClient
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.contrib.workflow_streams import WorkflowStream, WorkflowStreamState

from config import (
    AGENT_CREATE_RETRY_POLICY,
    AGENT_OPERATION_HEARTBEAT,
    AGENT_OPERATION_RETRY_POLICY,
    AGENT_OPERATION_SCHEDULE_TO_CLOSE,
    AGENT_OPERATION_START_TO_CLOSE,
    COMPUTER_USE_HEARTBEAT,
    COMPUTER_USE_SCHEDULE_TO_CLOSE,
    COMPUTER_USE_START_TO_CLOSE,
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
    MODEL_SCHEDULE_TO_CLOSE,
    MODEL_START_TO_CLOSE,
)

with workflow.unsafe.imports_passed_through():
    import perplexity_operations
    import think_activity
    from computer_use_activity import COMPUTER_USE_ACTIVITIES, COMPUTER_USE_TOOL_NAMES
    from graph_activity import graph_activity
    from load_tool import (
        mcp_client_activity,
        register_community_tool,
        tool_file_path,
        wrap_loaded_io_tool,
    )
    from use_skill_activity import use_skill_activity

    from strands import tool
    from strands.tools.mcp import MCPClient
    from strands_tools.load_tool import load_tool

    # Official load_tool is sync; Strands stream() uses asyncio.to_thread, which
    # Temporal workflows block. Not in PERMANENT — swap at execution via hook.
    @tool
    async def _load_tool_workflow_exec(path: str, name: str, agent: Any = None) -> dict[str, Any]:
        return load_tool._tool_func(path=tool_file_path(path), name=name, agent=agent)

# Temporal validates that every activity carries start_to_close_timeout OR
# schedule_to_close_timeout (_workflow_instance._outbound_schedule_activity).
# config.py currently leaves the MODEL_* / AGENT_OPERATION_* envelopes fully
# unset ("we do not cap"), which that validation rejects at schedule time and
# permanently fails the workflow task. Until config.py carries a valid
# envelope, fall back to a generous schedule-to-close cap instead of crashing
# the session (graceful degradation, telemetry.py convention).
_UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE = timedelta(days=1)


def _closable(options: dict[str, Any]) -> dict[str, Any]:
    """Ensure the SDK's required timeout is present, preserving config intent."""
    if options.get("start_to_close_timeout") or options.get(
        "schedule_to_close_timeout"
    ):
        return options
    return {
        **options,
        "schedule_to_close_timeout": _UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE,
    }


_MCP_ACTIVITY_OPTIONS = _closable(
    dict(
        start_to_close_timeout=MODEL_START_TO_CLOSE,
        schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE,
        heartbeat_timeout=MODEL_HEARTBEAT,
        retry_policy=MODEL_RETRY_POLICY,
    )
)

PERMANENT_COMMUNITY_TOOLS = (
    load_tool,
    activity_as_tool(mcp_client_activity, **_MCP_ACTIVITY_OPTIONS),
    activity_as_tool(graph_activity, **_MCP_ACTIVITY_OPTIONS),
    activity_as_tool(use_skill_activity, **_MCP_ACTIVITY_OPTIONS),
)

# The think activity streams nested-agent cycles on THINKING_TOPIC and
# heartbeats per chunk. No THINK_* timeout constants exist in config.py yet,
# so the envelope lives here: one attempt (a failed thought is re-thought by
# the orchestrator, not blindly replayed), 10 minutes per attempt, and a
# heartbeat window generous enough for slow model chunks.
_THINK_ACTIVITY_OPTIONS = dict(
    start_to_close_timeout=timedelta(minutes=10),
    heartbeat_timeout=timedelta(minutes=2),
    retry_policy=RetryPolicy(maximum_attempts=1),
)

THINK_TOOL = activity_as_tool(think_activity.think, **_THINK_ACTIVITY_OPTIONS)

# Perplexity Agent API sub-agent operations (perplexity_operations.py).
# Background preset runs can research for many minutes; the AGENT_OPERATION_*
# envelope from config.py is the generous-stream envelope. Creates get exactly
# one automatic attempt (no idempotency key on POST /v1/responses — see
# config.AGENT_CREATE_RETRY_POLICY); retrieve/list/download are read-only and
# retry normally.
_AGENT_OPERATION_OPTIONS = _closable(
    dict(
        start_to_close_timeout=AGENT_OPERATION_START_TO_CLOSE,
        schedule_to_close_timeout=AGENT_OPERATION_SCHEDULE_TO_CLOSE,
        heartbeat_timeout=AGENT_OPERATION_HEARTBEAT,
    )
)

_AGENT_CREATE_ACTIVITIES = (
    perplexity_operations.create_fast_agent_response,
    perplexity_operations.create_low_agent_response,
    perplexity_operations.create_medium_agent_response,
    perplexity_operations.create_high_agent_response,
    perplexity_operations.create_xhigh_agent_response,
    perplexity_operations.create_wide_research_agent_response,
)

_AGENT_READ_ACTIVITIES = (
    perplexity_operations.retrieve_agent_response,
    perplexity_operations.list_agent_response_files,
    perplexity_operations.download_agent_response_file,
    perplexity_operations.list_agent_models,
)

AGENT_API_TOOLS = (
    *(
        activity_as_tool(
            activity_fn,
            retry_policy=AGENT_CREATE_RETRY_POLICY,
            **_AGENT_OPERATION_OPTIONS,
        )
        for activity_fn in _AGENT_CREATE_ACTIVITIES
    ),
    *(
        activity_as_tool(
            activity_fn,
            retry_policy=AGENT_OPERATION_RETRY_POLICY,
            **_AGENT_OPERATION_OPTIONS,
        )
        for activity_fn in _AGENT_READ_ACTIVITIES
    ),
)

_MCP_CONFIG_PATH = Path(__file__).resolve().parent / "mcp.json"
_SHELL_MCP = Path(__file__).resolve().parent / ".venv/bin/strands-shell"


def _eager_mcp_server_names() -> frozenset[str]:
    """Servers registered on the worker via StrandsPlugin (shell only today)."""
    servers = json.loads(_MCP_CONFIG_PATH.read_text()).get("mcpServers", {})
    return frozenset(
        name for name, cfg in servers.items() if not cfg.get("continue_on_error")
    )


def mcp_client_factories() -> dict[str, Callable[[], MCPClient]]:
    """Worker-side MCP factories for StrandsPlugin — shell only.

    Remote catalog entries (``continue_on_error`` in mcp.json, e.g. datacommons)
    are real servers but must not register Temporal ``{server}-list-tools``
    activities. Those use the native ``mcp_client`` tool
    (connect / list_tools / call_tool) on demand instead.

    https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
    """
    raw = json.loads(_MCP_CONFIG_PATH.read_text())
    if _SHELL_MCP.is_file():
        raw["mcpServers"]["shell"]["command"] = str(_SHELL_MCP.resolve())
    eager = _eager_mcp_server_names()
    return {
        client._application_name: (lambda c=client: c)
        for client in MCPClient.load_servers(raw)
        if client._application_name in eager
    }


def temporal_mcp_clients() -> tuple[TemporalMCPClient, ...]:
    """Workflow-side TemporalMCPClient handles for eager mcp.json servers only."""
    return tuple(
        TemporalMCPClient(server=name, cache_tools=True, **_MCP_ACTIVITY_OPTIONS)
        for name in mcp_client_factories()
    )

# Topic names shared with server.py's subscriber and, through it, the SSE frames
# the protected Next.js route parses. Changing one of these without changing the
# route breaks the UI silently.

EVENTS_TOPIC = "events"
THINKING_TOPIC = "thinking"

APPROVAL_TOPIC = "approval"
HANDOFF_TOPIC = "handoff"


def _clamp_tool_results(messages: Messages) -> Messages:
    """Drop Computer Use screenshot bytes before continue-as-new.

    Screenshots are not part of the model's token budget and must not ride
    the carried message history. Text is not truncated.
    """
    clamped: Messages = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            clamped.append(message)
            continue
        new_content = []
        for block in content:
            result = block.get("toolResult") if isinstance(block, dict) else None
            if result and isinstance(result.get("content"), list):
                new_result_content = []
                for item in result["content"]:
                    if not isinstance(item, dict):
                        new_result_content.append(item)
                        continue
                    if "image" in item:
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.lstrip().startswith("{"):
                        try:
                            data = json.loads(text)
                            if isinstance(data, dict) and "screenshot" in data:
                                data.pop("screenshot", None)
                                data.pop("mediaType", None)
                                item = {**item, "text": json.dumps(data)}
                                text = item["text"]
                        except json.JSONDecodeError:
                            pass
                    new_result_content.append(item)
                block = {**block, "toolResult": {**result, "content": new_result_content}}
            new_content.append(block)
        clamped.append({**message, "content": new_content})
    return clamped


TOOL_RESULTS_TOPIC = "tool_results"


@dataclass
class TurnImage:
    """An image attached to a user turn.

    ``format`` is one of png/jpeg/gif/webp and ``data`` is bare base64 with the
    ``data:`` URL prefix stripped — the Strands Gemini image block shape.
    """

    format: str
    data: str


@dataclass
class TurnDocument:
    """A document attached to a user turn (Strands ``document`` content block)."""

    format: str
    data: str


@dataclass
class TurnVideo:
    """A video attached to a user turn (Strands ``video`` content block)."""

    format: str
    data: str


@dataclass
class TurnInput:
    """One user turn: prompt text plus multimodal attachments.

    ``model_id`` is an optional per-turn model switch: when set and different
    from the session's current model, the workflow rebuilds its agent on that
    registered factory name (carried messages, loaded tools, and MCP servers
    intact) before running the turn. None keeps the current model.
    """

    prompt: str
    images: list[TurnImage] = field(default_factory=list)
    documents: list[TurnDocument] = field(default_factory=list)
    videos: list[TurnVideo] = field(default_factory=list)
    model_id: str | None = None


@dataclass
class LoadedTool:
    """A community tool loaded via ``load_tool`` and carried across continue-as-new."""

    path: str
    name: str


@dataclass
class ChatInput:
    """Serializable session state, carried across continue-as-new.

    ``stream_state`` is typed ``WorkflowStreamState | None`` rather than ``Any``
    deliberately: the docstring on ``WorkflowStream.__init__`` warns that an
    ``Any``-typed field deserializes to a plain dict, which silently strips the
    type and breaks the new run.
    """

    model_id: str
    system_prompt: str
    session_id: str = ""
    messages: Messages = field(default_factory=list)
    stream_state: WorkflowStreamState | None = None
    # Test-only rollover threshold (canonical plan, Task 8 Step 5): after this
    # many completed turns the run hands off via continue-as-new even though
    # real history is nowhere near is_continue_as_new_suggested(). None in
    # production, where the SDK's own suggestion is the only trigger.
    rollover_turns: int | None = None
    loaded_tools: list[LoadedTool] = field(default_factory=list)
    extra_mcp_servers: list[str] = field(default_factory=list)
    connected_mcp_servers: list[str] = field(default_factory=list)


# Formats from the Strands Gemini multimodal docs (image / document / video).
# Anything else is dropped rather than sent as a guessed MIME type.
_SUPPORTED_IMAGE_FORMATS = frozenset({"png", "jpeg", "gif", "webp"})
_SUPPORTED_DOCUMENT_FORMATS = frozenset(
    {"pdf", "txt", "html", "csv", "md", "json"}
)
_SUPPORTED_VIDEO_FORMATS = frozenset({"mp4", "mpeg", "mov", "avi", "webm", "wmv", "flv", "mpg", "mpegps", "3gpp"})


def turn_content_blocks(turn: TurnInput) -> list[dict[str, Any]]:
    """Strands ContentBlocks for one turn (pure; safe to unit-test).

    Shapes match the Strands Gemini multimodal docs: image, document, video.
    """
    blocks: list[dict[str, Any]] = []
    if turn.prompt:
        blocks.append({"text": turn.prompt})
    for image in turn.images:
        if image.format not in _SUPPORTED_IMAGE_FORMATS:
            continue
        blocks.append(
            {
                "image": {
                    "format": image.format,
                    "source": {"bytes": base64.b64decode(image.data)},
                }
            }
        )
    for document in turn.documents:
        if document.format not in _SUPPORTED_DOCUMENT_FORMATS:
            continue
        blocks.append(
            {
                "document": {
                    "format": document.format,
                    "source": {"bytes": base64.b64decode(document.data)},
                }
            }
        )
    for video in turn.videos:
        if video.format not in _SUPPORTED_VIDEO_FORMATS:
            continue
        blocks.append(
            {
                "video": {
                    "format": video.format,
                    "source": {"bytes": base64.b64decode(video.data)},
                }
            }
        )
    return blocks


class _ToolResultHook(HookProvider):
    """Publishes each tool's result on the ``tool_results`` topic.

    Deterministic: it only reads the event and appends to the workflow-owned
    stream log, which is itself part of replayable workflow state.
    """

    def __init__(self, publish: Any) -> None:
        self._publish = publish

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(AfterToolCallEvent, self._record)

    def _record(self, event: AfterToolCallEvent) -> None:
        result = event.result
        if not result:
            return
        self._publish(
            {
                "tool_use_id": result.get("toolUseId", event.tool_use["toolUseId"]),
                "status": result.get("status", "success"),
                "content": result.get("content", []),
            }
        )


def _tool_result_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """The official tool's own result dict from an activity ToolResult.

    ``TemporalActivityTool`` serializes the activity's return value to text,
    so the official mcp_client status/content live inside that JSON.
    """
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            inner = json.loads(text)
        except ValueError:
            continue
        if isinstance(inner, dict) and "status" in inner:
            return inner
    return None


class _HotLoadHook(HookProvider):
    """Persist load_tool loads and mcp_client servers across turns and CAN.

    ``load_tools`` cannot execute anywhere: the activity cannot take the live
    agent, and the workflow cannot see the worker's connections. The hook
    cancels it with Strands' documented ``cancel_tool`` and records the server;
    the workflow then re-attaches it the one documented way — a construct-time
    ``TemporalMCPClient`` on a rebuilt agent (Temporal Strands README, MCP).
    """

    def __init__(
        self,
        loaded_tools: list[LoadedTool],
        extra_mcp: list[str],
        connected_mcp: list[str],
    ) -> None:
        self._loaded_tools = loaded_tools
        self._extra_mcp = extra_mcp
        self._connected_mcp = connected_mcp

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _before(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use.get("name") == "load_tool":
            event.selected_tool = _load_tool_workflow_exec

        inp = event.tool_use.get("input") or {}
        if event.tool_use.get("name") != "mcp_client" or inp.get("action") != "load_tools":
            return
        connection_id = inp.get("connection_id")
        if not connection_id or connection_id not in self._connected_mcp:
            # Unknown server: let the official tool report its own error.
            return
        catalog = {handle.server for handle in temporal_mcp_clients()}
        if connection_id not in self._extra_mcp and connection_id not in catalog:
            self._extra_mcp.append(connection_id)
        event.cancel_tool = True

    def _after(self, event: AfterToolCallEvent) -> None:
        result = event.result or {}
        inp = event.tool_use.get("input") or {}
        name = event.tool_use.get("name")
        if name == "load_tool":
            path = inp.get("path")
            tool_name = inp.get("name")
            if result.get("status") != "success" or not path or not tool_name:
                return
            path = tool_file_path(path)
            wrap_loaded_io_tool(event.agent, tool_name, path)
            if not any(rec.name == tool_name for rec in self._loaded_tools):
                self._loaded_tools.append(LoadedTool(path=path, name=tool_name))
            return
        if name != "mcp_client":
            return
        connection_id = inp.get("connection_id")
        if not connection_id:
            return
        action = inp.get("action")
        if action == "connect":
            inner = _tool_result_payload(result)
            if (
                inner is not None
                and inner.get("status") == "success"
                and connection_id not in self._connected_mcp
            ):
                self._connected_mcp.append(connection_id)
        elif action == "disconnect":
            if connection_id in self._connected_mcp:
                self._connected_mcp.remove(connection_id)
            if connection_id in self._extra_mcp:
                self._extra_mcp.remove(connection_id)


def _prompt_text_from_message(message: Any) -> str | None:
    """The joined text of a plain user message, or None if not think-eligible.

    Eligible means: a dict message with role == "user" whose content list has
    at least one text block and no interruptResponse blocks (the HITL resume
    path re-fires BeforeInvocationEvent with interruptResponse content).
    """
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if "interruptResponse" in block:
            return None
        text = block.get("text")
        if isinstance(text, str):
            texts.append(text)
    joined = "\n".join(texts).strip()
    return joined or None


def _think_notes_text(result: Any) -> str:
    """Joined content[].text of the think activity's returned dict."""
    if not isinstance(result, dict):
        return ""
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts).strip()


class _ThinkFirstHook(HookProvider):
    """Runs the ``think`` activity BEFORE the model on each new user prompt.

    Registered on ``BeforeInvocationEvent``: at hook time the new user message
    is ``event.messages`` (not yet appended to ``agent.messages``), so folding
    the think notes into the last message's content mutates exactly what the
    model will see. ``HookRegistry.invoke_callbacks_async`` awaits coroutine
    callbacks, and awaiting ``workflow.execute_activity`` inside an async hook
    is the same pattern the SDK's own MCP refresh hook uses on
    ``BeforeModelCallEvent`` — the hook itself is deterministic apart from the
    activity call, so it is replay-safe.

    Guards:
    - Skips interruptResponse resumes and any non-user/non-text message.
    - Runs at most once per turn (``mark_turn_start`` resets the flag).
    - A failed think activity is logged and swallowed; the turn proceeds
      without notes (graceful degradation, telemetry.py convention).

    ``executor`` defaults to ``workflow.execute_activity`` and is injectable
    for unit tests.
    """

    def __init__(
        self,
        system_prompt: str,
        executor: Callable[..., Any] | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._executor = executor
        self._ran_this_turn = False

    def mark_turn_start(self) -> None:
        """Reset the once-per-turn flag; called at the start of ``turn``."""
        self._ran_this_turn = False

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(BeforeInvocationEvent, self._think_first)

    async def _think_first(self, event: BeforeInvocationEvent) -> None:
        if self._ran_this_turn:
            return
        messages = event.messages
        if not messages:  # None on some invocation paths, or empty
            return
        prompt = _prompt_text_from_message(messages[-1])
        if prompt is None:
            return
        self._ran_this_turn = True
        executor = self._executor or workflow.execute_activity
        try:
            result = await executor(
                think_activity.think,
                args=[prompt, 1, self._system_prompt],
                **_THINK_ACTIVITY_OPTIONS,
            )
        except Exception as error:
            # workflow.logger requires the workflow event loop; unit tests
            # drive the hook outside one.
            log = workflow.logger if workflow.in_workflow() else logging.getLogger(__name__)
            log.warning(
                "think-first hook failed; continuing without notes: %s", error
            )
            return
        notes = _think_notes_text(result)
        if not notes:
            return
        message = messages[-1]
        content = message.get("content")
        if isinstance(content, list):
            content.append(
                {"text": "\n\n<think_notes>\n" + notes + "\n</think_notes>"}
            )


class _ComputerUseSafetyHook(HookProvider):
    """HITL gate for Gemini Computer Use safety_decision payloads.

    Gemini may attach a safety decision to a Computer Use function call.
    Confirmation uses the existing interrupt/approve loop; a block cancels
    the tool without executing Playwright.
    """

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(BeforeToolCallEvent, self._gate)

    def _gate(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name")
        if name not in COMPUTER_USE_TOOL_NAMES:
            return
        args = event.tool_use.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return
        if not isinstance(args, dict):
            return
        decision = args.get("safety_decision")
        if not isinstance(decision, dict):
            return
        action = str(
            decision.get("decision") or decision.get("action") or ""
        ).lower()
        reason = str(
            decision.get("explanation")
            or decision.get("reason")
            or "Gemini Computer Use safety check"
        )
        if "confirm" in action or action == "require_confirmation":
            approval = event.interrupt("computer_use_safety", reason=reason)
            if str(approval).strip().lower() not in {"a", "yes", "y", "approve", "true"}:
                event.cancel_tool = "computer use action was not approved"
            return
        if "block" in action:
            event.cancel_tool = f"blocked by Gemini safety: {reason}"


class _HumanControlHook(HookProvider):
    """Browser handoff using Strands ``BeforeToolCallEvent.interrupt``.

    ``handoff_to_user`` from strands_tools is the canonical tool; the preview
    panel's Take control signal sets the same interrupt name so the existing
    approve / resume loop applies.
    """

    def __init__(
        self,
        human_control: list[bool],
        handoff_requested: list[bool],
        publish: Any,
    ) -> None:
        self._human_control = human_control
        self._handoff_requested = handoff_requested
        self._publish = publish

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(BeforeToolCallEvent, self._gate)

    def _gate(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name")
        if self._human_control[0] and name in COMPUTER_USE_TOOL_NAMES:
            event.cancel_tool = "human has control of the browser session"
            return
        if not self._handoff_requested[0]:
            return
        self._handoff_requested[0] = False
        self._human_control[0] = True
        reason = "The user took control of the browser. Wait for their instructions before any further browser actions."
        self._publish({"active": True, "reason": reason})
        event.interrupt("handoff_to_user", reason=reason)


@workflow.defn
class ChatWorkflow:
    """One durable chat session. Its id is the session id the UI holds."""

    @workflow.init
    def __init__(self, input: ChatInput) -> None:
        # WorkflowStream must be constructed directly from a method named
        # __init__ -- it inspects the caller's frame and raises otherwise.
        self._stream = WorkflowStream(prior_state=input.stream_state)
        self._approvals = self._stream.topic(APPROVAL_TOPIC)
        self._handoffs = self._stream.topic(HANDOFF_TOPIC)
        self._tool_results = self._stream.topic(TOOL_RESULTS_TOPIC)

        self._model_id = input.model_id
        self._system_prompt = input.system_prompt
        self._session_id = input.session_id
        self._rollover_turns = input.rollover_turns
        self._loaded_tools = list(input.loaded_tools)
        self._extra_mcp_servers = list(input.extra_mcp_servers)
        self._connected_mcp_servers = list(input.connected_mcp_servers)
        # Extra-MCP set the current agent was constructed with; a difference
        # after a turn means a load_tools ran and the agent must be rebuilt.
        self._agent_extra_mcp: tuple[str, ...] = ()
        self._completed_turns = 0

        # Serializes concurrent turn updates. Two browser tabs posting at once
        # must not interleave inside a single agent invocation.
        self._lock = asyncio.Lock()
        self._agent: TemporalAgent | None = None
        self._done = False
        # Set once the run is handing off or ending; the turn validator
        # rejects new turns while it is true.
        self._closing = False
        # Stream offset where the in-flight turn's frames begin, or None
        # between turns. Exposed as a query so a client that reconnects
        # mid-turn can resume from the start of that turn instead of the live
        # tail: get_offset() returns base_offset + log length, so a browser
        # refresh would otherwise subscribe past every frame already emitted
        # for the partial turn and silently show nothing until the next one.
        self._turn_start_offset: int | None = None
        self._approval: str | None = None
        self._human_control = [False]
        self._handoff_requested = [False]
        self._pending_reason: str | None = None
        # Installed by _build_agent; turn() resets its once-per-turn flag.
        self._think_hook: _ThinkFirstHook | None = None

    def _stream_offset(self) -> int:
        """Current global stream offset: base_offset + log length.

        Built from the public ``get_state()`` snapshot, which exposes both
        fields. It is the same value ``server.py`` reads through
        ``get_offset()`` before subscribing, and the unit ``truncate()``
        expects.
        """
        state = self._stream.get_state()
        return state.base_offset + len(state.log)

    def _build_agent(self, messages: Messages) -> TemporalAgent:
        catalog = temporal_mcp_clients()
        catalog_names = {handle.server for handle in catalog}
        extras = tuple(
            TemporalMCPClient(server=name, cache_tools=True, **_MCP_ACTIVITY_OPTIONS)
            for name in self._extra_mcp_servers
            if name not in catalog_names
        )
        # Computer Use is Gemini-only (the tools drive Gemini's native
        # computer_use function calls). Non-Gemini sessions get neither the
        # tools nor their gating hooks.
        is_gemini = self._model_id.startswith("gemini")
        computer_use_tools = (
            tuple(
                activity_as_tool(
                    computer_use_activity,
                    **_closable(
                        dict(
                            start_to_close_timeout=COMPUTER_USE_START_TO_CLOSE,
                            schedule_to_close_timeout=COMPUTER_USE_SCHEDULE_TO_CLOSE,
                            heartbeat_timeout=COMPUTER_USE_HEARTBEAT,
                            retry_policy=MODEL_RETRY_POLICY,
                        )
                    ),
                )
                for computer_use_activity in COMPUTER_USE_ACTIVITIES
            )
            if is_gemini
            else ()
        )
        computer_use_hooks = (
            [
                _ComputerUseSafetyHook(),
                _HumanControlHook(
                    self._human_control,
                    self._handoff_requested,
                    self._handoffs.publish,
                ),
            ]
            if is_gemini
            else []
        )
        self._think_hook = _ThinkFirstHook(self._system_prompt)
        model_options = _closable(
            dict(
                start_to_close_timeout=MODEL_START_TO_CLOSE,
                schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE,
            )
        )
        agent = TemporalAgent(
            # A registered factory NAME from run_worker.py's models= mapping,
            # never a Model instance (guide R1).
            model=self._model_id,
            start_to_close_timeout=model_options["start_to_close_timeout"],
            schedule_to_close_timeout=model_options["schedule_to_close_timeout"],
            heartbeat_timeout=MODEL_HEARTBEAT,
            retry_policy=MODEL_RETRY_POLICY,
            # Temporal's own value for LLM streaming (docs.temporal.io,
            # "Stream LLM output"). Every batch is a durable Signal appended to
            # workflow history, so this is a history-pressure dial, not a
            # latency dial: at 25ms a single turn produced 5,158 signals and
            # 24,953 history events, and the workflow spent its time replaying
            # history instead of streaming.
            streaming_batch_interval=timedelta(milliseconds=200),
            # Publishes every StreamEvent on EVENTS_TOPIC from inside the model
            # activity. server.py subscribes to the identical name.
            streaming_topic=EVENTS_TOPIC,
            system_prompt=self._system_prompt,
            messages=list(messages),
            tools=[
                *PERMANENT_COMMUNITY_TOOLS,
                THINK_TOOL,
                *AGENT_API_TOOLS,
                *catalog,
                *extras,
                *computer_use_tools,
            ],
            hooks=[
                self._think_hook,
                _ToolResultHook(self._tool_results.publish),
                *computer_use_hooks,
                _HotLoadHook(
                    self._loaded_tools,
                    self._extra_mcp_servers,
                    self._connected_mcp_servers,
                ),
            ],
        )
        for rec in self._loaded_tools:
            register_community_tool(agent, rec.path, rec.name)
        self._agent_extra_mcp = tuple(self._extra_mcp_servers)
        return agent

    def _content_blocks(self, turn: TurnInput) -> list[dict[str, Any]]:
        """Strands ContentBlocks for one turn.

        Image, document, and video blocks match the Strands Gemini multimodal
        docs: ``{"image"|"document"|"video": {"format": ..., "source": {"bytes": ...}}}``.
        """
        return turn_content_blocks(turn)

    @workflow.update
    async def turn(self, turn: TurnInput) -> str:
        """Run one turn and return its reply text.

        An update rather than a signal so the HTTP caller receives the reply
        from the same call it made (guide Pattern 9).
        """
        await workflow.wait_condition(lambda: self._agent is not None)
        async with self._lock:
            agent = self._agent
            if agent is None:  # pragma: no cover - guarded by wait_condition
                raise RuntimeError("agent not initialized")

            # Per-turn model switch: rebuild the agent on the new registered
            # factory name, carrying the conversation and every loaded tool /
            # MCP server exactly as the post-MCP-change rebuild below does.
            # self._model_id is what the `model_id` query returns (the think
            # activity resolves its model through it) and what continue-as-new
            # carries, so both stay in step automatically.
            if turn.model_id and turn.model_id != self._model_id:
                self._model_id = turn.model_id
                agent = self._agent = self._build_agent(list(agent.messages))

            # Where this turn's stream frames begin. Everything before it
            # belongs to turns already delivered and is dead weight: the
            # stream log only ever grows (_stream.py appends, never trims),
            # and get_state() serializes the WHOLE log into the
            # continue-as-new payload. One 54k-character answer produced
            # ~12,000 delta entries / 886 KB, which tripped Temporal's
            # payload limit and put the workflow task in a permanent retry
            # loop. The SDK's continue_as_new note prescribes exactly this:
            # combine it with truncate() "to keep the carried log itself
            # small".
            turn_start_offset = self._stream_offset()
            self._turn_start_offset = turn_start_offset

            # New turn: the think-first hook runs once for this turn's fresh
            # user prompt, and never again for HITL interrupt resumes below.
            if self._think_hook is not None:
                self._think_hook.mark_turn_start()

            blocks = self._content_blocks(turn)

            # EventLoopException is Strands' wrapper for a model/tool activity
            # that exhausted its retries (strands/event_loop/event_loop.py:396).
            # It is a plain Exception, and per Temporal's update semantics a
            # non-FailureError raised from an update handler is a Workflow TASK
            # failure -- the server retries the task forever, replaying the
            # same doomed turn while the caller's stream stays silent
            # (docs.temporal.io/handling-messages, "Exceptions in Updates").
            # Re-raising as ApplicationError fails only the UPDATE: server.py
            # surfaces the error to the caller and the session keeps running,
            # matching how a failed model activity already reaches the caller
            # (tests/test_workflow.py::test_failed_model_activity_surfaces_to_the_caller).
            try:
                result = await agent.invoke_async(blocks)
            except EventLoopException as error:
                raise ApplicationError(
                    f"Turn failed: {error.__cause__ or error}",
                    type="TurnFailed",
                    non_retryable=True,
                ) from error

            # HITL resume loop. The agent never self-resumes: every interrupt
            # must be answered and the complete list passed back (guide R9).
            # One approval is collected per interrupt -- broadcasting a single
            # answer would approve things the human was never shown.
            while result.stop_reason == "interrupt":
                interrupts = list(result.interrupts or [])
                responses: list[InterruptResponseContent] = []
                for pending in interrupts:
                    self._pending_reason = pending.reason
                    self._approvals.publish({"reason": pending.reason})
                    await workflow.wait_condition(
                        lambda: self._approval is not None
                    )
                    responses.append(
                        {
                            "interruptResponse": {
                                "interruptId": pending.id,
                                "response": self._approval,
                            }
                        }
                    )
                    self._approval = None
                self._pending_reason = None
                # Clear the prompt so the UI's reconciled data-approval part
                # stops showing an answered question.
                self._approvals.publish({"reason": None})
                result = await agent.invoke_async(responses)

            # A load_tools this turn recorded a new extra MCP server. Only
            # TemporalAgent's constructor installs the Temporal refresh for
            # TemporalMCPClient providers, so re-construct the agent with the
            # provider in tools=[...] (Temporal Strands README, MCP).
            if tuple(self._extra_mcp_servers) != self._agent_extra_mcp:
                agent = self._agent = self._build_agent(list(agent.messages))

            # Drop the previous turns' frames now that this turn's reply is
            # ready. Truncating only up to THIS turn's start leaves every
            # frame the current subscriber is still reading intact -- a
            # subscriber polling from a truncated offset gets an
            # ApplicationError, so the boundary has to be the live turn.
            self._stream.truncate(turn_start_offset)
            self._turn_start_offset = None

            # Counted only when the reply is ready: the rollover trigger in
            # run() must never hand off mid-turn.
            self._completed_turns += 1
            return str(result).strip()

    @turn.validator
    def _validate_turn(self, turn: TurnInput) -> None:
        """Reject new turns once the run is closing.

        A validator rejection is not written to history and reaches the caller
        as an update failure, so server.py can surface it rather than the turn
        hanging until the successor run picks it up.
        """
        if self._closing:
            raise ApplicationError(
                "Session is rolling over; retry this turn.",
                type="SessionRollingOver",
            )

    @workflow.signal
    def approve(self, response: str) -> None:
        """Answer the approval the turn loop is currently waiting on.

        Ignored when nothing is pending: an approval banked while no gate is
        open would silently pre-approve the NEXT gated tool call, which the
        human was never shown. Signals cannot be rejected (they have no
        validator and no reply channel), so dropping the stale answer is the
        whole guard.
        """
        if self._pending_reason is None:
            return
        self._approval = response

    @workflow.signal
    def take_control(self) -> None:
        """User took the browser from the Computer Use preview."""
        self._human_control[0] = True
        self._handoff_requested[0] = True
        self._handoffs.publish(
            {
                "active": True,
                "reason": "User took control of the browser session",
            }
        )

    @workflow.signal
    def give_control(self, message: str) -> None:
        """User returned control with instructions for the agent."""
        self._human_control[0] = False
        self._handoff_requested[0] = False
        self._handoffs.publish({"active": False, "reason": None})
        text = message.strip()
        if self._pending_reason is not None and text:
            self._approval = text

    @workflow.signal
    def end_chat(self) -> None:
        """Idempotent: repeated ends simply leave the flag set."""
        self._done = True

    @workflow.query
    def messages(self) -> Messages:
        return list(self._agent.messages) if self._agent else []

    @workflow.query
    def pending_approval(self) -> str | None:
        return self._pending_reason

    @workflow.query
    def model_id(self) -> str:
        return self._model_id

    @workflow.query
    def session_id(self) -> str:
        return self._session_id

    @workflow.query
    def turn_start_offset(self) -> int | None:
        """Stream offset where the in-flight turn began, or None if idle."""
        return self._turn_start_offset

    @workflow.run
    async def run(self, input: ChatInput) -> None:
        # Rebuilt here, not in __init__, so a continued run resumes from the
        # messages the previous run carried over (guide Pattern 9).
        self._agent = self._build_agent(input.messages)

        # The test-only threshold ORs with the SDK's own suggestion, never
        # replaces it: production leaves rollover_turns at None and rolls over
        # exactly when Temporal says history is getting large.
        def should_rollover() -> bool:
            if workflow.info().is_continue_as_new_suggested():
                return True
            return (
                self._rollover_turns is not None
                and self._completed_turns >= self._rollover_turns
            )

        await workflow.wait_condition(lambda: self._done or should_rollover())

        # Closed to new turns from here on. all_handlers_finished waits for
        # the turn currently running, but does nothing to stop a fresh update
        # being accepted during that wait -- under sustained load each new
        # turn would re-extend the wait and starve the rollover forever. The
        # validator below rejects new turns once this is set, so the wait can
        # actually converge.
        self._closing = True

        if self._done:
            # Order matters: detach BEFORE waiting on all_handlers_finished.
            # The stream's own long-poll is an update handler, so a subscriber
            # parked in it keeps all_handlers_finished false forever and run()
            # never returns. detach_pollers() releases in-flight polls and
            # rejects new ones at the validator, which lets the wait below
            # settle. The SDK docstring on detach_pollers states this ordering
            # explicitly.
            self._stream.detach_pollers()
            # Let in-flight turn updates finish, or their replies are lost.
            await workflow.wait_condition(workflow.all_handlers_finished)
            return

        agent = self._agent
        messages: Messages = _clamp_tool_results(
            list(agent.messages) if agent else []
        )
        # This helper detaches pollers, drains handlers, captures stream state,
        # and calls workflow.continue_as_new. It raises internally: nothing
        # after it runs.
        await self._stream.continue_as_new(
            lambda state: [
                ChatInput(
                    model_id=self._model_id,
                    system_prompt=self._system_prompt,
                    session_id=self._session_id,
                    messages=messages,
                    stream_state=state,
                    # The test threshold is session state: a rollover must not
                    # silently disable the threshold that triggered it. The
                    # successor's turn counter starts at zero, so a threshold
                    # of N means N turns per run, not N turns per session.
                    rollover_turns=self._rollover_turns,
                    loaded_tools=list(self._loaded_tools),
                    extra_mcp_servers=list(self._extra_mcp_servers),
                    connected_mcp_servers=list(self._connected_mcp_servers),
                )
            ]
        )
