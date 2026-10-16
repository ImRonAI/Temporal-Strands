from collections.abc import Mapping
from pathlib import Path
from typing import Any

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeToolCallEvent
from temporalio import workflow


with workflow.unsafe.imports_passed_through():
    DELEGATION_SOP = (Path(__file__).parent / "sops" / "delegation.sop.md").read_text(
        encoding="utf-8"
    )


class DelegationHook(HookProvider):
    """Validate against live mappings without reading the agent in callbacks.

    Import this module through ``workflow.unsafe.imports_passed_through()``.
    Pass the session's ``tool_registry.registry`` and ``LoadedTools.paths``;
    retaining their references makes successful loads and unloads visible.
    """

    def __init__(
        self,
        session_tools: Mapping[str, Any],
        loaded_paths: Mapping[str, str],
    ) -> None:
        self._session_tools = session_tools
        self._loaded_paths = loaded_paths

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)

    def _before(self, event: BeforeToolCallEvent) -> None:
        call = event.tool_use
        if call["name"] not in {"graph", "use_agent", "use_skill"}:
            return
        inputs = call.get("input", {})
        if not isinstance(inputs, dict):
            return
        reason = self._node_error(inputs)
        if reason:
            event.cancel_tool = reason

    def _node_error(self, node: dict[str, Any], *, swarm_member: bool = False) -> str | None:
        if "model_id" in node or "model_settings" in node:
            return "Omit model_id and model_settings to inherit the session model."
        kind = node.get("type", "agent")
        if not isinstance(kind, str):
            return "Graph node type must name agent, skill_agent, swarm, or graph."
        if kind in {"workflow", "parallel"}:
            return "Graph nodes support agent, skill_agent, swarm, or graph; not workflow or parallel."
        if swarm_member and kind not in {"agent", "skill_agent"}:
            return "Graph swarm members must be agent or skill_agent nodes."
        if kind == "skill_agent" and not node.get("skill"):
            return "A skill_agent node requires its registered skill name."
        tools = node.get("tools")
        if tools is not None:
            if not isinstance(tools, list):
                return "tools must be a list of session registry names; omit it to inherit."
            for name in tools:
                if not isinstance(name, str) or (
                    name not in self._session_tools and name not in self._loaded_paths
                ):
                    return (
                        f"Tool {name!r} is not on the session or in loaded-tool paths. "
                        "Call discover_tools, then load_tool, or omit tools to inherit. "
                        "Agent API names are not registry names."
                    )
        topology = node.get("topology")
        if isinstance(topology, str):
            return "Graph topology must be an object, not a string."
        if isinstance(topology, dict):
            nodes = topology.get("nodes")
            for child in nodes if isinstance(nodes, list) else []:
                if isinstance(child, dict):
                    reason = self._node_error(child)
                    if reason:
                        return reason
        if kind == "swarm":
            members = node.get("agents")
            for member in members if isinstance(members, list) else []:
                if not isinstance(member, dict):
                    return "Graph swarm members must be agent or skill_agent nodes."
                reason = self._node_error(member, swarm_member=True)
                if reason:
                    return reason
        return None
