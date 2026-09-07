"""Native browser registration and Temporal activity dispatch."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker


def test_browser_is_permanently_registered_with_native_input_schema() -> None:
    from workflow import PERMANENT_COMMUNITY_TOOLS

    tools = {tool.tool_name: tool for tool in PERMANENT_COMMUNITY_TOOLS}
    assert "browser" in tools
    tool = tools["browser"]
    assert tool.tool_type == "temporal_activity"
    assert list(tool.tool_spec["inputSchema"]["json"]["properties"]) == [
        "browser_input"
    ]


@workflow.defn
class BrowserToolWorkflow:
    @workflow.run
    async def run(self) -> list[str]:
        from browser_activity import browser_activity

        tool = activity_as_tool(
            browser_activity, start_to_close_timeout=timedelta(seconds=30)
        )
        statuses: list[str] = []
        for action in (
            {"type": "init_session", "session_name": "browser-test-session",
             "description": "Native browser activity integration test"},
            {"type": "new_tab", "session_name": "browser-test-session", "tab_id": "second"},
            {"type": "switch_tab", "session_name": "browser-test-session", "tab_id": "main"},
            {"type": "close", "session_name": "browser-test-session"},
        ):
            async for event in tool.stream(
                {"toolUseId": action["type"], "name": "browser",
                 "input": {"browser_input": {"action": action}}},
                {},
            ):
                result = event["tool_result"]
                statuses.append(json.loads(result["content"][0]["text"])["status"])
        return statuses


@pytest.mark.asyncio
async def test_native_browser_runs_through_temporal_activity(monkeypatch, tmp_path) -> None:
    from browser_activity import browser_activity

    monkeypatch.setenv("STRANDS_BROWSER_HEADLESS", "true")
    monkeypatch.setenv("STRANDS_BROWSER_USER_DATA_DIR", str(tmp_path))
    async with await WorkflowEnvironment.start_local() as env:
        client = await Client.connect(
            env.client.service_client.config.target_host,
            plugins=[StrandsPlugin(models={})],
        )
        queue = f"browser-test-{uuid4()}"
        async with Worker(
            client,
            task_queue=queue,
            workflows=[BrowserToolWorkflow],
            activities=[browser_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await client.execute_workflow(
                BrowserToolWorkflow.run,
                id=f"browser-test-{uuid4()}",
                task_queue=queue,
                execution_timeout=timedelta(seconds=60),
            )
    assert result == ["success"] * 4
