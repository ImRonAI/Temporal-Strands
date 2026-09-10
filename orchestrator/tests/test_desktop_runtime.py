"""Desktop startup contracts; these tests never launch a host browser."""

import asyncio
import fcntl
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio import activity, workflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.exceptions import ApplicationError

import browser_activity as browser


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def forbid_host_desktop(monkeypatch):
    monkeypatch.setattr(browser.LocalChromiumBrowser, "browser", MagicMock(
        side_effect=AssertionError("Tests must not execute a browser on the host"),
    ))
    monkeypatch.setattr(browser.ImageGrab, "grab", MagicMock(
        side_effect=AssertionError("Tests must not capture the host display"),
    ))


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser.platform, "system", lambda: "Linux")
    info = SimpleNamespace(namespace="test", workflow_id="owner", workflow_run_id="run-1", activity_id="action-1")
    monkeypatch.setattr(browser.activity, "info", lambda: info)
    browser.initialize_desktop()
    return info


def test_image_has_an_allowlisted_source_context_and_desktop_dependencies():
    dockerfile = (ROOT / "desktop/Dockerfile").read_text()
    ignore = (ROOT / ".dockerignore").read_text()
    assert ignore.splitlines()[0] == "**"
    assert "COPY . ." not in dockerfile
    assert "COPY desktop/requirements.txt" in dockerfile
    requirements = (ROOT / "desktop/requirements.txt").read_text()
    assert "strands-agents-tools[local-chromium-browser]==0.8.5" in requirements
    assert "PyAutoGUI==0.9.54" in requirements
    assert "../" not in requirements
    for source in ("computer_use_activity.py", "desktop_observation.py", "workspace_state.py"):
        assert source in dockerfile
        assert f"!{source}" in ignore.splitlines()
    assert "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright" in dockerfile
    assert "USER desktop" in dockerfile
    assert "HEALTHCHECK" in dockerfile


@pytest.mark.asyncio
async def test_worker_uses_native_pydantic_converter_and_single_activity_slot(monkeypatch):
    import desktop_worker
    from computer_use_activity import COMPUTER_USE_TOOL_NAMES
    from config import DESKTOP_BROWSER_TASK_QUEUE

    monkeypatch.setenv("TEMPORAL_ADDRESS", "test-temporal:7233")
    client = object()
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(desktop_worker.Client, "connect", connect)
    monkeypatch.setattr(desktop_worker, "telemetry_plugins", lambda: [])
    verify = MagicMock()
    initialize = MagicMock()
    monkeypatch.setattr(desktop_worker, "verify_browser_runtime", verify)
    monkeypatch.setattr(desktop_worker, "initialize_desktop", initialize)
    stopped = SimpleNamespace(set=MagicMock(), wait=AsyncMock())
    monkeypatch.setattr(desktop_worker.asyncio, "Event", lambda: stopped)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", MagicMock())
    worker = MagicMock(return_value=AsyncMock())
    monkeypatch.setattr(desktop_worker, "Worker", worker)

    await desktop_worker.main()

    connect.assert_awaited_once_with("test-temporal:7233", data_converter=pydantic_data_converter, plugins=[])
    verify.assert_called_once_with()
    initialize.assert_called_once_with()
    worker.assert_called_once()
    assert worker.call_args.args == (client,)
    options = worker.call_args.kwargs
    assert options["task_queue"] == DESKTOP_BROWSER_TASK_QUEUE
    assert options["max_concurrent_activities"] == 1
    assert options["activity_executor"]._max_workers == 1
    assert {activity._Definition.must_from_callable(fn).name for fn in options["activities"]} == {
        "browser", *COMPUTER_USE_TOOL_NAMES,
    }
    stopped.wait.assert_awaited_once_with()


def test_both_tool_paths_route_natively_with_finite_no_retry_options():
    from computer_use_activity import COMPUTER_USE_ACTIVITIES, COMPUTER_USE_TOOL_NAMES
    from config import DESKTOP_BROWSER_TASK_QUEUE
    from workflow import PERMANENT_COMMUNITY_TOOLS, _DESKTOP_ACTIVITY_OPTIONS
    import run_worker

    tools = [next(tool for tool in PERMANENT_COMMUNITY_TOOLS if tool.tool_name == "browser")]
    tools.extend(activity_as_tool(fn, **_DESKTOP_ACTIVITY_OPTIONS) for fn in COMPUTER_USE_ACTIVITIES)
    for tool in tools:
        assert tool.tool_type == "temporal_activity"
        assert tool._options["task_queue"] == DESKTOP_BROWSER_TASK_QUEUE
        assert tool._options["retry_policy"].maximum_attempts == 1
        assert tool._options["start_to_close_timeout"].total_seconds() > 0
        assert tool._options["schedule_to_close_timeout"].total_seconds() > 0
        assert tool._options["cancellation_type"] == workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
    host_names = {activity._Definition.must_from_callable(fn).name for fn in run_worker.WORKER_ACTIVITIES}
    assert not host_names & {"browser", *COMPUTER_USE_TOOL_NAMES}


def test_stock_browser_explicitly_preserves_chromium_sandbox():
    from browser_activity import _browser

    assert _browser._launch_options["chromium_sandbox"] is True
    assert _browser._launch_options["headless"] is False


@pytest.mark.parametrize("failure", [None, "returned_error", "raised_exception"])
def test_readiness_always_closes_its_disposable_stock_session(monkeypatch, failure):
    import desktop_worker

    stock = MagicMock()

    def dispatch(*, browser_input):
        if browser_input.action.type == "init_session":
            if failure == "returned_error":
                return {"status": "error", "content": [{"text": "unavailable"}]}
            if failure == "raised_exception":
                raise RuntimeError("unavailable")
        return {"status": "success", "content": []}

    stock.browser.side_effect = dispatch
    constructor = MagicMock(return_value=stock)
    monkeypatch.setattr(desktop_worker, "LocalChromiumBrowser", constructor)
    if failure:
        with pytest.raises(RuntimeError, match="unavailable"):
            desktop_worker.verify_browser_runtime()
    else:
        desktop_worker.verify_browser_runtime()
    constructor.assert_called_once_with(launch_options={"headless": False, "chromium_sandbox": True})
    actions = [item.kwargs["browser_input"].action for item in stock.browser.call_args_list]
    assert [item.type for item in actions] == ["init_session", "close"]
    assert all(item.session_name == "desktop-readiness" for item in actions)


@pytest.mark.parametrize("system,display", [("Darwin", ":99"), ("Windows", ":99"), ("Linux", None)])
def test_platform_fence_rejects_startup_and_both_tools_before_gui_access(monkeypatch, tmp_path, system, display):
    import computer_use_activity as cua
    from strands_tools.browser.models import BrowserInput

    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path / "must-not-be-created"))
    monkeypatch.setattr(browser.platform, "system", lambda: system)
    if display:
        monkeypatch.setenv("DISPLAY", display)
    else:
        monkeypatch.delenv("DISPLAY", raising=False)
    info = MagicMock(side_effect=AssertionError("Platform fence must precede activity context"))
    monkeypatch.setattr(browser.activity, "info", info)
    with pytest.raises(RuntimeError, match="isolated Linux X display"):
        browser.initialize_desktop()
    for invoke in (
        lambda: cua.execute_computer_use("click", {"x": 500, "y": 500}),
        lambda: browser.browser_activity(BrowserInput.model_validate({"action": {"type": "list_local_sessions"}})),
    ):
        with pytest.raises(ApplicationError, match="cannot execute on the host") as error:
            invoke()
        assert error.value.non_retryable
    info.assert_not_called()
    assert not browser.artifact_root().exists()


def test_action_binds_owner_and_preserves_session_across_run_ids(runtime):
    with browser.desktop_action() as first_epoch:
        with browser.desktop_state() as state:
            state["session_name"] = "shared-browser-session"
    runtime.workflow_run_id = "run-2"
    runtime.activity_id = "action-2"
    with browser.desktop_action() as second_epoch:
        assert second_epoch == first_epoch
    with browser.desktop_state() as state:
        assert state["owner"] == {"namespace": "test", "workflow_id": "owner"}
        assert state["session_name"] == "shared-browser-session"
        assert state["mode"] == "agent"


@pytest.mark.parametrize("field,value", [("namespace", "other-namespace"), ("workflow_id", "other-owner")])
def test_action_rejects_other_workflows_and_namespaces(runtime, field, value):
    with browser.desktop_action():
        pass
    setattr(runtime, field, value)
    with pytest.raises(ApplicationError, match="owned, paused, or requires recovery") as error:
        with browser.desktop_action():
            pytest.fail("Foreign owner entered the action")
    assert error.value.non_retryable
    with browser.desktop_state() as state:
        assert state["owner"] == {"namespace": "test", "workflow_id": "owner"}
        assert state["mode"] == "agent"


@pytest.mark.parametrize("mode", ["stopping", "human", "relinquishing", "steering", "resuming", "recovery"])
def test_non_agent_modes_fence_input_without_changing_ownership(runtime, mode):
    with browser.desktop_state() as state:
        state["mode"] = mode
    with pytest.raises(ApplicationError, match="owned, paused, or requires recovery") as error:
        with browser.desktop_action():
            pytest.fail("Paused desktop admitted input")
    assert error.value.non_retryable
    with browser.desktop_state() as state:
        assert state["mode"] == mode
        assert state["owner"] is None


def test_inflight_action_keeps_lock_during_stopping_until_cleanup(runtime):
    with browser.desktop_action():
        # Ownership can be fenced without waiting on the GUI lock, but another
        # action cannot enter until the original context actually finishes.
        with browser.desktop_state() as state:
            state["mode"] = "stopping"
        with pytest.raises(ApplicationError, match="already in flight") as error:
            with browser.desktop_action():
                pytest.fail("Overlapping action entered")
        assert error.value.non_retryable
    descriptor = os.open(browser.artifact_root() / "action.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
    with pytest.raises(ApplicationError, match="owned, paused, or requires recovery"):
        with browser.desktop_action():
            pytest.fail("Stopping desktop admitted new input")


@pytest.mark.parametrize("exception", [RuntimeError("native failure"), asyncio.CancelledError()])
def test_action_exception_releases_lock_but_requires_recovery(runtime, exception):
    with pytest.raises(type(exception)):
        with browser.desktop_action():
            raise exception
    with browser.desktop_state() as state:
        assert state["mode"] == "recovery"
        assert state["owner"] == {"namespace": "test", "workflow_id": "owner"}
    descriptor = os.open(browser.artifact_root() / "action.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
    with pytest.raises(ApplicationError, match="owned, paused, or requires recovery"):
        with browser.desktop_action():
            pytest.fail("Failed action was blindly retried")


def test_worker_restart_changes_epoch_and_requires_recovery_for_existing_owner(runtime, monkeypatch):
    with browser.desktop_action() as epoch:
        with browser.desktop_state() as state:
            state["session_name"] = "shared-browser-session"
    monkeypatch.setattr(browser.time, "time_ns", lambda: epoch + 1)
    browser.initialize_desktop()
    with browser.desktop_state() as state:
        assert state["epoch"] == epoch + 1
        assert state["owner"] == {"namespace": "test", "workflow_id": "owner"}
        assert state["mode"] == "recovery"
        assert state["session_name"] == "shared-browser-session"


def test_state_exception_does_not_commit_partial_changes_and_releases_lock(runtime):
    with pytest.raises(RuntimeError, match="abort state update"):
        with browser.desktop_state() as state:
            state["mode"] = "human"
            raise RuntimeError("abort state update")
    with browser.desktop_state() as state:
        assert state["mode"] == "agent"
    assert (browser.artifact_root() / "runtime.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("name", ["state.lock", "action.lock"])
def test_native_lock_files_cannot_follow_symlinks(runtime, tmp_path, name):
    lock = browser.artifact_root() / name
    lock.unlink(missing_ok=True)
    target = tmp_path / "outside-lock"
    target.write_text("untouched")
    lock.symlink_to(target)
    context = browser.desktop_state if name == "state.lock" else browser.desktop_action
    with pytest.raises(OSError):
        with context():
            pytest.fail("Followed symlink lock")
    assert target.read_text() == "untouched"


def test_startup_waits_for_display_and_supervises_all_children():
    source = (ROOT / "desktop/start-desktop.sh").read_text()
    assert source.index("xdpyinfo") < source.index("openbox &")
    assert "wait -n" in source
    assert "trap" in source
    assert "kill" in source
    assert "-nolisten tcp" in source
    assert "-localhost" in source
    assert "-viewonly" in source
