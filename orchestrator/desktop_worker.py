"""Temporal worker for the desktop browser activity.

Runs inside the Linux desktop container: polls the desktop task queue and
executes the stock strands browser activity. The stock LocalChromiumBrowser
launches headed Chromium on this machine's X display (Xvfb), visible through
noVNC. Nothing else runs here — model factories and credentials stay on the
host worker.
"""

import asyncio
import fcntl
import logging
import os
import signal

os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
from concurrent.futures import ThreadPoolExecutor

from PIL import ImageGrab
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from browser_activity import (
    artifact_root, browser_activity, desktop_control, desktop_state, initialize_desktop, release_desktop,
)
from computer_use_activity import COMPUTER_USE_ACTIVITIES
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import BrowserInput
from config import DESKTOP_BROWSER_TASK_QUEUE, DESKTOP_OWNER_PROBE_TIMEOUT
from telemetry import telemetry_plugins

logger = logging.getLogger(__name__)


async def reclaim_orphaned_desktop(client: Client) -> bool:
    """Drop ownership left by a workflow that no longer exists or has closed.

    A container restart hands ``initialize_desktop`` whatever ``runtime.json``
    the previous worker left. If that owner is still running, the desktop must
    start in ``recovery`` so no agent mutates a session it cannot verify. If
    the owner has closed (or Temporal never heard of it), nothing can ever
    release it, so keep the state honest: clear the owner and start in
    ``agent``. Doubt (RPC failure, timeout) keeps the owner — recovery is the
    safe default. Returns True when ownership was cleared.
    """
    with desktop_state() as state:
        owner = state.get("owner")
    if not isinstance(owner, dict) or not owner.get("workflow_id"):
        return False
    if owner.get("namespace") != client.namespace:
        logger.warning("Desktop owner %r belongs to another namespace; keeping recovery", owner)
        return False
    try:
        description = await client.get_workflow_handle(owner["workflow_id"]).describe(
            rpc_timeout=DESKTOP_OWNER_PROBE_TIMEOUT,
        )
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            logger.warning("Desktop owner probe failed for %r; keeping recovery: %s", owner, error)
            return False
        status = None
    else:
        status = description.status
    if status in (WorkflowExecutionStatus.RUNNING, WorkflowExecutionStatus.CONTINUED_AS_NEW):
        return False
    with desktop_state() as state:
        if state.get("owner") != owner:
            return False
        state.update(owner=None, mode="agent")
        state.pop("session_name", None)
    logger.info("Reclaimed desktop from closed workflow %r (status=%s)", owner["workflow_id"],
                getattr(status, "name", "NOT_FOUND"))
    return True


def verify_browser_runtime() -> None:
    """Fail before polling if native input, capture, or headed Chromium fails."""
    # PyAutoGUI opens Xlib connections at import; xdpyinfo alone misses Xauth errors.
    import pyautogui

    # Validate the APIs used by the activities without sending probe input.
    for name in ("click", "moveTo", "mouseDown", "mouseUp", "write", "dragTo",
                 "press", "keyDown", "keyUp", "hotkey", "scroll", "hscroll"):
        if not callable(getattr(pyautogui, name, None)):
            raise RuntimeError(f"PyAutoGUI input API unavailable: {name}")

    size = pyautogui.size()
    with ImageGrab.grab(xdisplay=os.environ["DISPLAY"]) as image:
        image.load()
        if min(image.size) <= 0 or image.size != tuple(size):
            raise RuntimeError("Desktop screenshot dimensions do not match the X display")

    browser = LocalChromiumBrowser(launch_options={"headless": False, "chromium_sandbox": True})
    try:
        result = browser.browser(browser_input=BrowserInput.model_validate({"action": {
            "type": "init_session", "session_name": "desktop-readiness",
            "description": "Native desktop readiness check",
        }}))
        if result["status"] != "success":
            raise RuntimeError(f"Desktop Chromium unavailable: {result}")
    finally:
        browser.browser(browser_input=BrowserInput.model_validate({"action": {
            "type": "close", "session_name": "desktop-readiness",
        }}))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(
        os.environ["TEMPORAL_ADDRESS"],
        data_converter=pydantic_data_converter,
        plugins=[*telemetry_plugins()],
    )
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    with ThreadPoolExecutor(max_workers=1) as executor:
        await asyncio.get_running_loop().run_in_executor(executor, verify_browser_runtime)
        fd = os.open(artifact_root() / "desktop-worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            initialize_desktop()
            # Before polling: a previous owner that has already closed can never
            # release the desktop, so it must not pin the runtime in recovery.
            await reclaim_orphaned_desktop(client)
            async with Worker(
                client,
                task_queue=DESKTOP_BROWSER_TASK_QUEUE,
                activities=[browser_activity, desktop_control, release_desktop, *COMPUTER_USE_ACTIVITIES],
                activity_executor=executor,
                max_concurrent_activities=1,
            ):
                logger.info("Desktop worker polling %r", DESKTOP_BROWSER_TASK_QUEUE)
                await stopped.wait()
        finally:
            os.close(fd)


if __name__ == "__main__":
    asyncio.run(main())
