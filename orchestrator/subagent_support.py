"""Shared support for sub-agent activities (``graph``, ``use_agent``, ``use_skill``).

One place for the three things every nested-agent activity needs:

1. **Session model resolution** — the worker's registered model factories
   (the same mapping ``StrandsPlugin`` gets, installed via :func:`configure`),
   resolved through the parent workflow's ``model_id`` query. Api-key-bearing
   clients stay inside the factory closures and never enter workflow state.
2. **Publish sanitization** — :func:`publishable` produces a JSON-safe copy of
   a native Strands event for the Temporal payload converter. Verified live
   (worker log, chat-d4188f93584c7d2d): publishing raw ``multiagent_*`` events
   fails the batch flush with ``Unable to serialize unknown type:
   strands.agent.agent.Agent``. Runtime ``agent`` / ``model`` handles are
   dropped, result dataclasses keep the fields the frontend reads, circular
   structures are cut by a depth cap.
3. **Activity-side parent Agent** — a live ``TemporalAgent`` cannot cross the
   activity boundary (skill Pattern 2). :func:`parent_agent` rebuilds a plain
   ``Agent`` so ``graph_tool._select_tools`` can read ``parent.tool_registry``.

``think_activity`` deliberately keeps its own copy of the model registry — it
predates this module and its behavior is pinned by tests; do not fold it in
opportunistically.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentResult, NodeResult
from temporalio import activity
from temporalio.exceptions import ApplicationError

# Worker-set registry of model factories, keyed by registered model id.
# Empty until configure() runs, which only happens in the worker.
_MODEL_FACTORIES: dict[str, Callable[[], Any]] = {}
logger = logging.getLogger(__name__)


def configure(model_factories: Mapping[str, Callable[[], Any]]) -> None:
    """Install the worker's model factories for all sub-agent activities."""
    _MODEL_FACTORIES.clear()
    _MODEL_FACTORIES.update(model_factories)


def model_factories() -> dict[str, Callable[[], Any]]:
    return dict(_MODEL_FACTORIES)


async def session_model(tool_name: str) -> Any:
    """The session's model: parent workflow's ``model_id`` query -> factory.

    ``tool_name`` labels the raised ``ApplicationError`` so failures say which
    tool could not resolve its model.
    """
    info = activity.info()
    error_type = f"{tool_name.title().replace('_', '')}ActivityError"
    if not info.workflow_id:
        raise ApplicationError(
            f"{tool_name} must be scheduled by a workflow",
            type=error_type,
            non_retryable=True,
        )
    handle = activity.client().get_workflow_handle(info.workflow_id)
    model_id = await handle.query("model_id")
    factory = _MODEL_FACTORIES.get(model_id)
    if factory is None:
        # Non-retryable: the catalog is fixed for the worker's lifetime, so
        # retrying cannot make an unregistered id appear.
        raise ApplicationError(
            f"{tool_name}: no registered model factory for {model_id!r}",
            type=error_type,
            non_retryable=True,
        )
    return factory()


async def session_model_id() -> str | None:
    """The session's model id (for display), or None outside a workflow."""
    info = activity.info()
    if not info.workflow_id:
        return None
    handle = activity.client().get_workflow_handle(info.workflow_id)
    return await handle.query("model_id")


async def session_loaded_tools() -> list[dict[str, str]]:
    """``{"path", "name"}`` records of the tools the orchestrator loaded onto
    itself via ``load_tool`` (the parent workflow's ``loaded_tools`` query).

    Empty outside a workflow. A run started before the query existed cannot
    answer it; that degrades to "nothing loaded" with a warning rather than
    failing the sub-agent call (telemetry.py convention).
    """
    try:
        info = activity.info()
    except RuntimeError:
        return []
    if not info.workflow_id:
        return []
    handle = activity.client().get_workflow_handle(info.workflow_id)
    try:
        records = await handle.query("loaded_tools")
    except Exception as error:  # noqa: BLE001 - optional, see docstring
        activity.logger.warning("loaded_tools query unavailable: %s", error)
        return []
    if not isinstance(records, list):
        return []
    return [
        {"path": str(rec["path"]), "name": str(rec["name"])}
        for rec in records
        if isinstance(rec, dict) and rec.get("path") and rec.get("name")
    ]


# Strands ModelStreamEvent.prepare() merges the node's invocation_state into
# every delta event, so text/reasoning/toolUse deltas carry the live Agent
# (and model) handles. They are never wire data.
_RUNTIME_HANDLE_KEYS = frozenset({"agent", "model"})
_MAX_DEPTH = 24
_REPR_LIMIT = 300


def publishable(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy of one native event for the Temporal payload converter."""
    if depth > _MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): publishable(item, depth + 1)
            for key, item in value.items()
            if key not in _RUNTIME_HANDLE_KEYS
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [publishable(item, depth + 1) for item in value]
    if isinstance(value, AgentResult):
        return {
            "__type__": "AgentResult",
            "stop_reason": publishable(value.stop_reason, depth + 1),
            "message": publishable(value.message, depth + 1),
        }
    if isinstance(value, NodeResult):
        return {
            "__type__": "NodeResult",
            "status": publishable(value.status, depth + 1),
            "result": publishable(value.result, depth + 1),
            "execution_time": value.execution_time,
            "execution_count": value.execution_count,
        }
    if isinstance(value, MultiAgentResult):
        return {
            "__type__": "MultiAgentResult",
            "status": publishable(value.status, depth + 1),
            "results": publishable(value.results, depth + 1),
            "execution_time": value.execution_time,
            "execution_count": value.execution_count,
        }
    if isinstance(value, BaseException):
        return {"__type__": type(value).__name__, "error": str(value)[:_REPR_LIMIT]}
    return {"__type__": type(value).__qualname__, "__repr__": repr(value)[:_REPR_LIMIT]}


def json_safe(value: Any) -> Any:
    """The value if json.dumps accepts it, else its sanitized copy."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return publishable(value)


def sanitize_stream_payload(raw: Any, name_key: str = "skill_name") -> dict[str, Any]:
    """Sanitize one sub-agent stream frame for the thinking topic.

    Shape shared by ``use_skill`` and ``use_agent`` standalone frames:
    ``{<name_key>, text, event}`` where ``text`` is the surfaced delta when
    the inner event carries one, and ``event`` is the sanitized native event
    (text AND tool use/results are retained for frontend actions).
    """
    if not isinstance(raw, dict):
        return {"payload": json_safe(raw)}
    label = raw.get(name_key)
    inner = raw.get("event")
    if isinstance(inner, dict):
        delta = inner.get("data")
        if isinstance(delta, str):
            return {name_key: label, "text": delta, "event": publishable(inner)}
    return {
        name_key: label,
        "event": json_safe(inner),
        "text": raw.get("text"),
    }


# Worker-factory keys that belong on the outer TemporalAgent request only.
_OUTER_MODEL_PARAMS = frozenset({"background", "store", "tools", "skills"})


def in_process_model(model: Any) -> Any:
    """The factory Model, stripped for a plain ``Agent`` inside an activity.

    Official ``create_agent_with_model`` / ``use_agent`` inherit a plain Model.
    The worker factory also attaches Agent API native tools, builtin skills,
    and ``background``+``store`` for the outer TemporalAgent. Merging those
    with Strands function tools is the live ``invalid request`` /
    ``An error occurred during streaming`` failure on graph nodes
    (chat-975a5afb58315ff9). The activity is already durable — drop the outer
    envelope so the node request is the official function-tool surface.
    """
    getter = getattr(model, "get_config", None)
    if not callable(getter):
        return model
    config = getter() or {}
    params = config.get("params")
    if not isinstance(params, dict) or not _OUTER_MODEL_PARAMS.intersection(params):
        return model
    cleaned = copy.deepcopy(
        {key: value for key, value in params.items() if key not in _OUTER_MODEL_PARAMS}
    )
    updater = getattr(model, "update_config", None)
    if callable(updater):
        updater(params=cleaned, stateful=False)
    return model


async def parent_agent(model: Any) -> Any:
    """Plain ``Agent`` whose ``tool_registry`` official ``_select_tools`` reads.

    Skill Pattern 2: a live ``TemporalAgent`` cannot cross the activity
    boundary. Official ``create_agent_with_model`` inherits
    ``parent.tool_registry`` by name (omit ``tools`` → every key; unknown
    name → warning, not an import). Rebuild that parent with the tools that
    actually run inside this activity: official ``load_tool`` records from
    the session. Names that were not loaded are not on the registry.
    """
    from strands import Agent
    from strands_tools.load_tool import load_tool as official_load_tool

    from load_tool import tool_file_path

    parent = Agent(
        model=model,
        tools=[],
        system_prompt="",
        callback_handler=None,
    )
    have: set[str] = set()
    for rec in await session_loaded_tools():
        if rec["name"] in have:
            continue
        result = official_load_tool(
            path=tool_file_path(rec["path"]), name=rec["name"], agent=parent
        )
        if result.get("status") != "success":
            logger.warning("load_tool %s not restored: %s", rec["name"], result)
        else:
            have.add(rec["name"])
    return parent


def agent_workspace(workflow_id: str | None = None) -> Path:
    """Session directory for graph/skill/use_agent writes. Not the host home."""
    from config import AGENT_WORKSPACE_ROOT

    raw = workflow_id or "anon"
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)
    root = AGENT_WORKSPACE_ROOT / name
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    return root


def workspace_task_prefix(workflow_id: str | None = None) -> str:
    path = agent_workspace(workflow_id) / "workspace"
    return (
        f"Session workspace (write only here): {path}\n"
        "Do not use /home/user, ~, or the host home directory.\n\n"
    )


def heartbeat() -> None:
    """One beat per streamed chunk; no-op outside an activity context."""
    try:
        activity.heartbeat()
    except RuntimeError:  # pragma: no cover - no activity context (unit tests)
        logger.debug("Sub-agent heartbeat skipped outside activity context")


async def quiet_heartbeat_ticker(interval_seconds: float) -> None:
    """Keep the activity visibly alive through quiet stream periods.

    Sub-agent streams go silent while a node waits on a long tool call, so a
    per-chunk heartbeat alone can outlast the heartbeat timeout. Run this as a
    task alongside the stream loop and cancel it in ``finally``.
    """
    import asyncio

    while True:
        await asyncio.sleep(interval_seconds)
        heartbeat()
