"""Sibling ``strands_graph_tool.graph`` as a streaming Temporal activity.

Thin ``@activity.defn`` wrapper per the Temporal Strands README (decorate
non-deterministic tools with ``@activity.defn``, register on the worker,
expose with ``activity_as_tool``). The async-generator ``graph`` tool runs
here; every native ``multiagent_*`` event is flattened, sanitized, and
published on ``THINKING_TOPIC`` through
``WorkflowStreamClient.from_within_activity()`` -- the same side-channel
``use_skill_activity`` and ``think_activity`` use.

Frame contract (published on ``THINKING_TOPIC``, one frame per event):

    {"tool_use": {"name": "graph", "toolUseId": <activity_id>},
     "data": <event>}

``data`` events, in order:

1. One initial ``{"type": "graph_topology", "graph_id", "nodes", "edges"}``
   planned-topology event derived recursively from the supplied topology.
   Every node entry carries ``node_id`` (path-qualified, ``/``-joined),
   ``label`` (the raw declared id), ``parent_id`` (containing formation's
   path or null), ``node_type`` (``agent`` | ``skill_agent`` | ``swarm`` |
   ``graph`` | ``workflow`` | ``parallel``), plus optional ``skill`` and
   ``model`` (declared model_provider). Edges are the declared structural
   edges (nested graph edges and workflow dependencies included), all
   path-qualified. Structural containment is ``parent_id``, never an edge.
2. Flattened native events. Nested executors wrap inner ``multiagent_*``
   events in ``multiagent_node_stream.event`` recursively; this activity
   unwraps them losslessly and re-qualifies ``node_id`` with the full path
   (``outer/inner``), so duplicate leaf ids under different parents never
   collide. Start/stop/stream frames carry ``label``/``parent_id`` (and
   declared ``node_type``/``skill``/``model`` when the path appears in the
   supplied topology). ``multiagent_handoff`` ``from_node_ids`` /
   ``to_node_ids`` are path-prefixed the same way. Leaf
   ``multiagent_node_stream`` frames retain the whole sanitized agent event
   (text deltas AND tool use / tool results), not text only.
3. One final ``{"status": "success" | "error" | "cancelled", "content":
   [{"text": ...}]}`` terminal frame (also the activity's return value).

Runtime ``Agent``/``model`` handles, result dataclasses, and circular
structures are sanitized by ``subagent_support.publishable`` before publish.

One-shot semantics: ``action="execute"`` with an inline ``topology`` AND
``task`` creates the formation under a unique per-attempt id
(``<activity_id>-a<attempt>``), executes it, and deletes the registry entry
in ``finally`` -- no substring error masking, no cross-run collisions.
``create``/``list``/``delete``/``execute``-by-id remain supported through the
sibling tool's public actions, with honest process-local semantics: the graph
registry lives in one worker process, so a graph created by one activity is
only visible to a later activity if Temporal happens to run it on the same
worker. Prefer the one-shot form.
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any, Optional, cast

from strands import Agent
from strands.types._events import ToolResultEvent, ToolStreamEvent
from strands_graph_tool.graph import graph as sibling_graph
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import (
    GRAPH_EXECUTION_TIMEOUT,
    GRAPH_QUIET_HEARTBEAT_INTERVAL,
    THINK_STREAM_BATCH_INTERVAL,
)
from subagent_support import (
    UnknownToolError,
    heartbeat,
    publishable,
    quiet_heartbeat_ticker,
    resolve_tools,
    session_model,
)

_NODE_KINDS = ("agent", "skill_agent", "swarm", "graph", "workflow", "parallel")


def _qualify(path: str, node_id: str) -> str:
    return f"{path}/{node_id}" if path else node_id


def planned_topology(topology: dict[str, Any], graph_id: str) -> dict[str, Any]:
    """The initial ``graph_topology`` event: declared nodes/edges, recursively.

    Structural edges come only from the supplied topology (top-level and
    nested graph ``edges``, workflow ``dependencies``); swarm/parallel members
    have no static edges -- their flow arrives as runtime handoffs.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def add_edges(raw_edges: Any, path: str) -> None:
        for edge in raw_edges or []:
            edges.append(
                {
                    "from": _qualify(path, edge["from"]),
                    "to": _qualify(path, edge["to"]),
                }
            )

    def walk(node_def: dict[str, Any], path: str, parent: str | None) -> None:
        kind = node_def.get("type", "agent")
        label = node_def.get("id") or node_def.get("task_id") or ""
        full = _qualify(path, label)
        entry: dict[str, Any] = {
            "node_id": full,
            "label": label,
            "parent_id": parent,
            "node_type": kind,
        }
        if node_def.get("skill"):
            entry["skill"] = node_def["skill"]
        if node_def.get("model_provider"):
            entry["model"] = node_def["model_provider"]
        nodes.append(entry)
        if kind == "graph":
            for child in node_def.get("nodes") or []:
                walk(child, full, full)
            add_edges(node_def.get("edges"), full)
        elif kind in ("swarm", "parallel"):
            for child in node_def.get("agents") or []:
                walk(child, full, full)
        elif kind == "workflow":
            for task_def in node_def.get("tasks") or []:
                task_id = task_def["task_id"]
                task_full = _qualify(full, task_id)
                task_entry: dict[str, Any] = {
                    "node_id": task_full,
                    "label": task_id,
                    "parent_id": full,
                    "node_type": "skill_agent" if "skill" in task_def else "agent",
                }
                if task_def.get("skill"):
                    task_entry["skill"] = task_def["skill"]
                if task_def.get("model_provider"):
                    task_entry["model"] = task_def["model_provider"]
                nodes.append(task_entry)
                for dep in task_def.get("dependencies") or []:
                    edges.append({"from": _qualify(full, dep), "to": task_full})

    for node_def in topology.get("nodes") or []:
        walk(node_def, "", None)
    add_edges(topology.get("edges"), "")
    return {"type": "graph_topology", "graph_id": graph_id, "nodes": nodes, "edges": edges}


def _meta_index(planned: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["node_id"]: node for node in planned.get("nodes", [])}


def flatten_native_event(
    event: Any,
    path: str = "",
    meta: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Flatten one native graph event to path-qualified, JSON-safe frames.

    Nested ``multiagent_*`` events wrapped in ``multiagent_node_stream.event``
    are unwrapped recursively; everything else passes through sanitized. The
    normalization is lossless for actions: leaf stream frames keep the whole
    sanitized inner agent event.
    """
    meta = meta or {}
    if not isinstance(event, dict):
        return [{"type": "unknown", "event": publishable(event)}]
    etype = str(event.get("type") or "")

    def enrich(frame: dict[str, Any], node_path: str) -> dict[str, Any]:
        parent, _, label = node_path.rpartition("/")
        frame.setdefault("label", label or node_path)
        frame.setdefault("parent_id", parent or None)
        declared = meta.get(node_path)
        if declared:
            frame["node_type"] = declared["node_type"]
            if declared.get("skill"):
                frame["skill"] = declared["skill"]
            if declared.get("model"):
                frame["model"] = declared["model"]
        return frame

    if etype == "multiagent_node_stream":
        node_path = _qualify(path, str(event.get("node_id") or ""))
        inner = event.get("event")
        if isinstance(inner, dict) and str(inner.get("type") or "").startswith(
            "multiagent_"
        ):
            return flatten_native_event(inner, node_path, meta)
        return [
            enrich(
                {
                    "type": "multiagent_node_stream",
                    "node_id": node_path,
                    "event": publishable(inner),
                },
                node_path,
            )
        ]
    if etype in ("multiagent_node_start", "multiagent_node_stop",
                 "multiagent_node_cancel", "multiagent_node_interrupt"):
        node_path = _qualify(path, str(event.get("node_id") or ""))
        frame = {
            key: publishable(value)
            for key, value in event.items()
            if key != "node_id"
        }
        frame["node_id"] = node_path
        return [enrich(frame, node_path)]
    if etype == "multiagent_handoff":
        frame = {
            "type": "multiagent_handoff",
            "from_node_ids": [
                _qualify(path, str(n)) for n in event.get("from_node_ids") or []
            ],
            "to_node_ids": [
                _qualify(path, str(n)) for n in event.get("to_node_ids") or []
            ],
        }
        if event.get("message") is not None:
            frame["message"] = publishable(event["message"])
        return [frame]
    if etype == "multiagent_result":
        frame = {"type": "multiagent_result", "result": publishable(event.get("result"))}
        if path:
            frame["node_id"] = path
        return [frame]
    # Final {status, content} tool yields and anything unrecognized: sanitized
    # passthrough so the terminal frame reaches the frontend unchanged.
    return [cast(dict, publishable(event))]


def _error_result(activity_id: str, text: str) -> dict[str, Any]:
    return {
        "status": "error",
        "content": [{"text": text}],
        "toolUseId": activity_id,
    }


def _tool_input(**kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


async def _run_sibling(
    tool_input: dict[str, Any],
    parent: Agent,
    activity_id: str,
    on_event: Any = None,
) -> dict[str, Any]:
    """Run one sibling-graph tool action, forwarding stream frames.

    Closes the generator on the way out (including cancellation) and never
    swallows ``CancelledError``.
    """
    tool_use = {"toolUseId": activity_id, "name": "graph", "input": tool_input}
    result: dict[str, Any] = {}
    stream = sibling_graph.stream(tool_use, {"agent": parent})
    try:
        async for event in stream:
            heartbeat()
            if isinstance(event, ToolStreamEvent) and on_event is not None:
                data = cast(dict[str, Any], event["tool_stream_event"]).get("data")
                on_event(data)
            elif isinstance(event, ToolResultEvent):
                result = cast(dict[str, Any], event.tool_result)
    finally:
        await stream.aclose()
    return result


async def _heartbeat_ticker() -> None:
    """Keep the activity visibly alive through quiet stream periods."""
    await quiet_heartbeat_ticker(GRAPH_QUIET_HEARTBEAT_INTERVAL.total_seconds())


@activity.defn(name="graph")
async def graph_activity(
    action: str = "execute",
    graph_id: Optional[str] = None,
    topology: Optional[dict] = None,
    task: Optional[str] = None,
    model_provider: Optional[str] = None,
    model_settings: Optional[dict[str, Any]] = None,
    tools: Optional[list[str]] = None,
) -> dict:
    """Create and execute multi-agent formations with live streaming.

    The single-call form is preferred: pass ``action="execute"`` with
    ``topology`` AND ``task`` together -- the formation is built, executed,
    and cleaned up in one call, and every node's progress streams live.

    Node "type" values (default "agent"): "agent" (one specialist:
    id, system_prompt, optional model_provider/model_settings/tools),
    "skill_agent" (a registered skill's isolated sub-agent: id, skill),
    "swarm" (dynamic handoffs between 2-5 agents: id, agents), "graph"
    (a nested pipeline as one node, recursive: id, nodes, edges),
    "workflow" (task list with dependencies: id, tasks -- each task has
    task_id, description, optional dependencies/skill/system_prompt), and
    "parallel" (independent fan-out: id, agents). Nodes that omit
    model_provider/model_settings inherit the session's model.

    Args:
        action: "execute" (default), "create", "list", or "delete". "create",
            "list", "delete", and "execute" by graph_id alone are process-local
            to one worker; prefer the single-call execute form.
        graph_id: Optional stable label for the run (auto-derived if omitted).
        task: Task prompt executed through the formation (required for execute).
        topology: Formation topology: {"nodes": [...], "edges": [{"from", "to"}],
            "entry_points": [...]} with the per-node "type" extension described
            above. Required for create and for single-call execute.
        model_provider: Optional default model provider override for nodes;
            omit to inherit the session model everywhere.
        model_settings: Optional default model configuration for override nodes.
        tools: Optional tool names available to formation agents, resolved from
            the built-ins (use_skill, file_read, file_write) plus community
            modules under orchestrator/tools/. Unknown names fail the call with
            the available list.
    """
    from workflow import THINKING_TOPIC

    info = activity.info()
    activity_id = info.activity_id or "graph_run"
    attempt = getattr(info, "attempt", 1) or 1
    tool_use = {"name": "graph", "toolUseId": activity_id}

    if action == "execute" and not task:
        return _error_result(activity_id, "task prompt is required for execute action")
    if action == "create" and not (
        isinstance(topology, dict) and topology.get("nodes")
    ):
        return _error_result(
            activity_id,
            "topology with a non-empty 'nodes' list is required for create action. "
            "Example: {'nodes': [{'id': 'researcher', 'type': 'agent', "
            "'system_prompt': 'Research requirements.'}]}",
        )

    one_shot = (
        action == "execute"
        and isinstance(topology, dict)
        and bool(topology.get("nodes"))
    )

    try:
        model = await session_model("graph")
        try:
            parent_tools = resolve_tools(tools, model)
        except UnknownToolError as error:
            return _error_result(activity_id, f"graph tools: {error}")
        parent = Agent(
            model=model, tools=parent_tools, system_prompt="", callback_handler=None
        )

        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=THINK_STREAM_BATCH_INTERVAL,
        )
        topic = stream_client.topic(THINKING_TOPIC)

        run_label = graph_id or "graph"
        planned = (
            planned_topology(topology, run_label)
            if isinstance(topology, dict)
            else None
        )
        meta = _meta_index(planned) if planned else {}

        def publish(data: Any) -> None:
            topic.publish({"tool_use": tool_use, "data": data})

        def publish_native(data: Any) -> None:
            for frame in flatten_native_event(data, "", meta):
                publish(frame)

        common = _tool_input(
            model_provider=model_provider,
            model_settings=model_settings,
            tools=tools,
        )

        ticker = asyncio.create_task(_heartbeat_ticker())
        try:
            async with stream_client:
                if not one_shot:
                    # Management actions and execute-by-id: the sibling tool's
                    # own public actions, process-local registry semantics.
                    result = await _run_sibling(
                        _tool_input(
                            action=action,
                            graph_id=graph_id,
                            topology=topology,
                            task=task,
                            **common,
                        ),
                        parent,
                        activity_id,
                        on_event=publish_native,
                    )
                    result.setdefault("toolUseId", activity_id)
                    return result

                # Single-call execute: unique per-attempt registry id, create ->
                # execute -> delete-in-finally. No substring error matching and
                # no collisions between runs or retry attempts. Activity ids are
                # only unique within one workflow run, and the sibling registry
                # is process-local to the worker, so the workflow run id is
                # required to keep concurrent sessions on one worker apart.
                run_correlation = getattr(info, "workflow_run_id", "") or ""
                unique_id = f"{run_label}-{run_correlation}-{activity_id}-a{attempt}"
                if planned is not None:
                    publish(planned)
                created = await _run_sibling(
                    _tool_input(
                        action="create",
                        graph_id=unique_id,
                        topology=topology,
                        **common,
                    ),
                    parent,
                    f"{activity_id}-create",
                )
                if created.get("status") == "error":
                    created.setdefault("toolUseId", activity_id)
                    publish(publishable(created))
                    return created
                try:
                    async with asyncio.timeout(
                        GRAPH_EXECUTION_TIMEOUT.total_seconds()
                    ):
                        result = await _run_sibling(
                            _tool_input(
                                action="execute",
                                graph_id=unique_id,
                                task=task,
                                **common,
                            ),
                            parent,
                            activity_id,
                            on_event=publish_native,
                        )
                except TimeoutError:
                    final = _error_result(
                        activity_id,
                        f"Graph run exceeded {GRAPH_EXECUTION_TIMEOUT} and was stopped.",
                    )
                    publish(publishable(final))
                    return final
                except asyncio.CancelledError:
                    try:
                        publish(
                            {
                                "status": "cancelled",
                                "content": [{"text": "Graph run cancelled."}],
                                "toolUseId": activity_id,
                            }
                        )
                    except Exception:  # noqa: BLE001 - best-effort terminal frame
                        pass
                    raise
                finally:
                    # Best-effort registry cleanup: a failure here must not
                    # mask the real outcome. The id is unique per attempt, so
                    # a leaked entry can never collide with a later run.
                    try:
                        await _run_sibling(
                            _tool_input(action="delete", graph_id=unique_id),
                            parent,
                            f"{activity_id}-delete",
                        )
                    except Exception as cleanup_error:  # noqa: BLE001
                        activity.logger.warning(
                            "graph cleanup failed for %s: %s",
                            unique_id,
                            cleanup_error,
                        )
                result.setdefault("toolUseId", activity_id)
                return result
        finally:
            ticker.cancel()
    except (ApplicationError, asyncio.CancelledError):
        raise
    except Exception as error:  # noqa: BLE001 - tool boundary
        message = f"Error in graph tool: {error}\n{traceback.format_exc()}"
        activity.logger.error("graph activity failed: %s", error)
        return _error_result(activity_id, message)
