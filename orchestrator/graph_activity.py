"""Vendored ``graph_tool.graph`` as a streaming Temporal activity.

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
   event. Each node carries ``node_id`` (path-qualified for nested members),
   ``label``, ``parent_id`` (null at the top level), the real ``node_type``
   (``agent``/``skill_agent``/``swarm``/``graph``), and optional ``role``,
   ``model`` (``model_settings.model_id``), and ``skill``. Swarm members and
   nested-graph nodes are emitted recursively under their parent path; nested
   graph edges are path-qualified the same way. Edges are the declared
   ``{"from", "to"}`` pairs.
2. Flattened native events from ``Graph.stream_async``. Nested executors
   wrap inner ``multiagent_*`` events in ``multiagent_node_stream.event``;
   this activity unwraps them and path-qualifies ``node_id``.
   ``multiagent_handoff`` is the SDK batch transition
   (``from_node_ids`` / ``to_node_ids``, optional ``message``). Leaf
   ``multiagent_node_stream`` frames retain the whole sanitized agent event.
3. One final ``{"status": "success" | "error" | "cancelled", "content":
   [{"text": ...}]}`` terminal frame. On success it is also the activity's
   return value; on failure the activity RAISES a non-retryable
   ``ApplicationError`` so Strands hands the model a ``status: "error"`` tool
   result (a returned error dict would arrive as a successful call).

Parameters are typed Pydantic models (``GraphTopology`` / ``GraphNode`` /
``GraphEdge`` / ``GraphModelSettings``) so the generated tool schema is fully
structural; the Perplexity Agent API rejects untyped ``{"type": "object"}``
parameters outright.

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
from contextlib import aclosing, suppress
from typing import Any, Literal, Optional, cast

from pydantic import BaseModel, ConfigDict, Field
from strands import Agent
from strands.types._events import ToolResultEvent, ToolStreamEvent
from strands.types.tools import ToolResult, ToolUse
from graph_tool import graph as sibling_graph
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import (
    GRAPH_EXECUTION_TIMEOUT,
    GRAPH_QUIET_HEARTBEAT_INTERVAL,
    SUBAGENT_STREAM_BATCH_INTERVAL,
)
from subagent_support import (
    heartbeat,
    parent_agent,
    publishable,
    quiet_heartbeat_ticker,
    session_model,
    workspace_task_prefix,
)

# Typed activity parameters. Strands' FunctionToolMetadata emits their JSON
# Schema for the tool spec and the plugin's pydantic_data_converter carries
# them across the activity boundary (Temporal Strands README, Structured
# Output). A bare ``dict`` parameter serializes as an untyped ``{"type":
# "object"}``, which the Perplexity Agent API rejects (400 invalid request)
# and which leaves the model nothing structural to fill.


class GraphEdge(BaseModel):
    """One directed edge between two node ids in the same formation."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from", description="Source node id.")
    to: str = Field(description="Target node id.")


class GraphModelSettings(BaseModel):
    """Official ``model_settings``. ``model_id`` is a registered factory name."""

    model_config = ConfigDict(extra="forbid")

    model_id: Optional[str] = Field(
        default=None,
        description="Ignored. Every node uses the parent model. Leave this off.",
    )


class GraphNode(BaseModel):
    """One formation node. ``type`` selects the executor (default agent).

    ``swarm`` nodes carry ``agents`` (member nodes; plain agents or
    skill_agents only -- the SDK Swarm nests nothing) plus optional SDK limit
    knobs. ``graph`` nodes carry a nested ``topology``. ``skill_agent`` nodes
    carry ``skill`` (a registered skill name).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique node id.")
    type: Optional[Literal["agent", "skill_agent", "swarm", "graph"]] = Field(
        default=None,
        description=(
            "Node executor type. Omit for 'agent'. 'skill_agent' requires "
            "skill; 'swarm' requires agents; 'graph' requires topology. "
            "workflow/parallel are not supported."
        ),
    )
    role: Optional[str] = Field(default=None, description="Node role label.")
    system_prompt: Optional[str] = Field(
        default=None,
        description="The agent's instructions. Required for agent nodes; unused by swarm/graph container nodes.",
    )
    model_settings: Optional[GraphModelSettings] = Field(
        default=None,
        description="Ignored. Every node uses the parent model. Leave this off.",
    )
    tools: Optional[list[str]] = Field(
        default=None,
        description=(
            "Registry names this node needs. Name only the tools its work "
            "requires. Omit this and the node receives every tool the parent "
            "has. Load a community tool with load_tool before naming it. "
            "Unknown names are skipped. An empty list gives the node no tools."
        ),
    )
    skills: Optional[list[str]] = Field(
        default=None,
        description=(
            "Catalog skill names the parent assigns. The node gets the official "
            "skills tool scoped to exactly these names. Omit and the node has "
            "no skills. Unknown names fail the call."
        ),
    )
    skill: Optional[str] = Field(
        default=None,
        description="skill_agent nodes: the registered skill name this node embodies.",
    )
    agents: Optional[list["GraphNode"]] = Field(
        default=None,
        description=(
            "swarm nodes: member nodes (agent or skill_agent only, unique ids, "
            "no nesting). The SDK caps runs at 20 handoffs / 20 iterations by default."
        ),
    )
    topology: Optional["GraphTopology"] = Field(
        default=None,
        description="graph nodes: a nested {nodes, edges, entry_points} topology (recursive).",
    )
    entry_point: Optional[str] = Field(
        default=None,
        description="swarm nodes: member id that receives the task first; omit to use the first member.",
    )
    max_handoffs: Optional[int] = Field(
        default=None, description="swarm nodes: maximum handoffs (SDK default 20)."
    )
    max_iterations: Optional[int] = Field(
        default=None, description="swarm nodes: maximum node executions (SDK default 20)."
    )
    execution_timeout: Optional[float] = Field(
        default=None, description="swarm nodes: total execution timeout in seconds (SDK default 900)."
    )
    node_timeout: Optional[float] = Field(
        default=None, description="swarm nodes: per-member timeout in seconds (SDK default 300)."
    )


class GraphTopology(BaseModel):
    """A formation: nodes, structural edges, and entry points."""

    nodes: list[GraphNode] = Field(description="Formation nodes (at least one).")
    edges: list[GraphEdge] = Field(
        default_factory=list, description="Directed edges between top-level node ids."
    )
    entry_points: list[str] = Field(
        default_factory=list,
        description="Node ids that receive the task first; omit to auto-detect.",
    )


GraphNode.model_rebuild()


def _as_topology_dict(topology: GraphTopology | dict[str, Any] | None) -> dict[str, Any] | None:
    """The sibling tool's plain-dict topology (aliases such as ``from`` kept)."""
    if topology is None:
        return None
    if isinstance(topology, BaseModel):
        return topology.model_dump(by_alias=True, exclude_none=True)
    return topology


def _qualify(path: str, node_id: str) -> str:
    return f"{path}/{node_id}" if path else node_id


def planned_topology(topology: dict[str, Any], graph_id: str) -> dict[str, Any]:
    """The initial ``graph_topology`` event: declared nodes and edges.

    Nodes carry their real ``node_type``; swarm members and nested-graph
    nodes are emitted recursively with path-qualified ``node_id`` and
    ``parent_id`` so ``flatten_native_event`` can enrich runtime frames with
    the declared type/skill/model. Nested graph edges are path-qualified the
    same way. Runtime transitions arrive later as ``multiagent_handoff``
    events.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def visit(node_def: dict[str, Any], parent_path: str) -> None:
        label = node_def.get("id") or node_def.get("name") or ""
        path = _qualify(parent_path, label)
        entry: dict[str, Any] = {
            "node_id": path,
            "label": label,
            "parent_id": parent_path or None,
            "node_type": node_def.get("type") or "agent",
        }
        if node_def.get("role"):
            entry["role"] = node_def["role"]
        if node_def.get("skill"):
            entry["skill"] = node_def["skill"]
        nodes.append(entry)
        kind = node_def.get("type") or "agent"
        if kind == "swarm":
            for member in node_def.get("agents") or []:
                visit(member, path)
        elif kind == "graph":
            nested = node_def.get("topology") or {}
            for child in nested.get("nodes") or []:
                visit(child, path)
            for edge in nested.get("edges") or []:
                edges.append({
                    "from": _qualify(path, str(edge["from"])),
                    "to": _qualify(path, str(edge["to"])),
                })

    for node_def in topology.get("nodes") or []:
        visit(node_def, "")
    for edge in topology.get("edges") or []:
        edges.append({"from": edge["from"], "to": edge["to"]})
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
                    "event": publishable(inner, agent_event=True),
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


def _fail(message: str) -> ApplicationError:
    """A non-retryable tool failure.

    Raising (not returning) is the framework contract: the Temporal Strands
    ``activity_as_tool`` wrapper turns a *returned* value into a
    ``status: "success"`` tool result, so an error dict would reach the model
    as a successful call. A raised ``ApplicationError`` fails the activity;
    Strands' tool executor converts the exception into a ``status: "error"``
    tool result the model actually sees. Non-retryable because the input is
    what is wrong; a retry would repeat the same billable formation run.
    """
    return ApplicationError(message, type="GraphActivityError", non_retryable=True)


def _tool_input(**kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


async def _run_sibling(
    tool_input: dict[str, Any],
    parent: Agent,
    activity_id: str,
    on_event: Any = None,
) -> ToolResult:
    """Run one sibling-graph tool action, forwarding stream frames.

    Closes the generator on the way out (including cancellation) and never
    swallows ``CancelledError``.
    """
    tool_use: ToolUse = {"toolUseId": activity_id, "name": "graph", "input": tool_input}
    result: ToolResult | None = None
    async with aclosing(sibling_graph.stream(tool_use, {"agent": parent})) as stream:
        async for event in stream:
            heartbeat()
            if isinstance(event, ToolStreamEvent) and on_event is not None:
                data = cast(dict[str, Any], event["tool_stream_event"]).get("data")
                on_event(data)
            elif isinstance(event, ToolResultEvent):
                result = event.tool_result
    if result is None:
        raise _fail("graph produced no tool result")
    return result


def _result_text(result: dict[str, Any]) -> str:
    return "\n".join(
        block["text"]
        for block in result.get("content") or []
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


async def _heartbeat_ticker() -> None:
    """Keep the activity visibly alive through quiet stream periods."""
    await quiet_heartbeat_ticker(GRAPH_QUIET_HEARTBEAT_INTERVAL.total_seconds())


@activity.defn(name="graph")
async def graph_activity(
    action: str = "execute",
    graph_id: Optional[str] = None,
    topology: Optional[GraphTopology] = None,
    task: Optional[str] = None,
    tools: Optional[list[str]] = None,
) -> dict:
    """Create and execute multi-agent formations with live streaming.

    The single-call form is preferred: pass ``action="execute"`` with
    ``topology`` AND ``task`` together -- the formation is built, executed,
    and cleaned up in one call, and every node's progress streams live.

    A node's ``type`` (default ``agent``) selects the executor: ``agent``
    (id, role, system_prompt, optional tools, optional skills), ``skill_agent``
    (id + ``skill``, a registered skill name), ``swarm`` (id + ``agents``
    member nodes, optional entry_point/max_handoffs/max_iterations/
    execution_timeout/node_timeout), or ``graph`` (id + nested ``topology``,
    recursive). Edges are {"from", "to"}. Nodes with no edge between them run
    in the same batch. Every node uses the parent model; leave model_settings
    off. Name the tools and skills each node needs. Omit a node's tools and
    it receives every tool the parent has. Omit skills and the node has none.
    Each batch transition streams a multiagent_handoff event
    (from_node_ids, to_node_ids); nested formations' events arrive wrapped in
    multiagent_node_stream and are flattened with path-qualified node ids.

    Args:
        action: "execute" (default), "create", "status", "list", or "delete". "create",
            "list", "delete", and "execute" by graph_id alone are process-local
            to one worker; prefer the single-call execute form.
        graph_id: Optional stable label for the run (auto-derived if omitted).
        task: Task prompt executed through the formation (required for execute).
        topology: Official topology extended with node types: nodes (id,
            optional type agent|skill_agent|swarm|graph, role, system_prompt,
            optional model_settings, optional tools, optional skills; swarm
            nodes add agents + optional limit knobs, graph nodes add a nested
            topology, skill_agent nodes add skill), edges ({"from", "to"}),
            optional entry_points. Required for create and for single-call
            execute.
        tools: Name tools on each node. This argument is not a formation-wide
            tool list. A node that omits tools receives every tool the parent has.
    """
    from workflow import THINKING_TOPIC

    info = activity.info()
    activity_id = info.activity_id or "graph_run"
    attempt = getattr(info, "attempt", 1) or 1
    tool_use = {"name": "graph", "toolUseId": activity_id}
    topology_dict = _as_topology_dict(topology)

    if action == "execute" and not task:
        raise _fail("task prompt is required for execute action")
    if action == "create" and not (topology_dict and topology_dict.get("nodes")):
        raise _fail(
            "topology with a non-empty 'nodes' list is required for create action. "
            "Example: {'nodes': [{'id': 'researcher', 'role': 'research', "
            "'system_prompt': 'Research requirements.'}]}"
        )

    one_shot = action == "execute" and bool(
        topology_dict and topology_dict.get("nodes")
    )

    # TemporalAgent cannot cross the activity boundary (skill Pattern 2).
    # Rebuild a plain Agent; official graph_tool._select_tools filters
    # node / formation ``tools`` against parent.tool_registry by name.
    model = await session_model("graph")
    try:
        parent = await parent_agent(model)
    except ValueError as error:
        raise _fail(f"graph tools: {error}") from error

    stream_client = WorkflowStreamClient.from_within_activity(
        batch_interval=SUBAGENT_STREAM_BATCH_INTERVAL,
    )
    topic = stream_client.topic(THINKING_TOPIC)

    if task:
        task = workspace_task_prefix(info.workflow_id) + task

    run_label = graph_id or "graph"
    planned = planned_topology(topology_dict, run_label) if topology_dict else None
    meta = _meta_index(planned) if planned else {}

    def publish(data: Any) -> None:
        topic.publish({"tool_use": tool_use, "data": data})

    def publish_native(data: Any) -> None:
        for frame in flatten_native_event(data, "", meta):
            publish(frame)

    common = _tool_input(tools=tools)

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
                        topology=topology_dict,
                        task=task,
                        **common,
                    ),
                    parent,
                    activity_id,
                    on_event=publish_native,
                )
                if result.get("status") == "error":
                    raise _fail(_result_text(result))
                result.setdefault("toolUseId", activity_id)
                return result

            # Single-call execute: unique per-attempt registry id, create ->
            # execute -> delete-in-finally. Activity ids are only unique within
            # one workflow run, and the sibling registry is process-local to
            # the worker, so the workflow run id keeps concurrent sessions on
            # one worker apart.
            run_correlation = getattr(info, "workflow_run_id", "") or ""
            unique_id = f"{run_label}-{run_correlation}-{activity_id}-a{attempt}"
            if planned is not None:
                publish(planned)
            created = await _run_sibling(
                _tool_input(
                    action="create",
                    graph_id=unique_id,
                    topology=topology_dict,
                    **common,
                ),
                parent,
                f"{activity_id}-create",
            )
            if created.get("status") == "error":
                publish(publishable(created))
                raise _fail(_result_text(created))
            try:
                async with asyncio.timeout(GRAPH_EXECUTION_TIMEOUT.total_seconds()):
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
                message = f"Graph run exceeded {GRAPH_EXECUTION_TIMEOUT} and was stopped."
                publish({"status": "error", "content": [{"text": message}]})
                raise _fail(message) from None
            except asyncio.CancelledError:
                try:
                    publish(
                        {
                            "status": "cancelled",
                            "content": [{"text": "Graph run cancelled."}],
                            "toolUseId": activity_id,
                        }
                    )
                except Exception as publish_error:  # noqa: BLE001 - best-effort terminal frame
                    activity.logger.warning(
                        "graph cancellation notification failed for %s: %s",
                        unique_id,
                        publish_error,
                    )
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
                        "graph cleanup failed for %s: %s", unique_id, cleanup_error
                    )
            if result.get("status") == "error":
                raise _fail(_result_text(result))
            result.setdefault("toolUseId", activity_id)
            return result
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker
