"""Offline native handoff dispatch: real runtime files, mocked Temporal/VNC only."""

import asyncio
import json
import shlex
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import httpx
import pytest
from fastapi import HTTPException, Response
from temporalio.client import WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError
from temporalio.exceptions import ApplicationError

import server


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    handle = SimpleNamespace(
        execute_update=AsyncMock(return_value="old-run"),
        query=AsyncMock(return_value={"run_id": "new-run", "ready": True}),
        cancel=AsyncMock(),
    )
    client = SimpleNamespace(namespace="test", get_workflow_handle=MagicMock(return_value=handle))
    client.workflow_service = SimpleNamespace(describe_task_queue=AsyncMock(
        return_value=SimpleNamespace(pollers=[object()]),
    ))
    monkeypatch.setattr(server, "_desktop_poller_cache", {"checked_at": 0.0, "ok": False})
    monkeypatch.setitem(server._state, "client", client)
    monkeypatch.setattr(server.Client, "connect", AsyncMock(side_effect=AssertionError("No real Temporal")))
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", AsyncMock(
        side_effect=AssertionError("No real native commands"),
    ))
    vnc = AsyncMock()
    monkeypatch.setattr(server, "_vnc_input", vnc)
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({
        "owner": {"namespace": "test", "workflow_id": "chat-1"},
        "epoch": 1_789_000_000_123_456_789, "mode": "agent", "session_name": "browser-1",
    }))
    return SimpleNamespace(handle=handle, client=client, vnc=vnc, runtime=runtime, root=tmp_path)


def set_state(desktop, **changes):
    state = json.loads(desktop.runtime.read_text())
    state.update(changes)
    desktop.runtime.write_text(json.dumps(state))
    return state


def test_host_mode_reads_never_rewrite_runtime_or_create_locks(desktop):
    before = desktop.runtime.read_bytes(), desktop.runtime.stat()
    for _ in range(3):
        assert server._desktop_mode("chat-1")["mode"] == "agent"
    after = desktop.runtime.stat()
    assert desktop.runtime.read_bytes() == before[0]
    assert (after.st_ino, after.st_mtime_ns) == (before[1].st_ino, before[1].st_mtime_ns)
    assert list(desktop.root.iterdir()) == [desktop.runtime]


@pytest.mark.asyncio
async def test_take_dispatches_claim_before_native_transfer_without_host_mutations(desktop):
    before = desktop.runtime.read_bytes()
    await server.handoff("chat-1", server.HandoffRequest(action="take"))
    assert desktop.handle.execute_update.await_args_list == [
        call(server.ChatWorkflow.claim_control, rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT),
        call(server.ChatWorkflow.desktop_control, "take", rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT),
    ]
    assert desktop.runtime.read_bytes() == before
    assert list(desktop.root.iterdir()) == [desktop.runtime]
    desktop.vnc.assert_not_awaited()
    desktop.handle.query.assert_not_awaited()
    desktop.handle.cancel.assert_not_awaited()
    desktop.client.get_workflow_handle.assert_called_once_with("chat-1")


@pytest.mark.asyncio
async def test_release_only_dispatches_native_release(desktop):
    set_state(desktop, mode="human")
    await server.handoff("chat-1", server.HandoffRequest(action="release"))
    desktop.handle.execute_update.assert_awaited_once_with(
        server.ChatWorkflow.desktop_control, "release", rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT,
    )
    desktop.handle.query.assert_not_awaited()
    desktop.vnc.assert_not_awaited()
    assert server._desktop_mode("chat-1")["mode"] == "human"


@pytest.mark.asyncio
async def test_resume_revokes_before_steering_and_waits_for_ready_successor(desktop):
    set_state(desktop, mode="instructions")
    calls = []
    statuses = iter([
        {"run_id": "old-run", "ready": True},
        {"run_id": "new-run", "ready": False},
        {"run_id": "new-run", "ready": True},
    ])

    async def update(method, *args, **kwargs):
        calls.append((method, args))
        assert kwargs == {"rpc_timeout": server.DESKTOP_HANDOFF_TIMEOUT}
        return "old-run"

    async def query(method, **kwargs):
        calls.append((method, ()))
        return next(statuses)

    desktop.handle.execute_update.side_effect = update
    desktop.handle.query.side_effect = query
    feedback = "  HUMAN-UPDATED: keep my visible note  "
    await server.handoff("chat-1", server.HandoffRequest(action="give", message=feedback))
    assert calls == [
        (server.ChatWorkflow.desktop_control, ("prepare_resume",)),
        (server.ChatWorkflow.relinquish_control, (feedback,)),
        (server.ChatWorkflow.control_status, ()),
        (server.ChatWorkflow.control_status, ()),
        (server.ChatWorkflow.control_status, ()),
        (server.ChatWorkflow.desktop_control, ("resume",)),
    ]
    desktop.client.get_workflow_handle.assert_called_once_with("chat-1")
    desktop.vnc.assert_not_awaited()
    desktop.handle.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_transfer_retries_only_rollover_without_repeating_prior_steps(desktop):
    rollover = WorkflowUpdateFailedError(ApplicationError("rollover", type="SessionRollingOver"))
    desktop.handle.execute_update.side_effect = ["old-run", rollover, {"mode": "human"}]
    await server.handoff("chat-1", server.HandoffRequest(action="take"))
    assert desktop.handle.execute_update.await_args_list == [
        call(server.ChatWorkflow.claim_control, rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT),
        call(server.ChatWorkflow.desktop_control, "take", rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT),
        call(server.ChatWorkflow.desktop_control, "take", rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT),
    ]
    desktop.handle.cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["take", "release", "give"])
@pytest.mark.parametrize("owner", [None, {"namespace": "other", "workflow_id": "chat-1"},
                                   {"namespace": "test", "workflow_id": "other"}])
async def test_foreign_owner_is_rejected_before_dispatch(desktop, action, owner):
    set_state(desktop, owner=owner)
    with pytest.raises(HTTPException) as caught:
        await server.handoff("chat-1", server.HandoffRequest(action=action, message="Keep feedback"))
    assert caught.value.status_code == 409
    desktop.handle.execute_update.assert_not_awaited()
    desktop.handle.query.assert_not_awaited()
    desktop.vnc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,boundary", [
    ("take", "claim"), ("take", "take"), ("release", "release"),
    ("give", "prepare_resume"), ("give", "steering"), ("give", "query"), ("give", "resume"),
])
@pytest.mark.parametrize("failure,status", [("conflict", 409), ("missing", 404), ("rpc", 502)])
async def test_dispatch_errors_stop_at_failing_boundary(desktop, action, boundary, failure, status):
    if failure == "conflict":
        error = WorkflowUpdateFailedError(ApplicationError("Native control rejected", non_retryable=True))
    else:
        code = server.RPCStatusCode.NOT_FOUND if failure == "missing" else server.RPCStatusCode.UNAVAILABLE
        error = server.RPCError("worker unavailable", code, b"")
    calls = []

    async def dispatch(method, *args, **kwargs):
        phase = {
            server.ChatWorkflow.claim_control: "claim",
            server.ChatWorkflow.relinquish_control: "steering",
            server.ChatWorkflow.control_status: "query",
        }.get(method, args[0] if args else "")
        calls.append(phase)
        if phase == boundary:
            raise error
        return {"run_id": "new-run", "ready": True} if phase == "query" else "old-run"

    desktop.handle.execute_update.side_effect = dispatch
    desktop.handle.query.side_effect = dispatch
    with pytest.raises(HTTPException) as caught:
        await server.handoff("chat-1", server.HandoffRequest(action=action, message="Keep feedback"))
    assert caught.value.status_code == status
    assert calls[-1] == boundary
    desktop.handle.cancel.assert_not_awaited()
    desktop.vnc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,phase", [("take", "takeover"), ("release", "release"), ("give", "resume")])
@pytest.mark.parametrize("sdk_wraps_cancel", [False, True])
async def test_update_timeout_is_bounded_without_cancelling_workflow(desktop, monkeypatch, action, phase, sdk_wraps_cancel):
    monkeypatch.setattr(server, "DESKTOP_HANDOFF_TIMEOUT", timedelta(milliseconds=30))

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            if sdk_wraps_cancel:
                raise WorkflowUpdateRPCTimeoutOrCancelledError() from error
            raise

    desktop.handle.execute_update.side_effect = blocked
    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(server.handoff("chat-1", server.HandoffRequest(action=action, message="Keep feedback")), 1)
    assert caught.value.status_code == 504
    assert phase in caught.value.detail
    assert "unconfirmed" in caught.value.detail
    assert desktop.handle.execute_update.await_count == 1
    desktop.handle.cancel.assert_not_awaited()
    desktop.vnc.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_poll_timeout_never_dispatches_resume_or_repeats_steering(desktop, monkeypatch):
    monkeypatch.setattr(server, "DESKTOP_HANDOFF_TIMEOUT", timedelta(milliseconds=300))
    desktop.handle.query.return_value = {"run_id": "old-run", "ready": True}
    with pytest.raises(HTTPException) as caught:
        await server.handoff("chat-1", server.HandoffRequest(action="give", message="Keep feedback"))
    assert caught.value.status_code == 504
    assert desktop.handle.execute_update.await_args_list == [
        call(server.ChatWorkflow.desktop_control, "prepare_resume", rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT),
        call(server.ChatWorkflow.relinquish_control, "Keep feedback", rpc_timeout=server.DESKTOP_HANDOFF_TIMEOUT),
    ]
    desktop.handle.cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_wraps_cancel", [False, True])
async def test_disconnected_request_does_not_cancel_native_workflow(desktop, sdk_wraps_cancel):
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            if sdk_wraps_cancel:
                raise WorkflowUpdateRPCTimeoutOrCancelledError() from error
            raise

    desktop.handle.execute_update.side_effect = blocked
    task = asyncio.create_task(server.handoff("chat-1", server.HandoffRequest(action="take")))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    desktop.handle.cancel.assert_not_awaited()
    assert desktop.handle.execute_update.await_count == 1
    desktop.vnc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["agent", "stopping", "human", "relinquishing", "instructions", "resuming", "recovery"])
async def test_status_uses_native_mode_string_epoch_and_read_only_vnc_query(desktop, mode):
    state = set_state(desktop, mode=mode)
    before = desktop.runtime.read_bytes(), desktop.runtime.stat()
    desktop.handle.query.return_value = {"run_id": "run-1", "ready": False, "mode": "agent", "epoch": 0}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
        response = await client.get("/sessions/chat-1/desktop-control")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"run_id": "run-1", "ready": mode != "recovery", "mode": mode, "epoch": str(state["epoch"])}
    assert desktop.vnc.await_count == 1
    command, receipt = desktop.vnc.await_args_list[0].args
    assert "-R" not in shlex.split(command)
    assert shlex.split(command)[-2:] == ["-Q", "viewonly,deny"]
    assert receipt == (f"ans=viewonly:{0 if mode == 'human' else 1}", "ans=deny:0")
    assert desktop.runtime.read_bytes() == before[0]
    after = desktop.runtime.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before[1].st_ino, before[1].st_mtime_ns)
    assert list(desktop.root.iterdir()) == [desktop.runtime]
    desktop.handle.execute_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_serializes_nanosecond_epoch_losslessly_without_changing_native_int(desktop):
    epoch = 1_789_000_000_123_456_789
    assert epoch > 2**53 - 1
    set_state(desktop, epoch=epoch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
        response = await client.get("/sessions/chat-1/desktop-control")
    assert response.status_code == 200
    assert response.json()["epoch"] == "1789000000123456789"
    native_epoch = server._desktop_mode("chat-1")["epoch"]
    assert type(native_epoch) is int
    assert native_epoch == epoch


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rpc", "timeout"])
async def test_status_preserves_human_ownership_when_workflow_unavailable(desktop, monkeypatch, failure):
    state = set_state(desktop, mode="human")
    monkeypatch.setattr(server, "READINESS_POLLER_RPC_TIMEOUT", timedelta(milliseconds=30))

    async def query(*args, **kwargs):
        if failure == "rpc":
            raise server.RPCError("worker unavailable", server.RPCStatusCode.UNAVAILABLE, b"")
        await asyncio.Event().wait()

    desktop.handle.query.side_effect = query
    result = await asyncio.wait_for(server.desktop_control_status("chat-1", Response()), 1)
    assert result == {"run_id": None, "ready": True, "mode": "human", "epoch": str(state["epoch"])}
    desktop.handle.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_turn_keeps_take_control_available_without_queued_activity(desktop):
    desktop.handle.query.return_value = {"run_id": "busy-run", "ready": False, "human_control": False}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
        response = await client.get("/sessions/chat-1/desktop-control")
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["mode"] == "agent"
    assert desktop.handle.query.return_value["ready"] is False
    desktop.handle.query.assert_awaited_once_with(
        server.ChatWorkflow.control_status, rpc_timeout=server.READINESS_POLLER_RPC_TIMEOUT,
    )
    desktop.handle.execute_update.assert_not_awaited()
    desktop.handle.cancel.assert_not_awaited()
    assert "-R" not in shlex.split(desktop.vnc.await_args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [HTTPException(503, "No VNC receipt"), TimeoutError(), FileNotFoundError("x11vnc")])
async def test_status_native_probe_failure_is_unavailable_not_lost_ownership(desktop, error):
    state = set_state(desktop, mode="human")
    desktop.vnc.side_effect = error
    result = await server.desktop_control_status("chat-1", Response())
    assert result["ready"] is False
    assert result["mode"] == "recovery"
    assert result["epoch"] == str(state["epoch"])
    assert server._desktop_mode("chat-1")["mode"] == "human"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"epoch": 456}, {"mode": "human"}])
async def test_status_race_returns_current_native_state_unavailable(desktop, change):
    async def query(*args, **kwargs):
        set_state(desktop, **change)
        return {"run_id": "new-run", "ready": True}

    desktop.handle.query.side_effect = query
    result = await server.desktop_control_status("chat-1", Response())
    current = server._desktop_mode("chat-1")
    assert result["ready"] is False
    assert result["mode"] == current["mode"]
    assert result["epoch"] == str(current["epoch"])


@pytest.mark.asyncio
async def test_status_rejects_owner_changed_during_query(desktop):
    async def query(*args, **kwargs):
        set_state(desktop, owner={"namespace": "other", "workflow_id": "chat-1"})
        return {"run_id": "new-run", "ready": True}

    desktop.handle.query.side_effect = query
    with pytest.raises(HTTPException) as caught:
        await server.desktop_control_status("chat-1", Response())
    assert caught.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["agent", "human", "resuming"])
@pytest.mark.parametrize("failure", ["no_pollers", "rpc", "vnc"])
async def test_missing_worker_overrides_stale_ready_without_metadata_writes(desktop, mode, failure):
    state = set_state(desktop, mode=mode)
    before = desktop.runtime.read_bytes(), desktop.runtime.stat()
    if failure == "no_pollers":
        desktop.client.workflow_service.describe_task_queue.return_value = SimpleNamespace(pollers=[])
    elif failure == "rpc":
        desktop.client.workflow_service.describe_task_queue.side_effect = server.RPCError(
            "unavailable", server.RPCStatusCode.UNAVAILABLE, b"",
        )
    else:
        desktop.vnc.side_effect = HTTPException(503, "Native VNC unavailable")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
        response = await client.get("/sessions/chat-1/desktop-control")
    assert response.status_code == 200
    assert response.json() == {"run_id": "new-run", "ready": False, "mode": "recovery", "epoch": str(state["epoch"])}
    assert desktop.vnc.await_count == 1
    after = desktop.runtime.stat()
    assert desktop.runtime.read_bytes() == before[0]
    assert (after.st_ino, after.st_mtime_ns) == (before[1].st_ino, before[1].st_mtime_ns)
    assert list(desktop.root.iterdir()) == [desktop.runtime]
    desktop.handle.execute_update.assert_not_awaited()
    desktop.handle.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_desktop_poller_cache_is_queue_specific_but_vnc_is_always_fresh(desktop, monkeypatch):
    from temporalio.api.enums.v1 import TaskQueueType

    monkeypatch.setattr(server, "_poller_cache", {"checked_at": server.time.monotonic(), "ok": False})
    for _ in range(2):
        assert (await server.desktop_control_status("chat-1", Response()))["ready"] is True
    probe = desktop.client.workflow_service.describe_task_queue
    probe.assert_awaited_once()
    request = probe.await_args.args[0]
    assert request.namespace == "test"
    assert request.task_queue.name == server.DESKTOP_BROWSER_TASK_QUEUE
    assert request.task_queue_type == TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY
    assert desktop.vnc.await_count == 2
    desktop.vnc.side_effect = HTTPException(503, "VNC disappeared while pollers are cached")
    assert (await server.desktop_control_status("chat-1", Response()))["ready"] is False
    assert probe.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [b"ans=viewonly:1,ans=deny:0", b"ans=viewonly:1,ans=deny:1", b"ans=viewonly:0,ans=deny:0", b"ans=viewonly:1", b""])
async def test_readonly_vnc_probe_verifies_both_native_receipts(monkeypatch, answer):
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(answer, b"")))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", spawn)
    command = "x11vnc -Q viewonly,deny"
    if answer == b"ans=viewonly:1,ans=deny:0":
        await server._vnc_input(command, ("ans=viewonly:1", "ans=deny:0"))
    else:
        with pytest.raises(HTTPException):
            await server._vnc_input(command, ("ans=viewonly:1", "ans=deny:0"))
    assert spawn.await_args.args == ("x11vnc", "-Q", "viewonly,deny")


@pytest.mark.asyncio
async def test_readonly_vnc_probe_timeout_kills_and_reaps_process(monkeypatch):
    async def blocked():
        await asyncio.Event().wait()

    process = SimpleNamespace(communicate=blocked, kill=MagicMock(), wait=AsyncMock())
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(server, "DESKTOP_VNC_COMMAND_TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        await server._vnc_input("x11vnc -Q viewonly,deny", ("ans=viewonly:1", "ans=deny:0"))
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
