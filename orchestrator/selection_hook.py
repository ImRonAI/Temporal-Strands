"""Per-turn tool/skill selection enforcement as a Strands hook.

A ``TurnSelection`` holder lives on the workflow; the turn update fills it
before ``invoke_async`` and resets it in ``finally``. ``SelectionHook`` reads
that holder before every tool call (rules D1/D2):

- An empty ``tools`` set means no tool-name restriction; an empty ``skills``
  set means no skill restriction.
- Meta tools (load_tool/discover_tools/unload_tools/use_skill) are never
  cancelled by their own name, and names the worker registered as MCP tools
  always pass.
- Every other name — starters and loaded community tools alike — is cancelled
  when absent from the turn's selected tools.
- ``load_tool`` is additionally checked against the ``name`` it requests;
  ``use_skill`` against ``skill_name`` in the selected skills.

Import this module through ``workflow.unsafe.imports_passed_through()``.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeToolCallEvent

from config import (
    SELECTION_CANCEL_REASON,
    SELECTION_META_TOOLS,
    SKILL_SELECTION_CANCEL_REASON,
)


@dataclass
class TurnSelection:
    """Mutable per-turn selection, reset by the turn update."""

    tools: frozenset[str] = frozenset()
    skills: frozenset[str] = frozenset()


class SelectionHook(HookProvider):
    """Cancel tool calls outside the turn's selected tools/skills."""

    def __init__(
        self,
        selection: TurnSelection,
        mcp_tool_names: Collection[str],
    ) -> None:
        self._selection = selection
        self._mcp_tool_names = frozenset(mcp_tool_names)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)

    def _before(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        selected_tools = self._selection.tools
        selected_skills = self._selection.skills
        if name in self._mcp_tool_names:
            return
        restricted_tools = bool(selected_tools)
        restricted_skills = bool(selected_skills)
        if name == "load_tool":
            inputs = event.tool_use.get("input", {})
            if not isinstance(inputs, dict):
                return
            requested = inputs.get("name")
            if restricted_tools and requested not in selected_tools:
                event.cancel_tool = SELECTION_CANCEL_REASON.format(name=requested)
            return
        if name == "use_skill":
            inputs = event.tool_use.get("input", {})
            if not isinstance(inputs, dict):
                return
            skill = inputs.get("skill_name")
            if restricted_skills and skill not in selected_skills:
                event.cancel_tool = SKILL_SELECTION_CANCEL_REASON.format(name=skill)
            return
        if name in SELECTION_META_TOOLS:
            return
        if restricted_tools and name not in selected_tools:
            event.cancel_tool = SELECTION_CANCEL_REASON.format(name=name)
