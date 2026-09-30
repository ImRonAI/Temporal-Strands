"""Native browser binding and dispatch; real browser execution is opt-in/remote."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from strands_tools.browser.models import BrowserInput
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.contrib.strands import StrandsPlugin
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
def stock(monkeypatch, tmp_path):
    # browser_activity runs inside desktop_action(), which needs the Linux
    # desktop runtime and an activity owner; bind the same fixture shape that
    # test_desktop_runtime.py uses.
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser.platform, "system", lambda: "Linux")
    info = SimpleNamespace(namespace="test", workflow_id="desktop-owner", workflow_run_id="run-1",
                           activity_id="browser-1")
    monkeypatch.setattr(browser.activity, "info", lambda: info)
    browser.initialize_desktop()
    stock = MagicMock()
    monkeypatch.setattr(browser, "_browser", stock)
    stock.capture = MagicMock(return_value={"artifact_id": "observation-1", "desktop_epoch": 1})
    monkeypatch.setattr(browser, "capture_desktop", stock.capture)
    return stock


def test_browser_is_permanently_registered_with_native_input_schema() -> None:
    from workflow import PERMANENT_COMMUNITY_TOOLS

    tools = {tool.tool_name: tool for tool in PERMANENT_COMMUNITY_TOOLS}
    tool = tools["browser"]
    assert tool.tool_type == "temporal_activity"
    assert list(tool.tool_spec["inputSchema"]["json"]["properties"]) == ["browser_input"]
    # The model reads the stock strands_tools description, not an app rewrite.
    assert tool.tool_spec["description"].startswith("Browser automation tool for web scraping")
    definition = activity._Definition.must_from_callable(browser.browser_activity)
    assert definition.arg_types == [BrowserInput]


def test_browser_routes_to_desktop_queue_and_never_the_host_worker() -> None:
    """Native routing: the desktop container's worker owns the browser."""
    from config import DESKTOP_BROWSER_TASK_QUEUE
    from workflow import PERMANENT_COMMUNITY_TOOLS

    tools = {tool.tool_name: tool for tool in PERMANENT_COMMUNITY_TOOLS}
    assert tools["browser"]._options["task_queue"] == DESKTOP_BROWSER_TASK_QUEUE
    # desktop_action() heartbeats, so the binding shares the desktop heartbeat window.
    from config import DESKTOP_JOB_HEARTBEAT_TIMEOUT
    assert tools["browser"]._options["heartbeat_timeout"] == DESKTOP_JOB_HEARTBEAT_TIMEOUT

    import run_worker

    names = [getattr(a, "__temporal_activity_definition", None) for a in run_worker.WORKER_ACTIVITIES]
    assert not any(getattr(name, "name", None) == "browser" for name in names)


@pytest.mark.parametrize("action", [
    {"type": "init_session", "session_name": "any-session", "description": "Test"},
    {"type": "init_session", "session_name": "second-session", "description": "Second"},
    {"type": "navigate", "session_name": "any-session", "url": "https://example.com/"},
    {"type": "close", "session_name": "any-session"},
])
def test_binding_forwards_every_action_unchanged_to_stock_tool(stock, action):
    request = BrowserInput.model_validate({"action": action})
    stock.browser.return_value = {"status": "success", "content": [{"text": "native result"}]}
    assert browser.browser_activity(request) == stock.browser.return_value
    stock.browser.assert_called_once_with(browser_input=request)
    stock.capture.assert_not_called()


def test_screenshot_success_attaches_an_observation_reference(stock):
    """The stock screenshot returns only a filename (browser.py:609-638); the
    activity adds the same integrity-checked ObservationRef flow as
    computer-use, keeping PNG bytes out of the Temporal payload."""
    stock.browser.return_value = {"status": "success", "content": [{"text": "Screenshot saved as x.png"}]}
    request = BrowserInput.model_validate({"action": {
        "type": "screenshot", "session_name": "any-session", "path": "model-chosen.png"}})
    with browser.desktop_state() as state:
        epoch = state["epoch"]
    result = browser.browser_activity(request)
    stock.browser.assert_called_once_with(browser_input=request)
    stock.capture.assert_called_once_with(epoch)
    assert result == {**stock.browser.return_value, "observation": stock.capture.return_value}


def test_screenshot_error_dict_is_returned_without_observation(stock):
    error = {"status": "error", "content": [{"text": "Error: No active page for session"}]}
    stock.browser.return_value = error
    request = BrowserInput.model_validate({"action": {"type": "screenshot", "session_name": "any-session"}})
    assert browser.browser_activity(request) == error
    stock.capture.assert_not_called()


def test_stock_call_binds_workflow_as_desktop_owner_for_take_control(stock):
    """Regression: without desktop_action() runtime.json had owner=None, so
    /desktop-control answered 409 and the Take Control badge stayed disabled."""
    owner_during_call = {}

    def observe(*, browser_input):
        with browser.desktop_state() as state:
            owner_during_call.update(state)
        return {"status": "success", "content": [{"text": "ok"}]}

    stock.browser.side_effect = observe
    browser.browser_activity(BrowserInput.model_validate({"action": {
        "type": "init_session", "session_name": "shared-browser-session", "description": "Test"}}))
    assert owner_during_call["owner"] == {"namespace": "test", "workflow_id": "desktop-owner"}
    assert owner_during_call["mode"] == "agent"
    with browser.desktop_state() as state:
        # Ownership persists after the action so the handoff endpoint can pause it.
        assert state["owner"] == {"namespace": "test", "workflow_id": "desktop-owner"}
        assert state["mode"] == "agent"


def test_stock_exception_marks_desktop_for_recovery_and_propagates(stock):
    stock.browser.side_effect = RuntimeError("native browser crashed")
    with pytest.raises(RuntimeError, match="native browser crashed"):
        browser.browser_activity(BrowserInput.model_validate({"action": {
            "type": "init_session", "session_name": "shared-browser-session", "description": "Test"}}))
    with browser.desktop_state() as state:
        assert state["mode"] == "recovery"


def test_stock_close_discards_tool_so_next_use_gets_a_fresh_instance(stock):
    """Live regression: stock close nulls Playwright but never resets
    `_started` (strands-tools/browser/browser.py:940-946, 975), so reuse failed
    with 'Playwright not initialized'. The activity discards the closed
    instance; the next action runs on a fresh stock tool — no subclass."""
    stock.browser.return_value = {"status": "success", "content": [{"text": "Browser closed"}]}
    request = BrowserInput.model_validate({"action": {"type": "close", "session_name": "any-session"}})
    assert browser.browser_activity(request) == stock.browser.return_value
    stock.browser.assert_called_once_with(browser_input=request)
    assert browser._browser is not stock
    assert type(browser._browser) is browser.LocalChromiumBrowser
    assert browser._browser._launch_options == {"headless": False, "chromium_sandbox": True}


def test_failed_close_keeps_the_same_tool_for_recovery(stock):
    error = {"status": "error", "content": [{"text": "Error: close failed"}]}
    stock.browser.return_value = error
    request = BrowserInput.model_validate({"action": {"type": "close", "session_name": "any-session"}})
    assert browser.browser_activity(request) == error
    assert browser._browser is stock


def test_stock_error_dict_is_returned_to_the_model_not_raised(stock):
    error = {"status": "error", "content": [{"text": "Error: Timeout 30000ms exceeded."}]}
    stock.browser.return_value = error
    request = BrowserInput.model_validate({"action": {
        "type": "click", "session_name": "s", "selector": "#missing"}})
    assert browser.browser_activity(request) == error


def test_tool_schema_exposes_every_stock_browser_action() -> None:
    from workflow import PERMANENT_COMMUNITY_TOOLS

    tool = next(tool for tool in PERMANENT_COMMUNITY_TOOLS if tool.tool_name == "browser")
    exposed = json.dumps(tool.tool_spec["inputSchema"]["json"])
    stock = BrowserInput.model_json_schema()["properties"]["action"]["discriminator"]["mapping"]
    assert len(stock) >= 20
    for action_type in stock:
        assert f'"{action_type}"' in exposed, action_type


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
    for result in results:
        assert result["status"] == "success"
        assert json.loads(result["content"][0]["text"])["status"] == "success"
