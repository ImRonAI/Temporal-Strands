"""Async streaming graph tool mirroring the official ``strands_tools.graph``.

Same tool contract as the installed community tool: actions
``create | execute | status | list | delete``, topology dicts shaped
``{nodes, edges, entry_points}``, and ``{"status", "content"}`` results.
Construction and execution use ONLY the real SDK multiagent primitives --
``GraphBuilder``/``Graph`` and ``Swarm`` -- exactly as the installed SDK
defines them. No vendored orchestration logic, no reimplemented graph or
swarm loops, no patched SDK classes.

Two extensions past the official tool:

1. Async streaming: ``execute`` drives ``Graph.stream_async`` and yields
   every raw ``multiagent_*`` event (nested formations' events pass through
   untouched inside ``multiagent_node_stream`` wrappers); the final yield is
   the official ``{"status", "content"}`` result. Sync ``__call__`` forms are
   never used -- they spawn a thread.
2. Nested formations as nodes, which the SDK supports natively
   (``GraphBuilder.add_node`` accepts any ``MultiAgentBase``). A node's
   ``type`` field (default ``"agent"``) selects the executor:

   - ``"agent"``: one agent (id, role, system_prompt, optional tools,
     optional skills).
   - ``"skill_agent"``: a registered skill's isolated sub-agent, built
     through :func:`build_skill_agent` (the same path ``use_skill`` uses).
     Requires ``skill`` (a registered skill name).
   - ``"swarm"``: ``agents`` (member agent nodes, unique ids, no nesting --
     the SDK Swarm takes plain Agents only) plus optional ``entry_point``,
     ``max_handoffs``, ``max_iterations``, ``execution_timeout``,
     ``node_timeout``. Omitted knobs keep the SDK defaults.
   - ``"graph"``: ``topology`` (a nested ``{nodes, edges, entry_points}``,
     recursive -- its nodes may themselves be any type).

   ``workflow`` and ``parallel`` are NOT supported node types.

Every node uses the parent model. A ``model_settings.model_id`` on a node
is ignored, the same way Think ignores a picked model and stays on the
session model. ``model_provider`` is not called: the official
``create_model`` builds a provider from the environment and bypasses the
worker factories. A node that names ``tools`` receives those parent-registry
tools. A node that omits ``tools`` receives every tool the parent has,
which is what official ``create_agent_with_model`` does.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional, Union

from strands import Agent, tool
from strands.models import Model
from strands.multiagent.graph import Graph, GraphBuilder
from strands.multiagent.swarm import Swarm

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

_VALID_NODE_TYPES = ("agent", "skill_agent", "swarm", "graph")


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

    ``model`` is the parent model. The configured base model is the fallback
    when the parent has none.

    A node ``skills`` list assigns those registered skills to the sub-agent
    as **inline tools**: a Pattern-2 ``skill(skill_name)`` tool scoped to
    exactly those names is added to ``additional_tools`` and the scoped
    catalog prompt is appended to the sub-agent's system prompt, so it loads
    their instructions into its own context — never a nested sub-agent.

    A node ``tools`` list assigns those parent-registry tools. Omit ``tools``
    and the node receives every parent tool, on top of the configured skill
    sandbox tools.
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
    declared = node_def["tools"] if "tools" in node_def else None
    have = {_tool_name(t) for t in tools}
    tools.extend(
        t for t in _select_tools(parent_agent, declared) if _tool_name(t) not in have
    )
    from skills_config import create_inline_skill_tool, skill_resource_note, skills_prompt

    if assigned:
        tools.append(create_inline_skill_tool(assigned))
    agent = _create_skill_agent(skill, instructions, resolved, tools)
    extras = []
    if assigned:
        catalog = skills_prompt(assigned)
        if catalog:
            extras.append(catalog)
    resources = skill_resource_note(skill.name, skill.skill_dir)
    if resources:
        extras.append(resources)
    if extras:
        agent.system_prompt = f"{agent.system_prompt}\n\n" + "\n\n".join(extras)
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
    """Tools from the parent registry.

    ``None`` selects every parent tool, matching official
    ``create_agent_with_model``. A list selects those names. Unknown names
    log a warning and are skipped. They are not imported. An empty list
    selects nothing.
    """
    if not parent_agent or not parent_agent.tool_registry:
        return []
    registry = parent_agent.tool_registry.registry
    if tools is None:
        return list(registry.values())
    selected = []
    for name in tools:
        if name in registry:
            selected.append(registry[name])
        else:
            logger.warning("Tool '%s' not found in parent agent's tool registry", name)
    return selected


def _build_agent(
    node_def: Dict[str, Any],
    parent_agent: Optional[Agent],
    model_id: Optional[str],
    tools: Optional[List[str]],
) -> Agent:
    """Official ``GraphManager.create_graph`` agent branch.

    A node is an agent: ``id``, ``role``, ``system_prompt``, optional ``tools``,
    optional ``skills``. ``tools`` is the registry names the parent assigns.
    Omit it and the node receives every parent tool. The model is always the
    parent model; ``model_id`` and ``model_settings`` are ignored.
    """
    del model_id, tools
    system_prompt = node_def.get("system_prompt")
    if not system_prompt:
        raise ValueError(
            f"Agent node {node_def.get('id')!r} requires a system_prompt."
        )
    declared = node_def["tools"] if "tools" in node_def else None
    selected = _select_tools(parent_agent, declared)
    resolved = resolve_model(None, parent_agent)
    extra: Dict[str, Any] = {}
    if parent_agent:
        extra["callback_handler"] = parent_agent.callback_handler
        extra["trace_attributes"] = parent_agent.trace_attributes
    skill_names = node_def.get("skills") or None
    if skill_names:
        from skills_config import agent_skills_plugin

        extra["plugins"] = [agent_skills_plugin(list(skill_names))]
    agent = Agent(
        system_prompt=system_prompt,
        model=resolved,
        tools=selected,
        **extra,
    )
    agent.name = node_def["id"]
    return agent


def _build_swarm_member(
    member: Dict[str, Any],
    parent_agent: Optional[Agent],
    model_id: Optional[str],
    tools: Optional[List[str]],
) -> Agent:
    """One swarm member: a plain agent or a skill sub-agent. No nesting."""
    spec = dict(member)
    member_id = spec.get("id") or spec.get("name")
    if not member_id:
        raise ValueError("Swarm members require a unique 'id' (agent.name).")
    spec["id"] = member_id
    kind = spec.get("type", "agent")
    if kind == "skill_agent":
        if not spec.get("skill"):
            raise ValueError(
                f"skill_agent swarm member {member_id!r} requires 'skill' "
                "(a registered skill name)."
            )
        return build_skill_agent(
            spec, parent_agent, resolve_model(None, parent_agent)
        )
    if kind != "agent":
        raise ValueError(
            f"Swarm member {member_id!r} has type {kind!r}; the SDK Swarm "
            "takes plain Agents only (agent or skill_agent members)."
        )
    return _build_agent(spec, parent_agent, model_id, tools)


def _build_swarm(
    node_def: Dict[str, Any],
    parent_agent: Optional[Agent],
    model_id: Optional[str],
    tools: Optional[List[str]],
) -> Swarm:
    """A native SDK ``Swarm`` node: members plus optional SDK limit knobs.

    Omitted knobs keep the SDK defaults (``max_handoffs=20``,
    ``max_iterations=20``, ``execution_timeout=900.0``, ``node_timeout=300.0``).
    """
    members_spec = node_def.get("agents") or []
    if not members_spec:
        raise ValueError(
            f"Swarm node {node_def.get('id')!r} requires a non-empty 'agents' list."
        )
    members = [
        _build_swarm_member(member, parent_agent, model_id, tools)
        for member in members_spec
    ]
    kwargs: Dict[str, Any] = {"id": str(node_def["id"])}
    entry_point = node_def.get("entry_point")
    if entry_point:
        entry_agent = next((a for a in members if a.name == entry_point), None)
        if entry_agent is None:
            raise ValueError(
                f"Swarm entry_point {entry_point!r} is not one of its member ids."
            )
        kwargs["entry_point"] = entry_agent
    for key in ("max_handoffs", "max_iterations"):
        if node_def.get(key) is not None:
            kwargs[key] = int(node_def[key])
    for key in ("execution_timeout", "node_timeout"):
        if node_def.get(key) is not None:
            kwargs[key] = float(node_def[key])
    return Swarm(members, **kwargs)


def _build_executor(
    node_def: Dict[str, Any],
    parent_agent: Optional[Agent],
    model_id: Optional[str],
    tools: Optional[List[str]],
) -> Union[Agent, Swarm, Graph]:
    """Compile one topology node to its native SDK executor.

    ``GraphBuilder.add_node`` accepts any ``AgentBase`` or ``MultiAgentBase``,
    so swarm and nested-graph nodes participate directly; the SDK dispatches
    them at execution and wraps their events in ``multiagent_node_stream``.
    """
    kind = node_def.get("type", "agent")

    if kind == "agent":
        return _build_agent(node_def, parent_agent, model_id, tools)

    if kind == "skill_agent":
        if not node_def.get("skill"):
            raise ValueError(
                f"skill_agent node {node_def.get('id')!r} requires 'skill' "
                "(a registered skill name)."
            )
        return build_skill_agent(
            node_def,
            parent_agent,
            resolve_model(None, parent_agent),
        )

    if kind == "swarm":
        return _build_swarm(node_def, parent_agent, model_id, tools)

    if kind == "graph":
        nested = node_def.get("topology")
        if not isinstance(nested, dict) or not nested.get("nodes"):
            raise ValueError(
                f"graph node {node_def.get('id')!r} requires a nested "
                "'topology' with a non-empty 'nodes' list."
            )
        return build_graph(nested, parent_agent, model_id, tools)

    raise ValueError(
        f"Unknown node type: {kind!r}. Valid: {', '.join(_VALID_NODE_TYPES)}"
    )


def build_graph(
    topology: Dict[str, Any],
    parent_agent: Optional[Agent] = None,
    model_id: Optional[str] = None,
    tools: Optional[List[str]] = None,
) -> Graph:
    """Official ``GraphManager.create_graph``: one executor per node, then edges.

    Node ``type`` dispatches through :func:`_build_executor`; default is an
    agent node. Every node uses the parent model. ``model_id`` is ignored.
    """
    builder = GraphBuilder()
    for node_def in topology["nodes"]:
        builder.add_node(
            _build_executor(node_def, parent_agent, model_id, tools),
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
                "type": node_def.get("type", "agent"),
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
    """Official ``strands_tools.graph`` topology, streamed, with nested formations.

    Actions: create, execute, status, list, delete. Node ``type`` (default
    ``"agent"``):

    - ``"agent"``: ``{"id", "role", "system_prompt", "tools": [...] (optional),
      "skills": [...] (optional)}``. Omit ``tools`` and the node receives
      every tool the parent has. Every node uses the parent model.
    - ``"skill_agent"``: a registered skill's isolated sub-agent. Fields:
      ``id``, ``skill`` (registered skill name); optional ``tools``/``skills``.
    - ``"swarm"``: specialists that hand off to EACH OTHER dynamically.
      Fields: ``id``, ``agents`` (member agent nodes, unique ids, no nesting);
      optional ``entry_point``, ``max_handoffs``, ``max_iterations``,
      ``execution_timeout``, ``node_timeout`` (SDK defaults when omitted).
    - ``"graph"``: a nested deterministic pipeline as one node (recursive).
      Fields: ``id``, ``topology`` (``{nodes, edges, entry_points}``).

    ``workflow``/``parallel`` node types are not supported. Nested
    ``multiagent_*`` events stream through untouched inside
    ``multiagent_node_stream`` wrappers; ``multiagent_handoff`` fires on each
    batch transition (``from_node_ids`` / ``to_node_ids``).

    Args:
        action: "create", "execute", "status", "list", or "delete".
        graph_id: Unique identifier for the graph.
        topology: Official topology extended with the per-node ``type`` field.
            Edges are {"from", "to"}.
        task: Task to execute through the graph (required for execute).
        model_id: Ignored. Every node uses the parent model.
        tools: Ignored. Name tools on each node. A node that omits tools
            receives every tool the parent has.
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
