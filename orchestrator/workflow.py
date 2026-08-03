"""Durable chat session workflow.

Composed from three patterns in the strands-temporal guide
(/Applications/strands-temporal/references/strands_temporal_agent_guide.md),
verified against temporalio 1.31.0 / strands-agents 1.50.2:

- Pattern 9 (continue-as-new): each turn is a ``@workflow.update`` so the caller
  gets its reply from the same call, and the agent is rebuilt inside ``run`` from
  carried messages. This is the one documented exception to "build the agent in
  ``__init__``" (guide R7).
- Pattern 3 (HITL, hook style): ``BeforeToolCallEvent.interrupt(name, reason=...)``
  pauses before a gated tool; ``approve`` signals the answer back. Every interrupt
  in ``result.interrupts`` is answered and the full list handed back (guide R9).
- Pattern 8 (streaming): ``WorkflowStream`` hosts the topic named by
  ``TemporalAgent(streaming_topic=...)``; ``server.py`` subscribes to the same
  name. The names must match exactly (guide R10).

The stream topics are this repository's own SSE protocol, consumed by the
protected route ``app/api/orchestrator/route.ts``:

- ``events``      -- raw Strands ``StreamEvent`` dicts published by the model
                     activity itself (``_model_activity.invoke_model_streaming``).
- ``approval``    -- ``{"reason": str | None}``, published here when a hook
                     interrupt is pending and again with ``None`` once answered.
- ``tool_results`` -- ``{"tool_use_id", "status", "content"}``, published from an
  ``AfterToolCallEvent`` hook.
- ``thinking``    -- ``{"data": str, "cycle": int}``, published from the think
  activity as each cycle completes. The route renders these into the Chain of
  Thought component; the cycle number only keys the UI part and is never shown.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
from dataclasses import dataclass, field
from typing import Any

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent
from strands.types.content import Messages
from strands.types.interrupt import InterruptResponseContent
from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.contrib.strands import TemporalAgent, TemporalMCPClient
from temporalio.contrib.workflow_streams import WorkflowStream, WorkflowStreamState

from config import (
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
    MODEL_SCHEDULE_TO_CLOSE,
    MODEL_START_TO_CLOSE,
)

# Topic names shared with server.py's subscriber and, through it, the SSE frames
# the protected Next.js route parses. Changing one of these without changing the
# route breaks the UI silently.
# The MCP servers registered on StrandsPlugin(mcp_clients=...) in run_worker.
# Referenced by name only: TemporalMCPClient is a pure handle (guide R11).
MCP_SERVERS = ("datacommons", "pophive")

EVENTS_TOPIC = "events"
THINKING_TOPIC = "thinking"

# The reasoning stage never addresses the user: its output is preparation the
# orchestrator reads, so the two read as one assistant.
THINK_SYSTEM_PROMPT = (
    "You are the reasoning stage of a single engineering assistant. You never "
    "address the user: your output is private preparation that the assistant "
    "reads before it answers. Work out what the user actually needs, look up "
    "anything that would make the final answer more accurate, and state your "
    "findings and plan plainly as working notes."
)
APPROVAL_TOPIC = "approval"
TOOL_RESULTS_TOPIC = "tool_results"

# Tools whose calls pause for human approval. Empty today: no tool this agent
# registers has side effects worth gating. The hook and the whole resume loop
# stay wired so adding a destructive tool is a one-line change here rather than
# a workflow rewrite -- and, because the set is empty, no turn can currently
# block waiting for an approval that the UI would have to answer.
APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset()


@dataclass
class TurnImage:
    """An image attached to a user turn, already split by the Next.js route.

    ``format`` is one of png/jpeg/gif/webp and ``data`` is bare base64 with the
    ``data:`` URL prefix stripped -- exactly what ``route.ts`` sends.
    """

    format: str
    data: str


@dataclass
class TurnInput:
    """One user turn: prompt text plus any image attachments."""

    prompt: str
    images: list[TurnImage] = field(default_factory=list)


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


# Image formats the Perplexity Agent API accepts, mirroring the IMAGE_FORMATS
# map in app/api/orchestrator/route.ts. Anything else is dropped rather than
# sent, because PerplexityModel._image_part would resolve it to
# application/octet-stream and the API would reject the whole request.
_SUPPORTED_IMAGE_FORMATS = frozenset({"png", "jpeg", "gif", "webp"})


class _ApprovalHook(HookProvider):
    """Pauses before a gated tool call and reports the reason to the workflow.

    Guide Pattern 3: the callback runs in workflow context, so it must stay
    deterministic -- pure state reads and ``event.interrupt``, no I/O.
    """

    def __init__(self, gated: frozenset[str]) -> None:
        self._gated = gated

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:
        registry.add_callback(BeforeToolCallEvent, self._gate)

    def _gate(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name not in self._gated:
            return
        approval = event.interrupt("approval", reason=f"Approve {name}?")
        if approval != "approve":
            event.cancel_tool = "denied"


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


@workflow.defn
class ChatWorkflow:
    """One durable chat session. Its id is the session id the UI holds."""

    @workflow.init
    def __init__(self, input: ChatInput) -> None:
        # WorkflowStream must be constructed directly from a method named
        # __init__ -- it inspects the caller's frame and raises otherwise.
        self._stream = WorkflowStream(prior_state=input.stream_state)
        self._approvals = self._stream.topic(APPROVAL_TOPIC)
        self._tool_results = self._stream.topic(TOOL_RESULTS_TOPIC)

        self._model_id = input.model_id
        self._system_prompt = input.system_prompt
        self._session_id = input.session_id

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
        self._pending_reason: str | None = None

    def _stream_offset(self) -> int:
        """Current global stream offset: base_offset + log length.

        Built from the public ``get_state()`` snapshot, which exposes both
        fields. It is the same value ``server.py`` reads through
        ``get_offset()`` before subscribing, and the unit ``truncate()``
        expects.
        """
        state = self._stream.get_state()
        return state.base_offset + len(state.log)

    def _mcp_tools(self) -> list[TemporalMCPClient]:
        """Handles for the plugin-registered MCP servers.

        The plugin caches each manifest at worker startup, so the tool
        definitions never ride along in a model request or its stream.
        """
        return [
            TemporalMCPClient(
                server=name,
                # Defaults to False, which re-lists every tool on every turn
                # and writes the whole manifest into history each time. The
                # Temporal AI cookbook's Strands recipe sets it True.
                cache_tools=True,
                start_to_close_timeout=MODEL_START_TO_CLOSE,
                retry_policy=MODEL_RETRY_POLICY,
            )
            for name in MCP_SERVERS
        ]

    def _build_agent(self, messages: Messages) -> TemporalAgent:
        return TemporalAgent(
            # A registered factory NAME from run_worker.py's models= mapping,
            # never a Model instance (guide R1).
            model=self._model_id,
            start_to_close_timeout=MODEL_START_TO_CLOSE,
            schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE,
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
            tools=self._mcp_tools(),
            hooks=[
                _ApprovalHook(APPROVAL_REQUIRED_TOOLS),
                _ToolResultHook(self._tool_results.publish),
            ],
        )

    def _build_thinker(self) -> TemporalAgent:
        """The reasoning stage: same model, its own stream topic.

        streaming_topic makes the SDK publish every StreamEvent it produces --
        native tool frames, reasoning, text -- from inside the model activity,
        exactly as it does for the orchestrator. Nothing is republished by hand.
        """
        return TemporalAgent(
            model=self._model_id,
            start_to_close_timeout=MODEL_START_TO_CLOSE,
            schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE,
            heartbeat_timeout=MODEL_HEARTBEAT,
            retry_policy=MODEL_RETRY_POLICY,
            # Temporal's documented value for LLM streaming; see the note on
            # the orchestrator agent above.
            streaming_batch_interval=timedelta(milliseconds=200),
            streaming_topic=THINKING_TOPIC,
            system_prompt=THINK_SYSTEM_PROMPT,
            tools=self._mcp_tools(),
        )

    def _content_blocks(self, turn: TurnInput) -> list[dict[str, Any]]:
        """Strands ContentBlocks for one turn.

        Returns a plain prompt string's block list; images become ``image``
        blocks with raw bytes, which is what PerplexityModel._image_part reads.
        """
        blocks: list[dict[str, Any]] = []
        if turn.prompt:
            blocks.append({"text": turn.prompt})
        for image in turn.images:
            if image.format not in _SUPPORTED_IMAGE_FORMATS:
                continue
            # b64decode is pure computation, deterministic under replay.
            blocks.append(
                {
                    "image": {
                        "format": image.format,
                        "source": {"bytes": base64.b64decode(image.data)},
                    }
                }
            )
        return blocks

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

            blocks = self._content_blocks(turn)

            # Sequential reasoning stage: it researches and plans, finishes,
            # and its analysis is prepended to the turn as the assistant's own
            # prior reasoning. The orchestrator then answers from that plan
            # plus the user's prompt. Its own activity streams live on
            # THINKING_TOPIC while it works.
            analysis = str(await self._build_thinker().invoke_async(blocks)).strip()
            if analysis:
                blocks = [
                    {"text": f"My prior analysis:\n{analysis}"},
                    *blocks,
                ]

            result = await agent.invoke_async(blocks)

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

            # Drop the previous turns' frames now that this turn's reply is
            # ready. Truncating only up to THIS turn's start leaves every
            # frame the current subscriber is still reading intact -- a
            # subscriber polling from a truncated offset gets an
            # ApplicationError, so the boundary has to be the live turn.
            self._stream.truncate(turn_start_offset)
            self._turn_start_offset = None

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
        self._approval = response

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
    def turn_start_offset(self) -> int | None:
        """Stream offset where the in-flight turn began, or None if idle."""
        return self._turn_start_offset

    @workflow.run
    async def run(self, input: ChatInput) -> None:
        # Rebuilt here, not in __init__, so a continued run resumes from the
        # messages the previous run carried over (guide Pattern 9).
        self._agent = self._build_agent(input.messages)

        await workflow.wait_condition(
            lambda: self._done or workflow.info().is_continue_as_new_suggested()
        )

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
        messages: Messages = list(agent.messages) if agent else []
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
                )
            ]
        )
