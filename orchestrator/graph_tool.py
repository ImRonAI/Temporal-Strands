"""Async streaming graph tool built directly on the official ``strands_tools.graph``.

Vendored from the sibling graph-tool package in the ``strands-tools`` project
(``~/Desktop/strands-tools/src``: ``graph.py`` + ``skill_nodes.py``),
consolidated into this one module at the user's explicit direction. Behavior
is identical to the source; only the module layout and import paths changed --
this module never imports the external package.

Extends the official graph tool in exactly two ways: nested formations as
nodes (``swarm``/``graph``/``workflow``/``parallel``/``skill_agent`` compile
to the native SDK executors ``GraphBuilder.add_node`` already accepts), and
async streaming (``execute`` yields every native ``multiagent_*`` event from
``Graph.stream_async``; the final yield is the official ``{"status",
"content"}`` result).

Model selection is by registered **model id**, never by provider. The
application registers named model factories (the same mapping Temporal's
``StrandsPlugin(models=...)`` receives) via :func:`configure_models`; a node
that names ``model_id`` gets ``factories[model_id]()``, and every other node
inherits the parent agent's model -- the official tool's inheritance rule.
The official ``model_provider``/``model_settings`` knobs are intentionally
absent: they build models from provider names and environment variables,
which bypasses the application's provider entirely.

``skill_agent`` nodes build a skill's isolated sub-agent EXACTLY as the
vendored use_skill tool does (agentskills/tool/agent_skill.py): validate the
name, load SKILL.md instructions, and call ``_create_skill_agent`` -- which
injects the skill's references/ and scripts/ paths via ``build_skill_header``.
The resulting Agent participates directly in a Graph or Swarm: the SDK's
agents-as-nodes pattern, no wrapper.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional

from strands import Agent, tool
from strands.models import Model
from strands.multiagent import Swarm
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


def resolve_model(model_id: Optional[str], parent_agent: Optional[Agent]) -> Optional[Model]:
    """The model for one node: its ``model_id`` factory, else the parent's model.

    Raises ``ValueError`` listing the registered ids when ``model_id`` is not
    registered, so a bad id fails at create rather than silently inheriting.
    """
    if model_id is None:
        return parent_agent.model if parent_agent else None
    factory = _model_factories.get(model_id)
    if factory is None:
        raise ValueError(
            f"Unknown model_id {model_id!r}; registered: {sorted(_model_factories)}"
        )
    return factory()


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
    agent = _create_skill_agent(skill, instructions, resolved, _skill_tools)
    agent.name = node_def["id"]
    return agent


# Tool names that expose the Agent Skills catalog (reference patterns 2 & 3).
_SKILL_TOOL_NAMES = frozenset({"use_skill", "skill"})


def _tool_name(tool_obj: Any) -> str:
    name = getattr(tool_obj, "tool_name", None)
    if isinstance(name, str) and name:
        return name
    spec = getattr(tool_obj, "TOOL_SPEC", None)
    if isinstance(spec, dict) and isinstance(spec.get("name"), str):
        return spec["name"]
    return str(getattr(tool_obj, "__name__", "") or "").rpartition(".")[2]


def _skills_prompt() -> str:
    """The reference Phase-1 catalog prompt for agents that carry skill tools.

    Exactly ``agentskills.generate_skills_prompt`` over the configured skills
    registry — the aws-samples example-3 wiring, where the agent owning
    ``use_skill`` gets ``f"{base_prompt}\\n\\n{skills_prompt}"`` so it can only
    name skills that actually exist.
    """
    if not _skills:
        return ""
    try:
        from agentskills import generate_skills_prompt
    except ImportError:
        return ""
    return generate_skills_prompt(list(_skills.values()))


def _select_tools(
    parent_agent: Optional[Agent], tools: Optional[List[str]]
) -> List[Any]:
    """The official create_agent_with_model tool rule: named subset of the
    parent's registry, or the whole registry when no names are given."""
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


def _build_agent(
    node_def: Dict[str, Any],
    parent_agent: Optional[Agent],
    model_id: Optional[str],
    tools: Optional[List[str]],
) -> Agent:
    """Build one agent node: registered model by id (or inherited), parent tools.

    When the node carries a skill tool (``use_skill`` / ``skill``), the skills
    catalog prompt (``agentskills.generate_skills_prompt``) is appended to its
    system prompt exactly as the reference example-3 agent does — without it
    the node's model has no ``<available_skills>`` list and invents skill
    names, which is how the parallel ``use_skill`` calls failed with
    ``SkillNotFoundError: Skill 'pplx_sdk' not found``.
    """
    selected = _select_tools(parent_agent, node_def.get("tools") or tools)
    system_prompt = node_def["system_prompt"]
    if any(_tool_name(t) in _SKILL_TOOL_NAMES for t in selected):
        catalog = _skills_prompt()
        if catalog:
            system_prompt = f"{system_prompt}\n\n{catalog}"
    agent = Agent(
        system_prompt=system_prompt,
        model=resolve_model(node_def.get("model_id") or model_id, parent_agent),
        tools=selected,
        callback_handler=parent_agent.callback_handler if parent_agent else None,
        trace_attributes=parent_agent.trace_attributes if parent_agent else None,
    )
    # Swarm requires uniquely named members; Graph node ids also read name.
    agent.name = node_def["id"]
    return agent


def _build_executor(
    node_def: Dict[str, Any],
    parent_agent: Optional[Agent],
    model_id: Optional[str],
    tools: Optional[List[str]],
):
    """Compile one topology node to a native SDK executor."""
    kind = node_def.get("type", "agent")

    if kind == "agent":
        return _build_agent(node_def, parent_agent, model_id, tools)

    if kind == "skill_agent":
        # The skill's isolated sub-agent, built by the vendored use_skill
        # path, participating directly as a formation node.
        return build_skill_agent(
            node_def, parent_agent, resolve_model(node_def.get("model_id") or model_id, parent_agent)
        )

    if kind == "swarm":
        # Native Swarm takes list[Agent] only (Graph nests, Swarm does not).
        members = [
            build_skill_agent(
                member, parent_agent, resolve_model(member.get("model_id") or model_id, parent_agent)
            )
            if member.get("type") == "skill_agent"
            else _build_agent(member, parent_agent, model_id, tools)
            for member in node_def["agents"]
        ]
        return Swarm(members)

    if kind == "graph":
        # Nested Graph as a node is native (GraphBuilder.add_node accepts it).
        return build_graph(node_def, parent_agent, model_id, tools)

    if kind == "workflow":
        # Workflow compiles to a nested Graph: task dependencies become edges.
        # A task carrying "skill" becomes a skill_agent node; any other task
        # becomes an agent node, exactly as before.
        nested = {
            "nodes": [
                {"id": task["task_id"], "type": "skill_agent", "skill": task["skill"]}
                if "skill" in task
                else {
                    "id": task["task_id"],
                    "system_prompt": task.get("system_prompt")
                    or task["description"],
                    **{key: task[key] for key in ("model_id", "tools") if key in task},
                }
                for task in node_def["tasks"]
            ],
            "edges": [
                {"from": dep, "to": task["task_id"]}
                for task in node_def["tasks"]
                for dep in task.get("dependencies", [])
            ],
        }
        return build_graph(nested, parent_agent, model_id, tools)

    if kind == "parallel":
        # Parallel agents compile to a nested Graph of sibling entry nodes.
        nested = {"nodes": list(node_def["agents"]), "edges": []}
        return build_graph(nested, parent_agent, model_id, tools)

    raise ValueError(
        f"Unknown node type: {kind!r}. Valid: agent, skill_agent, swarm, graph, workflow, parallel"
    )


def build_graph(
    topology: Dict[str, Any],
    parent_agent: Optional[Agent] = None,
    model_id: Optional[str] = None,
    tools: Optional[List[str]] = None,
) -> Graph:
    """Build a native Graph from a (possibly nested) topology.

    Mirrors the official GraphManager.create_graph construction, with
    ``_build_executor`` in place of the agent-only branch. ``model_id`` is
    the formation-wide default a node may override with its own ``model_id``.
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
                "topology": topology,
            },
        }
        return {
            "status": "success",
            "message": f"Graph {graph_id} created successfully with {len(topology['nodes'])} nodes",
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
    """Create and execute multi-agent graphs with nested formations and streaming.

    Same actions and topology format as the official graph tool, plus nested
    node types. "execute" streams every native multiagent event before the
    final result.

    Choosing a node "type" (default "agent"):
    - "agent": one specialist doing one job. Fields: id, system_prompt,
      optional model_id/tools. Exactly 1 agent.
    - "skill_agent": a registered skill's isolated sub-agent as a node
      (built exactly like use_skill builds it, references/scripts paths
      included). Fields: id, skill (registered skill name). Also valid
      inside swarm "agents" lists. Requires configure_skills(...) at app
      setup; unknown skill names fail at create.
    - "swarm": specialists that should hand off to EACH OTHER dynamically
      (exploration, debate, collaborative research -- no fixed order).
      Fields: id, agents (list of agent nodes ONLY, no nesting; each needs
      a unique id). Use 2-5 agents; the SDK caps runs at 20 handoffs / 20
      iterations by default.
    - "graph": a nested deterministic pipeline as one node (recursive:
      its nodes may themselves be any type). Fields: id, nodes, edges.
    - "workflow": a task list with dependencies -- tasks run in dependency
      order, independent tasks run in parallel. Fields: id, tasks (each
      task: task_id, description, optional dependencies/system_prompt/
      model_id/tools; a task with "skill" runs that registered skill's
      sub-agent instead). One agent per task.
    - "parallel": independent fan-out -- all listed agents run at once with
      the same input. Fields: id, agents (list of agent or skill_agent
      nodes). One agent per entry, no ordering.
    Rule of thumb: known order -> graph/workflow edges; unknown order ->
    swarm; same input to N specialists at once -> parallel.

    Using skills: give a node the skill sub-agent tool by name, e.g.
    {"id": "coder", "system_prompt": "...", "tools": ["use_skill"]}.
    The node's agent then calls use_skill(skill_name=..., request=...) to
    run that skill in an isolated sub-agent (its references/ and scripts/
    paths are injected into the sub-agent's prompt). The parent agent that
    owns this graph tool must also have the use_skill tool registered --
    node "tools" lists are filtered from the parent's tool registry.
    Omitting "tools" on a node inherits ALL parent tools (official
    behavior); list exact tool names to scope a node down.

    Args:
        action: "create", "execute", "list", or "delete".
        graph_id: Unique identifier for the graph.
        topology: Graph topology (required for create); official format with
            the optional per-node "type" extension described above.
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

        yield {"status": "error", "content": [{"text": f"Unknown action: {action}. Valid actions: create, execute, list, delete"}]}
    except Exception as exc:  # noqa: BLE001 -- tool boundary, official tool does the same
        logger.error("graph tool error: %s", exc, exc_info=True)
        yield {"status": "error", "content": [{"text": f"⚠️ Graph Error: {exc}"}]}
