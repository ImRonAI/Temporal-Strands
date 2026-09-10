"""Native desktop transfer safety with real temp locks and mocked X11/context."""

import asyncio
import fcntl
import json
import shlex
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError

import browser_activity as browser


REVOKE = "script:viewonly;deny;disconnect:all;clear_all;fakebuttonevent:1,0;fakebuttonevent:2,0;fakebuttonevent:3,0"
REVOKED = "ans=viewonly:1,ans=deny:1,aro=client_count:0,aro=pointer_mask:0x0"
VIEW = "x11vnc -display :99 -R script:viewonly;nodeny -Q viewonly,deny"


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser, "DESKTOP_VNC_VIEW_COMMAND", VIEW)
    monkeypatch.setattr(browser.platform, "system", lambda: "Linux")
    info = SimpleNamespace(namespace="test", workflow_id="owner", workflow_run_id="run-1")
    monkeypatch.setattr(browser.activity, "info", lambda: info)
    monkeypatch.setattr(browser.LocalChromiumBrowser, "browser", MagicMock(side_effect=AssertionError("No live browser")))
    monkeypatch.setattr(browser.ImageGrab, "grab", MagicMock(side_effect=AssertionError("No display capture")))
    owner = {"namespace": "test", "workflow_id": "owner"}
    browser.initialize_desktop()
    with browser.desktop_state() as state:
        state.update(owner=owner, epoch=123, mode="agent", session_name="shared-session")

    def native(command, **kwargs):
        assert command[:4] == ["x11vnc", "-display", ":99", "-R"]
        assert kwargs["capture_output"] is True
        assert not kwargs.get("shell")
        assert 0 < kwargs["timeout"] <= browser.DESKTOP_VNC_COMMAND_TIMEOUT
        # Real flock probes verify both locks are held at every native command,
        # including revocation following an ambiguous grant.
        for name in ("handoff.lock", "action.lock"):
            with (tmp_path / name).open("a") as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        answer = {
            REVOKE: REVOKED,
            "script:nodeny;noviewonly": "ans=viewonly:0,ans=deny:0",
            "script:viewonly;nodeny": "ans=viewonly:1,ans=deny:0",
        }[command[4]]
        return SimpleNamespace(returncode=0, stdout=answer)

    run = MagicMock(side_effect=native)
    monkeypatch.setattr(browser.subprocess, "run", run)
    yield SimpleNamespace(root=tmp_path, info=info, owner=owner, run=run, native=native)
    for name in ("handoff.lock", "action.lock"):
        with (tmp_path / name).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def read_state():
    return json.loads((browser.artifact_root() / "runtime.json").read_text())


def assert_recovery(desktop):
    state = read_state()
    assert state["owner"] == desktop.owner
    assert state["mode"] == "recovery"
    assert state["session_name"] == "shared-session"
    with pytest.raises(ApplicationError):
        with browser.desktop_action():
            pytest.fail("Uncertain native control admitted an agent action")


def test_control_is_native_typed_cancellation_safe_activity():
    definition = activity._Definition.must_from_callable(browser.desktop_control)
    assert definition.name == "desktop_control"
    assert definition.arg_types == [str]
    assert not definition.is_async
    assert definition.no_thread_cancel_exception


def test_full_control_order_preserves_binding_and_invalidates_human_era_images(desktop, monkeypatch):
    monkeypatch.setattr(browser.time, "time_ns", lambda: 456)
    calls = []

    def native(command, **kwargs):
        calls.append((read_state()["mode"], command[4], command[6]))
        return desktop.native(command, **kwargs)

    desktop.run.side_effect = native
    for action, mode in (("take", "human"), ("release", "instructions"),
                         ("prepare_resume", "resuming"), ("resume", "agent"), ("status", "agent")):
        result = browser.desktop_control(action)
        assert result == read_state()
        assert result["mode"] == mode
        assert result["owner"] == desktop.owner
        assert result["session_name"] == "shared-session"
        assert result["epoch"] == (123 if action == "take" else 456)
        if mode != "agent":
            with pytest.raises(ApplicationError):
                with browser.desktop_action():
                    pytest.fail("Agent input admitted during human transfer")
    assert calls == [
        ("stopping", REVOKE, "viewonly,deny,client_count,pointer_mask"),
        ("stopping", "script:nodeny;noviewonly", "viewonly,deny"),
        ("relinquishing", REVOKE, "viewonly,deny,client_count,pointer_mask"),
        ("relinquishing", "script:viewonly;nodeny", "viewonly,deny"),
        ("resuming", REVOKE, "viewonly,deny,client_count,pointer_mask"),
        ("resuming", "script:viewonly;nodeny", "viewonly,deny"),
        ("resuming", REVOKE, "viewonly,deny,client_count,pointer_mask"),
        ("resuming", "script:viewonly;nodeny", "viewonly,deny"),
    ]
    with browser.desktop_action() as epoch:
        assert epoch == 456


@pytest.mark.parametrize("action,mode", [("take", "human"), ("release", "instructions"), ("resume", "agent"),
                                       ("status", "human"), ("status", "recovery")])
def test_completed_control_and_status_are_idempotent(desktop, action, mode):
    with browser.desktop_state() as state:
        state["mode"] = mode
    before = read_state()
    for _ in range(2):
        assert browser.desktop_control(action) == before
    assert read_state() == before
    desktop.run.assert_not_called()


def test_prepare_resume_retry_reverifies_readonly_input_without_changing_epoch(desktop):
    with browser.desktop_state() as state:
        state["mode"] = "instructions"
    first = browser.desktop_control("prepare_resume")
    assert browser.desktop_control("prepare_resume") == first
    assert first["epoch"] == 123
    assert desktop.run.call_count == 4
    assert all(call.args[0][4] != "script:nodeny;noviewonly" for call in desktop.run.call_args_list)


@pytest.mark.parametrize("action", ["take", "release", "prepare_resume", "resume", "status"])
@pytest.mark.parametrize("owner", [None, {"namespace": "other", "workflow_id": "owner"},
                                   {"namespace": "test", "workflow_id": "other"}])
def test_foreign_owner_is_rejected_without_native_side_effects(desktop, action, owner):
    with browser.desktop_state() as state:
        state["owner"] = owner
    before = read_state()
    with pytest.raises(ApplicationError, match="does not own") as caught:
        browser.desktop_control(action)
    assert caught.value.non_retryable
    assert read_state() == before
    desktop.run.assert_not_called()


@pytest.mark.parametrize("action,mode", [("take", "recovery"), ("take", "resuming"),
                                       ("release", "agent"), ("prepare_resume", "human"),
                                       ("resume", "human"), ("resume", "instructions")])
def test_out_of_order_control_is_rejected_without_changing_state(desktop, action, mode):
    with browser.desktop_state() as state:
        state["mode"] = mode
    before = read_state()
    with pytest.raises(ApplicationError, match="cannot") as caught:
        browser.desktop_control(action)
    assert caught.value.non_retryable
    assert read_state() == before
    desktop.run.assert_not_called()


@pytest.mark.parametrize("system,display", [("Darwin", ":99"), ("Windows", ":99"), ("Linux", "")])
def test_control_platform_fence_precedes_context_and_native_calls(desktop, monkeypatch, system, display):
    monkeypatch.setattr(browser.platform, "system", lambda: system)
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setattr(browser.activity, "info", MagicMock(side_effect=AssertionError("Platform fence first")))
    before = read_state()
    with pytest.raises(ApplicationError, match="cannot execute on the host"):
        browser.desktop_control("take")
    desktop.run.assert_not_called()
    assert read_state() == before


def test_unknown_action_has_no_side_effects(desktop):
    before = read_state()
    with pytest.raises(ApplicationError, match="Unknown"):
        browser.desktop_control("grant")
    desktop.run.assert_not_called()
    assert read_state() == before


@pytest.mark.parametrize("lock_name", ["handoff.lock", "action.lock"])
@pytest.mark.parametrize("timeout", [False, True])
def test_transfer_waits_for_native_locks_before_commands(desktop, monkeypatch, lock_name, timeout):
    with (desktop.root / lock_name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        waits = []

        def wait(seconds):
            desktop.run.assert_not_called()
            waits.append(seconds)
            if timeout:
                monkeypatch.setattr(browser.time, "monotonic", lambda: float("inf"))
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)

        monkeypatch.setattr(browser.time, "sleep", wait)
        if timeout:
            with pytest.raises(ApplicationError, match="did not settle") as caught:
                browser.desktop_control("take")
            assert caught.value.non_retryable
            desktop.run.assert_not_called()
            assert read_state()["mode"] != "human"
        else:
            assert browser.desktop_control("take")["mode"] == "human"
        assert waits


@pytest.mark.parametrize("action,mode", [("take", "agent"), ("release", "human"),
                                       ("prepare_resume", "instructions"), ("resume", "resuming")])
@pytest.mark.parametrize("answer,returncode", [
    ("", 0), (REVOKED, 1), (REVOKED.replace("viewonly:1", "viewonly:0"), 0),
    (REVOKED.replace("deny:1", "deny:0"), 0), (REVOKED.replace("client_count:0", "client_count:1"), 0),
    (REVOKED.replace("pointer_mask:0x0", "pointer_mask:0x1"), 0), ("ans=viewonly:1", 0),
])
def test_cleanup_receipts_are_required_before_any_connection_is_enabled(desktop, action, mode, answer, returncode):
    with browser.desktop_state() as state:
        state["mode"] = mode

    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        return SimpleNamespace(returncode=returncode, stdout=answer) if desktop.run.call_count == 1 else result

    desktop.run.side_effect = native
    with pytest.raises(ApplicationError) as caught:
        browser.desktop_control(action)
    assert caught.value.non_retryable
    assert [call.args[0][4] for call in desktop.run.call_args_list] == [REVOKE, REVOKE]
    assert_recovery(desktop)


@pytest.mark.parametrize("failure", ["receipt", "exit", "timeout", "cancel", "rollback"])
def test_failed_grant_attempts_revoke_under_both_locks_and_fences_agent(desktop, failure):
    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        if desktop.run.call_count == 2:
            if failure == "timeout":
                raise subprocess.TimeoutExpired("x11vnc", 1)
            if failure == "cancel":
                raise asyncio.CancelledError()
            return SimpleNamespace(returncode=1 if failure == "exit" else 0, stdout="ans=viewonly:1,ans=deny:0")
        if desktop.run.call_count == 3 and failure == "rollback":
            raise subprocess.CalledProcessError(1, "x11vnc")
        return result

    desktop.run.side_effect = native
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ApplicationError) as caught:
        browser.desktop_control("take")
    assert [call.args[0][4] for call in desktop.run.call_args_list] == [REVOKE, "script:nodeny;noviewonly", REVOKE]
    if failure == "rollback":
        assert any("revocation also failed" in note for note in caught.value.__cause__.__notes__)
    assert_recovery(desktop)


@pytest.mark.parametrize("action,mode", [("release", "human"), ("prepare_resume", "instructions"), ("resume", "resuming")])
@pytest.mark.parametrize("boundary", [1, 2])
def test_release_or_resume_receipt_failure_never_enables_agent(desktop, action, mode, boundary):
    with browser.desktop_state() as state:
        state["mode"] = mode

    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        return SimpleNamespace(returncode=0, stdout="") if desktop.run.call_count == boundary else result

    desktop.run.side_effect = native
    with pytest.raises(ApplicationError):
        browser.desktop_control(action)
    assert desktop.run.call_count == boundary + 1
    assert desktop.run.call_args_list[-1].args[0][4] == REVOKE
    assert_recovery(desktop)
    assert read_state()["epoch"] == 123


@pytest.mark.parametrize("change", [{"owner": {"namespace": "test", "workflow_id": "other"}}, {"epoch": 456}])
def test_take_never_overwrites_changed_binding_after_grant(desktop, change):
    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        if desktop.run.call_count == 2:
            with browser.desktop_state() as state:
                state.update(change, mode="recovery")
        return result

    desktop.run.side_effect = native
    with pytest.raises(ApplicationError, match="ownership changed"):
        browser.desktop_control("take")
    assert desktop.run.call_args_list[-1].args[0][4] == REVOKE
    state = read_state()
    assert state["mode"] == "recovery"
    assert all(state[key] == value for key, value in change.items())


@pytest.mark.parametrize("change", [{"mode": "recovery"}, {"epoch": 456}])
def test_take_rechecks_binding_before_grant_after_native_revoke(desktop, change):
    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        if desktop.run.call_count == 1:
            with browser.desktop_state() as state:
                state.update(change)
        return result

    desktop.run.side_effect = native
    with pytest.raises(ApplicationError):
        browser.desktop_control("take")
    assert "script:nodeny;noviewonly" not in [call.args[0][4] for call in desktop.run.call_args_list]
    assert read_state()["mode"] == "recovery"


def test_late_grant_success_after_deadline_is_revoked_not_acknowledged(desktop, monkeypatch):
    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        if desktop.run.call_count == 2:
            monkeypatch.setattr(browser.time, "monotonic", lambda: float("inf"))
        return result

    desktop.run.side_effect = native
    with pytest.raises(ApplicationError):
        browser.desktop_control("take")
    assert desktop.run.call_args_list[-1].args[0][4] == REVOKE
    assert_recovery(desktop)


@pytest.mark.parametrize("action,mode", [("release", "human"), ("prepare_resume", "instructions"), ("resume", "resuming")])
def test_viewer_command_is_configured_and_verified_before_ack(desktop, monkeypatch, action, mode):
    with browser.desktop_state() as state:
        state["mode"] = mode
    command = VIEW.replace("x11vnc", "/usr/bin/x11vnc", 1)
    monkeypatch.setattr(browser, "DESKTOP_VNC_VIEW_COMMAND", command)

    def native(args, **kwargs):
        if args[0] == "/usr/bin/x11vnc":
            assert desktop.run.call_count == 2
            assert read_state()["mode"] in {"relinquishing", "resuming"}
            args = ["x11vnc", *args[1:]]
        return desktop.native(args, **kwargs)

    desktop.run.side_effect = native
    result = browser.desktop_control(action)
    assert desktop.run.call_args_list[1].args[0] == shlex.split(command)
    assert result["mode"] == {"release": "instructions", "prepare_resume": "resuming", "resume": "agent"}[action]
    assert all("noviewonly" not in call.args[0][4] for call in desktop.run.call_args_list)


def test_default_viewer_configuration_never_grants_input():
    import config

    # The worker inherits DISPLAY; deployment overrides stay inside the desktop.
    assert shlex.split(config.DESKTOP_VNC_VIEW_COMMAND) == [
        "x11vnc", "-R", "script:viewonly;nodeny", "-Q", "viewonly,deny",
    ]


@pytest.mark.parametrize("action,mode", [("release", "human"), ("prepare_resume", "instructions"), ("resume", "resuming")])
@pytest.mark.parametrize("failure", ["receipt", "exit", "timeout", "cancel", "deadline", "owner", "epoch", "mode"])
def test_failed_viewer_enable_rolls_back_even_after_losing_ownership(desktop, monkeypatch, action, mode, failure):
    with browser.desktop_state() as state:
        state["mode"] = mode
    foreign_owner = {"namespace": "test", "workflow_id": "other"}

    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        if desktop.run.call_count != 2:
            return result
        if failure == "timeout":
            raise subprocess.TimeoutExpired("x11vnc", 1)
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure in {"receipt", "exit"}:
            return SimpleNamespace(returncode=1 if failure == "exit" else 0, stdout="ans=viewonly:1")
        if failure == "deadline":
            monkeypatch.setattr(browser.time, "monotonic", lambda: float("inf"))
        else:
            with browser.desktop_state() as state:
                state.update({"owner": foreign_owner} if failure == "owner" else
                             {"epoch": 456} if failure == "epoch" else {"mode": "recovery"})
        return result

    desktop.run.side_effect = native
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ApplicationError):
        browser.desktop_control(action)
    assert [call.args[0][4] for call in desktop.run.call_args_list] == [REVOKE, "script:viewonly;nodeny", REVOKE]
    assert desktop.run.call_args_list[-1].args[0][6] == "viewonly,deny,client_count,pointer_mask"
    if failure == "owner":
        assert read_state()["owner"] == foreign_owner
        assert read_state()["mode"] in {"relinquishing", "resuming"}
    else:
        assert_recovery(desktop)
        assert read_state()["epoch"] == (456 if failure == "epoch" else 123)


@pytest.mark.parametrize("action,mode", [("release", "human"), ("prepare_resume", "instructions"), ("resume", "resuming")])
@pytest.mark.parametrize("change", [{"mode": "recovery"}, {"epoch": 456},
                                    {"owner": {"namespace": "test", "workflow_id": "other"}}])
def test_viewer_is_not_enabled_after_binding_changes_during_cleanup(desktop, action, mode, change):
    with browser.desktop_state() as state:
        state["mode"] = mode

    def native(command, **kwargs):
        result = desktop.native(command, **kwargs)
        if desktop.run.call_count == 1:
            with browser.desktop_state() as state:
                state.update(change)
        return result

    desktop.run.side_effect = native
    with pytest.raises(ApplicationError):
        browser.desktop_control(action)
    assert [call.args[0][4] for call in desktop.run.call_args_list] == [REVOKE, REVOKE]
    assert all(read_state()[key] == value for key, value in change.items())


def test_invalid_viewer_config_cannot_skip_fail_closed_cleanup(desktop, monkeypatch):
    with browser.desktop_state() as state:
        state["mode"] = "human"
    monkeypatch.setattr(browser, "DESKTOP_VNC_VIEW_COMMAND", 'x11vnc "')
    with pytest.raises(ApplicationError):
        browser.desktop_control("release")
    assert [call.args[0][4] for call in desktop.run.call_args_list] == [REVOKE, REVOKE]
    assert_recovery(desktop)


def test_native_status_never_creates_locks_or_rewrites_metadata(desktop):
    runtime = desktop.root / "runtime.json"
    before = runtime.read_bytes(), runtime.stat()
    files = set(desktop.root.iterdir())
    assert browser.desktop_control("status") == read_state()
    assert set(desktop.root.iterdir()) == files
    after = runtime.stat()
    assert runtime.read_bytes() == before[0]
    assert (after.st_ino, after.st_mtime_ns) == (before[1].st_ino, before[1].st_mtime_ns)
    desktop.run.assert_not_called()
