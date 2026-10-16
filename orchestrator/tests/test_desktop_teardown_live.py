"""The session desktop is torn down on every final workflow exit.

Real ``ChatWorkflow`` runs on an isolated ``WorkflowEnvironment.start_local``
server (needs the ``temporal`` CLI on PATH). Docker is not part of the proof:
``provision_desktop`` / ``teardown_desktop`` are stubbed under their real
activity names and result shapes, with the teardown stub recording its calls.

``gwen-desktop-<session_id>`` only disappears when something stops it, so the
workflow must reach ``teardown_desktop`` on a normal ``end_chat``, when an
operator cancels the run, and when the run fails — but NOT on Continue-As-New,
where the binding is carried into the next run.
"""

from __future__ import annotations

import os
import shutil
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.strands import StrandsPlugin
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from config import DEFAULT_MODEL_ID, MODEL_START_TO_CLOSE, desktop_container_name, desktop_task_queue
from load_tool import load_tool_activity
from workflow import ChatInput, ChatWorkflow, DesktopBinding

pytestmark = pytest.mark.asyncio


def _stub_desktop_activities(torn_down: list[str]):
    @activity.defn(name="provision_desktop")
    async def claimed_desktop(session_id: str) -> dict[str, str]:
        return {"container": desktop_container_name(session_id),
                "queue": desktop_task_queue(session_id), "novnc_url": "http://viewer.test/vnc.html"}

    @activity.defn(name="teardown_desktop")
    async def released_desktop(session_id: str) -> None:
        torn_down.append(session_id)

    return claimed_desktop, released_desktop


async def _environment(*dev_server_extra_args: str):
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
    return await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"), plugins=[StrandsPlugin()],
        dev_server_extra_args=list(dev_server_extra_args),
    )


async def test_end_chat_after_claim_tears_down_once() -> None:
    torn_down: list[str] = []
    async with await _environment() as env:
        queue = f"teardown-end-{uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[ChatWorkflow],
            activities=[load_tool_activity, *_stub_desktop_activities(torn_down)],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ChatWorkflow.run, ChatInput(DEFAULT_MODEL_ID, "Teardown on end"),
                id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
            )
            bound = await handle.execute_update(ChatWorkflow.desktop)
            assert bound["container"] == desktop_container_name(queue)
            await handle.signal(ChatWorkflow.end_chat)
            await handle.result()
    assert torn_down == [queue]


async def test_end_chat_without_claim_never_tears_down() -> None:
    torn_down: list[str] = []
    async with await _environment() as env:
        queue = f"teardown-none-{uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[ChatWorkflow],
            activities=[load_tool_activity, *_stub_desktop_activities(torn_down)],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ChatWorkflow.run, ChatInput(DEFAULT_MODEL_ID, "No desktop, no teardown"),
                id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
            )
            await handle.signal(ChatWorkflow.end_chat)
            await handle.result()
    assert torn_down == []


async def test_operator_cancel_after_claim_tears_down_once() -> None:
    torn_down: list[str] = []
    async with await _environment() as env:
        queue = f"teardown-cancel-{uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[ChatWorkflow],
            activities=[load_tool_activity, *_stub_desktop_activities(torn_down)],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ChatWorkflow.run, ChatInput(DEFAULT_MODEL_ID, "Teardown on cancel"),
                id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
            )
            await handle.execute_update(ChatWorkflow.desktop)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError) as failure:
                await handle.result()
            assert isinstance(failure.value.cause, CancelledError)
            history = await handle.fetch_history()
            scheduled = [event.activity_task_scheduled_event_attributes.activity_type.name
                         for event in history.events
                         if event.HasField("activity_task_scheduled_event_attributes")]
            assert scheduled.count("teardown_desktop") == 1
    assert torn_down == [queue]


async def test_startup_failure_after_carried_claim_tears_down_once() -> None:
    """A run that inherits a desktop from a prior run and then fails must release it."""
    torn_down: list[str] = []

    @activity.defn(name="load_tool")
    async def broken_starter(path: str, name: str) -> dict:
        return {"status": "error", "content": [{"text": f"{name} unavailable"}]}

    async with await _environment() as env:
        queue = f"teardown-failure-{uuid4().hex}"
        carried = DesktopBinding(container=desktop_container_name(queue),
                                 queue=desktop_task_queue(queue), novnc_url="http://viewer.test/vnc.html")
        async with Worker(
            env.client, task_queue=queue, workflows=[ChatWorkflow],
            activities=[broken_starter, *_stub_desktop_activities(torn_down)],
            workflow_runner=UnsandboxedWorkflowRunner(),
            workflow_failure_exception_types=[RuntimeError],
        ):
            handle = await env.client.start_workflow(
                ChatWorkflow.run,
                ChatInput(DEFAULT_MODEL_ID, "Teardown on failure", desktop=carried),
                id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
            )
            with pytest.raises(WorkflowFailureError) as failure:
                await handle.result()
            assert isinstance(failure.value.cause, ApplicationError)
            assert "Cannot register starter tool" in str(failure.value.cause)
    assert torn_down == [queue]


async def test_continue_as_new_carries_the_desktop_instead_of_tearing_down() -> None:
    torn_down: list[str] = []
    # The server, not the workflow, decides when rollover is suggested; lower
    # its history-count threshold so a handful of updates triggers it.
    async with await _environment(
        "--dynamic-config-value", "limit.historyCount.suggestContinueAsNew=40",
    ) as env:
        queue = f"teardown-can-{uuid4().hex}"
        async with Worker(
            env.client, task_queue=queue, workflows=[ChatWorkflow],
            activities=[load_tool_activity, *_stub_desktop_activities(torn_down)],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ChatWorkflow.run, ChatInput(DEFAULT_MODEL_ID, "Carry desktop across rollover"),
                id=queue, task_queue=queue, execution_timeout=MODEL_START_TO_CLOSE,
            )
            first_run = (await handle.describe()).run_id
            bound = await handle.execute_update(ChatWorkflow.desktop)
            # Each accepted update adds history events; keep nudging until the
            # workflow has rolled over into a new run.
            for _ in range(100):
                if (await handle.describe()).run_id != first_run:
                    break
                await handle.execute_update(ChatWorkflow.truncate_stream, 0)
            assert (await handle.describe()).run_id != first_run, "server never suggested Continue-As-New"
            assert torn_down == [], "rollover must not release the desktop"
            # The next run already holds the same binding without provisioning.
            assert await handle.execute_update(ChatWorkflow.desktop) == bound
            await handle.signal(ChatWorkflow.end_chat)
            await handle.result()
            history = await handle.fetch_history()
            provisioned = [event for event in history.events
                           if event.HasField("activity_task_scheduled_event_attributes")
                           and event.activity_task_scheduled_event_attributes.activity_type.name == "provision_desktop"]
            assert provisioned == []
    assert torn_down == [queue]
