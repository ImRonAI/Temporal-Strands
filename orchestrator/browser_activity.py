"""Native Strands browser exposed using Temporal's documented activity adapter.

https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md#tools
"""

import asyncio
import json
import socket
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor

from strands.types.tools import ToolResult
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import BrowserInput
from temporalio import activity

# Native browser instances own event loops. Keep their calls on one thread,
# outside the worker's asyncio loop, and isolate instances by workflow ID.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="strands-browser")
_browsers: dict[str, LocalChromiumBrowser] = {}
_ports: dict[str, int] = {}


def _invoke(workflow_id: str, browser_input: BrowserInput) -> ToolResult:
    browser = _browsers.get(workflow_id)
    if browser is None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        _ports[workflow_id] = port
        browser = LocalChromiumBrowser(launch_options={"args": [
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=http://localhost:3000,http://127.0.0.1:3000",
        ]})
        _browsers[workflow_id] = browser
    result = browser.browser(browser_input=browser_input)
    if browser_input.action.type == "close":
        _browsers.pop(workflow_id, None)
        _ports.pop(workflow_id, None)
    elif result.get("status") == "success" and hasattr(browser_input.action, "session_name"):
        # Viewer metadata is separate from native result content. Ask the
        # native CDP action for identity; never match pages by URL.
        target = browser.browser(browser_input=BrowserInput.model_validate({"action": {
            "type": "execute_cdp", "session_name": browser_input.action.session_name,
            "method": "Target.getTargetInfo",
        }}))
        if target.get("status") == "success":
            try:
                info = json.loads(target["content"][0]["text"])["targetInfo"]
            except (KeyError, IndexError, TypeError, ValueError):
                return result
            endpoint = f"localhost:{_ports[workflow_id]}/devtools/page/{info['targetId']}"
            result = {**result, "browserPreview": {
                "url": info["url"], "action": browser_input.action.type,
                "livePreviewUrl": f"/computer-use-live.html?{urlencode({'ws': 'ws://' + endpoint})}",
                "devtoolsFrontendUrl": f"http://localhost:{_ports[workflow_id]}/devtools/inspector.html?{urlencode({'ws': endpoint})}",
            }}
    return result


@activity.defn(name="browser")
async def browser_activity(browser_input: BrowserInput) -> ToolResult:
    """Run the native Strands browser action without changing its schema or result."""
    operation = asyncio.get_running_loop().run_in_executor(
        _executor, _invoke, activity.info().workflow_id, browser_input
    )
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        # Cancelling a Future cannot stop the thread. Drain the native action
        # before reporting cancellation so it cannot outlive control transfer.
        await operation
        raise


def shutdown_browser_activity() -> None:
    """Close native browser resources before the worker exits."""
    def close_all() -> None:
        for browser in _browsers.values():
            browser.browser(browser_input=BrowserInput.model_validate({
                "action": {"type": "close", "session_name": "worker-shutdown"}
            }))
        _browsers.clear()
        _ports.clear()

    try:
        _executor.submit(close_all).result()
    finally:
        _executor.shutdown(wait=True)
