"""Native Strands browser exposed as a Temporal activity.

One stock ``LocalChromiumBrowser`` per worker process. This module contains no
platform code: the worker that registers this activity runs inside the desktop
environment (Xvfb), so Chromium launches headed on that display via the stock
tool and its own environment variables (STRANDS_BROWSER_*).

The activity is synchronous: Temporal runs it in its own thread executor, and
the stock tool manages its own event loop internally.
"""

import fcntl
import json
import os
import platform
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from PIL import ImageGrab
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import BrowserInput
from temporalio import activity
from temporalio.exceptions import ApplicationError

from desktop_observation import store_observation

_browser = LocalChromiumBrowser(launch_options={"headless": False, "chromium_sandbox": True})


def artifact_root() -> Path:
    return Path(os.environ.get("DESKTOP_ARTIFACT_ROOT", str(Path(tempfile.gettempdir()) / "kilo" / "gwen-desktop-artifacts")))


@contextmanager
def desktop_state():
    """Serialize ownership transitions independently of a running GUI action."""
    root = artifact_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        path = root / "runtime.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        yield state
        with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as file:
            json.dump(state, file)
            name = file.name
        os.replace(name, path)
    finally:
        os.close(fd)


def initialize_desktop() -> None:
    if platform.system() != "Linux" or not os.environ.get("DISPLAY"):
        raise RuntimeError("Desktop activities require the isolated Linux X display")
    with desktop_state() as state:
        owner = state.get("owner")
        state.update(epoch=time.time_ns(), owner=owner, mode="recovery" if owner else "agent")


@contextmanager
def desktop_action():
    """Keep the native action lock until GUI completion, not Temporal timeout."""
    if platform.system() != "Linux" or not os.environ.get("DISPLAY"):
        raise ApplicationError("Desktop input cannot execute on the host", non_retryable=True)
    info = activity.info()
    owner = {"namespace": info.namespace, "workflow_id": info.workflow_id}
    fd = os.open(artifact_root() / "action.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ApplicationError("Desktop action already in flight", non_retryable=True) from error
        with desktop_state() as state:
            if state.get("mode") != "agent" or state.get("owner") not in (None, owner):
                raise ApplicationError("Desktop is owned, paused, or requires recovery", non_retryable=True)
            state["owner"] = owner
            epoch = state["epoch"]
        try:
            yield epoch
        except BaseException:
            with desktop_state() as state:
                state["mode"] = "recovery"
            raise
    finally:
        os.close(fd)


def capture_desktop(epoch: int) -> dict:
    """Record physical display pixels; only compact metadata enters history."""
    info = activity.info()
    image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
    ref = store_observation(image, namespace=info.namespace, workflow_id=info.workflow_id,
                            operation_id=info.activity_id, desktop_epoch=epoch)
    return ref.model_dump(mode="json")


@activity.defn(name="browser", no_thread_cancel_exception=True)
def browser_activity(browser_input: BrowserInput) -> dict:
    """Stock browser on the shared Linux desktop. Results include full-display screenshots.

    Use computer click/move actions for screenshot coordinates (0-999 across
    the full display); browser selector/CDP coordinates refer to page content.
    """
    with desktop_action() as epoch:
        action = browser_input.action
        with desktop_state() as state:
            session = getattr(action, "session_name", None)
            if action.type == "init_session" and not state.get("session_name"):
                state["session_name"] = session
            elif session is not None and session != state.get("session_name"):
                raise ApplicationError("Browser session does not match desktop owner", non_retryable=True)
        # Never accept a model-chosen output path. Stock screenshot writing is
        # scoped to a disposable directory; the model sees the actual display.
        with tempfile.TemporaryDirectory(prefix="desktop-capture-") as scratch:
            if action.type == "screenshot":
                browser_input = browser_input.model_copy(update={"action": action.model_copy(
                    update={"path": str(Path(scratch) / "page.png")})})
            result = _browser.browser(browser_input=browser_input)
        if result.get("status") == "error":
            raise ApplicationError(str(result), non_retryable=True)
        return {**result, "action": action.type, "observation": capture_desktop(epoch)}
