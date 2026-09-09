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
3. **Safe tool resolution** — :func:`resolve_tools` builds the in-activity
   tool list for ephemeral parent/sub agents: the base set (``use_skill``,
   ``file_read``, ``file_write``) plus community tools resolved through the
   existing ``load_tool`` search roots (``load_tool.tool_file_path`` +
   ``strands.tools.loader``). Unknown names raise a clear error instead of an
   unbounded import bypass.

``think_activity`` deliberately keeps its own copy of the model registry — it
predates this module and its behavior is pinned by tests; do not fold it in
opportunistically.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any

from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentResult, NodeResult
from temporalio import activity
from temporalio.exceptions import ApplicationError

# Worker-set registry of model factories, keyed by registered model id.
# Empty until configure() runs, which only happens in the worker.
_MODEL_FACTORIES: dict[str, Callable[[], Any]] = {}


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


class UnknownToolError(ValueError):
    """A requested tool name resolves to nothing in the safe registry."""


def canonical_tool_name(tool: Any) -> str:
    """The name Strands registers the tool under.

    Decorated tools carry ``tool_name``; module-style tools (e.g.
    ``strands_tools.file_read``) carry their name in ``TOOL_SPEC["name"]`` and
    have a dotted ``__name__`` that never matches a requested name.
    """
    name = getattr(tool, "tool_name", None)
    if isinstance(name, str) and name:
        return name
    spec = getattr(tool, "TOOL_SPEC", None)
    if isinstance(spec, dict) and isinstance(spec.get("name"), str):
        return spec["name"]
    return str(getattr(tool, "__name__", "") or "").rpartition(".")[2]


def base_subagent_tools(model: Any) -> list[Any]:
    """Tools every ephemeral parent/sub agent carries.

    The two reference agentskills tools when the catalog is available —
    Pattern 3 ``use_skill(skill_name, request)`` (create_skill_agent_tool)
    and Pattern 2 ``skill(skill_name)`` (create_skill_tool) — plus the
    Pattern-3 sandbox tools (``file_read`` / ``file_write``), matching
    examples 2 and 3 of aws-samples/sample-strands-agents-agentskills.
    Never includes ``graph`` or ``use_agent`` themselves — recursion is
    structural, via topology.
    """
    from skills_config import (
        SkillsUnavailable,
        create_inline_skill_tool,
        create_use_skill_tool,
        skill_subagent_tools,
    )

    tools: list[Any] = []
    try:
        tools.append(create_use_skill_tool(model))
        tools.append(create_inline_skill_tool())
    except SkillsUnavailable:
        pass
    tools.extend(skill_subagent_tools())
    return tools


def resolve_tools(names: list[str] | None, model: Any) -> list[Any]:
    """The base sub-agent tools plus any requested community tools.

    Extra names resolve through the existing safe loader path only
    (``load_tool.tool_file_path`` search roots -> ``load_tools_from_file_path``).
    Raises :class:`UnknownToolError` with the full unknown list so callers can
    return a clear tool error instead of silently running with fewer tools.
    """
    import os

    from strands.tools.loader import load_tools_from_file_path

    from load_tool import tool_file_path

    tools = base_subagent_tools(model)
    have = {canonical_tool_name(tool) for tool in tools}
    unknown: list[str] = []
    for name in names or []:
        if name in have:
            continue
        path = tool_file_path(name)
        if not os.path.exists(path):
            unknown.append(name)
            continue
        loaded = [
            candidate
            for candidate in load_tools_from_file_path(path)
            if candidate.tool_name == name
        ]
        if not loaded:
            unknown.append(name)
            continue
        tools.append(loaded[0])
        have.add(name)
    if unknown:
        raise UnknownToolError(
            f"unknown tool(s) {sorted(unknown)!r}; available: "
            f"{sorted(have)!r} plus community modules under orchestrator/tools/"
        )
    return tools


def heartbeat() -> None:
    """One beat per streamed chunk; no-op outside an activity context."""
    try:
        activity.heartbeat()
    except RuntimeError:  # pragma: no cover - no activity context (unit tests)
        pass


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
