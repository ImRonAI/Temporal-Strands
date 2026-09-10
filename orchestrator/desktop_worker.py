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
from concurrent.futures import ThreadPoolExecutor

from PIL import ImageGrab
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from browser_activity import artifact_root, browser_activity, desktop_control, initialize_desktop, release_desktop
from computer_use_activity import COMPUTER_USE_ACTIVITIES
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import BrowserInput
from config import DESKTOP_BROWSER_TASK_QUEUE
from telemetry import telemetry_plugins


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
            async with Worker(
                client,
                task_queue=DESKTOP_BROWSER_TASK_QUEUE,
                activities=[browser_activity, desktop_control, release_desktop, *COMPUTER_USE_ACTIVITIES],
                activity_executor=executor,
                max_concurrent_activities=1,
            ):
                logging.getLogger(__name__).info(
                    "Desktop worker polling %r", DESKTOP_BROWSER_TASK_QUEUE
                )
                await stopped.wait()
        finally:
            os.close(fd)


if __name__ == "__main__":
    asyncio.run(main())
