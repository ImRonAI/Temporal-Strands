"""Official ``strands_graph_tool.graph`` as a durable Temporal activity.

Thin ``@activity.defn`` wrapper per the Temporal Strands README (decorate
non-deterministic tools with ``@activity.defn``, register on the worker,
expose with ``activity_as_tool``). The async-generator ``graph`` tool runs
here; each ``ToolStreamEvent`` is published on ``THINKING_TOPIC`` through
``WorkflowStreamClient.from_within_activity()`` — the same side-channel
mechanism ``think_activity.py`` uses and ``TemporalAgent(streaming_topic=...)``
documents.

https://docs.temporal.io/develop/python/integrations/strands-agents#tools
"""

from __future__ import annotations

import traceback
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Literal, TypedDict, cast

from strands import Agent
from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentResult, NodeResult
from strands.types._events import ToolResultEvent, ToolStreamEvent
from strands.types.tools import ToolUse
from strands_graph_tool import graph as official_graph
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import THINK_STREAM_BATCH_INTERVAL

_MODEL_FACTORIES: dict[str, Callable[[], Any]] = {}

# Strands ModelStreamEvent.prepare() merges the node's invocation_state into
# every delta event, so text/reasoning/toolUse deltas carry the live Agent
# (and model) handles. They are never wire data.
_RUNTIME_HANDLE_KEYS = frozenset({"agent", "model"})
_MAX_DEPTH = 24
_REPR_LIMIT = 300


def _publishable(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy of one native graph event for the Temporal payload converter.

    Verified live (worker log, chat-d4188f93584c7d2d): publishing the raw
    ``multiagent_*`` events fails the batch flush with ``Unable to serialize
    unknown type: strands.agent.agent.Agent`` — the activity then errors
    before its final ``{"status", "content"}`` frame, so the UI's snapshot
    stays "running" forever. Result dataclasses (``NodeResult`` /
    ``AgentResult`` / ``MultiAgentResult``) keep the fields the frontend
    reads (status, final message); metrics are dropped as payload weight.
    """
    if depth > _MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _publishable(item, depth + 1)
            for key, item in value.items()
            if key not in _RUNTIME_HANDLE_KEYS
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_publishable(item, depth + 1) for item in value]
    if isinstance(value, AgentResult):
        return {
            "__type__": "AgentResult",
            "stop_reason": _publishable(value.stop_reason, depth + 1),
            "message": _publishable(value.message, depth + 1),
        }
    if isinstance(value, NodeResult):
        return {
            "__type__": "NodeResult",
            "status": _publishable(value.status, depth + 1),
            "result": _publishable(value.result, depth + 1),
            "execution_time": value.execution_time,
            "execution_count": value.execution_count,
        }
    if isinstance(value, MultiAgentResult):
        return {
            "__type__": "MultiAgentResult",
            "status": _publishable(value.status, depth + 1),
            "results": _publishable(value.results, depth + 1),
            "execution_time": value.execution_time,
            "execution_count": value.execution_count,
        }
    if isinstance(value, BaseException):
        return {"__type__": type(value).__name__, "error": str(value)[:_REPR_LIMIT]}
    return {"__type__": type(value).__qualname__, "__repr__": repr(value)[:_REPR_LIMIT]}


def configure(model_factories: Mapping[str, Callable[[], Any]]) -> None:
    """Install the worker's model factories (same mapping as StrandsPlugin)."""
    _MODEL_FACTORIES.clear()
    _MODEL_FACTORIES.update(model_factories)


async def _session_model() -> Any:
    info = activity.info()
    if not info.workflow_id:
        raise ApplicationError(
            "graph must be scheduled by a workflow",
            type="GraphActivityError",
            non_retryable=True,
        )
    handle = activity.client().get_workflow_handle(info.workflow_id)
    model_id = await handle.query("model_id")
    factory = _MODEL_FACTORIES.get(model_id)
    if factory is None:
        raise ApplicationError(
            f"graph: no registered model factory for {model_id!r}",
            type="GraphActivityError",
            non_retryable=True,
        )
    return factory()


class NodeDef(TypedDict, total=False):
    id: str
    type: Literal["agent", "skill_agent", "swarm", "graph", "workflow", "parallel"]
    system_prompt: str
    skill: str
    agents: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    tools: list[str]


class EdgeDef(TypedDict, total=False):
    from_node: str
    to_node: str


class Topology(TypedDict, total=False):
    nodes: list[NodeDef]
    edges: list[dict[str, Any]]
    entry_points: list[str]


def _tool_input(
    action: str,
    graph_id: str | None,
    topology: dict[str, Any] | None,
    task: str | None,
    model_provider: str | None,
    model_settings: dict[str, Any] | None,
    tools: list[str] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": action}
    if graph_id is not None:
        payload["graph_id"] = graph_id
    if topology is not None:
        payload["topology"] = topology
    if task is not None:
        payload["task"] = task
    if model_provider is not None:
        payload["model_provider"] = model_provider
    if model_settings is not None:
        payload["model_settings"] = model_settings
    if tools is not None:
        payload["tools"] = tools
    return payload


@activity.defn(name="graph")
async def graph_activity(
    action: Literal["execute", "create", "list", "delete"] = "execute",
    graph_id: str | None = None,
    topology: Topology | None = None,
    task: str | None = None,
    model_provider: str | None = None,
    model_settings: dict[str, Any] | None = None,
    tools: list[str] | None = None,
) -> dict[str, Any]:
    """Formation graph tool: create, execute, list, or delete multi-agent graphs.

    Node ``type`` values (default ``agent``): ``agent``, ``skill_agent``,
    ``swarm``, ``graph`` (nested), ``workflow`` (task DAG), ``parallel``.
    ``execute`` streams native ``multiagent_*`` events before the final
    ``{"status", "content"}`` result.

    The ``agent`` parameter on the underlying tool is supplied here as the
    session's parent ``Agent`` (model inheritance for nodes that omit
    ``model_provider`` / ``model_settings``).

    Args:
        action: Action to perform: "execute" (default), "create", "list", or "delete".
        graph_id: Unique identifier for the graph. Auto-generated if omitted.
        topology: Graph topology definition. Must contain "nodes" list. Nodes can be specialists ("agent"), registered skills ("skill_agent"), collaborative swarms ("swarm"), task workflows ("workflow"), parallel fan-outs ("parallel"), or nested pipelines ("graph").
        task: Task prompt to execute through the graph (required for "execute").
        model_provider: Optional default model provider for nodes that omit it.
        model_settings: Optional default model configuration for nodes.
        tools: Optional default tool names available to agents in the graph.
    """
    from workflow import THINKING_TOPIC

    activity_id = activity.info().activity_id or "graph_run"
    effective_graph_id = graph_id or f"graph_{activity_id}"

    # Validation with clear actionable guidance
    if action == "create":
        if not topology or not isinstance(topology, dict) or not topology.get("nodes"):
            return {
                "status": "error",
                "content": [
                    {
                        "text": (
                            "topology with a non-empty 'nodes' list is required for create action. "
                            "Example: {'nodes': [{'id': 'researcher', 'type': 'agent', 'system_prompt': 'Research requirements.'}]}"
                        )
                    }
                ],
                "toolUseId": activity_id,
            }

    if action == "execute":
        if not task:
            return {
                "status": "error",
                "content": [{"text": "task prompt is required for execute action"}],
                "toolUseId": activity_id,
            }

    try:
        model = await _session_model()
        parent = Agent(model=model, tools=[], system_prompt="", callback_handler=None)
        invocation_state: dict[str, Any] = {"agent": parent, "model": model}

        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=THINK_STREAM_BATCH_INTERVAL,
        )
        topic = stream_client.topic(THINKING_TOPIC)
        result: dict[str, Any] = {}

        async with stream_client:
            try:
                # If executing with an inline topology, auto-create the graph first in the same turn
                if action == "execute" and topology and isinstance(topology, dict) and topology.get("nodes"):
                    create_use: ToolUse = {
                        "toolUseId": f"{activity_id}_create",
                        "name": "graph",
                        "input": _tool_input(
                            "create",
                            effective_graph_id,
                            topology,
                            None,
                            model_provider,
                            model_settings,
                            tools,
                        ),
                    }
                    create_result: dict[str, Any] = {}
                    async for event in official_graph.stream(create_use, invocation_state):
                        if isinstance(event, ToolResultEvent):
                            create_result = cast(dict[str, Any], event.tool_result)
                    if create_result.get("status") == "error":
                        return create_result

                exec_use: ToolUse = {
                    "toolUseId": activity_id,
                    "name": "graph",
                    "input": _tool_input(
                        action,
                        effective_graph_id,
                        topology if action == "create" else None,
                        task,
                        model_provider,
                        model_settings,
                        tools,
                    ),
                }

                async for event in official_graph.stream(exec_use, invocation_state):
                    if isinstance(event, ToolStreamEvent):
                        topic.publish(
                            _publishable(cast(dict[str, Any], event["tool_stream_event"]))
                        )
                    elif isinstance(event, ToolResultEvent):
                        result = cast(dict[str, Any], event.tool_result)
            except Exception as error:  # noqa: BLE001 -- tool boundary
                result = {
                    "status": "error",
                    "content": [{"text": f"Error in graph tool: {error}\n{traceback.format_exc()}"}],
                    "toolUseId": activity_id,
                }
                activity.logger.error("graph activity failed: %s", error)
                topic.publish({"tool_use": exec_use, "data": _publishable(result)})

        return result
    except ApplicationError:
        raise
    except Exception as error:
        message = f"Error in graph tool: {error}\n{traceback.format_exc()}"
        activity.logger.error("graph activity failed: %s", error)
        return {
            "status": "error",
            "content": [{"text": message}],
            "toolUseId": activity_id,
        }
