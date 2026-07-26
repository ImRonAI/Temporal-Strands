"""HTTP bridge between the Next.js chat UI and the orchestrator's durable,
long-horizon ChatWorkflow sessions.

Run alongside `run_worker.py` (which must be connected to a live Temporal
server — `temporal server start-dev`). See README.md for the full local
run sequence.

One Temporal workflow execution (chained forward via continue_as_new inside
ChatWorkflow itself) backs one chat session for its entire lifetime, however
long that is — turns are Workflow Updates against a stable workflow ID, so a
session can run indefinitely without losing conversation context.
"""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import perplexity as perplexity_sdk
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from temporalio.client import Client, WorkflowUpdateStage
from temporalio.contrib.strands import StrandsPlugin
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.service import RPCError, RPCStatusCode
from typing_extensions import Literal

from compare_workflow import CompareInput, CompareWorkflow, topic_for
from telemetry import telemetry_plugins
from workflow import ChatInput, ChatWorkflow, TurnImage, TurnInput

# Load the repo-root .env.local (PERPLEXITY_API_KEY, optional MCP_* / OTEL_*)
# so this process works whether it is launched directly or by `pnpm dev`.
# python-dotenv is already a declared dependency. Real environment variables
# win — override=False — so an explicitly exported key is never clobbered.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local", override=False)

TASK_QUEUE = "perplexity-orchestrator"

_client: Optional[Client] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _client
    # StrandsPlugin has to be on THIS client too, not just the worker's — it
    # installs the data converter (pydantic_data_converter) and the
    # StrandsFailureConverter, and both ends of a workflow must agree on
    # them. The plugin README says so directly: "Attach StrandsPlugin to the
    # client (not just the worker)". Without it this process would encode
    # ChatInput and decode update results / streamed payloads with the
    # default converter while the worker used the pydantic one.
    #
    # No models= mapping here on purpose: model factories are only ever
    # invoked inside the worker's model activity, so the client side has no
    # use for them (and building them would mean a needless GET /v1/models
    # on every API-server boot). Client-side, the plugin is purely converter
    # configuration.
    # telemetry_plugins() is empty unless OTEL_EXPORTER_OTLP_ENDPOINT is set,
    # so tracing costs nothing when it isn't configured. See telemetry.py.
    _client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[*telemetry_plugins(), StrandsPlugin()],
    )
    yield


app = FastAPI(lifespan=lifespan)


def _require_client() -> Client:
    if _client is None:
        raise HTTPException(status_code=503, detail="Temporal client not connected")
    return _client


class StartSessionRequest(BaseModel):
    model_id: Optional[str] = None


class StartSessionResponse(BaseModel):
    session_id: str


@app.post("/sessions", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest) -> StartSessionResponse:
    client = _require_client()
    session_id = f"chat-{uuid.uuid4().hex}"
    await client.start_workflow(
        ChatWorkflow.run,
        ChatInput(model_id=body.model_id),
        id=session_id,
        task_queue=TASK_QUEUE,
    )
    return StartSessionResponse(session_id=session_id)


class TurnImageBody(BaseModel):
    format: Literal["png", "jpeg", "gif", "webp"] = Field(
        ..., description="Strands ImageFormat; Perplexity carries it as an input_image part."
    )
    data: str = Field(..., description="Base64-encoded image bytes (no data: prefix).")


class TurnRequest(BaseModel):
    prompt: str
    images: list[TurnImageBody] = Field(
        default_factory=list, description="Images attached to this turn."
    )


class TurnResponse(BaseModel):
    reply: str


def _turn_input(body: TurnRequest) -> TurnInput:
    return TurnInput(
        prompt=body.prompt,
        images=[TurnImage(format=i.format, data=i.data) for i in body.images],
    )


@app.post("/sessions/{session_id}/turns", response_model=TurnResponse)
async def send_turn(session_id: str, body: TurnRequest) -> TurnResponse:
    client = _require_client()
    handle = client.get_workflow_handle(session_id)
    try:
        reply = await handle.execute_update(ChatWorkflow.turn, _turn_input(body))
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as-is
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TurnResponse(reply=reply)


@app.post("/sessions/{session_id}/turns/stream")
async def send_turn_stream(session_id: str, body: TurnRequest) -> StreamingResponse:
    """Same turn as above, but forwards the agent's real-time activity as
    Server-Sent Events while the turn is still running, instead of only
    returning the final reply once it's done — three WorkflowStream topics
    from workflow.py, all forwarded here:

    - "events": raw model StreamEvents (text/reasoning/tool-use-input
      deltas), via TemporalAgent(streaming_topic="events") — the SDK's own
      documented mechanism, not a custom one (see the "Stream agent output
      to clients" section of Temporal's Strands Agents guide).
    - "thinking": each cycle of the agent's `think` tool (the vendored
      strands tool, think.py, run via thinking_activity.run_thinking_cycles
      when the agent calls it) — plain text per cycle, not a dataclass.
    - "subagent": live output from a create_agent_response sub-agent —
      {subagent_id, status|delta|reasoning|output_item|error}. Perplexity
      only exposes a background run's stream on the create call itself
      (retrieve has no stream option), so this is published from inside that
      activity as it runs.
    - "approval": {reason} when a turn parks on a gated tool, and
      {reason: null} when it resumes. Pushed, not polled — the client used to
      hit a query endpoint once a second for the whole turn.
    - "tool_results": each tool's OUTPUT as it completes, as
      {tool_use_id, status, content}. Published by ChatWorkflow's
      ToolResultStreamer off Strands' shipped AfterToolCallEvent hook —
      tools run outside the model activity, so the "events" topic never
      carries their results and a tool card would otherwise stall at
      "input available".

    Each SSE payload carries a "topic" field so the client can tell them
    apart without guessing from shape alone.
    """
    client = _require_client()
    handle = client.get_workflow_handle(session_id)
    stream_client = WorkflowStreamClient.create(client, workflow_id=session_id)

    try:
        # The stream is the session's whole history, not this turn's — a
        # subscription defaults to from_offset=0 and would replay every prior
        # turn's deltas before the current ones, so turn N's reply arrived
        # prefixed with turns 1..N-1 verbatim. Reading the offset BEFORE the
        # update starts marks exactly where this turn begins; nothing can
        # publish in between, since no turn is running yet.
        start_offset = await stream_client.get_offset()
        update_handle = await handle.start_update(
            ChatWorkflow.turn,
            _turn_input(body),
            wait_for_stage=WorkflowUpdateStage.ACCEPTED,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as-is
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def event_source():
        result_task = asyncio.ensure_future(update_handle.result())
        queue: "asyncio.Queue[dict]" = asyncio.Queue()

        # The subscription runs uninterrupted in its own task, draining
        # stream_client.subscribe()'s async generator with plain `async
        # for` and no repeated wait_for-cancellation on it — cancelling a
        # long-poll-based __anext__() call via wait_for timeouts, then
        # resuming the same generator on the next call, was found (by
        # direct testing) to wedge the subscription so it never yields
        # again. Consuming it into a plain asyncio.Queue instead, and
        # polling THAT with wait_for, avoids the problem entirely — a
        # Queue.get() cancellation is safe to retry indefinitely.
        async def pump():
            async for item in stream_client.subscribe(
                ["events", "thinking", "tool_results", "subagent", "approval"],
                from_offset=start_offset,
            ):
                if isinstance(item.data, dict):
                    payload = item.data
                elif isinstance(item.data, str):
                    # "thinking" topic — see think.py / thinking_activity.py,
                    # which publish each cycle's raw text directly, no
                    # wrapper dataclass.
                    payload = {"text": item.data}
                else:
                    payload = vars(item.data)
                await queue.put({"topic": item.topic, **payload})

        pump_task = asyncio.ensure_future(pump())

        try:
            while not result_task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.3)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    pass

            # Drain whatever arrived in the queue right around completion.
            while not queue.empty():
                yield f"data: {json.dumps(queue.get_nowait())}\n\n"

            try:
                reply = await result_task
                yield f"data: {json.dumps({'done': True, 'reply': reply})}\n\n"
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller as-is
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            pump_task.cancel()

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.post("/sessions/{session_id}/end", status_code=204)
async def end_session(session_id: str) -> None:
    """End a chat session. Idempotent.

    The browser fires this from `pagehide` via sendBeacon, which means it is
    routinely sent for sessions that are already gone — the workflow
    completed, or a dev-server restart wiped Temporal's database. "Already
    ended" is the desired state, not an error, so a missing workflow returns
    204 like any other success instead of raising a 500 and a full traceback
    on every page unload.
    """
    client = _require_client()
    handle = client.get_workflow_handle(session_id)
    try:
        await handle.signal(ChatWorkflow.end_chat)
    except RPCError as exc:
        if exc.status is RPCStatusCode.NOT_FOUND:
            return
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Model comparison -------------------------------------------------------


class CompareRequest(BaseModel):
    prompt: str
    model_ids: list[str] = Field(..., description="Models to answer the same prompt.")


@app.post("/compare/stream")
async def compare_stream(body: CompareRequest) -> StreamingResponse:
    """Run the SAME agent once per model and stream all panes at once.

    This is the orchestrator answering N times — same tools, same `think`,
    same identity, same durability — not N raw model completions.
    Each model publishes to its own `model:<id>` topic, so a slow or failing
    model never blocks the others and every chunk is attributable without
    inspecting its content. Payloads carry `model` alongside the usual event
    shape; a failed model arrives as {"model": ..., "error": ...} rather than
    taking down the comparison.
    """
    client = _require_client()
    if not body.prompt.strip() or not body.model_ids:
        raise HTTPException(status_code=400, detail="prompt and model_ids are required")

    model_ids = list(dict.fromkeys(body.model_ids))
    compare_id = f"compare-{uuid.uuid4().hex}"
    handle = await client.start_workflow(
        CompareWorkflow.run,
        CompareInput(prompt=body.prompt, model_ids=model_ids),
        id=compare_id,
        task_queue=TASK_QUEUE,
    )

    async def event_source():
        result_task = asyncio.ensure_future(handle.result())
        stream_client = WorkflowStreamClient.create(client, workflow_id=compare_id)
        queue: "asyncio.Queue[dict]" = asyncio.Queue()
        topics = {topic_for(m): m for m in model_ids}

        # Same pattern as send_turn_stream: drain the subscription in its own
        # task and poll a plain Queue, never cancelling the generator itself.
        async def pump():
            async for item in stream_client.subscribe(list(topics)):
                model = topics.get(item.topic, item.topic)
                data = item.data
                if isinstance(data, dict):
                    payload = {"model": model, **data}
                elif isinstance(data, str):
                    payload = {"model": model, "text": data}
                else:
                    payload = {"model": model, **vars(data)}
                await queue.put(payload)

        pump_task = asyncio.ensure_future(pump())
        try:
            while not result_task.done():
                try:
                    yield f"data: {json.dumps(await asyncio.wait_for(queue.get(), timeout=0.3))}\n\n"
                except asyncio.TimeoutError:
                    pass
            while not queue.empty():
                yield f"data: {json.dumps(queue.get_nowait())}\n\n"
            try:
                result = await result_task
                yield f"data: {json.dumps({'done': True, 'replies': result.replies, 'errors': result.errors})}\n\n"
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller as-is
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            pump_task.cancel()

    return StreamingResponse(event_source(), media_type="text/event-stream")


# --- Human-in-the-loop ------------------------------------------------------
# A tool pauses the agent by raising InterruptException; ChatWorkflow.turn then
# parks on workflow.wait_condition until the `approve` signal supplies an
# answer. Both endpoints are thin wrappers over the workflow's own query and
# signal — no state is kept here.


class ApprovalStatus(BaseModel):
    reason: Optional[str] = Field(None, description="What is being asked; null when not parked.")


@app.get("/sessions/{session_id}/approval", response_model=ApprovalStatus)
async def get_approval(session_id: str) -> ApprovalStatus:
    client = _require_client()
    handle = client.get_workflow_handle(session_id)
    try:
        reason = await handle.query(ChatWorkflow.pending_approval)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as-is
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ApprovalStatus(reason=reason)


class ApprovalRequest(BaseModel):
    response: str = Field(..., description="Answer passed to the waiting tool verbatim.")


@app.post("/sessions/{session_id}/approve", status_code=204)
async def approve(session_id: str, body: ApprovalRequest) -> None:
    client = _require_client()
    handle = client.get_workflow_handle(session_id)
    try:
        await handle.signal(ChatWorkflow.approve, body.response)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as-is
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Contextualized embeddings ---------------------------------------------
# Standalone HTTP access to Perplexity's contextualized embeddings
# (POST /v1/contextualizedembeddings), independent of any chat session or
# the agent loop — for building an external memory/retrieval layer (embed
# once here, store the vectors, compare against a query embedding later)
# without needing to go through the orchestrator's Temporal workflow at
# all. The same capability is also available to the agent itself as a
# strands-temporal tool: perplexity_tools.create_contextualized_embedding,
# wired into every session via AGENT_API_FUNCTION_TOOLS in workflow.py.
# Every parameter and response field Perplexity's own SDK types expose is
# surfaced here — see contextualized_embedding_create_params.py /
# contextualized_embedding_create_response.py in the installed `perplexity`
# package for the source of truth this mirrors.


class ContextualizedEmbeddingRequest(BaseModel):
    input: list[list[str]] = Field(
        ...,
        description=(
            "Nested array: each inner list is the chunks of ONE document. "
            "Chunks within the same document are encoded with document-level "
            "context awareness. Max 512 documents; max 16,000 chunks total; "
            "max 32K tokens per document; max 120,000 tokens combined. Empty "
            "strings are not allowed."
        ),
    )
    model: Literal["pplx-embed-context-v1-0.6b", "pplx-embed-context-v1-4b"] = Field(
        ..., description="0.6b = 1024 dims, cheaper/faster. 4b = 2560 dims, higher quality."
    )
    dimensions: Optional[int] = Field(
        None,
        description=(
            "Matryoshka truncation. 128-1024 for the 0.6b model, 128-2560 for "
            "the 4b model. Omit for full dimensions."
        ),
    )
    encoding_format: Optional[Literal["base64_int8", "base64_binary"]] = Field(
        None,
        description=(
            "base64_int8: base64-encoded signed int8 array (length = "
            "dimensions). base64_binary: base64-encoded packed bits (length "
            "= dimensions / 8 bytes). Omit for the API default."
        ),
    )


class EmbeddingObjectModel(BaseModel):
    embedding: Optional[str] = Field(
        None, description="Base64-encoded embedding vector — see encoding_format for how to decode it."
    )
    index: Optional[int] = Field(None, description="Index of the input chunk this embedding corresponds to.")
    object: Optional[str] = None


class ContextualizedEmbeddingObjectModel(BaseModel):
    data: Optional[list[EmbeddingObjectModel]] = Field(
        None, description="One embedding per chunk in this document, in input order."
    )
    index: Optional[int] = Field(None, description="Index of the document (top-level input entry) this belongs to.")
    object: Optional[str] = None


class EmbeddingsCostModel(BaseModel):
    currency: Optional[Literal["USD"]] = None
    input_cost: Optional[float] = None
    total_cost: Optional[float] = None


class EmbeddingsUsageModel(BaseModel):
    cost: Optional[EmbeddingsCostModel] = None
    prompt_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class ContextualizedEmbeddingResponse(BaseModel):
    data: Optional[list[ContextualizedEmbeddingObjectModel]] = None
    model: Optional[str] = None
    object: Optional[str] = None
    usage: Optional[EmbeddingsUsageModel] = None


@app.post("/embeddings/contextualized", response_model=ContextualizedEmbeddingResponse)
async def create_contextualized_embedding(
    body: ContextualizedEmbeddingRequest,
) -> ContextualizedEmbeddingResponse:
    api_key = os.environ["PERPLEXITY_API_KEY"]
    sdk_client = perplexity_sdk.AsyncPerplexity(api_key=api_key)
    kwargs: dict = {"input": body.input, "model": body.model}
    if body.dimensions is not None:
        kwargs["dimensions"] = body.dimensions
    if body.encoding_format is not None:
        kwargs["encoding_format"] = body.encoding_format
    try:
        response = await sdk_client.contextualized_embeddings.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as-is
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ContextualizedEmbeddingResponse.model_validate(response.model_dump())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "connected": _client is not None}
