"""Pure-hook contract for per-turn tool/skill selection enforcement.

Given a ``TurnSelection`` holder the workflow resets per turn, the
``SelectionHook`` cancels a ``BeforeToolCallEvent`` exactly when rule D1/D2
demands. No Temporal; built on a bare strands Agent like
tests/test_session_registry_live.py L55-61.
"""

from __future__ import annotations

from strands import Agent
from strands.hooks.events import BeforeToolCallEvent

from config import (
    SELECTION_CANCEL_REASON,
    SKILL_SELECTION_CANCEL_REASON,
)
from selection_hook import SelectionHook, TurnSelection


def _event(name: str, arguments: object, agent: Agent) -> BeforeToolCallEvent:
    return BeforeToolCallEvent(
        agent=agent,
        selected_tool=None,
        tool_use={"name": name, "toolUseId": f"id-{name}", "input": arguments},
        invocation_state={},
    )


def _hook(tools=frozenset(), skills=frozenset(), mcp=()) -> SelectionHook:
    return SelectionHook(TurnSelection(tools=tools, skills=skills), mcp)


class TestToolNameRestriction:
    def test_given_tools_journal_when_environment_then_cancelled_with_reason(self):
        # Given
        hook = _hook(tools=frozenset({"journal"}))
        event = _event("environment", {"action": "get"}, Agent(tools=[], callback_handler=None))
        # When
        hook._before(event)
        # Then
        assert event.cancel_tool == SELECTION_CANCEL_REASON.format(name="environment")

    def test_given_tools_journal_when_journal_then_allowed(self):
        # Given
        hook = _hook(tools=frozenset({"journal"}))
        event = _event("journal", {"action": "list"}, Agent(tools=[], callback_handler=None))
        # When
        hook._before(event)
        # Then
        assert not event.cancel_tool

    def test_given_tools_journal_when_loaded_community_tool_then_cancelled(self):
        # Given: forge is a loaded community tool (restrictable, not a starter)
        hook = _hook(tools=frozenset({"journal"}))
        event = _event("forge", {"spec": {}}, Agent(tools=[], callback_handler=None))
        # When
        hook._before(event)
        # Then
        assert event.cancel_tool == SELECTION_CANCEL_REASON.format(name="forge")


class TestLoadToolInputRestriction:
    def test_given_tools_journal_when_load_tool_forge_then_cancelled_with_name(self):
        # Given
        hook = _hook(tools=frozenset({"journal"}))
        event = _event("load_tool", {"name": "forge"}, Agent(tools=[], callback_handler=None))
        # When
        hook._before(event)
        # Then
        assert event.cancel_tool == SELECTION_CANCEL_REASON.format(name="forge")

    def test_given_tools_journal_when_load_tool_journal_then_allowed(self):
        # Given
        hook = _hook(tools=frozenset({"journal"}))
        event = _event("load_tool", {"name": "journal"}, Agent(tools=[], callback_handler=None))
        # When
        hook._before(event)
        # Then
        assert not event.cancel_tool


class TestUseSkillRestriction:
    def test_given_skills_office_when_use_skill_other_then_cancelled_with_reason(self):
        # Given
        hook = _hook(skills=frozenset({"office"}))
        event = _event(
            "use_skill", {"skill_name": "other"}, Agent(tools=[], callback_handler=None)
        )
        # When
        hook._before(event)
        # Then
        assert event.cancel_tool == SKILL_SELECTION_CANCEL_REASON.format(name="other")

    def test_given_skills_office_when_use_skill_office_then_allowed(self):
        # Given
        hook = _hook(skills=frozenset({"office"}))
        event = _event(
            "use_skill", {"skill_name": "office"}, Agent(tools=[], callback_handler=None)
        )
        # When
        hook._before(event)
        # Then
        assert not event.cancel_tool


class TestEmptySelectionIsUnrestricted:
    def test_given_empty_selection_when_any_tool_then_nothing_cancelled(self):
        # Given
        hook = _hook()
        agent = Agent(tools=[], callback_handler=None)
        # When
        events = [
            _event("environment", {"action": "get"}, agent),
            _event("forge", {"spec": {}}, agent),
            _event("load_tool", {"name": "forge"}, agent),
            _event("use_skill", {"skill_name": "other"}, agent),
        ]
        for event in events:
            hook._before(event)
        # Then
        assert all(not event.cancel_tool for event in events)


class TestPassthroughNames:
    def test_given_tools_restriction_when_mcp_name_then_allowed(self):
        # Given: `shell_execute` stands in for a TemporalMCPClient-registered tool
        hook = _hook(tools=frozenset({"journal"}), mcp=("shell_execute",))
        event = _event("shell_execute", {"command": "ls"}, Agent(tools=[], callback_handler=None))
        # When
        hook._before(event)
        # Then
        assert not event.cancel_tool

    def test_given_tools_restriction_when_meta_tools_then_never_cancelled(self):
        # Given
        hook = _hook(tools=frozenset({"journal"}))
        agent = Agent(tools=[], callback_handler=None)
        # When: discover_tools/unload_tools look themselves up by name
        for name in ("discover_tools", "unload_tools"):
            event = _event(name, {}, agent)
            hook._before(event)
            # Then
            assert not event.cancel_tool, name

    def test_given_restrictions_when_input_not_dict_then_tolerated(self):
        # Given
        hook = _hook(tools=frozenset({"journal"}), skills=frozenset({"office"}))
        agent = Agent(tools=[], callback_handler=None)
        # When: malformed inputs must not raise
        for name in ("load_tool", "use_skill"):
            event = _event(name, "not-a-dict", agent)
            hook._before(event)
            # Then
            assert not event.cancel_tool, name
