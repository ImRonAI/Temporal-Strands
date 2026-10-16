"""Live Temporal proof that ChatWorkflow enforces per-turn tool/skill selection.

Same scaffold as tests/test_session_registry_live.py: a local dev server, an
UnsandboxedWorkflowRunner, and a probe workflow that wraps ChatWorkflow,
drives BeforeToolCallEvent callbacks through the SelectionHook the workflow
installs, and returns the observed cancellations. No model is called.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from strands.hooks.events import BeforeToolCallEvent
from temporalio import workflow
from temporalio.contrib.strands import StrandsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from config import (
    MODEL_START_TO_CLOSE,
    SELECTION_CANCEL_REASON,
    SKILL_SELECTION_CANCEL_REASON,
)
from load_tool import load_tool_activity, run_loaded_tool
from workflow import ChatInput, ChatWorkflow

EVIDENCE = Path(__file__).resolve().parents[2] / ".omo/evidence/13-tool-selector"


@workflow.defn
class ToolSelectionProbe:
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

        mcp_names = sorted(
            set(registry)
            - {"environment", "http_request", "journal"}
            - {"load_tool", "use_skill", "discover_tools", "unload_tools"}
        )

        async def drive(name: str, arguments: object) -> object:
            event = BeforeToolCallEvent(
                agent=agent,
                selected_tool=registry.get(name),
                tool_use={"name": name, "toolUseId": f"probe-{name}", "input": arguments},
                invocation_state={},
            )
            await agent.hooks.invoke_callbacks_async(event)
            return event.cancel_tool

        cancellations: dict[str, object] = {}
        # Tools selection: journal only.
        self.chat._selection.tools = frozenset({"journal"})
        cancellations["environment_restricted"] = await drive(
            "environment", {"action": "get"}
        )
        cancellations["journal_allowed"] = await drive("journal", {"action": "list"})
        cancellations["load_tool_forge_restricted"] = await drive(
            "load_tool", {"name": "forge"}
        )
        for name in mcp_names:
            cancellations[f"mcp_passthrough_{name}"] = await drive(name, {})
        # Skills selection: office only.
        self.chat._selection.skills = frozenset({"office"})
        cancellations["use_skill_unselected"] = await drive(
            "use_skill", {"skill_name": "unselected"}
        )
        cancellations["use_skill_selected"] = await drive(
            "use_skill", {"skill_name": "office"}
        )
        # Reset: tools selection gone again.
        self.chat._selection.tools = frozenset()
        self.chat._selection.skills = frozenset()
        cancellations["environment_after_reset"] = await drive(
            "environment", {"action": "get"}
        )
        self.chat.end_chat()
        await chat_task
        return {"cancellations": cancellations, "mcp_names": mcp_names}


def test_turn_signature_keeps_optional_selection_params() -> None:
    # Given the deployed handler
    signature = inspect.signature(ChatWorkflow.turn)
    # When reading the trailing parameters
    params = list(signature.parameters.values())
    # Then old single-arg histories stay decodable: first param is the prompt
    # string, tools/skills default to None.
    assert params[1].name == "prompt" or params[0].name == "prompt"
    assert signature.parameters["tools"].default is None
    assert signature.parameters["skills"].default is None


@pytest.mark.asyncio
async def test_selection_hook_enforced_in_live_workflow() -> None:
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"), plugins=[StrandsPlugin()]
    ) as env:
        queue = f"tool-selection-proof-{uuid4().hex}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[ToolSelectionProbe],
            activities=[load_tool_activity, run_loaded_tool],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ToolSelectionProbe.run,
                ChatInput(
                    model_id="tool-selection-proof",
                    system_prompt="Selection enforcement inspection",
                ),
                id=queue,
                task_queue=queue,
                execution_timeout=MODEL_START_TO_CLOSE,
            )
            result = await handle.result()
            history = await handle.fetch_history()
            (EVIDENCE / "workflow-history.json").write_text(history.to_json())
            (EVIDENCE / "selection-result.json").write_text(
                json.dumps(result, indent=2)
            )
    cancellations = result["cancellations"]
    assert cancellations["environment_restricted"] == SELECTION_CANCEL_REASON.format(
        name="environment"
    )
    assert cancellations["journal_allowed"] is False
    assert cancellations["load_tool_forge_restricted"] == SELECTION_CANCEL_REASON.format(
        name="forge"
    )
    for name in result["mcp_names"]:
        assert cancellations[f"mcp_passthrough_{name}"] is False, name
    assert cancellations["use_skill_unselected"] == SKILL_SELECTION_CANCEL_REASON.format(
        name="unselected"
    )
    assert cancellations["use_skill_selected"] is False
    assert cancellations["environment_after_reset"] is False
