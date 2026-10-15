import asyncio
import json
import os
import platform
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from temporalio.client import WorkflowUpdateFailedError
from temporalio.contrib.strands import StrandsPlugin
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from browser_activity import desktop_control
from config import DEFAULT_MODEL_ID, DESKTOP_BROWSER_TASK_QUEUE, MODEL_START_TO_CLOSE
from load_tool import load_tool_activity
from workflow import ChatInput, ChatWorkflow


EVIDENCE = Path(__file__).resolve().parents[2] / ".omo/evidence/2026-10-09-handoff-truncate"


@pytest.mark.asyncio
async def test_real_truncate_update_and_reconnect_below_base() -> None:
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"), plugins=[StrandsPlugin()],
    ) as env:
        queue = f"truncate-proof-{uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[ChatWorkflow],
            activities=[load_tool_activity], workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ChatWorkflow.run, ChatInput(DEFAULT_MODEL_ID, "Inspect stream truncation"),
                id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
            )
            async with WorkflowStreamClient.create(env.client, queue) as stream:
                for index in range(4):
                    stream.topic("events").publish({"index": index})
                await stream.flush()
                assert await stream.get_offset() == 4
                await handle.execute_update(ChatWorkflow.truncate_stream, 3)
                subscription = stream.subscribe(["events"], from_offset=1)
                try:
                    item = await asyncio.wait_for(anext(subscription), timeout=10)
                finally:
                    await subscription.aclose()
                assert item.offset == 3
                assert item.data == {"index": 3}
                with pytest.raises(WorkflowUpdateFailedError) as rejected:
                    await handle.execute_update(ChatWorkflow.truncate_stream, 5)
                assert rejected.value.cause.type == "TruncateOutOfRange"
                assert await stream.get_offset() == 4
                await handle.execute_update(ChatWorkflow.truncate_stream, 4)
                await handle.execute_update(ChatWorkflow.truncate_stream, 2)
                assert await stream.get_offset() == 4
            await handle.signal(ChatWorkflow.end_chat)
            await handle.result()
            (EVIDENCE / "truncate-history.json").write_text((await handle.fetch_history()).to_json())
            (EVIDENCE / "truncate-result.json").write_text(json.dumps({
                "reconnected_offset": item.offset, "reconnected_data": item.data,
                "out_of_range_error": rejected.value.cause.type, "global_end_offset": 4,
            }, indent=2))


@pytest.mark.asyncio
async def test_handoff_reaches_real_desktop_activity_and_preserves_rejection() -> None:
    # This test exercises the real host safety check, never desktop input.
    # Successful Linux/VNC transfers belong to the parent's browser app test.
    assert platform.system() == "Darwin", "Host-rejection scenario requires the Mac development host"
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
    import server

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    previous_client = server._state["client"]
    try:
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=shutil.which("temporal"), plugins=[StrandsPlugin()],
        ) as env:
            server._state["client"] = env.client
            queue = f"handoff-proof-{uuid4().hex}"
            with ThreadPoolExecutor(max_workers=2) as executor:
                async with Worker(
                    env.client, task_queue=queue, workflows=[ChatWorkflow],
                    activities=[load_tool_activity], workflow_runner=UnsandboxedWorkflowRunner(),
                ), Worker(
                    env.client, task_queue=DESKTOP_BROWSER_TASK_QUEUE,
                    activities=[desktop_control], activity_executor=executor,
                ):
                    handle = await env.client.start_workflow(
                        ChatWorkflow.run, ChatInput(DEFAULT_MODEL_ID, "Inspect desktop queue dispatch"),
                        id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
                    )
                    observed = []
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=server.app), base_url="http://test",
                    ) as api:
                        for action in ("take", "release", "give"):
                            response = await api.post(f"/sessions/{queue}/handoff", json={
                                "action": action, "message": "Continue in this chat",
                            })
                            assert response.status_code == 409
                            assert "Desktop control cannot execute on the host" in response.text
                            observed.append({"action": action, "status": response.status_code,
                                             "body": response.json()})
                        invalid = await api.post(f"/sessions/{queue}/handoff", json={
                            "action": "give", "message": " ",
                        })
                        assert invalid.status_code == 422
                    assert await handle.query(ChatWorkflow.model_id) == DEFAULT_MODEL_ID
                    assert await handle.query(ChatWorkflow.messages) == []
                    await handle.signal(ChatWorkflow.end_chat)
                    await handle.result()
                    history = await handle.fetch_history()
                    calls = [event.activity_task_scheduled_event_attributes
                             for event in history.events
                             if event.HasField("activity_task_scheduled_event_attributes")
                             and event.activity_task_scheduled_event_attributes.activity_type.name == "desktop_control"]
                    assert len(calls) == 3
                    assert all(call.task_queue.name == DESKTOP_BROWSER_TASK_QUEUE for call in calls)
                    actions = [json.loads(call.input.payloads[0].data) for call in calls]
                    assert actions == ["take", "release", "prepare_resume"]
                    (EVIDENCE / "handoff-history.json").write_text(history.to_json())
                    (EVIDENCE / "handoff-result.json").write_text(json.dumps({
                        "responses": observed, "activity_actions": actions,
                        "task_queue": DESKTOP_BROWSER_TASK_QUEUE,
                    }, indent=2))
    finally:
        server._state["client"] = previous_client
