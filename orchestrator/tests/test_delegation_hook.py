import copy
import json
from importlib.metadata import version
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from strands import Agent
from strands.hooks import HookRegistry
from strands.hooks.events import BeforeToolCallEvent
from strands_tools import environment
from strands_agents_sops.utils import load_external_sops

from delegation_hook import DELEGATION_SOP, DelegationHook


@pytest.fixture
def agent():
    return Agent(tools=[environment], callback_handler=None)


def invoke_hook(agent, name, inputs, *, loaded_paths=None):
    registry = HookRegistry()
    hook = DelegationHook(agent.tool_registry.registry, loaded_paths or {})
    hook.register_hooks(registry)
    call = {"toolUseId": "delegation-rule-test", "name": name, "input": inputs}
    before = copy.deepcopy(call)
    event = BeforeToolCallEvent(
        agent=agent, selected_tool=None, tool_use=call, invocation_state={}
    )
    returned, interrupts = registry.invoke_callbacks(event)
    assert returned is event
    assert not interrupts
    assert call == before
    return event.cancel_tool


@pytest.mark.parametrize(
    "inputs, reason",
    [
        ({"topology": "a -> b"}, "topology"),
        ({"topology": {"nodes": [{"model_id": "override"}]}}, "model_id"),
        ({"topology": {"nodes": [{"model_settings": {}}]}}, "model_settings"),
        ({"topology": {"nodes": [{"type": "workflow"}]}}, "workflow"),
        ({"topology": {"nodes": [{"type": "parallel"}]}}, "parallel"),
        ({"topology": {"nodes": [{"type": "skill_agent"}]}}, "skill"),
        ({"topology": {"nodes": [{"type": "skill_agent", "skill": ""}]}}, "skill"),
        ({"topology": {"nodes": [{"type": "swarm", "agents": [{"type": "graph"}]}]}}, "members"),
        ({"topology": {"nodes": [{"type": "swarm", "agents": ["member"]}]}}, "members"),
        ({"topology": {"nodes": [{"tools": ["not_loaded"]}]}}, "discover_tools"),
        ({"topology": {"nodes": [{"type": "graph", "topology": "nested"}]}}, "topology"),
        ({"topology": {"nodes": [{"type": "graph", "topology": {"nodes": [{"model_id": "override"}]}}]}}, "model_id"),
        ({"topology": {"nodes": [{"type": "swarm", "agents": [{"type": "skill_agent"}]}]}}, "skill"),
        ({"topology": {"nodes": [{"type": "swarm", "agents": [{"tools": ["not_loaded"]}]}]}}, "discover_tools"),
    ],
)
def test_graph_cancellations(agent, inputs, reason):
    assert reason in invoke_hook(agent, "graph", inputs)


@pytest.mark.parametrize("name", ["graph", "use_agent", "use_skill"])
@pytest.mark.parametrize("tool_name", ["not_loaded", "web_search", "sandbox"])
def test_delegation_rejects_unregistered_tools(agent, name, tool_name):
    assert "discover_tools" in invoke_hook(agent, name, {"tools": [tool_name]})


@pytest.mark.parametrize("name", ["graph", "use_agent", "use_skill"])
def test_delegation_accepts_current_and_loaded_tools(agent, name):
    assert invoke_hook(agent, name, {}) is False
    assert invoke_hook(agent, name, {"tools": ["environment"]}) is False
    assert invoke_hook(
        agent, name, {"tools": ["journal"]}, loaded_paths={"journal": "journal"}
    ) is False


def test_graph_accepts_nested_formations(agent):
    topology = {
        "nodes": [
            {"id": "nested", "type": "graph", "topology": {
                "nodes": [{"id": "team", "type": "swarm", "agents": [
                    {"id": "plain", "type": "agent"},
                    {"id": "skilled", "type": "skill_agent", "skill": "research"},
                ]}],
                "edges": [], "entry_points": ["team"],
            }},
        ],
        "edges": [], "entry_points": ["nested"],
    }
    assert invoke_hook(agent, "graph", {"topology": topology}) is False


def test_direct_swarm_keeps_its_own_schema(agent):
    assert invoke_hook(agent, "swarm", {
        "task": "Investigate",
        "agents": [{"name": "researcher", "model_settings": {}, "tools": ["web_search"]}],
    }) is False


def test_live_mapping_changes_are_observed(agent):
    paths = {}
    registry = HookRegistry()
    DelegationHook(agent.tool_registry.registry, paths).register_hooks(registry)
    def check(name):
        event = BeforeToolCallEvent(
            agent=agent, selected_tool=None,
            tool_use={"toolUseId": "registry-change", "name": "use_agent", "input": {"tools": [name]}},
            invocation_state={},
        )
        registry.invoke_callbacks(event)
        return event.cancel_tool

    assert check("journal")
    paths["journal"] = "journal"
    assert check("journal") is False
    del paths["journal"]
    assert check("journal")
    assert check("environment") is False
    del agent.tool_registry.registry["environment"]
    assert check("environment")


def test_installed_package_loads_delegation_sop():
    assert version("strands-agents-sops") == "1.1.4"
    directory = Path(__file__).resolve().parents[1] / "sops"
    sops = load_external_sops([directory])
    sop = next(item for item in sops if item["name"] == "delegation")
    assert sop["content"] == DELEGATION_SOP
    assert sop["description"]
    for heading in ["## Overview", "## Parameters", "## Steps"]:
        assert heading in DELEGATION_SOP


@pytest.mark.asyncio
async def test_real_sop_mcp_server_exposes_delegation_prompt():
    root = Path(__file__).resolve().parents[1]
    configuration = json.loads((root / "mcp.json").read_text())["mcpServers"]["agent-sops"]
    assert "continue_on_error" not in configuration
    parameters = StdioServerParameters(
        command=configuration["command"], args=configuration["args"], cwd=str(root)
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            prompts = await session.list_prompts()
            assert "delegation" in [prompt.name for prompt in prompts.prompts]
            result = await session.get_prompt("delegation")
            assert DELEGATION_SOP in result.messages[0].content.text
            assert not (await session.list_tools()).tools
