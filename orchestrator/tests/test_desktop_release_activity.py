"""Desktop lifecycle cleanup with mocked VNC, Playwright and activity context only."""

import asyncio
import fcntl
import json
import subprocess
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError

import browser_activity as browser


@pytest.fixture(autouse=True)
def forbid_live_desktop(monkeypatch):
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=AssertionError("No live commands")))
    monkeypatch.setattr(browser.LocalChromiumBrowser, "browser", MagicMock(
        side_effect=AssertionError("No live browser"),
    ))
    monkeypatch.setattr(browser.ImageGrab, "grab", MagicMock(side_effect=AssertionError("No display capture")))


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser.platform, "system", lambda: "Linux")
    info = SimpleNamespace(namespace="test", workflow_id="owner", workflow_run_id="run-2")
    monkeypatch.setattr(browser.activity, "info", lambda: info)
    owner = {"namespace": info.namespace, "workflow_id": info.workflow_id}
    browser.initialize_desktop()
    with browser.desktop_state() as state:
        state.update(owner=owner, mode="human", session_name="shared-browser-session", epoch=123)
    sessions = {}
    for name in ("shared-browser-session", "second-owned-session"):
        native = MagicMock()
        native.is_connected.return_value = True
        native.close = AsyncMock(side_effect=lambda native=native: setattr(native.is_connected, "return_value", False))
        sessions[name] = SimpleNamespace(browser=native, context=None)
    playwright = SimpleNamespace(stop=AsyncMock())
    stock = SimpleNamespace(_sessions=dict(sessions), _playwright=playwright,
                            _started=True, _execute_async=asyncio.run)
    monkeypatch.setattr(browser, "_browser", stock)
    answers = [
        "ans=viewonly:1,aro=client_count:0,aro=pointer_mask:0x0\n",
        "ans=viewonly:1,ans=deny:0,aro=pointer_mask:0x0\n",
    ]
    run = MagicMock(side_effect=[SimpleNamespace(returncode=0, stdout=value) for value in answers])
    monkeypatch.setattr(subprocess, "run", run)
    return SimpleNamespace(root=tmp_path, info=info, owner=owner, stock=stock,
                           sessions=sessions, playwright=playwright, run=run)


def read_state():
    return json.loads((browser.artifact_root() / "runtime.json").read_text())


def assert_recovery(desktop):
    state = read_state()
    assert state["owner"] == desktop.owner
    assert state["mode"] == "recovery"
    assert state["session_name"] == "shared-browser-session"
    with pytest.raises(ApplicationError):
        with browser.desktop_action():
            pytest.fail("Uncertain cleanup admitted input")
    for name in ("handoff.lock", "action.lock"):
        with (desktop.root / name).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_release_is_native_no_argument_cancellation_safe_activity():
    definition = activity._Definition.must_from_callable(browser.release_desktop)
    assert definition.name == "release_desktop"
    assert definition.arg_types == []
    assert not definition.is_async
    assert definition.no_thread_cancel_exception


@pytest.mark.parametrize("mode", ["agent", "stopping", "human", "relinquishing", "instructions", "resuming", "recovery"])
def test_release_cleans_all_owned_sessions_in_any_mode_and_preserves_artifacts(desktop, mode):
    with browser.desktop_state() as state:
        state["mode"] = mode
        state["unrelated_metadata"] = "preserved"
    artifact = desktop.root / "owner-artifacts"
    artifact.mkdir()
    image = artifact / "capture.png"
    image.write_bytes(b"immutable evidence")

    assert browser.release_desktop() is None

    state = read_state()
    assert state["owner"] is None
    assert state["mode"] == "agent"
    assert state["epoch"] != 123
    assert "session_name" not in state
    assert state["unrelated_metadata"] == "preserved"
    assert image.read_bytes() == b"immutable evidence"
    for session in desktop.sessions.values():
        session.browser.close.assert_awaited_once_with()
        assert not session.browser.is_connected()
    desktop.playwright.stop.assert_awaited_once_with()
    assert desktop.stock._sessions == {}
    assert desktop.stock._playwright is None
    assert desktop.stock._started is False

    first, second = desktop.run.call_args_list
    assert first.args[0][:4] == ["x11vnc", "-display", ":99", "-R"]
    assert first.args[0][4].split(";") == [
        "script:viewonly", "deny", "disconnect:all", "clear_all",
        "fakebuttonevent:1,0", "fakebuttonevent:2,0", "fakebuttonevent:3,0",
    ]
    assert first.args[0][5:] == ["-Q", "viewonly,client_count,pointer_mask"]
    assert second.args[0] == ["x11vnc", "-display", ":99", "-R", "script:viewonly;nodeny",
                              "-Q", "viewonly,deny,pointer_mask"]
    for call in (first, second):
        assert call.kwargs["capture_output"] is True
        assert call.kwargs["text"] is True
        assert 0 < call.kwargs["timeout"] <= browser.DESKTOP_VNC_COMMAND_TIMEOUT
        assert not call.kwargs.get("shell")

    # An end signal replay/duplicate never touches the next owner's desktop.
    browser.release_desktop()
    assert desktop.run.call_count == 2
    desktop.info.workflow_id = "next-chat"
    with browser.desktop_action():
        assert read_state()["owner"]["workflow_id"] == "next-chat"
    desktop.info.workflow_id = "owner"
    browser.release_desktop()
    assert read_state()["owner"]["workflow_id"] == "next-chat"
    assert desktop.run.call_count == 2


@pytest.mark.parametrize("owner", [None, {"namespace": "other", "workflow_id": "owner"},
                                   {"namespace": "test", "workflow_id": "other"}])
def test_release_never_touches_an_unowned_or_foreign_desktop(desktop, owner):
    with browser.desktop_state() as state:
        state["owner"] = owner
    before = read_state()
    browser.release_desktop()
    assert read_state() == before
    desktop.run.assert_not_called()
    desktop.playwright.stop.assert_not_called()
    assert desktop.stock._sessions == desktop.sessions


@pytest.mark.parametrize("system,display", [("Darwin", ":99"), ("Windows", ":99"), ("Linux", "")])
def test_release_platform_fence_precedes_side_effects(desktop, monkeypatch, system, display):
    monkeypatch.setattr(browser.platform, "system", lambda: system)
    monkeypatch.setenv("DISPLAY", display)
    before = read_state()
    with pytest.raises(ApplicationError, match="cannot execute on the host"):
        browser.release_desktop()
    assert read_state() == before
    desktop.run.assert_not_called()


@pytest.mark.parametrize("answer", ["", "ans=viewonly:0,aro=client_count:0,aro=pointer_mask:0x0",
                                   "ans=viewonly:1,aro=client_count:1,aro=pointer_mask:0x0",
                                   "ans=viewonly:1,aro=client_count:0,aro=pointer_mask:0x1",
                                   "ans=viewonly:1,aro=client_count:0"])
def test_release_requires_all_native_revocation_receipts(desktop, answer):
    desktop.run.side_effect = [SimpleNamespace(returncode=0, stdout=answer)]
    with pytest.raises(ApplicationError) as caught:
        browser.release_desktop()
    assert caught.value.non_retryable
    assert_recovery(desktop)
    desktop.playwright.stop.assert_not_called()
    assert desktop.stock._sessions == desktop.sessions


@pytest.mark.parametrize("failure", [FileNotFoundError("x11vnc"), subprocess.TimeoutExpired("x11vnc", 1),
                                    SimpleNamespace(returncode=1, stdout="ans=viewonly:1,aro=client_count:0,aro=pointer_mask:0x0")])
def test_native_process_failure_retains_owner_and_requires_recovery(desktop, failure):
    desktop.run.side_effect = [failure]
    with pytest.raises(ApplicationError):
        browser.release_desktop()
    assert_recovery(desktop)


@pytest.mark.parametrize("failure", ["close", "still_connected", "playwright", "timeout"])
def test_browser_cleanup_failure_is_not_hidden_or_released(desktop, monkeypatch, failure):
    first = next(iter(desktop.sessions.values())).browser
    if failure == "close":
        first.close.side_effect = RuntimeError("native close failed")
    elif failure == "still_connected":
        first.close.side_effect = None
    elif failure == "playwright":
        desktop.playwright.stop.side_effect = RuntimeError("driver stop failed")
    else:
        async def hang():
            await asyncio.Event().wait()
        first.close.side_effect = hang
        monkeypatch.setattr(browser, "DESKTOP_HANDOFF_TIMEOUT", timedelta(milliseconds=200))
    with pytest.raises(ApplicationError):
        browser.release_desktop()
    assert_recovery(desktop)
    assert desktop.run.call_count == 1  # Do not admit even view-only clients on failure.
    assert desktop.stock._sessions == desktop.sessions
    if failure in {"close", "still_connected"}:
        desktop.sessions["second-owned-session"].browser.close.assert_awaited_once_with()


@pytest.mark.parametrize("lock_name", ["action.lock", "handoff.lock"])
@pytest.mark.parametrize("timeout", [False, True])
def test_release_waits_for_native_locks_with_a_finite_budget(desktop, monkeypatch, lock_name, timeout):
    with (desktop.root / lock_name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        waited = []

        def wait(seconds):
            desktop.run.assert_not_called()
            waited.append(seconds)
            if timeout:
                monkeypatch.setattr(browser.time, "monotonic", lambda: float("inf"))
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)

        monkeypatch.setattr(browser.time, "sleep", wait)
        if timeout:
            with pytest.raises(ApplicationError):
                browser.release_desktop()
        else:
            browser.release_desktop()
        assert waited
    if timeout:
        assert_recovery(desktop)
    else:
        assert read_state()["owner"] is None


def test_release_holds_both_locks_until_cleanup_and_viewer_reset_finish(desktop):
    responses = iter(desktop.run.side_effect)

    def revoke(*args, **kwargs):
        assert read_state()["owner"] == desktop.owner
        assert read_state()["mode"] == "recovery"
        for name in ("action.lock", "handoff.lock"):
            with (desktop.root / name).open("a") as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return next(responses)

    desktop.run.side_effect = revoke
    browser.release_desktop()


def test_cleanup_does_not_initialize_a_browser_for_computer_only_owner(desktop):
    desktop.stock._sessions.clear()
    desktop.stock._playwright = None
    desktop.stock._started = False
    browser.release_desktop()
    browser.LocalChromiumBrowser.browser.assert_not_called()
    assert read_state()["owner"] is None


@pytest.mark.parametrize("field,value", [("namespace", ""), ("workflow_id", None)])
def test_release_requires_trusted_workflow_context(desktop, field, value):
    setattr(desktop.info, field, value)
    with pytest.raises(ApplicationError, match="requires a workflow owner"):
        browser.release_desktop()
    desktop.run.assert_not_called()
    assert read_state()["owner"] == desktop.owner


@pytest.mark.parametrize("field,value", [("owner", {"namespace": "test", "workflow_id": "other"}),
                                       ("epoch", 456)])
@pytest.mark.parametrize("phase", ["lock_wait", "cleanup"])
def test_release_rechecks_ownership_and_epoch(desktop, monkeypatch, field, value, phase):
    def change_state():
        with browser.desktop_state() as state:
            state.update(mode="human")
            state[field] = value

    if phase == "lock_wait":
        with (desktop.root / "handoff.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def wait(seconds):
                change_state()
                fcntl.flock(lock, fcntl.LOCK_UN)

            monkeypatch.setattr(browser.time, "sleep", wait)
            if field == "owner":
                browser.release_desktop()
            else:
                with pytest.raises(ApplicationError):
                    browser.release_desktop()
        desktop.run.assert_not_called()
    else:
        responses = iter(desktop.run.side_effect)

        def revoke(*args, **kwargs):
            if desktop.run.call_count == 2:
                change_state()
            return next(responses)

        desktop.run.side_effect = revoke
        with pytest.raises(ApplicationError):
            browser.release_desktop()
    state = read_state()
    assert state[field] == value
    assert state["mode"] == ("human" if field == "owner" else "recovery")


@pytest.mark.parametrize("answer", ["", "ans=viewonly:0,ans=deny:0,aro=pointer_mask:0x0",
                                   "ans=viewonly:1,ans=deny:1,aro=pointer_mask:0x0",
                                   "ans=viewonly:1,ans=deny:0,aro=pointer_mask:0x1"])
def test_viewer_reset_must_be_verified_before_releasing_owner(desktop, answer):
    desktop.run.side_effect = [next(desktop.run.side_effect), SimpleNamespace(returncode=0, stdout=answer)]
    with pytest.raises(ApplicationError):
        browser.release_desktop()
    assert_recovery(desktop)
    assert desktop.stock._sessions == {}


def test_late_success_cannot_clear_owner_after_cleanup_deadline(desktop, monkeypatch):
    responses = iter(desktop.run.side_effect)

    def revoke(*args, **kwargs):
        if desktop.run.call_count == 2:
            monkeypatch.setattr(browser.time, "monotonic", lambda: float("inf"))
        return next(responses)

    desktop.run.side_effect = revoke
    with pytest.raises(ApplicationError):
        browser.release_desktop()
    assert_recovery(desktop)


def test_missing_native_session_handle_is_not_cleanup_evidence(desktop):
    desktop.stock._sessions["shared-browser-session"].browser = None
    with pytest.raises(ApplicationError):
        browser.release_desktop()
    assert_recovery(desktop)
    desktop.playwright.stop.assert_not_called()


def test_cancelled_native_cleanup_retains_owner_and_releases_locks(desktop):
    desktop.playwright.stop.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        browser.release_desktop()
    assert_recovery(desktop)


def test_release_uses_stock_event_loop_without_starting_new_platform(desktop, monkeypatch):
    stock = browser.LocalChromiumBrowser(launch_options={"headless": False, "chromium_sandbox": True})
    stock._sessions = desktop.stock._sessions
    stock._playwright = desktop.stock._playwright
    stock._started = True
    monkeypatch.setattr(browser, "_browser", stock)
    try:
        browser.release_desktop()
        assert stock._sessions == {}
        assert stock._playwright is None
        assert stock._started is False
    finally:
        stock._started = False
        stock._loop.close()
