"""Temporal worker for the desktop browser activity.

Runs inside the Linux desktop container: polls the desktop task queue and
executes the stock strands browser activity. The stock LocalChromiumBrowser
launches headed Chromium on this machine's X display (Xvfb), visible through
noVNC. Nothing else runs here — model factories and credentials stay on the
host worker.
"""

import asyncio
import logging
import os
import signal
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from browser_activity import browser_activity, initialize_desktop
from computer_use_activity import COMPUTER_USE_ACTIVITIES
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import BrowserInput
from config import DESKTOP_BROWSER_TASK_QUEUE
from telemetry import telemetry_plugins


def verify_browser_runtime() -> None:
    """Fail startup before polling if headed sandboxed Chromium cannot run."""
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
        initialize_desktop()
        async with Worker(
            client,
            task_queue=DESKTOP_BROWSER_TASK_QUEUE,
            activities=[browser_activity, *COMPUTER_USE_ACTIVITIES],
            activity_executor=executor,
            max_concurrent_activities=1,
        ):
            logging.getLogger(__name__).info(
                "Desktop worker polling %r", DESKTOP_BROWSER_TASK_QUEUE
            )
            await stopped.wait()


if __name__ == "__main__":
    asyncio.run(main())
