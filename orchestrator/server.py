"""FastAPI bridge between the Next.js routes and the Temporal workflows.

Endpoint contract, fixed by the protected route files under ``app/api/``:

    POST /sessions                      -> {"session_id": str}
    POST /sessions/{id}/turns/stream    -> SSE
    POST /sessions/{id}/approve         -> 204
    POST /sessions/{id}/end             -> 204, idempotent
    POST /compare/stream                -> SSE
    GET  /health                        -> liveness + model catalog

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
import re
import shlex
import stat
import time
from collections.abc import AsyncIterator
import asyncio
from config import SSE_SUBSCRIBE_RESTART_LIMIT, SSE_SUBSCRIBE_RESTART_DELAY, SSE_HEARTBEAT_SECONDS, SSE_COMPLETION_DRAIN_TIMEOUT
from contextlib import asynccontextmanager, suppress

from starlette.concurrency import run_in_threadpool
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from temporalio.client import (
    Client,
    WorkflowUpdateStage,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.contrib.strands import StrandsPlugin
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.service import RPCError, RPCStatusCode
from temporalio.exceptions import ApplicationError

from compare_workflow import (
    MODEL_TOPIC_PREFIX,
    CompareInput,
    CompareResult,
    CompareWorkflow,
    model_topic,
)
from catalog_workflow import ModelCatalogWorkflow
from config import (
    CATALOG_CACHE_TTL,
    CATALOG_WORKFLOW_TIMEOUT,
    DEFAULT_MODEL_ID,
    DESKTOP_BROWSER_TASK_QUEUE,
    DESKTOP_VNC_GRANT_COMMAND,
    DESKTOP_VNC_COMMAND_TIMEOUT,
    DESKTOP_HANDOFF_TIMEOUT,
    PROVIDER_DISPLAY_NAMES,
    READINESS_POLLER_CACHE,
    READINESS_POLLER_RPC_TIMEOUT,
    TASK_QUEUE,
)
from perplexity_operations import AGENT_RUNS_TOPIC
from browser_activity import artifact_root
from run_worker import agent_identity
from skills_config import augmented_system_prompt
from workspace_api import WorkspaceRequestBoundary, router as workspace_router, workspace_lifespan
from workflow import (
    THINKING_TOPIC,
    APPROVAL_TOPIC,
    HANDOFF_TOPIC,
    EVENTS_TOPIC,
    TOOL_RESULTS_TOPIC,
    ChatInput,
    ChatWorkflow,
    TurnDocument,
    TurnImage,
    TurnInput,
    TurnVideo,
)

REASONING_LEVELS = json.loads(
    (Path(__file__).resolve().parent.parent / "lib" / "reasoning-levels.json").read_text()
)

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env.local", override=False)

logger = logging.getLogger(__name__)

MAX_COMPARE_MODELS = 4


# AGENT_RUNS_TOPIC is live again: perplexity_operations.py publishes every
# nested Agent API run event on it (imported above so the two sides can never
# drift). The protected route's agent_runs branch consumes these frames.

CHAT_TOPICS = [
    EVENTS_TOPIC,
    APPROVAL_TOPIC,
    HANDOFF_TOPIC,
    TOOL_RESULTS_TOPIC,
    THINKING_TOPIC,
    AGENT_RUNS_TOPIC,
]

_state: dict[str, Any] = {"client": None, "system_prompt": ""}


_poller_cache: dict[str, Any] = {"checked_at": 0.0, "ok": False}
_desktop_poller_cache: dict[str, Any] = {"checked_at": 0.0, "ok": False}


async def task_queue_has_pollers(*, desktop: bool = False) -> bool:
    """Cached DescribeTaskQueue probe for workflow or desktop activity pollers.

    Fail closed: no Temporal client, an RPC error, or a slow RPC all report
    False. Cached for READINESS_POLLER_CACHE so health/session gating stays
    cheap under load.
    """
    now = time.monotonic()
    cache = _desktop_poller_cache if desktop else _poller_cache
    if now - cache["checked_at"] < READINESS_POLLER_CACHE.total_seconds():
        return bool(cache["ok"])
    client = _state["client"]
    ok = False
    if client is not None:
        try:
            from temporalio.api.taskqueue.v1 import TaskQueue
            from temporalio.api.enums.v1 import TaskQueueType
            from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

            response = await asyncio.wait_for(
                client.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=client.namespace,
                        task_queue=TaskQueue(name=DESKTOP_BROWSER_TASK_QUEUE if desktop else TASK_QUEUE),
                        task_queue_type=(TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY if desktop
                                         else TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
                    )
                ),
                timeout=READINESS_POLLER_RPC_TIMEOUT.total_seconds(),
            )
            ok = len(response.pollers) > 0
        except Exception as error:  # noqa: BLE001 - fail closed, never crash
            logger.warning("task-queue poller probe failed: %s", error)
            ok = False
    cache["checked_at"] = now
    cache["ok"] = ok
    return ok


_catalog_cache: dict[str, Any] = {"models": None, "fetched_at": 0.0}


async def _execute_catalog_workflow() -> list[dict[str, str]]:
    """Run ModelCatalogWorkflow once and return its catalog as plain dicts.

    Failure is not an error condition here: no worker polling the task queue
    means the activity never starts and this raises, which is exactly the
    degraded state /health reports. Callers get an empty catalog.
    """
    client = _state["client"]
    if client is None:
        return []
    try:
        # Bounded on both sides: execution_timeout lets Temporal abandon the
        # run server-side, and the asyncio bound stops /health from hanging
        # when nothing is polling the queue at all.
        catalog = await asyncio.wait_for(
            client.execute_workflow(
                ModelCatalogWorkflow.run,
                id=f"model-catalog-{uuid4()}",
                task_queue=TASK_QUEUE,
                execution_timeout=CATALOG_WORKFLOW_TIMEOUT,
            ),
            timeout=CATALOG_WORKFLOW_TIMEOUT.total_seconds(),
        )
    except Exception as error:  # noqa: BLE001 - fail closed, never crash /health
        logger.warning("model catalog workflow failed: %s", error)
        return []
    return [
        {"id": entry.id, "provider": entry.provider, "label": entry.label}
        for entry in catalog
    ]


async def model_catalog(*, refresh: bool = False) -> list[dict[str, str]]:
    """The live worker catalog, cached for CATALOG_CACHE_TTL.

    ``refresh=True`` bypasses the cache: a model id the cache has not seen may
    simply predate the last fetch, so validation forces exactly one re-execute
    before rejecting it rather than making the caller wait out the TTL.
    """
    now = time.monotonic()
    cached = _catalog_cache["models"]
    fresh = (
        cached is not None
        and now - _catalog_cache["fetched_at"] < CATALOG_CACHE_TTL.total_seconds()
    )
    if fresh and not refresh:
        return cached
    models = await _execute_catalog_workflow()
    # An empty catalog is never cached: it means no worker answered, and
    # caching that would keep /health degraded for a whole TTL window after a
    # worker comes up.
    if models:
        _catalog_cache["models"] = models
        _catalog_cache["fetched_at"] = now
    return models


async def validated_model(model_id: str) -> None:
    """Reject a model id the live worker does not serve.

    503 when no catalog is available at all (no worker), 400 when the worker is
    up but does not register this id -- checked against a freshly refreshed
    catalog so a newly registered id is never wrongly rejected.
    """
    ids = [entry["id"] for entry in await model_catalog()]
    if ids and model_id not in ids:
        # Exactly one refresh: the id may have been registered after the cached
        # catalog was fetched. Skipped when the catalog is empty -- no worker
        # answered, and re-executing would only double the wait before the 503.
        ids = [entry["id"] for entry in await model_catalog(refresh=True)]
    if not ids:
        raise HTTPException(503, "No worker is ready")
    if model_id not in ids:
        preview = ", ".join(ids[:5])
        if len(ids) > 5:
            preview += ", ..."
        raise HTTPException(400, f"Unsupported model: {model_id}. Available: {preview}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _, system_prompt = agent_identity()
    _state["system_prompt"] = augmented_system_prompt(system_prompt)
    try:
        _state["client"] = await Client.connect(
            os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
            plugins=[StrandsPlugin()],
        )
    except Exception as error:  # noqa: BLE001 - API stays up so /health can report
        logger.warning("Temporal unavailable at startup: %s", error)
        _state["client"] = None
    async with httpx.AsyncClient(timeout=60) as http, workspace_lifespan(app):
        _state["http"] = http
        yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(WorkspaceRequestBoundary)
app.include_router(workspace_router)


def temporal() -> Client:
    client = _state["client"]
    if client is None:
        raise HTTPException(503, "Temporal is unavailable")
    return client


def sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _next_item(subscription: AsyncIterator[Any]) -> Any:
    """One step of a stream subscription, or None when it ends.

    Wrapping ``__anext__`` in a task is what lets the pump select the SDK
    iterator against the update's result with ``asyncio.wait``; StopAsyncIteration
    cannot cross a task boundary, so end-of-stream becomes ``None``.
    """
    try:
        return await subscription.__anext__()
    except StopAsyncIteration:
        return None


async def _aclose(subscription: AsyncIterator[Any]) -> None:
    """Release a stream subscription without touching the workflow."""
    closer = getattr(subscription, "aclose", None)
    if closer is None:
        return
    with suppress(Exception):
        await closer()


class StartSession(BaseModel):
    model_id: str = Field(min_length=1)


class TurnMediaPayload(BaseModel):
    format: str
    data: str


class TurnRequest(BaseModel):
    prompt: str = ""
    images: list[TurnMediaPayload] = Field(default_factory=list)
    documents: list[TurnMediaPayload] = Field(default_factory=list)
    videos: list[TurnMediaPayload] = Field(default_factory=list)
    # Optional per-turn model switch: validated against the live worker
    # catalog (ModelCatalogWorkflow) and forwarded to the workflow, which
    # rebuilds its agent on the new factory name before running the turn.
    # Omitted -> keep the session's current model.
    model_id: str | None = None
    reasoning_effort: str | None = None


class ApproveRequest(BaseModel):
    response: str = Field(min_length=1)


class HandoffRequest(BaseModel):
    action: str = Field(pattern="^(take|release|give)$")
    message: str = ""


class CompareRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model_ids: list[str] = Field(min_length=1)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness plus the worker-declared model catalog.

    Liveness is Temporal's own DescribeTaskQueue poller probe -- there is no
    readiness file and no PID/heartbeat lease. The catalog comes from
    ModelCatalogWorkflow, so every model object carries the provider its worker
    declared; the Next.js picker groups by that instead of guessing from the id.
    """
    temporal_ok = _state["client"] is not None
    pollers_ok = await task_queue_has_pollers()
    models = await model_catalog()
    default_model = None
    if models:
        ids = [entry["id"] for entry in models]
        default_model = DEFAULT_MODEL_ID if DEFAULT_MODEL_ID in ids else ids[0]
    return {
        "status": "ok" if (temporal_ok and pollers_ok and models) else "degraded",
        "temporal": temporal_ok,
        # A worker IS its pollers: nothing else can serve the task queue.
        "worker": pollers_ok,
        "pollers": pollers_ok,
        "models": models,
        "providers": PROVIDER_DISPLAY_NAMES,
        "default_model": default_model,
    }


@app.post("/sessions")
async def start_session(body: StartSession) -> dict[str, str]:
    await validated_model(body.model_id)
    if not await task_queue_has_pollers():
        raise HTTPException(503, "No worker is polling the task queue")

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
    if (
        not body.prompt.strip()
        and not body.images
        and not body.documents
        and not body.videos
    ):
        raise HTTPException(422, "A prompt or at least one attachment is required")
    if body.model_id is not None:
        await validated_model(body.model_id)
    client = temporal()
    handle = client.get_workflow_handle(session_id)
    if body.reasoning_effort is not None:
        model_id = body.model_id or await handle.query(ChatWorkflow.model_id)
        if body.reasoning_effort not in REASONING_LEVELS.get(model_id, []):
            raise HTTPException(422, f"Unsupported reasoning effort for {model_id}: {body.reasoning_effort}")

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
        # temporalio/contrib/workflow_streams/_client.py, whose subscribe()
        # returns only on AcceptedUpdateCompletedWorkflow, RPC timeout, or
        # terminal status. ChatWorkflow stays Running between turns, so this
        # per-turn pump drains to a frozen completion offset, never exhaustion.
        #
        # The SDK iterator is itself the buffer: it is selected against
        # directly, with no queue, consumer task, or drain sleep in between.
        last_offset = start_offset
        # Subscribe BEFORE the update is accepted, or early events are lost.
        # poll_cooldown stays at its 100ms default: each poll is a durable
        # Update against the workflow.
        subscription = stream_client.subscribe(CHAT_TOPICS, from_offset=last_offset)
        anext_task: asyncio.Task[Any] | None = None
        try:
            try:
                update_handle = await handle.start_update(
                ChatWorkflow.turn,
                TurnInput(
                    prompt=body.prompt,
                    images=[
                        TurnImage(format=image.format, data=image.data)
                        for image in body.images
                    ],
                    documents=[
                        TurnDocument(format=document.format, data=document.data)
                        for document in body.documents
                    ],
                    videos=[
                        TurnVideo(format=video.format, data=video.data)
                        for video in body.videos
                    ],
                    model_id=body.model_id,
                    reasoning_effort=body.reasoning_effort,
                ),
                wait_for_stage=WorkflowUpdateStage.ACCEPTED,
                )
            except Exception as error:  # noqa: BLE001 - surfaced to the client
                yield sse({"error": str(error)})
                return

            result_task = asyncio.create_task(update_handle.result())
            try:
                restarts = 0
                anext_task = asyncio.create_task(_next_item(subscription))
                # Forward frames as they land until the turn's reply is ready.
                while not result_task.done():
                    done, _ = await asyncio.wait(
                        {anext_task, result_task},
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                    if not done:
                        yield b": keep-alive\n\n"
                        continue
                    if anext_task not in done:
                        continue  # The result landed; the loop test ends us.
                    try:
                        item = anext_task.result()
                    except Exception as error:  # noqa: BLE001
                        item, lost = None, error
                    else:
                        lost = None
                    if item is not None:
                        data = item.data
                        frame = dict(data) if isinstance(data, dict) else {"data": data}
                        frame["topic"] = item.topic
                        last_offset = item.offset + 1
                        anext_task = asyncio.create_task(_next_item(subscription))
                        yield sse(frame)
                        continue
                    # The subscription ended or failed before the turn did.
                    if result_task.done():
                        break
                    if restarts >= SSE_SUBSCRIBE_RESTART_LIMIT:
                        logger.warning("Stream subscription lost for %s: %s", session_id, lost)
                        yield sse({"error": "Live event connection lost. The durable turn may still be running; do not repeat computer actions."})
                        return
                    restarts += 1
                    await asyncio.sleep(SSE_SUBSCRIBE_RESTART_DELAY)
                    await _aclose(subscription)
                    subscription = stream_client.subscribe(
                        CHAT_TOPICS, from_offset=last_offset
                    )
                    anext_task = asyncio.create_task(_next_item(subscription))

                # Completion can overtake a fetched batch or an in-flight poll.
                # Replay from the last forwarded item, not the SDK's read cursor.
                try:
                    try:
                        deadline = asyncio.get_running_loop().time() + SSE_COMPLETION_DRAIN_TIMEOUT
                        end_offset = await asyncio.wait_for(
                            stream_client.get_offset(), timeout=SSE_COMPLETION_DRAIN_TIMEOUT,
                        )
                        anext_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await anext_task
                        await _aclose(subscription)
                        if last_offset > end_offset:
                            raise RuntimeError("completion offset is behind the delivered offset")
                        if last_offset < end_offset:
                            # The watermark is global. Read every topic to reach it,
                            # but expose only the existing SSE topic contract.
                            subscription = stream_client.subscribe(None, from_offset=last_offset)
                        while last_offset < end_offset:
                            remaining = deadline - asyncio.get_running_loop().time()
                            if remaining <= 0:
                                raise TimeoutError
                            anext_task = asyncio.create_task(_next_item(subscription))
                            done, _ = await asyncio.wait({anext_task}, timeout=remaining)
                            if not done:
                                raise TimeoutError
                            item = anext_task.result()
                            if item is None:
                                raise RuntimeError("stream ended before the completion offset")
                            if item.offset != last_offset:
                                raise RuntimeError(f"expected offset {last_offset}, received {item.offset}")
                            last_offset = item.offset + 1
                            if item.topic in CHAT_TOPICS:
                                data = item.data
                                frame = dict(data) if isinstance(data, dict) else {"data": data}
                                frame["topic"] = item.topic
                                yield sse(frame)
                    finally:
                        anext_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await anext_task
                        await _aclose(subscription)
                except Exception as error:  # noqa: BLE001 - explicit incomplete delivery
                    detail = "completion drain timed out" if isinstance(error, TimeoutError) else str(error)
                    yield sse({"error": f"Incomplete event delivery: {detail}. Do not repeat computer actions."})
                    return

                try:
                    reply = result_task.result()
                except Exception as error:  # noqa: BLE001 - surfaced to the client
                    detail = str(error)
                    cause = error
                    seen = set()
                    while cause is not None and id(cause) not in seen:
                        seen.add(id(cause))
                        if isinstance(cause, ApplicationError) and cause.type == "PerplexityModelError":
                            detail = str(cause)
                            break
                        cause = getattr(cause, "cause", None) or cause.__cause__
                    yield sse({"error": detail})
                    return
                yield sse({"done": True, "reply": reply})
            finally:
                # Cancelling the client-side result waiter does not cancel the
                # accepted Temporal update or retry any computer action: a
                # closed browser tab must not abort a durable turn.
                if not result_task.done():
                    result_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await result_task
        finally:
            if anext_task is not None:
                anext_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await anext_task
            await _aclose(subscription)

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


async def _vnc_input(command: str, expected_answer: str | tuple[str, ...], *, verify_cleanup: bool = False) -> None:
    """Toggle desktop input through x11vnc's native remote control, verified."""
    process = await asyncio.create_subprocess_exec(
        *shlex.split(command), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), DESKTOP_VNC_COMMAND_TIMEOUT)
    except BaseException:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    answers = stdout.decode().strip().splitlines()[-1].split(",") if stdout.strip() else []
    expected = (expected_answer,) if isinstance(expected_answer, str) else expected_answer
    if process.returncode != 0 or not set(expected).issubset(answers):
        raise HTTPException(503, "Desktop input transfer could not be verified")
    if verify_cleanup and not {"aro=client_count:0", "aro=pointer_mask:0x0"}.issubset(answers):
        raise HTTPException(503, "Native desktop clients or held input did not clear")


def _desktop_mode(session_id: str) -> dict:
    # Read the atomically replaced file only. Host locks do not synchronize with
    # Linux flock on Colima, and even a read-only desktop_state context writes.
    try:
        fd = os.open(artifact_root() / "runtime.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("Desktop state must be a regular file")
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError("Desktop state exceeds limit")
        state = json.loads(raw)
        if not isinstance(state, dict) or type(state.get("epoch")) is not int or state.get("mode") not in {
            "agent", "stopping", "human", "relinquishing", "instructions", "resuming", "recovery",
        }:
            raise ValueError("Invalid desktop state")
    except (OSError, ValueError) as error:
        raise HTTPException(503, "Desktop unavailable or invalid state") from error
    if state.get("owner") != {"namespace": temporal().namespace, "workflow_id": session_id}:
        raise HTTPException(409, "This chat does not own the desktop")
    return state


@app.get("/sessions/{session_id}/desktop-control")
async def desktop_control_status(session_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    state = await asyncio.to_thread(_desktop_mode, session_id)
    try:
        async with asyncio.timeout(READINESS_POLLER_RPC_TIMEOUT.total_seconds()):
            workflow_state = await temporal().get_workflow_handle(session_id).query(
                ChatWorkflow.control_status, rpc_timeout=READINESS_POLLER_RPC_TIMEOUT,
            )
    except (TimeoutError, RPCError):
        # Reconnecting must not forget native ownership when the worker is down.
        workflow_state = {"run_id": None, "ready": False}
    runtime_available = await task_queue_has_pollers(desktop=True)
    try:
        command = shlex.split(DESKTOP_VNC_GRANT_COMMAND)
        command = command[:command.index("-R")] + ["-Q", "viewonly,deny"]
        # Pollers may linger after worker loss; require a fresh native receipt too.
        await _vnc_input(
            shlex.join(command), (f"ans=viewonly:{0 if state['mode'] == 'human' else 1}", "ans=deny:0"),
        )
    except (HTTPException, OSError, TimeoutError, ValueError):
        runtime_available = False
    current = await asyncio.to_thread(_desktop_mode, session_id)
    # GET readiness means native availability, not an idle turn. Take Control
    # must remain usable while workflow.control_status.ready is false during work.
    available = runtime_available and current == state and state["mode"] != "recovery"
    return {**workflow_state, "ready": available,
            "mode": current["mode"] if runtime_available else "recovery", "epoch": str(current["epoch"])}


@app.post("/sessions/{session_id}/handoff", status_code=204)
async def handoff(session_id: str, body: HandoffRequest) -> None:
    handle = temporal().get_workflow_handle(session_id)
    try:
        async with asyncio.timeout(DESKTOP_HANDOFF_TIMEOUT.total_seconds()):
            await asyncio.to_thread(_desktop_mode, session_id)
            steps = {
                "take": [(ChatWorkflow.claim_control, ()), (ChatWorkflow.desktop_control, ("take",))],
                "release": [(ChatWorkflow.desktop_control, ("release",))],
                "give": [(ChatWorkflow.desktop_control, ("prepare_resume",)),
                         (ChatWorkflow.relinquish_control, (body.message,))],
            }[body.action]
            old_run = None
            for method, arguments in steps:
                while True:
                    try:
                        old_run = await handle.execute_update(method, *arguments, rpc_timeout=DESKTOP_HANDOFF_TIMEOUT)
                        break
                    except WorkflowUpdateFailedError as error:
                        if getattr(error.cause, "type", None) != "SessionRollingOver":
                            raise
                        await asyncio.sleep(0.1)
            if body.action != "give":
                return
            while True:
                state = await handle.query(ChatWorkflow.control_status)
                if state["run_id"] != old_run and state["ready"]:
                    try:
                        await handle.execute_update(ChatWorkflow.desktop_control, "resume", rpc_timeout=DESKTOP_HANDOFF_TIMEOUT)
                        return
                    except WorkflowUpdateFailedError as error:
                        if getattr(error.cause, "type", None) != "SessionRollingOver":
                            raise
                await asyncio.sleep(0.1)
    except (TimeoutError, WorkflowUpdateRPCTimeoutOrCancelledError) as error:
        task = asyncio.current_task()
        if isinstance(error, WorkflowUpdateRPCTimeoutOrCancelledError) and task is not None and task.cancelling():
            raise asyncio.CancelledError() from error
        phase = {"take": "takeover", "release": "release", "give": "resume"}[body.action]
        raise HTTPException(504, f"Desktop {phase} timed out; transfer unconfirmed, check control status before retrying") from error
    except WorkflowUpdateFailedError as error:
        raise HTTPException(409, str(error.__cause__ or error)) from error
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(404, f"Unknown session: {session_id}") from error
        raise HTTPException(502, str(error)) from error


@app.get("/sessions/{session_id}/desktop-images/{artifact_id}")
async def desktop_image(session_id: str, artifact_id: str) -> Response:
    from desktop_observation import observation_image
    from workspace_state import StateConflict

    await asyncio.to_thread(_desktop_mode, session_id)
    try:
        image = await asyncio.to_thread(
            observation_image, artifact_id, namespace=temporal().namespace, workflow_id=session_id,
        )
    except (ValueError, KeyError, OSError, StateConflict) as error:
        raise HTTPException(404, "Desktop observation unavailable") from error
    return Response(image, media_type="image/png", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


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


_FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


@app.get("/responses/{response_id}/files/{file_id}/content")
@app.get("/agent/{response_id}/files/{file_id}/content")
async def response_file_content(response_id: str, file_id: str) -> Response:
    """Proxy a sandbox-produced file so the browser can load it.

    The Agent API returns raw bytes from
    GET /v1/agent/{response_id}/files/{file_id}/content and requires the API
    key, which must never reach the browser. `share_file` items carry a
    relative path of the form /v1/{responses|agent}/{id}/files/{id}/content;
    app/api/orchestrator/file/route.ts accepts either prefix and forwards to
    the /responses form here, but both are served. The body is streamed, not
    buffered, so large files do not sit in memory.
    """
    if not _FILE_ID_PATTERN.match(response_id) or not _FILE_ID_PATTERN.match(file_id):
        raise HTTPException(422, "Invalid response or file id")

    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise HTTPException(503, "PERPLEXITY_API_KEY is not configured")

    url = (
        f"https://api.perplexity.ai/v1/agent/{response_id}"
        f"/files/{file_id}/content"
    )
    # The pooled lifespan client keeps TLS sessions to api.perplexity.ai warm;
    # send() with stream=True defers the body so it can be forwarded chunkwise.
    request = _state["http"].build_request(
        "GET", url, headers={"Authorization": f"Bearer {api_key}"}
    )
    upstream = await _state["http"].send(request, stream=True)
    if upstream.status_code != 200:
        await upstream.aclose()
        raise HTTPException(upstream.status_code, "File unavailable")

    headers: dict[str, str] = {}
    disposition = upstream.headers.get("content-disposition")
    if disposition:
        headers["Content-Disposition"] = disposition

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body_iter(),
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )


@app.post("/compare/stream")
async def compare_stream(body: CompareRequest) -> StreamingResponse:
    ids = [entry["id"] for entry in await model_catalog()]
    if not ids:
        raise HTTPException(503, "No worker is ready")

    seen: set[str] = set()
    selected = [
        model_id
        for model_id in body.model_ids
        if not (model_id in seen or seen.add(model_id))
    ]
    if len(selected) > MAX_COMPARE_MODELS:
        raise HTTPException(422, f"At most {MAX_COMPARE_MODELS} models")
    if any(model_id not in ids for model_id in selected):
        # One refresh before rejecting: the worker may have registered these
        # ids after the cached catalog was fetched.
        ids = [entry["id"] for entry in await model_catalog(refresh=True)]
    unknown = [model_id for model_id in selected if model_id not in ids]
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
