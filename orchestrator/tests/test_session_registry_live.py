from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.contrib.strands import StrandsPlugin
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from agent_tools import COMPUTER_USE_TOOLS, PERMANENT_COMMUNITY_TOOLS
from config import MODEL_START_TO_CLOSE
from load_tool import (
    STRANDS_TOOLS_DIR, _SESSION_TOOLS, discover_tools_activity,
    load_tool_activity, register_community_tool, run_loaded_tool, tool_file_path,
)
from strands import Agent
from strands.hooks.events import BeforeToolCallEvent
from strands_tools.think import think
from workflow import ChatInput, ChatWorkflow


EVIDENCE = Path(__file__).resolve().parents[2] / ".omo/evidence/2026-10-09-session-registry"
EXPECTED = {"load_tool", "discover_tools", "unload_tools", "use_skill",
            "environment", "http_request", "journal"}


@workflow.defn
class RegistryProbe:
    @workflow.init
    def __init__(self, input: ChatInput) -> None:
        self.chat = ChatWorkflow(input)

    @workflow.run
    async def run(self, input: ChatInput) -> dict:
        chat_task = asyncio.create_task(self.chat.run(input))
        await workflow.wait_condition(lambda: self.chat._agent is not None)
        agent = self.chat._agent
        assert agent is not None
        registry = agent.tool_registry.registry
        names = sorted(registry)
        cancellations = {}
        for label, name, arguments in (
            ("string_topology", "graph", {"topology": "a -> b"}),
            ("model_override", "use_agent", {"model_id": "override"}),
            ("unknown_tool", "use_skill", {"tools": ["not_loaded"]}),
            ("registered_tool", "use_skill", {"tools": ["environment"]}),
            ("direct_swarm", "swarm", {"model_id": "override"}),
        ):
            event = BeforeToolCallEvent(
                agent=agent, selected_tool=registry.get(name),
                tool_use={"name": name, "toolUseId": label, "input": arguments},
                invocation_state={},
            )
            await agent.hooks.invoke_callbacks_async(event)
            cancellations[label] = event.cancel_tool
        results = {}
        for name, arguments in (
            ("journal", {"action": "list"}),
            ("environment", {"action": "get", "name": "STRANDS_NON_INTERACTIVE"}),
        ):
            use = {"name": name, "toolUseId": name, "input": arguments}
            events = []
            async for event in registry[name].stream(use, {"agent": agent}):
                if hasattr(event, "tool_result"):
                    events.append(event.tool_result)
            results[name] = events
        self.chat.end_chat()
        await chat_task
        return {"names": names, "results": results, "cancellations": cancellations}


@pytest.mark.asyncio
async def test_real_workflow_starts_with_only_session_registry() -> None:
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"), plugins=[StrandsPlugin()]
    ) as env:
        queue = f"registry-proof-{uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[RegistryProbe],
            activities=[load_tool_activity, run_loaded_tool],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                RegistryProbe.run,
                ChatInput(model_id="registry-proof", system_prompt="Registry inspection"),
                id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
            )
            result = await handle.result()
            history = await handle.fetch_history()
            (EVIDENCE / "workflow-history.json").write_text(history.to_json())
            (EVIDENCE / "registry-result.json").write_text(json.dumps(result, indent=2))
    assert set(result["names"]) == EXPECTED
    assert "topology" in result["cancellations"]["string_topology"]
    assert "model_id" in result["cancellations"]["model_override"]
    assert "discover_tools" in result["cancellations"]["unknown_tool"]
    assert result["cancellations"]["registered_tool"] is False
    assert result["cancellations"]["direct_swarm"] is False
    for results in result["results"].values():
        assert results and results[-1]["status"] == "success"


@pytest.mark.asyncio
async def test_installed_loads_and_existing_bindings() -> None:
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
    env = ActivityEnvironment()
    agent = Agent(tools=[], callback_handler=None)
    observations = {}
    native = {tool.tool_name: tool for tool in (*PERMANENT_COMMUNITY_TOOLS, *COMPUTER_USE_TOOLS)}
    native["think"] = think
    names = ["journal", "environment", "http_request", "graph", "use_agent", "think", "browser"]
    names.extend(tool.tool_name for tool in COMPUTER_USE_TOOLS)
    for name in names:
        loaded = await env.run(load_tool_activity, name, name)
        assert loaded["status"] == "success", loaded
        assert loaded["tool_spec"]["name"] == name
        register_community_tool(agent, loaded["path"], name, loaded["tool_spec"])
        if name in native:
            assert agent.tool_registry.registry[name] is native[name]
        observations[name] = {"path": loaded["path"], "tool_type": agent.tool_registry.registry[name].tool_type}
    assert Path(tool_file_path("journal")) == STRANDS_TOOLS_DIR / "journal.py"
    assert _SESSION_TOOLS == EXPECTED
    catalog = await env.run(discover_tools_activity)
    discovered = set(catalog["content"][0]["text"].splitlines())
    assert {"graph", "use_agent", "think", "swarm", "forge", "dicom", "fhir"} <= discovered
    assert {tool.tool_name for tool in COMPUTER_USE_TOOLS} <= discovered
    rejected = await env.run(load_tool_activity, "calculator", "journal")
    assert rejected["status"] == "error"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "binding-results.json").write_text(json.dumps(observations, indent=2))
