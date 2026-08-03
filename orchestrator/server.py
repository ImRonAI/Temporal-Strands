"""FastAPI bridge between the Next.js routes and the Temporal workflows.

Endpoint contract, fixed by the protected route files under ``app/api/``:

    POST /sessions                      -> {"session_id": str}
    POST /sessions/{id}/turns/stream    -> SSE
    POST /sessions/{id}/approve         -> 204
    POST /sessions/{id}/end             -> 204, idempotent
    POST /compare/stream                -> SSE
    GET  /health                        -> readiness detail

SSE frames are newline-delimited ``data: <json>`` lines. The chat stream emits
one frame per stream item plus exactly one terminal frame:

    data: {"topic":"events","contentBlockDelta":{"delta":{"text":"hi"}}}
    data: {"topic":"approval","reason":"Approve delete?"}
    data: {"topic":"tool_results","tool_use_id":"t1","status":"success","content":[]}
    data: {"done":true,"reply":"hi"}

Ordering matters: the subscriber is created BEFORE the turn update is started,
otherwise events published early in the turn are missed. On disconnect the pump
is cancelled but ``update_handle.result()`` is not -- cancelling it would abort
a durable turn just because a browser tab closed.

StrandsPlugin is attached to this client too (guide R3): it installs the
pydantic payload converter and the failure converter as one inseparable bundle.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
import asyncio
from contextlib import asynccontextmanager, suppress

from starlette.concurrency import run_in_threadpool
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from temporalio.client import Client, WorkflowUpdateStage
from temporalio.contrib.strands import StrandsPlugin
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.service import RPCError, RPCStatusCode

from compare_workflow import (
    MODEL_TOPIC_PREFIX,
    CompareInput,
    CompareResult,
    CompareWorkflow,
    model_topic,
)
from config import TASK_QUEUE
from run_worker import READINESS_PATH, agent_identity
from workflow import (
    THINKING_TOPIC,
    APPROVAL_TOPIC,
    EVENTS_TOPIC,
    TOOL_RESULTS_TOPIC,
    ChatInput,
    ChatWorkflow,
    TurnImage,
    TurnInput,
)

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env.local", override=False)

logger = logging.getLogger(__name__)

MAX_COMPARE_MODELS = 4
CHAT_TOPICS = [EVENTS_TOPIC, APPROVAL_TOPIC, TOOL_RESULTS_TOPIC, THINKING_TOPIC]

_state: dict[str, Any] = {"client": None, "system_prompt": ""}


async def readiness() -> dict[str, Any] | None:
    """Worker readiness record, or None when no worker is running.

    Off the event loop: FastAPI runs `async def` handlers directly on the loop,
    so a synchronous read_text here blocks every other in-flight request --
    including active SSE streams -- for the duration of the disk I/O.
    """

    def _read() -> dict[str, Any] | None:
        try:
            return json.loads(READINESS_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    return await run_in_threadpool(_read)


def models_of(record: dict[str, Any] | None) -> list[str]:
    models = (record or {}).get("models")
    return models if isinstance(models, list) else []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _, system_prompt = agent_identity()
    _state["system_prompt"] = system_prompt
    try:
        _state["client"] = await Client.connect(
            os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
            plugins=[StrandsPlugin()],
        )
    except Exception as error:  # noqa: BLE001 - API stays up so /health can report
        logger.warning("Temporal unavailable at startup: %s", error)
        _state["client"] = None
    # One pooled client for file proxying: constructing an AsyncClient per
    # request meant a fresh TLS handshake to api.perplexity.ai every time,
    # with no keep-alive reuse.
    async with httpx.AsyncClient(timeout=60) as http:
        _state["http"] = http
        yield


app = FastAPI(lifespan=lifespan)


def temporal() -> Client:
    client = _state["client"]
    if client is None:
        raise HTTPException(503, "Temporal is unavailable")
    return client


def sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


class StartSession(BaseModel):
    model_id: str = Field(min_length=1)


class TurnImagePayload(BaseModel):
    format: str
    data: str


class TurnRequest(BaseModel):
    prompt: str = ""
    images: list[TurnImagePayload] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    response: str = Field(min_length=1)


class CompareRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model_ids: list[str] = Field(min_length=1)


@app.get("/health")
async def health() -> dict[str, Any]:
    # One read, not two: this used to call readiness() and supported_models(),
    # each doing its own full read + parse.
    record = await readiness()
    temporal_ok = _state["client"] is not None
    worker_ok = record is not None
    return {
        "status": "ok" if (temporal_ok and worker_ok) else "degraded",
        "api": True,
        "temporal": temporal_ok,
        "worker": worker_ok,
        "models": len(models_of(record)),
    }


@app.post("/sessions")
async def start_session(body: StartSession) -> dict[str, str]:
    models = models_of(await readiness())
    if not models:
        raise HTTPException(503, "No worker is ready")
    if body.model_id not in models:
        raise HTTPException(400, f"Unsupported model: {body.model_id}")

    client = temporal()
    session_id = f"chat-{os.urandom(8).hex()}"
    await client.start_workflow(
        ChatWorkflow.run,
        ChatInput(
            model_id=body.model_id,
            system_prompt=_state["system_prompt"],
            session_id=session_id,
        ),
        id=session_id,
        task_queue=TASK_QUEUE,
    )
    return {"session_id": session_id}


@app.post("/sessions/{session_id}/turns/stream")
async def turn_stream(session_id: str, body: TurnRequest) -> StreamingResponse:
    if not body.prompt.strip() and not body.images:
        raise HTTPException(422, "A prompt or at least one image is required")

    client = temporal()
    handle = client.get_workflow_handle(session_id)

    # Start reading where the stream currently ends, so this turn's frames are
    # not preceded by every earlier turn's replay.
    stream_client = WorkflowStreamClient.create(client, session_id)
    try:
        # Prefer the in-flight turn's own start offset over the live tail.
        # get_offset() returns base_offset + log length, so a client that
        # reconnects while a turn is still streaming would subscribe past every
        # frame already emitted for it and see nothing until the next turn.
        # ChatWorkflow.turn_start_offset is None between turns, in which case
        # the tail is correct.
        start_offset = await handle.query(ChatWorkflow.turn_start_offset)
        if start_offset is None:
            start_offset = await stream_client.get_offset()
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(404, f"Unknown session: {session_id}") from error
        raise HTTPException(502, str(error)) from error

    async def body_iter() -> AsyncIterator[bytes]:
        # A subscription ends only when the WORKFLOW ends -- verified in
        # _client.py, which returns on AcceptedUpdateCompletedWorkflow, RPC
        # timeout, or terminal status and nothing else. ChatWorkflow is a
        # durable multi-turn session that stays Running between turns, so
        # iterating it to exhaustion inside a per-turn request never returns
        # and the response never closes.
        #
        # The strands-temporal guide (Pattern 8) documents the shape: the
        # consumer runs as its own task, the caller awaits the result, then
        # cancels the consumer after a short drain. The guide also warns
        # against breaking on messageStop -- a tool-using run emits one per
        # turn, so that truncates every later turn.
        frames: asyncio.Queue[bytes] = asyncio.Queue()

        async def consume() -> None:
            # poll_cooldown stays at its 100ms default: each poll is a durable
            # Update against the workflow.
            async for item in stream_client.subscribe(
                CHAT_TOPICS, from_offset=start_offset
            ):
                data = item.data
                frame = dict(data) if isinstance(data, dict) else {"data": data}
                frame["topic"] = item.topic
                await frames.put(sse(frame))

        # Subscribe before the update is accepted, or early events are lost.
        consume_task = asyncio.create_task(consume())
        update_handle = await handle.start_update(
            ChatWorkflow.turn,
            TurnInput(
                prompt=body.prompt,
                images=[
                    TurnImage(format=image.format, data=image.data)
                    for image in body.images
                ],
            ),
            wait_for_stage=WorkflowUpdateStage.ACCEPTED,
        )
        result_task = asyncio.create_task(update_handle.result())

        try:
            # Forward frames as they land until the turn's reply is ready.
            while not result_task.done():
                get = asyncio.create_task(frames.get())
                done, _ = await asyncio.wait(
                    {get, result_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if get in done:
                    yield get.result()
                    continue
                get.cancel()
                with suppress(asyncio.CancelledError):
                    await get

            # Drain what the consumer already published for this turn before
            # the terminal frame -- the guide's "give it a moment first".
            await asyncio.sleep(0.2)
            while not frames.empty():
                yield frames.get_nowait()

            try:
                reply = result_task.result()
            except Exception as error:  # noqa: BLE001 - surfaced to the client
                yield sse({"error": str(error)})
                return
            yield sse({"done": True, "reply": reply})
        finally:
            # Cancel the consumer, never the update: a closed browser tab must
            # not abort a durable turn.
            consume_task.cancel()
            with suppress(asyncio.CancelledError):
                await consume_task

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform"},
    )


@app.post("/sessions/{session_id}/approve", status_code=204)
async def approve(session_id: str, body: ApproveRequest) -> None:
    handle = temporal().get_workflow_handle(session_id)
    try:
        await handle.signal(ChatWorkflow.approve, body.response)
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(404, f"Unknown session: {session_id}") from error
        raise HTTPException(502, str(error)) from error


@app.post("/sessions/{session_id}/end", status_code=204)
async def end_session(session_id: str) -> None:
    client = _state["client"]
    if client is None:
        # The session is already unreachable, which is the state this endpoint
        # aims for. Reporting an error would make sendBeacon retry pointlessly.
        return
    try:
        await client.get_workflow_handle(session_id).signal(ChatWorkflow.end_chat)
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            return  # Idempotent: already gone.
        logger.warning("end_chat failed for %s: %s", session_id, error)


@app.get("/responses/{response_id}/files/{file_id}/content")
async def response_file_content(response_id: str, file_id: str) -> Response:
    """Proxy a sandbox-produced file so the browser can load it.

    The Agent API returns raw bytes from
    GET /v1/agent/{response_id}/files/{file_id}/content and requires the API
    key, which must never reach the browser. `share_file` items carry exactly
    this relative path, so the UI can use it directly as an <img> src.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise HTTPException(503, "PERPLEXITY_API_KEY is not configured")

    url = (
        f"https://api.perplexity.ai/v1/agent/{response_id}"
        f"/files/{file_id}/content"
    )
    upstream = await _state["http"].get(
        url, headers={"Authorization": f"Bearer {api_key}"}
    )
    if upstream.status_code != 200:
        raise HTTPException(upstream.status_code, "File unavailable")
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )


@app.post("/compare/stream")
async def compare_stream(body: CompareRequest) -> StreamingResponse:
    models = models_of(await readiness())
    if not models:
        raise HTTPException(503, "No worker is ready")

    seen: set[str] = set()
    selected = [
        model_id
        for model_id in body.model_ids
        if not (model_id in seen or seen.add(model_id))
    ]
    if len(selected) > MAX_COMPARE_MODELS:
        raise HTTPException(422, f"At most {MAX_COMPARE_MODELS} models")
    unknown = [model_id for model_id in selected if model_id not in models]
    if unknown:
        raise HTTPException(400, f"Unsupported models: {', '.join(unknown)}")

    client = temporal()
    workflow_id = f"compare-{os.urandom(8).hex()}"
    topics = [model_topic(model_id) for model_id in selected]
    handle = await client.start_workflow(
        CompareWorkflow.run,
        CompareInput(
            prompt=body.prompt,
            model_ids=selected,
            system_prompt=_state["system_prompt"],
        ),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    stream_client = WorkflowStreamClient.create(client, workflow_id)

    async def body_iter() -> AsyncIterator[bytes]:
        async for item in stream_client.subscribe(topics, from_offset=0):
            data = item.data
            frame = dict(data) if isinstance(data, dict) else {"data": data}
            # Every non-terminal frame is tagged with the model it came from;
            # app/api/compare/route.ts keys its panes on exactly this field.
            frame["model"] = item.topic[len(MODEL_TOPIC_PREFIX) :]
            yield sse(frame)

        try:
            result: CompareResult = await handle.result()
        except Exception as error:  # noqa: BLE001 - surfaced to the client
            yield sse({"error": str(error)})
            return
        yield sse({"done": True, "replies": result.replies, "errors": result.errors})

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform"},
    )
