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
    DESKTOP_VNC_GRANT_COMMAND,
    DESKTOP_VNC_COMMAND_TIMEOUT,
    DESKTOP_HANDOFF_TIMEOUT,
    DESKTOP_PROVISION_TIMEOUT,
    HANDOFF_ROLLOVER_RETRY_BACKOFF,
    PROVIDER_DISPLAY_NAMES,
    READINESS_POLLER_CACHE,
    READINESS_POLLER_RPC_TIMEOUT,
    SERVER_HTTP_TIMEOUT,
    TASK_QUEUE,
    desktop_container_name,
    desktop_task_queue,
)
from desktop_provision_activity import container_artifact_dir
from load_tool import _SESSION_TOOLS, discover_tools_activity
from skills_config import discovered_skills
from run_worker import agent_identity, assemble_model_factories
from workspace_api import WorkspaceRequestBoundary, router as workspace_router, workspace_lifespan
from workflow import (
    THINKING_TOPIC,
    AGENT_RUNS_TOPIC,
    APPROVAL_TOPIC,
    HANDOFF_TOPIC,
    EVENTS_TOPIC,
    TOOL_RESULTS_TOPIC,
    ChatInput,
    ChatWorkflow,
)

REASONING_LEVELS_PATH = Path(__file__).resolve().parent.parent / "lib" / "reasoning-levels.json"

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env.local", override=False)

logger = logging.getLogger(__name__)

MAX_COMPARE_MODELS = 4


# AGENT_RUNS_TOPIC stays in the subscription set (defined in workflow.py with
# the other topics) so the SSE frame contract with the protected route never
# drifts; nothing publishes on it since the Agent API sub-agent operations
# were removed.

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


async def _queue_has_pollers(queue: str, *, activity_queue: bool) -> bool:
    client = _state["client"]
    if client is None:
        return False
    try:
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.enums.v1 import TaskQueueType
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

        response = await asyncio.wait_for(
            client.workflow_service.describe_task_queue(
                DescribeTaskQueueRequest(
                    namespace=client.namespace,
                    task_queue=TaskQueue(name=queue),
                    task_queue_type=(TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY if activity_queue
                                     else TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
                )
            ),
            timeout=READINESS_POLLER_RPC_TIMEOUT.total_seconds(),
        )
        return len(response.pollers) > 0
    except Exception as error:  # noqa: BLE001 - fail closed, never crash
        logger.warning("task-queue poller probe failed for %r: %s", queue, error)
        return False


async def task_queue_has_pollers(*, desktop: str | None = None) -> bool:
    """DescribeTaskQueue probe for the host worker, or a session's desktop worker.

    Fail closed: no Temporal client, an RPC error, or a slow RPC all report
    False. The host probe is cached for READINESS_POLLER_CACHE so health and
    session gating stay cheap; a desktop probe names the session and is live.
    """
    if desktop is not None:
        return await _queue_has_pollers(desktop_task_queue(desktop), activity_queue=True)
    now = time.monotonic()
    if now - _poller_cache["checked_at"] < READINESS_POLLER_CACHE.total_seconds():
        return bool(_poller_cache["ok"])
    ok = await _queue_has_pollers(TASK_QUEUE, activity_queue=False)
    _poller_cache["checked_at"] = now
    _poller_cache["ok"] = ok
    return ok


_catalog_cache: dict[str, Any] = {"models": None, "fetched_at": 0.0}


async def _execute_catalog_workflow() -> list[dict[str, str]]:
    """Build the provider-declaring catalog directly in this process.

    Same code path the worker uses at boot (``assemble_model_factories``), so
    the list is identical to what the worker registers, without a Temporal
    round-trip that a busy single worker can miss. Empty only when the build
    itself fails.
    """
    try:
        _, catalog, _ = await asyncio.wait_for(
            assemble_model_factories(),
            timeout=CATALOG_WORKFLOW_TIMEOUT.total_seconds(),
        )
    except Exception as error:  # noqa: BLE001 - fail closed, never crash /health
        logger.warning("model catalog build failed: %s: %s", type(error).__name__, error)
        return []
    return [
        {"id": entry.id, "provider": entry.provider, "label": entry.label}
        for entry in catalog
    ]


def models_prompt(models: list[dict[str, str]]) -> str:
    """The current model list as a system-prompt section for the agent."""
    if not models:
        return ""
    lines = [f"- {m['id']} ({PROVIDER_DISPLAY_NAMES.get(m['provider'], m['provider'])})" for m in models]
    return "Models available in this deployment right now:\n" + "\n".join(lines)


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
    # A refresh that fails (typically ModelCatalogWorkflow exceeding
    # CATALOG_WORKFLOW_TIMEOUT while the single worker is busy with a turn)
    # must not blank a catalog the worker already declared: the picker would
    # empty and session starts would 503 even though the worker is up. Serve
    # the last known-good catalog when the pollers are still there, and only
    # report "no catalog" when the task queue really has no worker. The stale
    # fetched_at is left as-is so the next call retries the refresh.
    if cached and await task_queue_has_pollers():
        logger.warning("model catalog refresh failed; serving %d cached entries", len(cached))
        return cached
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
    # agent.json prompt only. The Agent Skills catalog is NOT baked in here:
    # the workflow's TemporalAgent carries the official strands.AgentSkills
    # plugin, which injects name/description metadata per invocation and
    # loads a SKILL.md only when the model calls its ``skills`` tool. Baking
    # the 527-skill dump into ChatInput.system_prompt shipped ~300KB on every
    # model activity (Temporal TMPRL1103 payload warnings).
    _, _state["system_prompt"] = agent_identity()
    try:
        _state["client"] = await Client.connect(
            os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
            plugins=[StrandsPlugin()],
        )
    except Exception as error:  # noqa: BLE001 - API stays up so /health can report
        logger.warning("Temporal unavailable at startup: %s", error)
        _state["client"] = None
    async with httpx.AsyncClient(timeout=SERVER_HTTP_TIMEOUT.total_seconds()) as http, workspace_lifespan(app):
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
    # Per-turn allowlist enforced by ChatWorkflow's SelectionHook; empty = unrestricted.
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


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
    # Desktops are per-session containers claimed from a warm pool, so the
    # readiness signal here is "the image exists"; the startup script checks it.
    desktop_ok = True
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
        "desktop": desktop_ok,
        "models": models,
        "providers": PROVIDER_DISPLAY_NAMES,
        "default_model": default_model,
    }


# The Agent API's server-side tool types must never surface in the local catalog.
_AGENT_API_TYPE_NAMES = frozenset({
    "web_search",
    "fetch_url",
    "image_search",
    "people_search",
    "finance_search",
    "sandbox",
})


@app.get("/catalog")
async def catalog(session_id: str | None = None) -> dict[str, Any]:
    """Read-only catalog of session/community tools and discovered skills.

    ``session`` is the always-on set (minus Agent API types); ``discoverable`` is
    everything ``discover_tools_activity()`` lists minus ``session``. With a
    ``session_id``, merge that workflow's ``loaded_tools`` into ``session`` --
    graceful degradation: any failure means skip, never 500.
    """
    discovery = await discover_tools_activity()
    names: list[str] = []
    if discovery.get("status") == "success":
        names = [line for line in discovery["content"][0]["text"].splitlines() if line]
    session_names = {name for name in _SESSION_TOOLS if name not in _AGENT_API_TYPE_NAMES}
    if session_id:
        client = _state.get("client")
        if client is not None:
            try:
                handle = client.get_workflow_handle(session_id)
                loaded = await handle.query(ChatWorkflow.loaded_tools)
                for entry in loaded:
                    name = entry.get("name") if isinstance(entry, dict) else entry
                    if isinstance(name, str) and name not in _AGENT_API_TYPE_NAMES:
                        session_names.add(name)
            except Exception as error:  # noqa: BLE001 - catalog must not 500
                logger.warning("Could not query loaded_tools for %s: %s", session_id, error)
    discoverable = sorted(
        name for name in set(names) - session_names if name not in _AGENT_API_TYPE_NAMES
    )
    return {
        "tools": {
            "session": sorted(session_names),
            "discoverable": discoverable,
        },
        "skills": [
            {"name": s.name, "description": s.description or ""}
            for s in discovered_skills()
        ],
    }


@app.post("/sessions")
async def start_session(body: StartSession) -> dict[str, str]:
    await validated_model(body.model_id)
    if not await task_queue_has_pollers():
        raise HTTPException(503, "No worker is polling the task queue")

    client = temporal()
    session_id = f"chat-{os.urandom(8).hex()}"
    handle = await client.start_workflow(
        ChatWorkflow.run,
        ChatInput(
            model_id=body.model_id,
            system_prompt=_state["system_prompt"],
        ),
        id=session_id,
        task_queue=TASK_QUEUE,
    )
    # Resolves as soon as the workflow has claimed its warm desktop.
    desktop = await handle.execute_update(ChatWorkflow.desktop, rpc_timeout=DESKTOP_PROVISION_TIMEOUT)
    return {"session_id": session_id, "desktop_url": desktop["novnc_url"]}


@app.post("/sessions/{session_id}/turns/stream")
async def turn_stream(session_id: str, body: TurnRequest) -> StreamingResponse:
    if not body.prompt.strip():
        raise HTTPException(422, "A prompt is required")
    client = temporal()
    handle = client.get_workflow_handle(session_id)

    # Start reading where the stream currently ends, so this turn's frames are
    # not preceded by every earlier turn's replay.
    stream_client = WorkflowStreamClient.create(client, session_id)
    try:
        if body.model_id is not None:
            await validated_model(body.model_id)
        if body.reasoning_effort is not None:
            model_id = body.model_id or await handle.query(ChatWorkflow.model_id)
            reasoning_levels = json.loads(await asyncio.to_thread(REASONING_LEVELS_PATH.read_text))
            if body.reasoning_effort not in reasoning_levels.get(model_id, []):
                raise HTTPException(422, f"Unsupported reasoning effort for {model_id}: {body.reasoning_effort}")
        start_offset = await stream_client.get_offset()
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(404, f"Unknown session: {session_id}") from error
        if error.status == RPCStatusCode.DEADLINE_EXCEEDED:
            logger.warning("Session %s did not answer the stream-start query: %s", session_id, error)
            raise HTTPException(503, "The workflow did not answer its startup query. Check the worker for activation errors. No new turn was submitted.") from error
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
                    args=[body.prompt, body.tools, body.skills],
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
                        completion_client = stream_client
                        query_run_id = update_handle.workflow_run_id
                        offset_task = asyncio.create_task(completion_client.get_offset())
                        try:
                            async with asyncio.timeout_at(deadline):
                                while True:
                                    done, _ = await asyncio.wait(
                                        {offset_task}, timeout=SSE_SUBSCRIBE_RESTART_DELAY,
                                    )
                                    if done:
                                        end_offset = offset_task.result()
                                        break
                                    # An unpinned query can still be admitted to a
                                    # closing run. Unlike subscribe(), get_offset()
                                    # does not follow CAN while awaiting its reply.
                                    latest = await handle.describe()
                                    if latest.run_id != query_run_id:
                                        offset_task.cancel()
                                        with suppress(asyncio.CancelledError, Exception):
                                            await offset_task
                                        query_run_id = latest.run_id
                                        completion_client = WorkflowStreamClient.create(client, session_id)
                                        offset_task = asyncio.create_task(completion_client.get_offset())
                        finally:
                            offset_task.cancel()
                            with suppress(asyncio.CancelledError, Exception):
                                await offset_task
                        anext_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await anext_task
                        await _aclose(subscription)
                        if last_offset > end_offset:
                            raise RuntimeError("completion offset is behind the delivered offset")
                        if last_offset < end_offset:
                            # The watermark is global. Read every topic to reach it,
                            # but expose only the existing SSE topic contract.
                            subscription = completion_client.subscribe(None, from_offset=last_offset)
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
            if last_offset > start_offset:
                try:
                    await handle.execute_update(ChatWorkflow.truncate_stream, last_offset)
                except (RPCError, WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError) as error:
                    logger.warning("Could not truncate forwarded stream for %s at %s: %s",
                                   session_id, last_offset, error)

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform"},
    )


@app.post("/sessions/{session_id}/approve", status_code=204)
async def approve(session_id: str, body: ApproveRequest) -> None:
    del session_id, body
    raise HTTPException(410, "Approval is an interrupt on the agent result, not a session signal")


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
    runtime = container_artifact_dir(desktop_container_name(session_id)) / "runtime.json"
    try:
        fd = os.open(runtime, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
    # The container is the session's own; before its first action the worker
    # has not recorded an owner yet, and that is still this session's desktop.
    if state.get("owner") not in (None, {"namespace": temporal().namespace, "workflow_id": session_id}):
        raise HTTPException(409, "This chat does not own the desktop")
    return state


@app.get("/sessions/{session_id}/desktop-control")
async def desktop_control_status(session_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    state = await asyncio.to_thread(_desktop_mode, session_id)
    workflow_state = {"run_id": None, "ready": False}
    runtime_available = await task_queue_has_pollers(desktop=session_id)
    try:
        command = shlex.split(DESKTOP_VNC_GRANT_COMMAND.format(container=desktop_container_name(session_id)))
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
    transitioning = current != state
    available = runtime_available and not transitioning and state["mode"] != "recovery"
    # A transfer that moved the native state between the two reads explains a
    # mismatched VNC receipt; report the in-flight mode, never a false recovery.
    mode = current["mode"] if runtime_available or transitioning else "recovery"
    return {**workflow_state, "ready": available, "mode": mode, "epoch": str(current["epoch"])}


@app.post("/sessions/{session_id}/handoff", status_code=204)
async def handoff(session_id: str, body: HandoffRequest) -> None:
    if body.action == "give" and not body.message.strip():
        raise HTTPException(422, "Resume instructions are required")
    try:
        await temporal().get_workflow_handle(session_id).execute_update(
            ChatWorkflow.desktop_control, body.action,
        )
    except WorkflowUpdateFailedError as error:
        cause = error.cause
        while getattr(cause, "cause", None) is not None:
            cause = cause.cause
        raise HTTPException(409, str(cause)) from error
    except RPCError as error:
        status = 404 if error.status == RPCStatusCode.NOT_FOUND else 502
        raise HTTPException(status, str(error)) from error


@app.get("/sessions/{session_id}/desktop-images/{artifact_id}")
async def desktop_image(session_id: str, artifact_id: str) -> Response:
    from desktop_observation import observation_image
    from workspace_state import StateConflict

    await asyncio.to_thread(_desktop_mode, session_id)
    try:
        image = await asyncio.to_thread(
            observation_image, artifact_id, namespace=temporal().namespace, workflow_id=session_id,
            root=container_artifact_dir(desktop_container_name(session_id)),
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
