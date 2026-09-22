"""Async streaming graph tool built directly on the official ``strands_tools.graph``.

Vendored from the sibling graph-tool package in the ``strands-tools`` project
(``~/Desktop/strands-tools/src``: ``graph.py`` + ``skill_nodes.py``),
consolidated into this one module at the user's explicit direction. Behavior
is identical to the source; only the module layout and import paths changed --
this module never imports the external package.

The only extension past ``strands_tools.graph`` is async streaming:
``execute`` yields every native ``multiagent_*`` event from
``Graph.stream_async``; the final yield is the official ``{"status",
"content"}`` result. Nodes are agents. There is no node ``type``.

Model selection is ``model_settings.model_id``, a registered factory name
(Temporal ``model=``), via :func:`configure_models`. Omit it and the node
inherits the parent model. ``model_provider`` is not called: ``create_model``
builds a provider from the environment and bypasses the worker factories.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional

from strands import Agent, tool
from strands.models import Model
from strands.multiagent.graph import Graph, GraphBuilder

logger = logging.getLogger(__name__)

__all__ = [
    "GraphManager",
    "build_graph",
    "build_skill_agent",
    "configure_models",
    "configure_skills",
    "graph",
    "resolve_model",
]

# Registered model factories, keyed by model id. Empty until the application
# calls configure_models(); then a node's ``model_id`` resolves here.
_model_factories: Dict[str, Callable[[], Model]] = {}

# Configured once by the application with the SAME inputs as the vendored
# create_skill_agent_tool factory: discovered skills, an optional base
# model, and optional additional tools for the skill sub-agents.
_skills: Dict[str, Any] = {}
_skill_model: Optional[Any] = None
_skill_tools: Optional[List[Any]] = None


def configure_models(model_factories: Mapping[str, Callable[[], Model]]) -> None:
    """Register the model ids formation nodes may select.

    Pass the same ``{model_id: factory}`` mapping the worker gives
    ``StrandsPlugin(models=...)`` so a node's ``model_id`` can only ever name
    a model the application itself provides.
    """
    _model_factories.clear()
    _model_factories.update(model_factories)


def _inherited_model_id(parent_agent: Optional[Agent]) -> Optional[str]:
    """The registered factory name on the parent model, if it has one."""
    if not parent_agent or parent_agent.model is None:
        return None
    getter = getattr(parent_agent.model, "get_config", None)
    if not callable(getter):
        return None
    config = getter() or {}
    inherited = config.get("model_id")
    return inherited if isinstance(inherited, str) and inherited else None


def resolve_model(model_id: Optional[str], parent_agent: Optional[Agent]) -> Optional[Model]:
    """The model for one node: its ``model_id`` factory, else a new parent factory.

    Official ``GraphManager.create_graph`` inherits ``parent.model`` when the
    node names no provider. Skill Rule 1: we inherit the registered factory
    *name* and construct a fresh instance. ``PerplexityModel`` is stateful
    (``store=True``); parallel nodes cannot share one instance.

    Blank means omit — inherit. Raises ``ValueError`` listing the registered
    ids when ``model_id`` is not registered, so a bad id fails at create
    rather than silently inheriting.
    """
    from subagent_support import in_process_model

    if not model_id:
        inherited = _inherited_model_id(parent_agent)
        if inherited and inherited in _model_factories:
            return in_process_model(_model_factories[inherited]())
        return parent_agent.model if parent_agent else None
    factory = _model_factories.get(model_id)
    if factory is None:
        raise ValueError(
            f"Unknown model_id {model_id!r}; registered: {sorted(_model_factories)}"
        )
    return in_process_model(factory())


def configure_skills(
    skills: List[Any],
    base_agent_model: Optional[Any] = None,
    additional_tools: Optional[List[Any]] = None,
) -> None:
    """Register discovered skills for skill_agent nodes.

    Mirrors create_skill_agent_tool's factory inputs: pass the result of
    agentskills.discover_skills(...), an optional default model, and the
    tools skill sub-agents should carry (e.g. file_read, shell).
    """
    global _skill_model, _skill_tools
    _skills.clear()
    _skills.update({skill.name: skill for skill in skills})
    _skill_model = base_agent_model
    _skill_tools = additional_tools


def build_skill_agent(
    node_def: Dict[str, Any],
    parent_agent: Optional[Agent],
    model: Optional[Any] = None,
) -> Agent:
    """Build one skill sub-agent through the vendored use_skill path.

    ``model`` is the node's resolved model (its registered ``model_id`` or the
    inherited parent model); the configured base model is the fallback when
    neither is available.

    A node ``skills`` list assigns those registered skills to the sub-agent
    as **inline tools**: a Pattern-2 ``skill(skill_name)`` tool scoped to
    exactly those names is added to ``additional_tools`` and the scoped
    catalog prompt is appended to the sub-agent's system prompt, so it loads
    their instructions into its own context — never a nested sub-agent.

    A node ``tools`` list assigns those parent-registry tools to the
    sub-agent (the official ``create_agent_with_model`` filter rule) on top
    of the configured skill sandbox tools.
    """
    try:
        from agentskills.parser import load_instructions
        from agentskills.tool.agent_skill import _create_skill_agent
        from agentskills.tool_utils import validate_skill_name
    except ImportError:  # vendored location fallback
        from strands_tools.sessions_and_skills.sample_agent_skills.agentskills.parser import (
            load_instructions,
        )
        from strands_tools.sessions_and_skills.sample_agent_skills.agentskills.tool.agent_skill import (
            _create_skill_agent,
        )
        from strands_tools.sessions_and_skills.sample_agent_skills.agentskills.tool_utils import (
            validate_skill_name,
        )

    skill = validate_skill_name(node_def["skill"], _skills)
    instructions = load_instructions(skill.path)
    resolved = model or _skill_model or (parent_agent.model if parent_agent else None)
    assigned = node_def.get("skills") or []
    tools = list(_skill_tools or [])
    if node_def.get("tools"):
        have = {_tool_name(t) for t in tools}
        tools.extend(
            t for t in _select_tools(parent_agent, node_def["tools"]) if _tool_name(t) not in have
        )
    if assigned:
        from skills_config import create_inline_skill_tool, skills_prompt

        tools.append(create_inline_skill_tool(assigned))
    agent = _create_skill_agent(skill, instructions, resolved, tools)
    if assigned:
        catalog = skills_prompt(assigned)
        if catalog:
            agent.system_prompt = f"{agent.system_prompt}\n\n{catalog}"
    agent.name = node_def["id"]
    return agent


def _tool_name(tool_obj: Any) -> str:
    name = getattr(tool_obj, "tool_name", None)
    if isinstance(name, str) and name:
        return name
    spec = getattr(tool_obj, "TOOL_SPEC", None)
    if isinstance(spec, dict) and isinstance(spec.get("name"), str):
        return spec["name"]
    return str(getattr(tool_obj, "__name__", "") or "").rpartition(".")[2]


def _select_tools(
    parent_agent: Optional[Agent], tools: Optional[List[str]]
) -> List[Any]:
    """Official ``create_agent_with_model``: named subset, or the whole registry.

    Unknown names log the official warning. They are not imported.
    """
    if not parent_agent or not parent_agent.tool_registry:
        return []
    registry = parent_agent.tool_registry.registry
    if not tools:
        return list(registry.values())
    selected = []
    for name in tools:
        if name in registry:
            selected.append(registry[name])
        else:
            logger.warning("Tool '%s' not found in parent agent's tool registry", name)
    return selected


def _node_model_id(node_def: Dict[str, Any], model_id: Optional[str]) -> Optional[str]:
    """Official ``model_settings.model_id``, else the formation default.

    Blank and ``"/"`` are omission. ``model_provider`` is not a factory name.
    """
    settings = node_def.get("model_settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    chosen = settings.get("model_id") or model_id
    if not chosen or chosen == "/":
        return None
    return str(chosen)


def _build_agent(
    node_def: Dict[str, Any],
    parent_agent: Optional[Agent],
    model_id: Optional[str],
    tools: Optional[List[str]],
) -> Agent:
    """Official ``GraphManager.create_graph`` agent branch.

    A node is an agent: ``id``, ``role``, ``system_prompt``, optional ``tools``,
    optional ``model_settings.model_id``. When ``model_settings`` or
    ``model_provider`` is set, ``create_agent_with_model`` filters
    ``parent.tool_registry`` by the tool names. Otherwise the node inherits
    ``parent.model`` and every parent-registry tool, and a node ``tools`` list
    is not applied.
    """
    kind = node_def.get("type", "agent")
    if kind != "agent":
        raise ValueError(
            f"Unknown node type: {kind!r}. Graph nodes are agents "
            "(id, role, system_prompt, optional tools, optional model_settings)."
        )
    settings = node_def.get("model_settings") or {}
    has_model = bool(node_def.get("model_provider") or settings or model_id)
    if has_model:
        selected = _select_tools(parent_agent, node_def.get("tools") or tools)
        resolved = resolve_model(_node_model_id(node_def, model_id), parent_agent)
    else:
        selected = (
            list(parent_agent.tool_registry.registry.values())
            if parent_agent and parent_agent.tool_registry
            else []
        )
        resolved = resolve_model(None, parent_agent)
    extra: Dict[str, Any] = {}
    if parent_agent:
        extra["callback_handler"] = parent_agent.callback_handler
        extra["trace_attributes"] = parent_agent.trace_attributes
    agent = Agent(
        system_prompt=node_def["system_prompt"],
        model=resolved,
        tools=selected,
        **extra,
    )
    agent.name = node_def["id"]
    return agent


def build_graph(
    topology: Dict[str, Any],
    parent_agent: Optional[Agent] = None,
    model_id: Optional[str] = None,
    tools: Optional[List[str]] = None,
) -> Graph:
    """Official ``GraphManager.create_graph``: one Agent per node, then edges."""
    builder = GraphBuilder()
    for node_def in topology["nodes"]:
        builder.add_node(
            _build_agent(node_def, parent_agent, model_id, tools),
            node_def["id"],
        )
    for edge in topology.get("edges", []):
        builder.add_edge(edge["from"], edge["to"])
    for entry_point in topology.get("entry_points", []):
        builder.set_entry_point(entry_point)
    return builder.build()


class GraphManager:
    """Manager for built Graph instances -- the official tool's shape."""

    def __init__(self) -> None:
        self.graphs: Dict[str, Dict[str, Any]] = {}

    def create(self, graph_id: str, topology: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        if graph_id in self.graphs:
            return {"status": "error", "message": f"Graph {graph_id} already exists"}
        graph_obj = build_graph(topology, **kwargs)
        self.graphs[graph_id] = {
            "graph": graph_obj,
            "metadata": {
                "graph_id": graph_id,
                "created_at": time.time(),
                "node_count": len(topology["nodes"]),
                "edge_count": len(topology.get("edges", [])),
                "entry_points": list(topology.get("entry_points") or []),
                "topology": topology,
                "last_execution": None,
            },
        }
        return {
            "status": "success",
            "message": f"Graph {graph_id} created successfully with {len(topology['nodes'])} nodes",
        }

    def status(self, graph_id: str) -> Dict[str, Any]:
        """Official ``GraphManager.get_graph_status``."""
        if graph_id not in self.graphs:
            return {"status": "error", "message": f"Graph {graph_id} not found"}
        metadata = self.graphs[graph_id]["metadata"]
        topology = metadata["topology"]
        nodes = []
        for node_def in topology["nodes"]:
            dependencies = [
                edge["from"]
                for edge in topology.get("edges", [])
                if edge["to"] == node_def["id"]
            ]
            tools = node_def.get("tools")
            nodes.append({
                "id": node_def["id"],
                "role": node_def.get("role") or node_def["id"],
                "model_provider": node_def.get("model_provider", "default"),
                "tools_count": len(tools) if tools else "default",
                "dependencies": dependencies,
            })
        return {
            "status": "success",
            "data": {
                "graph_id": graph_id,
                "total_nodes": metadata["node_count"],
                "entry_points": [{"node_id": ep} for ep in metadata["entry_points"]],
                "execution_status": "ready",
                "last_execution": metadata.get("last_execution"),
                "nodes": nodes,
            },
        }

    def record_execution(self, graph_id: str, task: str, result: Any, execution_time: int) -> None:
        metadata = self.graphs[graph_id]["metadata"]
        metadata["last_execution"] = {
            "task": task,
            "status": result.status.value,
            "completed_nodes": result.completed_nodes,
            "failed_nodes": result.failed_nodes,
            "execution_time": execution_time,
            "timestamp": time.time(),
        }

    def delete(self, graph_id: str) -> Dict[str, Any]:
        if graph_id not in self.graphs:
            return {"status": "error", "message": f"Graph {graph_id} not found"}
        del self.graphs[graph_id]
        return {"status": "success", "message": f"Graph {graph_id} deleted successfully"}


_manager = GraphManager()


@tool
async def graph(
    action: str,
    graph_id: Optional[str] = None,
    topology: Optional[Dict] = None,
    task: Optional[str] = None,
    model_id: Optional[str] = None,
    tools: Optional[List[str]] = None,
    agent: Optional[Any] = None,
) -> AsyncIterator[Any]:
    """Official ``strands_tools.graph`` topology, streamed.

    Actions: create, execute, status, list, delete. A node is an agent:

        {"id", "role", "system_prompt",
         "model_settings": {"model_id"} (optional),
         "tools": [registry names] (optional)}

    ``model_settings.model_id`` is a registered factory name (Temporal
    ``model=``). Omit it to inherit the parent model and every parent-registry
    tool. There is no node ``type``. Edges express order; parallel execution
    is whatever the SDK schedules from those edges.

    Args:
        action: "create", "execute", "status", "list", or "delete".
        graph_id: Unique identifier for the graph.
        topology: Official topology. Nodes are agents. Edges are {"from", "to"}.
            ``stream_async`` emits ``multiagent_handoff`` on each batch
            transition (``from_node_ids`` / ``to_node_ids``).
        task: Task to execute through the graph (required for execute).
        model_id: Default registered model id for agents in the graph; nodes
            may override with their own model_id. Omit to inherit the parent
            agent's model everywhere.
        tools: Default tool-name list for agents in the graph.
        agent: The parent agent (automatically passed by Strands framework).

    Yields:
        Native graph stream events, then a final {"status", "content"} result.
    """
    try:
        if action == "create":
            if not graph_id or not topology:
                yield {"status": "error", "content": [{"text": "graph_id and topology are required for create action"}]}
                return
            result = _manager.create(
                graph_id,
                topology,
                parent_agent=agent,
                model_id=model_id,
                tools=tools,
            )
            yield {"status": result["status"], "content": [{"text": result["message"]}]}
            return

        if action == "execute":
            if not graph_id or not task:
                yield {"status": "error", "content": [{"text": "graph_id and task are required for execute action"}]}
                return
            if graph_id not in _manager.graphs:
                yield {"status": "error", "content": [{"text": f"Graph {graph_id} not found"}]}
                return
            graph_obj: Graph = _manager.graphs[graph_id]["graph"]
            start_time = time.time()
            result = None
            async for event in graph_obj.stream_async(task):
                if "result" in event:
                    result = event["result"]
                yield event
            execution_time = round((time.time() - start_time) * 1000)
            if result is not None:
                _manager.record_execution(graph_id, task, result, execution_time)
            if result is None:
                yield {"status": "error", "content": [{"text": f"Graph {graph_id} produced no result"}]}
                return
            results_text = [
                f"Node {node_id}: {agent_result}"
                for node_id, node_result in result.results.items()
                for agent_result in node_result.get_agent_results()
            ]
            yield {
                "status": "success" if result.status.value == "completed" else "error",
                "content": [
                    {"text": f"Graph {graph_id} executed in {execution_time}ms ({result.status.value})."},
                    *({"text": text} for text in results_text),
                ],
            }
            return

        if action == "status":
            if not graph_id:
                yield {"status": "error", "content": [{"text": "graph_id is required for status action"}]}
                return
            result = _manager.status(graph_id)
            if result["status"] == "error":
                yield {"status": "error", "content": [{"text": result["message"]}]}
                return
            yield {"status": "success", "content": [{"text": str(result["data"])}]}
            return

        if action == "list":
            listed = [
                f"{gid}: {info['metadata']['node_count']} nodes, {info['metadata']['edge_count']} edges"
                for gid, info in _manager.graphs.items()
            ]
            yield {"status": "success", "content": [{"text": "\n".join(listed) or "No graphs"}]}
            return

        if action == "delete":
            if not graph_id:
                yield {"status": "error", "content": [{"text": "graph_id is required for delete action"}]}
                return
            result = _manager.delete(graph_id)
            yield {"status": result["status"], "content": [{"text": result["message"]}]}
            return

        yield {"status": "error", "content": [{"text": f"Unknown action: {action}. Valid actions: create, execute, status, list, delete"}]}
    except Exception as exc:  # noqa: BLE001 -- tool boundary, official tool does the same
        logger.error("graph tool error: %s", exc, exc_info=True)
        yield {"status": "error", "content": [{"text": f"⚠️ Graph Error: {exc}"}]}
