"""Native browser binding and dispatch; real browser execution is opt-in/remote."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from PIL import Image
from strands_tools.browser.models import BrowserInput
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.contrib.strands import StrandsPlugin
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

import browser_activity as browser


@pytest.fixture(autouse=True)
def forbid_host_browser(monkeypatch):
    monkeypatch.setattr(browser.LocalChromiumBrowser, "browser", MagicMock(
        side_effect=AssertionError("Tests must not execute a browser on the host"),
    ))
    monkeypatch.setattr(browser.ImageGrab, "grab", MagicMock(
        side_effect=AssertionError("Tests must not capture the host display"),
    ))


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser.platform, "system", lambda: "Linux")
    info = SimpleNamespace(namespace="test", workflow_id="desktop-owner", activity_id="browser-1")
    monkeypatch.setattr(browser.activity, "info", lambda: info)
    browser.initialize_desktop()
    stock = MagicMock()
    stock.browser.return_value = {"status": "success", "content": [{"text": "native result"}]}
    monkeypatch.setattr(browser, "_browser", stock)
    observation = {"artifact_id": "test-artifact", "desktop_epoch": 123}
    capture = MagicMock(return_value=observation)
    monkeypatch.setattr(browser, "capture_desktop", capture)
    return SimpleNamespace(browser=stock, capture=capture, observation=observation, info=info)


def test_browser_is_permanently_registered_with_native_input_schema() -> None:
    from workflow import PERMANENT_COMMUNITY_TOOLS

    tools = {tool.tool_name: tool for tool in PERMANENT_COMMUNITY_TOOLS}
    assert "browser" in tools
    tool = tools["browser"]
    assert tool.tool_type == "temporal_activity"
    assert list(tool.tool_spec["inputSchema"]["json"]["properties"]) == [
        "browser_input"
    ]
    definition = activity._Definition.must_from_callable(browser.browser_activity)
    assert definition.arg_types == [BrowserInput]
    assert definition.no_thread_cancel_exception


def test_browser_routes_to_desktop_queue_and_never_the_host_worker() -> None:
    """Native routing: the desktop container's worker owns the browser."""
    from config import DESKTOP_BROWSER_TASK_QUEUE
    from workflow import PERMANENT_COMMUNITY_TOOLS

    tools = {tool.tool_name: tool for tool in PERMANENT_COMMUNITY_TOOLS}
    assert tools["browser"]._options["task_queue"] == DESKTOP_BROWSER_TASK_QUEUE

    import run_worker

    names = [getattr(a, "__temporal_activity_definition", None) for a in run_worker.WORKER_ACTIVITIES]
    assert not any(getattr(name, "name", None) == "browser" for name in names)


def test_binding_dispatches_native_model_and_attaches_scoped_capture(desktop):
    request = BrowserInput.model_validate({"action": {
        "type": "init_session", "session_name": "shared-browser-session", "description": "Test",
    }})
    result = browser.browser_activity(request)
    desktop.browser.browser.assert_called_once_with(browser_input=request)
    with browser.desktop_state() as state:
        assert state["session_name"] == "shared-browser-session"
        assert state["owner"] == {"namespace": "test", "workflow_id": "desktop-owner"}
        desktop.capture.assert_called_once_with(state["epoch"])
    assert result == {"status": "success", "content": [{"text": "native result"}],
                      "action": "init_session", "observation": desktop.observation}


@pytest.mark.parametrize("action", [
    {"type": "init_session", "session_name": "another-browser-session", "description": "Wrong session"},
    {"type": "navigate", "session_name": "another-browser-session", "url": "https://example.com/"},
    {"type": "close", "session_name": "another-browser-session"},
])
def test_binding_rejects_cross_session_actions_before_native_execution(desktop, action):
    with browser.desktop_state() as state:
        state["session_name"] = "shared-browser-session"
    with pytest.raises(ApplicationError, match="does not match desktop owner") as error:
        browser.browser_activity(BrowserInput.model_validate({"action": action}))
    assert error.value.non_retryable
    desktop.browser.browser.assert_not_called()
    desktop.capture.assert_not_called()


def test_screenshot_scopes_model_path_and_cleans_scratch_directory(desktop, tmp_path):
    with browser.desktop_state() as state:
        state["session_name"] = "shared-browser-session"
    chosen_path = tmp_path / "model-chosen.png"
    request = BrowserInput.model_validate({"action": {
        "type": "screenshot", "session_name": "shared-browser-session", "path": str(chosen_path),
    }})
    written = []

    def screenshot(*, browser_input):
        path = Path(browser_input.action.path)
        assert path != chosen_path
        assert path.parent.is_dir()
        path.write_bytes(b"fake page screenshot")
        written.append(path)
        return {"status": "success", "content": [{"text": f"Screenshot saved: {path}"}]}

    desktop.browser.browser.side_effect = screenshot
    result = browser.browser_activity(request)
    assert result["observation"] == desktop.observation
    assert request.action.path == str(chosen_path)
    assert not chosen_path.exists()
    assert len(written) == 1
    assert not written[0].parent.exists()


@pytest.mark.parametrize("failure", ["returned_error", "raised_exception"])
def test_native_failures_clean_scratch_and_require_recovery(desktop, failure):
    with browser.desktop_state() as state:
        state["session_name"] = "shared-browser-session"
    written = []

    def fail(*, browser_input):
        path = Path(browser_input.action.path)
        path.write_bytes(b"partial screenshot")
        written.append(path)
        if failure == "raised_exception":
            raise RuntimeError("native browser failed")
        return {"status": "error", "content": [{"text": "native browser failed"}]}

    desktop.browser.browser.side_effect = fail
    expected = RuntimeError if failure == "raised_exception" else ApplicationError
    with pytest.raises(expected, match="native browser failed") as error:
        browser.browser_activity(BrowserInput.model_validate({"action": {
            "type": "screenshot", "session_name": "shared-browser-session",
        }}))
    if failure == "returned_error":
        assert error.value.non_retryable
    assert not written[0].parent.exists()
    desktop.capture.assert_not_called()
    with browser.desktop_state() as state:
        assert state["mode"] == "recovery"


def test_capture_sends_full_display_pixels_to_scoped_observation_store(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser.activity, "info", lambda: SimpleNamespace(
        namespace="test", workflow_id="desktop-owner", activity_id="capture-1",
    ))
    image = Image.new("RGB", (1440, 900), "blue")
    grab = MagicMock(return_value=image)
    monkeypatch.setattr(browser.ImageGrab, "grab", grab)
    ref = MagicMock()
    ref.model_dump.return_value = {"artifact_id": "test-artifact", "desktop_epoch": 123}
    store = MagicMock(return_value=ref)
    monkeypatch.setattr(browser, "store_observation", store)
    try:
        result = browser.capture_desktop(123)
    finally:
        image.close()
    grab.assert_called_once_with(xdisplay=":99")
    store.assert_called_once_with(image, namespace="test", workflow_id="desktop-owner",
                                  operation_id="capture-1", desktop_epoch=123)
    ref.model_dump.assert_called_once_with(mode="json")
    assert result == {"artifact_id": "test-artifact", "desktop_epoch": 123}


@workflow.defn
class BrowserToolWorkflow:
    @workflow.run
    async def run(self, actions: list[dict]) -> list[dict]:
        from workflow import PERMANENT_COMMUNITY_TOOLS

        tool = next(tool for tool in PERMANENT_COMMUNITY_TOOLS if tool.tool_name == "browser")
        results = []
        for action in actions:
            async for event in tool.stream(
                {"toolUseId": action["type"], "name": "browser",
                 "input": {"browser_input": {"action": action}}},
                {},
            ):
                results.append(event["tool_result"])
        return results


@activity.defn(name="browser")
def mock_browser_activity(browser_input: BrowserInput) -> dict:
    """Mock native GUI execution, not the converter, activity adapter or routing."""
    assert isinstance(browser_input, BrowserInput)
    return {"status": "success", "action": browser_input.action.model_dump(mode="json"),
            "task_queue": activity.info().task_queue}


@pytest.mark.asyncio
async def test_native_browser_runs_through_temporal_activity() -> None:
    from config import DESKTOP_BROWSER_TASK_QUEUE

    actions = [
        {"type": "init_session", "session_name": "browser-test-session", "description": "Mock dispatch"},
        {"type": "new_tab", "session_name": "browser-test-session", "tab_id": "second"},
        {"type": "switch_tab", "session_name": "browser-test-session", "tab_id": "main"},
        {"type": "close", "session_name": "browser-test-session"},
    ]
    async with await WorkflowEnvironment.start_local() as env:
        client = await Client.connect(
            env.client.service_client.config.target_host,
            data_converter=pydantic_data_converter,
            plugins=[StrandsPlugin(models={})],
        )
        queue = f"browser-test-{uuid4()}"
        # The desktop queue exists on an isolated test server, not the running
        # gwen-desktop server. The host worker has no browser activity binding.
        with ThreadPoolExecutor(max_workers=1) as executor:
            async with Worker(
                client, task_queue=queue, workflows=[BrowserToolWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ), Worker(
                client, task_queue=DESKTOP_BROWSER_TASK_QUEUE, activities=[mock_browser_activity],
                activity_executor=executor, max_concurrent_activities=1,
            ):
                results = await client.execute_workflow(
                    BrowserToolWorkflow.run, actions,
                    id=f"browser-test-{uuid4()}", task_queue=queue,
                    execution_timeout=timedelta(seconds=60),
                )
    assert len(results) == len(actions)
    for result, action in zip(results, actions, strict=True):
        assert result["toolUseId"] == action["type"]
        assert result["status"] == "success"
        payload = json.loads(result["content"][0]["text"])
        assert payload == {"status": "success", "action": action, "task_queue": DESKTOP_BROWSER_TASK_QUEUE}


@pytest.mark.skipif(os.environ.get("GWEN_DESKTOP_LIVE_TEST") != "1", reason=(
    "Set GWEN_DESKTOP_LIVE_TEST=1 only with an unowned gwen-desktop Linux worker; "
    "GWEN_DESKTOP_TEST_TEMPORAL_ADDRESS defaults to localhost:7233"
))
@pytest.mark.asyncio
async def test_live_browser_uses_existing_linux_desktop_worker():
    """Opt-in smoke only: never starts a local browser or desktop activity worker.

    This claims the disposable desktop for a fresh workflow. It does not reset
    ownership, restart services, or replace the parent's real UI acceptance.
    """
    from desktop_observation import ObservationRef

    client = await Client.connect(
        os.environ.get("GWEN_DESKTOP_TEST_TEMPORAL_ADDRESS", "localhost:7233"),
        data_converter=pydantic_data_converter, plugins=[StrandsPlugin(models={})],
    )
    queue = f"live-browser-test-{uuid4()}"
    session = f"live-test-{uuid4().hex[:20]}"
    actions = [
        {"type": "init_session", "session_name": session, "description": "Opt-in Linux smoke"},
        {"type": "new_tab", "session_name": session, "tab_id": "second"},
        {"type": "switch_tab", "session_name": session, "tab_id": "main"},
        {"type": "screenshot", "session_name": session},
        {"type": "close", "session_name": session},
    ]
    async with Worker(client, task_queue=queue, workflows=[BrowserToolWorkflow],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        results = await client.execute_workflow(
            BrowserToolWorkflow.run, actions, id=f"live-browser-test-{uuid4()}",
            task_queue=queue, execution_timeout=timedelta(seconds=90),
        )
    assert len(results) == len(actions)
    observations = []
    for result, action in zip(results, actions, strict=True):
        assert result["status"] == "success"
        payload = json.loads(result["content"][0]["text"])
        assert payload["status"] == "success"
        assert payload["action"] == action["type"]
        observation = ObservationRef.model_validate(payload["observation"])
        assert observation.width == observation.display_width
        assert observation.height == observation.display_height
        assert observation.scale == 1
        observations.append(observation)
    assert len({item.artifact_id for item in observations}) == len(actions)
    assert len({item.desktop_epoch for item in observations}) == 1
