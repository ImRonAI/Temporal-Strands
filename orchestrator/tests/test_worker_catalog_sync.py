import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.contrib.strands import StrandsPlugin
from temporalio.client import ScheduleOverlapPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from mcp import StdioServerParameters, stdio_client
from strands.tools.mcp import MCPClient

import run_worker
from config import DREAM_MIN_INTERVAL, GEMINI_MODEL_ID, REASONING_EFFORTS, THINK_MODEL_ID
from delegation_hook import DELEGATION_SOP
from load_tool import run_loaded_tool
from config import MCP_START_TO_CLOSE, MODEL_RETRY_POLICY


EVIDENCE = Path(__file__).resolve().parents[2] / ".omo/evidence/12-worker-wiring"


def test_catalog_add_remove_and_gemini_preserved(tmp_path, monkeypatch):
    path = tmp_path / "reasoning-levels.json"
    path.write_text(json.dumps({GEMINI_MODEL_ID: ["low", "medium", "high"]}))
    monkeypatch.setattr(run_worker, "REASONING_LEVELS_PATH", path)
    first, _ = run_worker.build_perplexity_factories("catalog-test", ["catalog/one", "catalog/two"])
    first_levels = json.loads(path.read_text())
    assert {"catalog/one", "catalog/two"} <= first.keys()
    for name in first:
        assert set(first_levels[name]) == REASONING_EFFORTS
    second, _ = run_worker.build_perplexity_factories("catalog-test", ["catalog/one"])
    second_levels = json.loads(path.read_text())
    assert "catalog/two" not in second
    assert "catalog/two" not in second_levels
    assert second_levels[GEMINI_MODEL_ID] == ["low", "medium", "high"]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "catalog.json").write_text(json.dumps({"first": first_levels, "second": second_levels}, indent=2))


def test_identity_and_gemini_tools():
    assert run_worker.agent_identity()[1].endswith(DELEGATION_SOP)
    factories, _ = run_worker.build_model_factory("configuration-only")
    for factory in factories.values():
        model = factory()
        assert not model.get_config().get("gemini_tools")
    assert run_loaded_tool in run_worker.WORKER_ACTIVITIES


@pytest.mark.asyncio
async def test_real_schedule_creation_is_idempotent():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=shutil.which("temporal")) as env:
        factories = {THINK_MODEL_ID: object}
        await run_worker.ensure_dream_schedule(env.client, factories)
        await run_worker.ensure_dream_schedule(env.client, factories)
        description = await env.client.get_schedule_handle("gwen-dream").describe()
        schedule = description.schedule
        assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
        assert schedule.spec.intervals[0].every == DREAM_MIN_INTERVAL
        assert schedule.action.workflow == "DreamWorkflow"
        args = await env.client.data_converter.decode(schedule.action.args)
        assert args == [THINK_MODEL_ID]
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "schedule.json").write_text(json.dumps({
            "workflow": schedule.action.workflow,
            "model_id": args[0],
            "overlap": schedule.policy.overlap.name,
            "interval_seconds": schedule.spec.intervals[0].every.total_seconds(),
            "duplicate_create": "accepted without replacing schedule",
        }, indent=2))


@workflow.defn
class MCPDiscoveryProbe:
    @workflow.run
    async def run(self, server: str) -> list:
        return await workflow.execute_activity(
            f"{server}-list-tools", start_to_close_timeout=MCP_START_TO_CLOSE,
            retry_policy=MODEL_RETRY_POLICY,
        )


@pytest.mark.asyncio
async def test_optional_mcp_failure_preserves_connected_discovery(caplog):
    def exited_server():
        return MCPClient(lambda: stdio_client(StdioServerParameters(command="/usr/bin/false")))

    clients = {"optional-exit": exited_server, "required-exit": exited_server,
               "shell": run_worker.mcp_client_factories()["shell"]}
    plugin = StrandsPlugin(mcp_clients=clients)
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"), plugins=[plugin],
    ) as env:
        queue = f"mcp-discovery-{uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[MCPDiscoveryProbe],
            workflow_runner=UnsandboxedWorkflowRunner(),
            interceptors=[run_worker.OptionalMCPDiscoveryInterceptor({"optional-exit"})],
        ):
            optional = await env.client.execute_workflow(
                MCPDiscoveryProbe.run, "optional-exit", id=f"{queue}-optional", task_queue=queue,
            )
            assert optional == []
            connected = await env.client.execute_workflow(
                MCPDiscoveryProbe.run, "shell", id=f"{queue}-shell", task_queue=queue,
            )
            assert connected
            from temporalio.client import WorkflowFailureError
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    MCPDiscoveryProbe.run, "required-exit", id=f"{queue}-required", task_queue=queue,
                )
    assert "Optional MCP server 'optional-exit' discovery failed" in caplog.text
    (EVIDENCE / "optional-mcp.json").write_text(json.dumps({
        "optional_exited_process_result": optional,
        "connected_shell_tools": connected,
        "required_exited_process": "WorkflowFailureError",
        "warning_recorded": True,
    }, indent=2))
