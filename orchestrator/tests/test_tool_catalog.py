"""Community tools load, unload, and execute in any number."""

from __future__ import annotations

import json

import pytest
from strands.tools.registry import ToolRegistry

from load_tool import (
    LoadedTools,
    apply_tool_result,
    catalog_tools,
    discover_tools_activity,
    drop_loaded,
    replay_catalog,
    unregistered_names,
)


class _Agent:
    def __init__(self) -> None:
        self.tool_registry = ToolRegistry()


def _spec(name: str) -> dict:
    return {
        "name": name,
        "description": name,
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    }


def _load(name: str, use_id: str) -> tuple[dict, dict]:
    tool_use = {
        "name": "load_tool",
        "toolUseId": use_id,
        "input": {"path": name, "name": name},
    }
    result = {
        "status": "success",
        "content": [{
            "text": json.dumps({
                "status": "success",
                "path": f"/tools/{name}.py",
                "tool_spec": _spec(name),
            }),
        }],
    }
    return tool_use, result


@pytest.mark.asyncio
async def test_discover_lists_catalog_names() -> None:
    result = await discover_tools_activity()
    names = result["content"][0]["text"].splitlines()
    assert "current_time" in names
    assert "dicom" in names
    assert "fhir" in names
    assert "forge" in names
    assert all(not name.startswith("_") for name in names)
    visible = unregistered_names(result["content"][0]["text"], {"current_time", "think"})
    assert "current_time" not in visible
    assert "dicom" in visible


def test_many_loads_register_and_many_unloads_drop() -> None:
    agent = _Agent()
    paths: dict[str, str] = {}
    for name in ("calculator", "current_time", "sleep"):
        tool_use, result = _load(name, f"use-{name}")
        apply_tool_result(agent, tool_use, result, paths)
    assert set(agent.tool_registry.registry) == {"calculator", "current_time", "sleep"}
    gone = drop_loaded(agent, ["calculator", "sleep", "think", "load_tool"], paths)
    assert gone == ["calculator", "sleep"]
    assert set(agent.tool_registry.registry) == {"current_time"}
    assert paths == {"current_time": "/tools/current_time.py"}


def test_reload_replaces_and_replay_keeps_message_order() -> None:
    agent = _Agent()
    paths: dict[str, str] = {}
    first_use, first_result = _load("calculator", "use-1")
    apply_tool_result(agent, first_use, first_result, paths)
    again_use, again_result = _load("calculator", "use-2")
    apply_tool_result(agent, again_use, again_result, paths)
    assert list(agent.tool_registry.registry) == ["calculator"]

    fresh = _Agent()
    restored: dict[str, str] = {}
    load_a, result_a = _load("calculator", "a")
    load_b, result_b = _load("current_time", "b")
    messages = [{
        "role": "assistant",
        "content": [
            {"toolUse": load_a},
            {"toolUse": load_b},
        ],
    }, {
        "role": "user",
        "content": [
            {"toolResult": {"toolUseId": "a", "status": "success", "content": result_a["content"]}},
            {"toolResult": {"toolUseId": "b", "status": "success", "content": result_b["content"]}},
            {"toolResult": {
                "toolUseId": "c",
                "status": "success",
                "content": [{"text": "calculator"}],
            }},
        ],
    }]
    messages[0]["content"].append({
        "toolUse": {"name": "unload_tools", "toolUseId": "c", "input": {"names": ["calculator"]}},
    })
    replay_catalog(fresh, messages, restored)
    assert set(fresh.tool_registry.registry) == {"current_time"}
    assert restored == {"current_time": "/tools/current_time.py"}


def test_hook_registers_each_load_result() -> None:
    agent = _Agent()
    hook = LoadedTools()

    class _Event:
        def __init__(self, tool_use, result):
            self.agent = agent
            self.tool_use = tool_use
            self.result = result

    for name in ("calculator", "current_time"):
        tool_use, result = _load(name, name)
        hook._after(_Event(tool_use, result))
    assert set(hook.paths) == {"calculator", "current_time"}
    assert set(agent.tool_registry.registry) == {"calculator", "current_time"}


def test_catalog_tools_accept_many_names() -> None:
    discover, unload = catalog_tools({})
    assert discover.tool_spec["name"] == "discover_tools"
    assert unload.tool_spec["name"] == "unload_tools"
    schema = unload.tool_spec["inputSchema"]["json"]
    assert schema["properties"]["names"]["type"] == "array"
    assert "tool_context" not in schema["properties"]


def test_session_name_cannot_replace_load_tool() -> None:
    agent = _Agent()
    paths: dict[str, str] = {}
    tool_use, result = _load("load_tool", "use-load")
    apply_tool_result(agent, tool_use, result, paths)
    assert agent.tool_registry.registry == {}
    assert paths == {}
