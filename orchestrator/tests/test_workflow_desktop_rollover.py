"""Desktop continuation regressions with native Temporal and a stub-only agent."""

from __future__ import annotations

import asyncio
import copy
import shutil
from datetime import timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from strands.models import Model
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowUpdateFailedError, WorkflowUpdateStage
from temporalio.common import RetryPolicy
from temporalio.contrib.strands import StrandsPlugin, TemporalAgent
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.testing import WorkflowEnvironment
from temporalio.exceptions import ActivityError, TimeoutError as ActivityTimeoutError, TimeoutType
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from config import (
    DESKTOP_BROWSER_TASK_QUEUE,
    DESKTOP_HANDOFF_TIMEOUT,
    DESKTOP_MUTATION_RETRY_POLICY,
    DESKTOP_TASK_TIMEOUT,
    DESKTOP_SCHEDULE_TO_START,
    DESKTOP_ACTION_SCHEDULE_TO_CLOSE,
)
from computer_use_activity import COMPUTER_USE_ACTIVITIES
from workflow import (
    ChatInput, ChatWorkflow, TurnInput, _HumanControlHook,
    _DESKTOP_ACTIVITY_OPTIONS, PERMANENT_COMMUNITY_TOOLS,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")
MODEL_MESSAGES = []
CAPTURES = []
RELEASES = []
CONTROLS = []
CONTROL_STARTED = asyncio.Event()
CONTROL_RELEASE = asyncio.Event()
MODEL_STARTED = asyncio.Event()
MODEL_RELEASE = asyncio.Event()


class DesktopStubModel(Model):
    def __init__(self):
        self.config = {}

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        MODEL_MESSAGES.append(copy.deepcopy(messages))
        MODEL_STARTED.set()
        await MODEL_RELEASE.wait()
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": "stub reply"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 1}}}


@activity.defn(name="take_screenshot")
async def stub_capture() -> str:
    CAPTURES.append(activity.info().workflow_run_id)
    return "fresh desktop observation"


@activity.defn(name="release_desktop")
async def stub_release() -> None:
    info = activity.info()
    RELEASES.append((info.workflow_id, info.workflow_run_id, info.task_queue))


@activity.defn(name="desktop_control")
async def stub_desktop_control(action: str) -> dict:
    info = activity.info()
    CONTROLS.append((action, info.workflow_id, info.workflow_run_id, info.task_queue))
    CONTROL_STARTED.set()
    await CONTROL_RELEASE.wait()
    return {"action": action, "owner": {"namespace": info.namespace, "workflow_id": info.workflow_id}}


@workflow.defn
class DesktopTestWorkflow(ChatWorkflow):
    @workflow.run
    async def run(self, input: ChatInput) -> None:
        await super().run(input)

    def _build_agent(self, messages):
        # Exercise the production desktop handlers without provider/MCP setup.
        return TemporalAgent(
            model="desktop-stub", messages=messages, callback_handler=None,
            tools=[activity_as_tool(stub_capture, task_queue=DESKTOP_BROWSER_TASK_QUEUE,
                                    start_to_close_timeout=timedelta(seconds=5))],
            hooks=[_HumanControlHook(self._human_control, self._handoff_requested,
                                     self._handoffs.publish)],
            streaming_topic="events", start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

    @workflow.signal
    def request_rollover(self) -> None:
        # The existing test threshold enters the same drain as history pressure.
        self._completed_turns = 1

    @workflow.update
    async def hold_drain(self) -> None:
        await workflow.wait_condition(lambda: getattr(self, "_drain_released", False))

    @workflow.signal
    def release_drain(self) -> None:
        self._drain_released = True

    @workflow.signal
    def record_history(self, messages: list[dict]) -> None:
        self._agent.messages.extend(messages)

    @workflow.query
    def desktop_state(self) -> dict:
        return {**self.control_status(), "closing": self._closing,
                "handoff_requested": self._handoff_requested[0],
                "resume_prompt": self._resume_prompt}


@workflow.defn
class QueuedDesktopActionWorkflow:
    @workflow.run
    async def run(self, tool_name: str) -> str:
        tools = [*PERMANENT_COMMUNITY_TOOLS, *[
            activity_as_tool(action, **_DESKTOP_ACTIVITY_OPTIONS)
            for action in COMPUTER_USE_ACTIVITIES
        ]]
        native = copy.copy(next(tool for tool in tools if tool.tool_name == tool_name))
        # Preserve production budgets; isolate the queue so no worker can execute.
        native._options = {**native._options, "task_queue": f"absent-{workflow.info().workflow_id}"}
        args = {"browser_input": {"action": {"type": "list_sessions"}}} if tool_name == "browser" else {}
        try:
            async for _ in native.stream({"toolUseId": "queued", "name": tool_name, "input": args}, {}):
                pass
        except ActivityError as error:
            assert isinstance(error.cause, ActivityTimeoutError)
            assert error.cause.type == TimeoutType.SCHEDULE_TO_START
            return "schedule-to-start timeout"
        raise AssertionError("Queued desktop action unexpectedly executed")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client():
    cli = shutil.which("temporal")
    assert cli, "The Temporal CLI is required; do not connect to the live server"
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=cli,
        plugins=[StrandsPlugin(models={"desktop-stub": DesktopStubModel})],
    ) as env:
        async with Worker(
            env.client, task_queue="desktop-rollover-tests",
            workflows=[DesktopTestWorkflow, QueuedDesktopActionWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ), Worker(env.client, task_queue=DESKTOP_BROWSER_TASK_QUEUE,
                  activities=[stub_capture, stub_release, stub_desktop_control]):
            yield env.client


@pytest_asyncio.fixture(loop_scope="module")
async def session(client):
    MODEL_MESSAGES.clear()
    CAPTURES.clear()
    RELEASES.clear()
    CONTROLS.clear()
    CONTROL_STARTED.clear()
    CONTROL_RELEASE.set()
    MODEL_STARTED.clear()
    MODEL_RELEASE.set()
    session_id = f"desktop-rollover-{uuid4()}"
    await client.start_workflow(
        DesktopTestWorkflow.run,
        ChatInput(model_id="desktop-stub", system_prompt="", session_id=session_id,
                  rollover_turns=1),
        id=session_id, task_queue="desktop-rollover-tests",
    )
    handle = client.get_workflow_handle(session_id)
    try:
        await state_when(handle, lambda state: state["ready"])
        yield handle
    finally:
        MODEL_RELEASE.set()
        CONTROL_RELEASE.set()
        if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
            await handle.signal(ChatWorkflow.end_chat)
        await asyncio.wait_for(handle.result(), 10)


async def state_when(handle, predicate):
    async with asyncio.timeout(5):
        while True:
            state = await handle.query(DesktopTestWorkflow.desktop_state)
            if predicate(state):
                return state
            await asyncio.sleep(0.02)


async def test_claim_during_automatic_rollover_drain_preserves_ownership(session):
    MODEL_RELEASE.clear()
    old_run = (await session.query("control_status"))["run_id"]
    turn = await session.start_update(
        ChatWorkflow.turn, TurnInput(prompt="active desktop task"),
        wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    await asyncio.wait_for(MODEL_STARTED.wait(), 5)
    await session.signal(DesktopTestWorkflow.request_rollover)
    await state_when(session, lambda state: state["closing"])
    claim = await session.start_update(
        ChatWorkflow.claim_control, wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    claim_result = asyncio.create_task(claim.result())
    state = await state_when(session, lambda state: state["human_control"])
    assert state["run_id"] == old_run
    assert not state["ready"]
    assert not claim_result.done()
    MODEL_RELEASE.set()
    await asyncio.wait_for(turn.result(), 5)
    await asyncio.wait_for(claim_result, 5)
    state = await state_when(session, lambda state: state["run_id"] != old_run and state["ready"])
    assert state["human_control"] is True
    assert state["handoff_requested"] is True
    with pytest.raises(WorkflowUpdateFailedError):
        await session.execute_update(ChatWorkflow.turn, TurnInput(prompt="must stay fenced"))
    assert len(MODEL_MESSAGES) == 1
    assert CAPTURES == []


async def test_automatic_rollover_while_human_retains_fence_and_session(session):
    await session.execute_update(ChatWorkflow.claim_control)
    old_run = (await session.query("control_status"))["run_id"]
    await session.signal(DesktopTestWorkflow.request_rollover)
    state = await state_when(session, lambda state: state["run_id"] != old_run and state["ready"])
    assert state["human_control"] is True
    assert state["handoff_requested"] is True
    assert await session.query(ChatWorkflow.session_id) == session.id
    with pytest.raises(WorkflowUpdateFailedError):
        await session.execute_update(ChatWorkflow.turn, TurnInput(prompt="must stay fenced"))
    assert MODEL_MESSAGES == []
    assert CAPTURES == []
    assert RELEASES == []
    await session.signal(ChatWorkflow.end_chat)
    await asyncio.wait_for(session.result(), 5)
    assert RELEASES == [(session.id, state["run_id"], DESKTOP_BROWSER_TASK_QUEUE)]


async def test_claim_during_committed_resume_is_rejected(session):
    drain = await session.start_update(
        DesktopTestWorkflow.hold_drain, wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    await session.execute_update(ChatWorkflow.claim_control)
    old_run = await session.execute_update(ChatWorkflow.relinquish_control, "resume now")
    try:
        with pytest.raises(WorkflowUpdateFailedError) as error:
            await session.execute_update(ChatWorkflow.claim_control)
        assert error.value.cause.type == "SessionRollingOver"
    finally:
        await session.signal(DesktopTestWorkflow.release_drain)
        await asyncio.wait_for(drain.result(), 5)
        await state_when(session, lambda state: state["run_id"] != old_run and state["ready"])


async def test_resume_changes_only_run_consumes_steering_once_and_streams_capture(session, client):
    await session.execute_update(ChatWorkflow.claim_control)
    steering = "Use the human-updated desktop note"
    old_run = await session.execute_update(ChatWorkflow.relinquish_control, steering)
    state = await state_when(session, lambda state: state["run_id"] != old_run and state["ready"])
    resumed_run = state["run_id"]
    assert state["human_control"] is False
    assert state["handoff_requested"] is False
    assert await session.query(ChatWorkflow.session_id) == session.id
    assert await session.execute_update(ChatWorkflow.relinquish_control, steering) == old_run
    frames = []

    async def collect_capture():
        stream = WorkflowStreamClient.create(client, session.id)
        async for frame in stream.subscribe(["events", "tool_results"]):
            frames.append((frame.topic, frame.data))
            if frame.topic == "tool_results":
                return

    capture_events = asyncio.create_task(collect_capture())
    try:
        await session.execute_update(ChatWorkflow.turn, TurnInput(prompt=steering))
        await asyncio.wait_for(capture_events, 5)
    finally:
        capture_events.cancel()
        await asyncio.gather(capture_events, return_exceptions=True)
    capture_id = f"resume-{resumed_run}"
    assert frames[:3] == [
        ("events", {"contentBlockStart": {"start": {"toolUse": {
            "toolUseId": capture_id, "name": "take_screenshot"}}}}),
        ("events", {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}),
        ("events", {"contentBlockStop": {}}),
    ]
    assert frames[3][0] == "tool_results"
    assert frames[3][1]["tool_use_id"] == capture_id
    assert CAPTURES == [resumed_run]
    assert any("fresh desktop observation" in str(message) for message in MODEL_MESSAGES[0])
    # The normal threshold rolls the resumed turn again; steering must stay consumed.
    await state_when(session, lambda state: state["run_id"] != resumed_run and state["ready"])
    await session.execute_update(ChatWorkflow.turn, TurnInput(prompt="follow-up"))
    messages = await session.query(ChatWorkflow.messages)
    assert sum(block.get("text") == steering for message in messages for block in message["content"]) == 1
    assert CAPTURES == [resumed_run]
    assert (await session.query(DesktopTestWorkflow.desktop_state))["resume_prompt"] == ""


@pytest.mark.parametrize("desktop_patches", [False, True], ids=["legacy", "patched"])
async def test_desktop_continuation_history_replays(client, desktop_patches):
    MODEL_RELEASE.set()
    session_id = f"desktop-replay-{uuid4()}"
    async with Worker(
        client, task_queue=session_id, workflows=[DesktopTestWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        patch_activation_callback=lambda patch: desktop_patches if patch.patch_id.startswith("desktop-") else True,
    ):
        # Old serialized input deliberately lacks the two new optional fields.
        original = await client.start_workflow(
            "DesktopTestWorkflow", {"model_id": "desktop-stub", "system_prompt": "",
                                    "session_id": session_id},
            id=session_id, task_queue=session_id,
        )
        handle = client.get_workflow_handle(session_id)
        await state_when(handle, lambda state: state["ready"])
        await handle.execute_update(ChatWorkflow.claim_control)
        old_run = await handle.execute_update(ChatWorkflow.relinquish_control, "replay steering")
        state = await state_when(handle, lambda state: state["run_id"] != old_run and state["ready"])
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="replay steering"))
        await handle.signal(ChatWorkflow.end_chat)
        await asyncio.wait_for(handle.result(), 5)
        histories = [await original.fetch_history(), await client.get_workflow_handle(
            session_id, run_id=state["run_id"],
        ).fetch_history()]

    replayer = Replayer(
        workflows=[DesktopTestWorkflow], workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=client.data_converter,
    )
    calls_before_replay = (len(MODEL_MESSAGES), len(CAPTURES), len(RELEASES))
    for history in histories:
        markers = [event.marker_recorded_event_attributes.details.get("patch-data")
                   for event in history.events if event.HasField("marker_recorded_event_attributes")]
        assert any(b"desktop-control-rollover-v1" in payload.data
                   for marker in markers if marker for payload in marker.payloads) is desktop_patches
        await replayer.replay_workflow(history)
    assert (len(MODEL_MESSAGES), len(CAPTURES), len(RELEASES)) == calls_before_replay


async def test_end_text_session_never_schedules_desktop_cleanup(session):
    await session.signal(DesktopTestWorkflow.record_history, [
        {"role": "user", "content": [{"text": 'Discuss {"toolUse":{"name":"browser"}} as text'}]},
    ])
    await session.execute_update(ChatWorkflow.turn, TurnInput(prompt="text only"))
    await state_when(session, lambda state: state["ready"])
    await session.signal(ChatWorkflow.end_chat)
    await asyncio.wait_for(session.result(), 5)
    assert RELEASES == []


@pytest.mark.parametrize("tool_name,nested", [
    ("browser", False), ("click", False), ("take_screenshot", True), ("click_at", True),
])
async def test_end_recorded_desktop_session_releases_on_mock_queue(session, tool_name, nested):
    messages = [
        {"role": "assistant", "content": [{"toolUse": {
            "toolUseId": "desktop-call", "name": tool_name, "input": {},
        }}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "desktop-call", "status": "success", "content": [{"text": "saved result"}],
        }}]},
    ]
    if nested:
        messages = [{"role": "user", "content": [{"toolResult": {
            "toolUseId": "delegated-call", "status": "success",
            "content": [{"json": {"messages": messages}}],
        }}]}]
    await session.signal(DesktopTestWorkflow.record_history, messages)
    state = await session.query("control_status")
    await session.signal(ChatWorkflow.end_chat)
    await asyncio.wait_for(session.result(), 5)
    assert RELEASES == [(session.id, state["run_id"], DESKTOP_BROWSER_TASK_QUEUE)]
    history = await session.fetch_history()
    scheduled = [event.activity_task_scheduled_event_attributes for event in history.events
                 if event.HasField("activity_task_scheduled_event_attributes")]
    assert len(scheduled) == 1
    release = scheduled[0]
    assert release.activity_type.name == "release_desktop"
    assert release.task_queue.name == DESKTOP_BROWSER_TASK_QUEUE
    assert not release.input.payloads
    assert release.start_to_close_timeout.ToTimedelta() == 2 * DESKTOP_HANDOFF_TIMEOUT
    assert release.schedule_to_start_timeout.ToTimedelta() == DESKTOP_HANDOFF_TIMEOUT
    assert release.schedule_to_close_timeout.ToTimedelta() == DESKTOP_TASK_TIMEOUT
    assert release.retry_policy.maximum_attempts == DESKTOP_MUTATION_RETRY_POLICY.maximum_attempts


async def test_end_during_rollover_drain_releases_without_successor(session):
    await session.execute_update(ChatWorkflow.claim_control)
    state = await session.query("control_status")
    drain = await session.start_update(
        DesktopTestWorkflow.hold_drain, wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    try:
        await session.signal(DesktopTestWorkflow.request_rollover)
        await state_when(session, lambda state: state["closing"])
        await session.signal(ChatWorkflow.end_chat)
        assert RELEASES == []
    finally:
        await session.signal(DesktopTestWorkflow.release_drain)
        await asyncio.wait_for(drain.result(), 5)
    await asyncio.wait_for(session.result(), 5)
    assert RELEASES == [(session.id, state["run_id"], DESKTOP_BROWSER_TASK_QUEUE)]
    assert (await session.describe()).run_id == state["run_id"]


async def test_completed_handoff_remains_cleanup_evidence_after_rollover(session):
    await session.execute_update(ChatWorkflow.claim_control)
    await session.signal(ChatWorkflow.give_control, "")
    state = await session.query("control_status")
    await session.signal(DesktopTestWorkflow.request_rollover)
    state = await state_when(session, lambda current: current["run_id"] != state["run_id"] and current["ready"])
    assert not state["human_control"]
    assert RELEASES == []
    await session.signal(ChatWorkflow.end_chat)
    await asyncio.wait_for(session.result(), 5)
    assert RELEASES == [(session.id, state["run_id"], DESKTOP_BROWSER_TASK_QUEUE)]


async def test_end_waits_for_handlers_then_detects_their_desktop_evidence(session):
    drain = await session.start_update(
        DesktopTestWorkflow.hold_drain, wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    state = await session.query("control_status")
    try:
        await session.signal(ChatWorkflow.end_chat)
        await state_when(session, lambda state: state["closing"])
        assert RELEASES == []
        with pytest.raises(WorkflowUpdateFailedError) as error:
            await session.execute_update(ChatWorkflow.claim_control)
        assert error.value.cause.type == "SessionEnding"
        await session.signal(DesktopTestWorkflow.record_history, [
            {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": "last-action", "name": "browser", "input": {},
            }}]},
        ])
    finally:
        await session.signal(DesktopTestWorkflow.release_drain)
        await asyncio.wait_for(drain.result(), 5)
    await asyncio.wait_for(session.result(), 5)
    assert RELEASES == [(session.id, state["run_id"], DESKTOP_BROWSER_TASK_QUEUE)]


async def test_native_desktop_control_roundtrip_and_successor_resume(session):
    status = await session.execute_update("desktop_control", "status")
    assert status["owner"]["workflow_id"] == session.id
    await session.execute_update(ChatWorkflow.claim_control)
    old_run = (await session.query("control_status"))["run_id"]
    for action in ("take", "release", "prepare_resume"):
        result = await session.execute_update("desktop_control", action)
        assert result["action"] == action
    await session.execute_update(ChatWorkflow.relinquish_control, "continue after native handoff")
    state = await state_when(session, lambda state: state["run_id"] != old_run and state["ready"])
    result = await session.execute_update("desktop_control", "resume")
    assert result["action"] == "resume"
    assert [call[0] for call in CONTROLS] == ["status", "take", "release", "prepare_resume", "resume"]
    assert all(call[1] == session.id and call[3] == DESKTOP_BROWSER_TASK_QUEUE for call in CONTROLS)
    assert CONTROLS[-1][2] == state["run_id"]
    assert RELEASES == []


@pytest.mark.parametrize("action", ["take", "release", "prepare_resume", "resume", "invalid"])
async def test_native_desktop_control_rejects_invalid_state_without_activity(session, action):
    with pytest.raises(WorkflowUpdateFailedError):
        await session.execute_update("desktop_control", action)
    assert CONTROLS == []


async def test_native_resume_rejects_human_ownership(session):
    await session.execute_update(ChatWorkflow.claim_control)
    with pytest.raises(WorkflowUpdateFailedError):
        await session.execute_update("desktop_control", "resume")
    assert CONTROLS == []


async def test_end_drains_native_control_before_release_and_rejects_new_updates(session):
    await session.execute_update(ChatWorkflow.claim_control)
    CONTROL_RELEASE.clear()
    control = await session.start_update(
        "desktop_control", "take", wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    await asyncio.wait_for(CONTROL_STARTED.wait(), 5)
    try:
        await session.signal(ChatWorkflow.end_chat)
        await state_when(session, lambda state: state["closing"])
        assert RELEASES == []
        for action in ("take", "release", "prepare_resume", "resume", "status"):
            with pytest.raises(WorkflowUpdateFailedError):
                await session.execute_update("desktop_control", action)
    finally:
        CONTROL_RELEASE.set()
        await asyncio.wait_for(control.result(), 5)
    await asyncio.wait_for(session.result(), 5)
    assert len(RELEASES) == 1
    assert len(CONTROLS) == 1
    history = await session.fetch_history()
    scheduled = [event.activity_task_scheduled_event_attributes for event in history.events
                 if event.HasField("activity_task_scheduled_event_attributes")]
    assert [entry.activity_type.name for entry in scheduled] == ["desktop_control", "release_desktop"]
    native = scheduled[0]
    assert native.task_queue.name == DESKTOP_BROWSER_TASK_QUEUE
    assert native.start_to_close_timeout.ToTimedelta() == 2 * DESKTOP_HANDOFF_TIMEOUT
    assert native.schedule_to_start_timeout.ToTimedelta() == DESKTOP_HANDOFF_TIMEOUT
    assert native.schedule_to_close_timeout.ToTimedelta() == DESKTOP_TASK_TIMEOUT
    assert native.retry_policy.maximum_attempts == 1


async def test_rollover_drains_native_control_and_preserves_human_fence(session):
    await session.execute_update(ChatWorkflow.claim_control)
    old_run = (await session.query("control_status"))["run_id"]
    CONTROL_RELEASE.clear()
    control = await session.start_update(
        "desktop_control", "take", wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    await asyncio.wait_for(CONTROL_STARTED.wait(), 5)
    try:
        await session.signal(DesktopTestWorkflow.request_rollover)
        await state_when(session, lambda state: state["closing"])
        with pytest.raises(WorkflowUpdateFailedError):
            await session.execute_update("desktop_control", "release")
        assert RELEASES == []
    finally:
        CONTROL_RELEASE.set()
        await asyncio.wait_for(control.result(), 5)
    state = await state_when(session, lambda state: state["run_id"] != old_run and state["ready"])
    assert state["human_control"] is True
    assert state["handoff_requested"] is True
    assert RELEASES == []
    await session.execute_update("desktop_control", "release")
    assert CONTROLS[-1][2] == state["run_id"]


async def test_status_during_active_turn_does_not_wait_on_turn_lock(session):
    MODEL_RELEASE.clear()
    turn = await session.start_update(
        ChatWorkflow.turn, TurnInput(prompt="active text"),
        wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    await asyncio.wait_for(MODEL_STARTED.wait(), 5)
    try:
        result = await asyncio.wait_for(session.execute_update("desktop_control", "status"), 5)
        assert result["action"] == "status"
        assert RELEASES == []
    finally:
        MODEL_RELEASE.set()
        await asyncio.wait_for(turn.result(), 5)


async def test_all_browser_computer_native_adapters_dispatch_action_budgets():
    tools = [next(tool for tool in PERMANENT_COMMUNITY_TOOLS if tool.tool_name == "browser"), *[
        activity_as_tool(action, **_DESKTOP_ACTIVITY_OPTIONS) for action in COMPUTER_USE_ACTIVITIES
    ]]
    for native in tools:
        dispatch = AsyncMock(return_value={})
        args = {"browser_input": {"action": {"type": "list_sessions"}}} if native.tool_name == "browser" else {}
        with patch("temporalio.contrib.strands._temporal_activity_tool.workflow.execute_activity", dispatch):
            async for _ in native.stream({"toolUseId": "budget", "name": native.tool_name, "input": args}, {}):
                pass
        options = dispatch.call_args.kwargs
        assert options["task_queue"] == DESKTOP_BROWSER_TASK_QUEUE
        assert options["start_to_close_timeout"] == timedelta(seconds=30)
        assert options["schedule_to_start_timeout"] == DESKTOP_SCHEDULE_TO_START == timedelta(seconds=5)
        assert options["schedule_to_close_timeout"] == DESKTOP_ACTION_SCHEDULE_TO_CLOSE == timedelta(seconds=40)
        assert options["retry_policy"].maximum_attempts == 1
        assert options["cancellation_type"] == workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED


@pytest.mark.parametrize("tool_name", ["browser", "take_screenshot"])
async def test_queued_native_action_times_out_before_execution(client, tool_name):
    handle = await client.start_workflow(
        QueuedDesktopActionWorkflow.run, tool_name, id=f"queued-action-{uuid4()}",
        task_queue="desktop-rollover-tests",
    )
    assert await asyncio.wait_for(handle.result(), 12) == "schedule-to-start timeout"
    history = await handle.fetch_history()
    scheduled = [event for event in history.events if event.HasField("activity_task_scheduled_event_attributes")]
    timed_out = [event for event in history.events if event.HasField("activity_task_timed_out_event_attributes")]
    assert len(scheduled) == len(timed_out) == 1
    assert not any(event.HasField("activity_task_started_event_attributes") for event in history.events)
    options = scheduled[0].activity_task_scheduled_event_attributes
    assert options.activity_type.name == tool_name
    assert options.start_to_close_timeout.ToTimedelta() == timedelta(seconds=30)
    assert options.schedule_to_start_timeout.ToTimedelta() == timedelta(seconds=5)
    assert options.schedule_to_close_timeout.ToTimedelta() == timedelta(seconds=40)
    assert options.retry_policy.maximum_attempts == 1
    elapsed = (timed_out[0].event_time.ToDatetime() - scheduled[0].event_time.ToDatetime()).total_seconds()
    assert 4.5 <= elapsed < 10
