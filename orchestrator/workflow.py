"""Main orchestrator: a Strands Agent running as a durable Temporal workflow.

Follows Temporal's official Strands Agents integration guide exactly:
https://docs.temporal.io/develop/python/integrations/strands-agents

Two things this file is built to guarantee, both straight from that guide
and the installed temporalio SDK source (not invented):

1. Long-horizon sessions without context-window loss. This is the guide's
   own "Handle long-running chat sessions" pattern verbatim: each user turn
   arrives as a Workflow Update (`turn`), so the caller gets the reply back
   from the same call, and the *same* logical session (one stable workflow
   ID) keeps running indefinitely. When Temporal signals that history is
   getting large (`is_continue_as_new_suggested()`), the workflow drains any
   in-flight updates and calls `continue_as_new`, carrying the agent's
   accumulated `messages` forward as input to a fresh execution — the
   conversation's context survives, only the workflow *history* resets.
   There is no length cap on how long a session can run.

   The agent is also built with Strands' `NullConversationManager` rather
   than the default `SlidingWindowConversationManager` (confirmed default:
   window_size=40), because the default silently drops the oldest messages
   once that window fills — which would be exactly the context loss this
   guarantee rules out. Trimming history is Temporal's job here, via the
   continue_as_new hand-off, not the conversation manager's.

2. No hanging. Every model-call activity gets an explicit, bounded
   RetryPolicy (transient failures — rate limits, brief network blips — get
   retried with backoff instead of failing the turn outright), an explicit
   heartbeat_timeout (which activates the SDK's own `auto_heartbeater` on
   the model activities — confirmed in
   temporalio/contrib/strands/_model_activity.py — so a genuinely-dead
   worker process is detected in seconds, not after the full timeout), and
   a schedule_to_close_timeout as a hard ceiling on retries so a truly-down
   upstream still surfaces as an error within a bounded time rather than
   retrying forever (which would look like hanging from the caller's side).
"""

import asyncio
import base64
import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from strands.agent.conversation_manager import NullConversationManager
from strands.hooks import HookProvider, HookRegistry
from strands.types.interrupt import InterruptResponseContent
from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent
from strands.types.agent import Limits
from strands.types.content import ContentBlock, Messages
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.contrib.strands import TemporalAgent, TemporalMCPClient
from temporalio.contrib.workflow_streams import WorkflowStream, WorkflowStreamState

from mcp_config import (
    MCP_ENABLED,
    MCP_RETRY_POLICY,
    MCP_SCHEDULE_TO_CLOSE_TIMEOUT,
    MCP_SERVER_NAME,
    MCP_START_TO_CLOSE_TIMEOUT,
)
from perplexity_tools import AGENT_API_FUNCTION_TOOLS, make_create_agent_response_tool
from think_tool import make_think_tool

DEFAULT_MODEL = "openai/gpt-5.6-sol"

# The agent's identity, read from agent.json — Strands' AgentConfig key names
# (name / prompt), renamed to the Agent constructor's own parameter names.
# TemporalAgent forwards **agent_kwargs straight to Agent.__init__, so these
# go in as ordinary constructor arguments and the same file drives both
# ChatWorkflow and CompareWorkflow.
#
# strands.experimental.config_to_agent is deliberately NOT used to load this.
# It returns a plain strands.Agent, which loses the two things TemporalAgent
# adds on top of the model wiring: the BeforeModelCallEvent hook that lists a
# TemporalMCPClient's tools (its own load_tools() returns [] at construction —
# see _temporal_mcp_client.py — so without TemporalAgent, MCP tools never load
# at all), and the disabled take_snapshot/load_snapshot. Going through
# config_to_agent also forces an import of the private
# temporalio.contrib.strands._temporal_model. agent.json carries only name and
# prompt, so config_to_agent contributed nothing but those losses.
#
# Loaded at import (module scope, outside the sandbox's per-replay path) so
# the workflow's configuration is identical on every replay.
_CONFIG_PATH = Path(__file__).with_name("agent.json")
AGENT_CONFIG: dict[str, Any] = json.loads(_CONFIG_PATH.read_text())
AGENT_KWARGS: dict[str, Any] = {}
if AGENT_CONFIG.get("name"):
    AGENT_KWARGS["name"] = AGENT_CONFIG["name"]
if AGENT_CONFIG.get("prompt"):
    AGENT_KWARGS["system_prompt"] = AGENT_CONFIG["prompt"]

# --- Timeouts -----------------------------------------------------------
# start_to_close: ceiling on a SINGLE attempt of one model call. Generous,
# since "long horizon tasks of any kind" includes models doing deep
# reasoning or multi-step tool use within one turn.
MODEL_START_TO_CLOSE_TIMEOUT = timedelta(minutes=10)
# schedule_to_close: ceiling on a model call's ENTIRE lifetime, including
# every retry. This is what actually prevents "hanging" — without it, a
# persistently-failing call could retry forever and never resolve.
MODEL_SCHEDULE_TO_CLOSE_TIMEOUT = timedelta(minutes=30)
# heartbeat: must be set for auto_heartbeater (built into the SDK's model
# activities) to activate at all. Kept well below start_to_close so a dead
# worker process is detected in seconds rather than after the full timeout.
MODEL_HEARTBEAT_TIMEOUT = timedelta(seconds=30)

# --- Retries --------------------------------------------------------------
# Bounded exponential backoff: enough attempts to ride out rate limits and
# brief network blips, capped so a truly-broken upstream fails within
# MODEL_SCHEDULE_TO_CLOSE_TIMEOUT instead of retrying silently forever.
MODEL_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
)


# Per-turn budget, enforced by Strands itself. `Limits` is the framework's own
# per-invocation cap (strands/types/agent.py): checked at the top of each loop
# iteration, so tools from the previous turn always finish and agent.messages
# stays reinvokable, and the turn ends with a clean stop_reason ("limit_turns")
# instead of an exception.
#
# Set to 100 — the same ceiling the Agent API itself allows for max_steps, so
# the agent gets the maximum the platform permits rather than a conservative
# client-chosen number. This is a runaway backstop only (a malformed tool call
# once made the agent repeat the identical mistake 315+ times in one turn;
# Strands has no default cap and Temporal's timeouts don't catch it, since
# every individual model call succeeds). It is not a budget the agent is
# expected to work within.
#
# An earlier revision of this codebase did the same job with a hand-written
# BeforeModelCallEvent hook. That predated Limits; the framework covers it now,
# so the hook is gone.
TURN_LIMITS: Limits = {"turns": 100}

# Tools that must not run until a human says so. Everything else runs freely.
GATED_TOOLS = {"cancel_agent_response"}


def _turn_content(input: "TurnInput") -> list[ContentBlock]:
    """One turn as Strands ContentBlocks — the `list[ContentBlock]` form
    `Agent.invoke_async` documents for multi-modal input.

    Images go in via ImageSource's `location`, not its `bytes`. Strands types
    `Location` as "a generic location for a document... its usage is
    determined by the underlying model provider" (strands/types/media.py), so
    a data URL under a provider-specific `type` is what that field is for, and
    PerplexityModel reads it back out as Perplexity's own `input_image` part.

    Raw `bytes` is not an option here, and that is a hard constraint rather
    than a preference: the agent's messages cross the workflow/activity
    boundary on every model call, and StrandsPlugin's pydantic data converter
    serializes payloads with pydantic_core.to_json, which encodes bytes as
    UTF-8 and therefore fails outright on binary image data
    (PydanticSerializationError: invalid utf-8 sequence). Confirmed by running
    it. A JSON-native string crosses that boundary cleanly.
    """
    content: list[ContentBlock] = []
    if input.prompt:
        content.append({"text": input.prompt})
    for image in input.images:
        content.append(
            {
                "image": {
                    "format": image.format,  # type: ignore[typeddict-item]
                    "source": {
                        "location": {
                            "type": "url",
                            "uri": f"data:image/{image.format};base64,{image.data}",
                        }
                    },
                }
            }
        )
    return content


class ApprovalHook(HookProvider):
    """Pauses the agent before a gated tool runs, per the plugin guide's
    "Hook-based interrupts". `event.interrupt()` raises the first time and
    returns the human's answer when the turn resumes, so no approval
    bookkeeping is needed — and a denial cancels the tool instead of
    running it."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._gate)

    def _gate(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name not in GATED_TOOLS:
            return
        answer = event.interrupt("approval", reason=f"Run {name}?")
        if answer != "approve":
            event.cancel_tool = f"{name} was denied by the user."


class ToolResultStreamer(HookProvider):
    """Publishes each tool's output to a stream topic as it completes.

    Subscribes to Strands' shipped `AfterToolCallEvent` — the documented hook
    for exactly this, and the only place tool OUTPUT is exposed. It is not
    reachable any other way: `streaming_topic="events"` is published from
    inside the model activity and tools don't run there, and `stream_async`
    filters to callback events only, which `ToolResultEvent` is explicitly
    not (`is_callback_event` returns False). Without this a tool card opens
    on tool-input-available and never completes.

    Registering a callback on a shipped event is the Strands plugin README's
    own example, and the callback is deterministic — it just appends to the
    stream's in-memory log — so it is safe to run in workflow context.
    """

    def __init__(self, topic: Any) -> None:
        self._topic = topic

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._on_tool_result)

    def _on_tool_result(self, event: AfterToolCallEvent) -> None:
        result = event.result
        self._topic.publish(
            {
                "tool_use_id": result["toolUseId"],
                "status": result.get("status", "success"),
                # ToolResult content blocks are already JSON-serializable
                # ({"text": ...} / {"json": ...}) — passed through untouched
                # so the client decides how to render them.
                "content": result.get("content", []),
            }
        )


@dataclass
class TurnImage:
    """One image attached to a turn. `data` is the raw image bytes,
    base64-encoded, so the turn input stays JSON-serializable across the
    update boundary. `format` is a Strands ImageFormat ("png", "jpeg",
    "gif", "webp")."""

    format: str
    data: str


@dataclass
class TurnInput:
    """One user turn: text plus any images attached to it. A dataclass rather
    than a bare string because Workflow Updates take a single argument, and
    attachments have to travel with the prompt they belong to — the composer
    accepts image attachments and they were previously dropped on the floor
    between the browser and the agent."""

    prompt: str
    images: list[TurnImage] = field(default_factory=list)


@dataclass
class ChatInput:
    """Workflow input. `model_id` is chosen once, at session start — Strands
    model providers can't be reconfigured after a TemporalAgent is built
    (confirmed: TemporalModel.update_config() is a no-op in the SDK source),
    so switching models mid-session isn't supported here. `messages` carries
    prior turns forward across a continue_as_new hand-off."""

    model_id: Optional[str] = None
    messages: Messages = field(default_factory=list)
    stream_state: Optional[WorkflowStreamState] = None


@workflow.defn
class ChatWorkflow:
    """One long-running chat session per workflow execution (chained via
    continue_as_new). Send turns with `client.execute_update`, end the
    session with the `end_chat` signal, inspect history with the `messages`
    query — see orchestrator/README.md for exact client-side calls."""

    @workflow.init
    def __init__(self, input: ChatInput) -> None:
        self._done = False
        self._lock = asyncio.Lock()
        self._agent: Optional[Any] = None
        # WorkflowStream must be constructed here, not in @workflow.run — it
        # has to register its signal/update/query handlers before the first
        # publish can arrive (confirmed in the Workflow Streams guide).
        # prior_state carries stream offsets across continue_as_new so the
        # bridge's subscription doesn't lose or duplicate events at the
        # rollover point.
        self.stream = WorkflowStream(prior_state=input.stream_state)
        # Each cycle of the agent's `think` tool, streamed live as it
        # completes — raw text, see the tool wrapper in run() below and
        # think.py / thinking_activity.py.
        self.thinking_topic = self.stream.topic("thinking")
        # Tool OUTPUT, which the model's own event stream never carries:
        # `streaming_topic="events"` is published from inside the model
        # activity, and tools don't run there, so a tool card would otherwise
        # reach "input available" and stop. Filled by ToolResultStreamer off
        # the shipped AfterToolCallEvent hook.
        self.tool_results_topic = self.stream.topic("tool_results")
        # Sub-agent runs (create_agent_response), streamed live as they work
        # instead of appearing only once the job finished. See
        # perplexity_tools.create_agent_response.
        self.subagent_topic = self.stream.topic("subagent")
        # Human-in-the-loop prompts, PUSHED when a turn parks on a gated tool
        # and again when it resumes. The client used to discover this by
        # polling a query once a second for the whole duration of a streaming
        # turn — thousands of requests per turn once sub-agent runs started
        # taking minutes. The session already has a stream; approval belongs
        # on it like everything else.
        self.approval_topic = self.stream.topic("approval")
        self._model_id = input.model_id or DEFAULT_MODEL
        # Human-in-the-loop, per the integration guide's
        # strands_plugin/human_in_the_loop/workflow.py.
        self._approval: Optional[str] = None
        self._pending_reason: Optional[str] = None

    @workflow.update
    async def turn(self, input: TurnInput) -> str:
        await workflow.wait_condition(lambda: self._agent is not None)
        async with self._lock:
            assert self._agent is not None
            result = await self._agent.invoke_async(
                _turn_content(input), limits=TURN_LIMITS
            )
            while result.stop_reason == "interrupt":
                interrupts = list(result.interrupts or [])
                self._pending_reason = interrupts[0].reason if interrupts else None
                self.approval_topic.publish({"reason": self._pending_reason})
                await workflow.wait_condition(lambda: self._approval is not None)
                response = self._approval
                self._approval = None
                self._pending_reason = None
                # Clears the prompt on the client the moment the answer lands.
                self.approval_topic.publish({"reason": None})
                responses: list[InterruptResponseContent] = [
                    {"interruptResponse": {"interruptId": i.id, "response": response}}
                    for i in interrupts
                ]
                result = await self._agent.invoke_async(responses, limits=TURN_LIMITS)
            return str(result).strip()

    @workflow.signal
    def approve(self, response: str) -> None:
        self._approval = response

    @workflow.signal
    def end_chat(self) -> None:
        self._done = True

    @workflow.query
    def pending_approval(self) -> Optional[str]:
        return self._pending_reason

    @workflow.query
    def messages(self) -> Messages:
        return list(self._agent.messages) if self._agent else []

    @workflow.run
    async def run(self, input: ChatInput) -> None:
        model_id = self._model_id
        thinking_topic_name = self.thinking_topic.name

        # The agent-callable `think` tool, shared with CompareWorkflow so
        # both surfaces expose the same agent — see think_tool.py.
        think = make_think_tool(
            model_id,
            thinking_topic_name,
            start_to_close_timeout=MODEL_START_TO_CLOSE_TIMEOUT,
            schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE_TIMEOUT,
            heartbeat_timeout=MODEL_HEARTBEAT_TIMEOUT,
            retry_policy=MODEL_RETRY_POLICY,
        )

        # Every tool on the configured MCP server, reached through Temporal
        # activities rather than a direct connection from workflow code: the
        # plugin registers {name}-list-tools / {name}-call-tool activities on
        # the worker, and this handle carries the options they run under. Left
        # on the default cache_tools=False so a server that gains, loses, or
        # renames tools mid-session is picked up on the next turn instead of
        # the agent holding a stale schema for the life of the session — the
        # right trade for sessions that run for days. Empty list when no MCP
        # server is configured; see mcp_config.py for why that is decided at
        # import rather than here.
        mcp_tools = (
            [
                TemporalMCPClient(
                    server=MCP_SERVER_NAME,
                    start_to_close_timeout=MCP_START_TO_CLOSE_TIMEOUT,
                    schedule_to_close_timeout=MCP_SCHEDULE_TO_CLOSE_TIMEOUT,
                    retry_policy=MCP_RETRY_POLICY,
                )
            ]
            if MCP_ENABLED
            else []
        )

        # TemporalAgent is the integration's documented entry point: it builds
        # the TemporalModel internally from these activity options, so every
        # model call runs as a Temporal activity under this module's timeouts
        # and retry policy, and it registers the BeforeModelCallEvent hook that
        # makes TemporalMCPClient's tools actually load. Everything else is
        # forwarded verbatim to Agent.__init__.
        self._agent = TemporalAgent(
            # A registered factory NAME (run_worker.py's models= mapping), not
            # a live Model object — that is what makes dynamic model selection
            # work at all; a live object isn't serializable across the
            # activity boundary.
            model=self._model_id,
            start_to_close_timeout=MODEL_START_TO_CLOSE_TIMEOUT,
            schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE_TIMEOUT,
            heartbeat_timeout=MODEL_HEARTBEAT_TIMEOUT,
            retry_policy=MODEL_RETRY_POLICY,
            # Publishes every raw StreamEvent (text, reasoning, tool-use
            # deltas) to the "events" topic as the turn runs — the SDK's
            # own documented streaming mechanism. Subscribed in server.py.
            streaming_topic="events",
            **AGENT_KWARGS,
            messages=list(input.messages),
            # The rest of the Agent API (background jobs, skills, presets,
            # wide-research, model fallback chains) as tools the agent can
            # call on itself — see perplexity_tools.py — plus the `think`
            # tool above and every tool on the configured MCP server.
            tools=[
                *AGENT_API_FUNCTION_TOOLS,
                make_create_agent_response_tool(self.subagent_topic.name),
                think,
                *mcp_tools,
            ],
            # Strands ships three ConversationManagers (each is a real
            # HookProvider registered by the Agent itself). The default,
            # SlidingWindowConversationManager, DROPS the oldest messages once
            # its window fills — the exact context loss guarantee (1) in this
            # module's docstring rules out. NullConversationManager never
            # truncates; history is Temporal's job, via continue_as_new.
            conversation_manager=NullConversationManager(),
            # Completes the tool cards the "events" topic can only open —
            # see ToolResultStreamer for why no other mechanism exposes this.
            hooks=[ApprovalHook(), ToolResultStreamer(self.tool_results_topic)],
        )

        await workflow.wait_condition(
            lambda: self._done or workflow.info().is_continue_as_new_suggested()
        )

        if self._done:
            # Ending the session cleanly. The continue_as_new path below
            # detaches the stream's pollers for us, but this one has to do it
            # explicitly: returning from run() with subscribers still parked
            # in __temporal_workflow_stream_poll leaves those update handlers
            # unfinished, which Temporal warns about (TMPRL1102) and which
            # hands every live subscriber — including server.py's SSE pump —
            # an "execution already completed" RPC error instead of a clean
            # end-of-stream. detach_pollers releases them with their current
            # batch so they can stop on their own terms; the wait then lets
            # those releases actually land before the workflow completes.
            self.stream.detach_pollers()
            await workflow.wait_condition(workflow.all_handlers_finished)
            return

        # stream.continue_as_new is the documented streaming replacement for
        # the detach_pollers() -> wait_condition(all_handlers_finished) ->
        # workflow.continue_as_new() recipe (see its SDK docstring): it
        # detaches the stream's subscriber pollers FIRST, THEN drains
        # in-flight handlers, then continues-as-new, carrying stream offsets
        # forward via stream_state. Doing our own all_handlers_finished wait
        # before this would hang, since those pollers only release once
        # detach sets their _detaching flag.
        await self.stream.continue_as_new(
            lambda stream_state: [
                ChatInput(
                    model_id=input.model_id,
                    messages=self._agent.messages,
                    stream_state=stream_state,
                )
            ]
        )
