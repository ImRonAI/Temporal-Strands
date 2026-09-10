"""Native Strands browser exposed as a Temporal activity.

One stock ``LocalChromiumBrowser`` per worker process. The worker runs inside the desktop
environment (Xvfb), so Chromium launches headed on that display via the stock
tool and its own environment variables (STRANDS_BROWSER_*). Lifecycle cleanup
uses native x11vnc and Playwright on that same display.

The activity is synchronous: Temporal runs it in its own thread executor, and
the stock tool manages its own event loop internally.
"""

import asyncio
import fcntl
import json
import os
import platform
import shlex
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from PIL import ImageGrab
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import BrowserInput
from temporalio import activity
from temporalio.exceptions import ApplicationError

from config import (
    DESKTOP_HANDOFF_TIMEOUT, DESKTOP_VNC_COMMAND_TIMEOUT, DESKTOP_VNC_VIEW_COMMAND,
    DESKTOP_VNC_REVOKE_SCRIPT, DESKTOP_VNC_GRANT_SCRIPT,
)
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


@activity.defn(name="release_desktop", no_thread_cancel_exception=True)
def release_desktop() -> None:
    """Release only the calling workflow's desktop after verified native cleanup."""
    if platform.system() != "Linux" or not os.environ.get("DISPLAY"):
        raise ApplicationError("Desktop input cannot execute on the host", non_retryable=True)
    info = activity.info()
    if not info.namespace or not info.workflow_id:
        raise ApplicationError("Desktop release requires a workflow owner", non_retryable=True)
    owner = {"namespace": info.namespace, "workflow_id": info.workflow_id}
    with desktop_state() as state:
        if state.get("owner") != owner:
            return
        epoch = state["epoch"]

    descriptors = []
    deadline = time.monotonic() + DESKTOP_HANDOFF_TIMEOUT.total_seconds()
    try:
        # Match the API's lock order; action.lock alone cannot fence VNC grants.
        # Wait here in the worker's sole activity slot, never in a detached task.
        for name in ("handoff.lock", "action.lock"):
            fd = os.open(artifact_root() / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            descriptors.append(fd)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Desktop cleanup lock wait expired")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(0.1, remaining))
        with desktop_state() as state:
            if state.get("owner") != owner:
                return
            if state.get("epoch") != epoch:
                raise RuntimeError("Desktop restarted during release")
            state["mode"] = "recovery"

        # The host-side config command includes docker exec; it cannot run here.
        command = ["x11vnc", "-display", os.environ["DISPLAY"]]
        for script, query, expected in (
            ("script:viewonly;deny;disconnect:all;clear_all;fakebuttonevent:1,0;fakebuttonevent:2,0;fakebuttonevent:3,0",
             "viewonly,client_count,pointer_mask",
             {"ans=viewonly:1", "aro=client_count:0", "aro=pointer_mask:0x0"}),
            ("script:viewonly;nodeny", "viewonly,deny,pointer_mask",
             {"ans=viewonly:1", "ans=deny:0", "aro=pointer_mask:0x0"}),
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Desktop cleanup deadline expired")
            result = subprocess.run(
                [*command, "-R", script, "-Q", query], capture_output=True, text=True,
                timeout=min(DESKTOP_VNC_COMMAND_TIMEOUT, remaining),
            )
            answers = set(result.stdout.strip().splitlines()[-1].split(",")) if result.stdout.strip() else set()
            if result.returncode != 0 or answers != expected:
                raise RuntimeError("Native desktop input cleanup could not be verified")
            if query != "viewonly,client_count,pointer_mask":
                continue

            async def close_sessions():
                # Stock close suppresses errors and clears handles even on failure.
                # Use its native Playwright handles and loop, retaining failed state.
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    errors = []
                    for session in list(_browser._sessions.values()):
                        try:
                            if session.browser is None:
                                raise RuntimeError("Browser session has no verifiable native handle")
                            await session.browser.close()
                            if session.browser.is_connected():
                                raise RuntimeError("Browser remained connected after close")
                        except Exception as error:
                            errors.append(error)
                    if errors:
                        raise RuntimeError("Not all desktop browsers closed") from errors[0]
                    if _browser._playwright is not None:
                        await _browser._playwright.stop()
                    _browser._sessions.clear()
                    _browser._playwright = None
                    _browser._started = False

            _browser._execute_async(close_sessions())

        if time.monotonic() >= deadline:
            raise TimeoutError("Desktop cleanup deadline expired")
        with desktop_state() as state:
            if state.get("owner") != owner or state.get("epoch") != epoch:
                raise RuntimeError("Desktop ownership changed during release")
            state.update(owner=None, mode="agent", epoch=time.time_ns())
            state.pop("session_name", None)
    except BaseException as error:
        with desktop_state() as state:
            if state.get("owner") == owner:
                state["mode"] = "recovery"
        if isinstance(error, Exception):
            raise ApplicationError("Desktop release could not be verified; recovery required",
                                   non_retryable=True) from error
        raise
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


@activity.defn(name="desktop_control", no_thread_cancel_exception=True)
def desktop_control(action: str) -> dict:
    """Native control transfer on the same Linux worker as physical input."""
    if platform.system() != "Linux" or not os.environ.get("DISPLAY"):
        raise ApplicationError("Desktop control cannot execute on the host", non_retryable=True)
    if action not in {"take", "release", "prepare_resume", "resume", "status"}:
        raise ApplicationError("Unknown desktop control action", non_retryable=True)
    info = activity.info()
    if not info.namespace or not info.workflow_id:
        raise ApplicationError("Desktop control requires a workflow owner", non_retryable=True)
    owner = {"namespace": info.namespace, "workflow_id": info.workflow_id}
    if action == "status":
        # Atomic runtime replacement permits inspection without acquiring or
        # creating mutation locks, even while a native GUI action is running.
        fd = os.open(artifact_root() / "runtime.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ApplicationError("Invalid desktop state", non_retryable=True)
            state = json.loads(source.read(4097))
        if state.get("owner") != owner:
            raise ApplicationError("This workflow does not own the desktop", non_retryable=True)
        return state
    descriptors = []
    deadline = time.monotonic() + DESKTOP_HANDOFF_TIMEOUT.total_seconds()
    transfer_pending = False
    epoch = None
    try:
        # All lock users and all state writers are in Linux. Host/virtiofs flock
        # does not provide cross-kernel exclusion and must not protect transfers.
        for name in ("handoff.lock", "action.lock"):
            fd = os.open(artifact_root() / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            descriptors.append(fd)
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Desktop control did not settle")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.05)
        with desktop_state() as state:
            if state.get("owner") != owner:
                raise ApplicationError("This workflow does not own the desktop", non_retryable=True)
            epoch = state["epoch"]
            mode = state["mode"]
            allowed = {"take": {"agent", "stopping", "human"},
                       "release": {"human", "relinquishing", "instructions"},
                       "prepare_resume": {"instructions", "resuming"},
                       "resume": {"resuming", "agent"}}
            if mode not in allowed[action]:
                raise ApplicationError(f"Desktop cannot {action} while {mode}", non_retryable=True)
            if (action, mode) in {("take", "human"), ("release", "instructions"), ("resume", "agent")}:
                return dict(state)
            pending_mode = {"take": "stopping", "release": "relinquishing",
                            "prepare_resume": "resuming", "resume": "resuming"}[action]
            state["mode"] = pending_mode

        command = ["x11vnc", "-display", os.environ["DISPLAY"]]
        commands = [
            ([*command, "-R", DESKTOP_VNC_REVOKE_SCRIPT,
              "-Q", "viewonly,deny,client_count,pointer_mask"],
             {"ans=viewonly:1", "ans=deny:1", "aro=client_count:0", "aro=pointer_mask:0x0"}),
            ([*command, "-R", DESKTOP_VNC_GRANT_SCRIPT, "-Q", "viewonly,deny"] if action == "take"
             else DESKTOP_VNC_VIEW_COMMAND,
             {f"ans=viewonly:{0 if action == 'take' else 1}", "ans=deny:0"}),
        ]
        for command, expected in commands:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Desktop control deadline expired")
            with desktop_state() as state:
                if state.get("owner") != owner or state.get("epoch") != epoch or state.get("mode") != pending_mode:
                    raise RuntimeError("Desktop ownership changed during control transfer")
            # Cleanup receipts must pass before either input or viewer connections
            # reopen. Any ambiguous native attempt is rolled back under both locks.
            transfer_pending = True
            result = subprocess.run(
                shlex.split(command) if isinstance(command, str) else command,
                capture_output=True, text=True, timeout=min(DESKTOP_VNC_COMMAND_TIMEOUT, remaining),
            )
            answers = set(result.stdout.strip().splitlines()[-1].split(",")) if result.stdout.strip() else set()
            if result.returncode or answers != expected:
                raise RuntimeError("Native desktop transfer could not be verified")
        if time.monotonic() >= deadline:
            raise TimeoutError("Desktop control deadline expired")
        with desktop_state() as state:
            if state.get("owner") != owner or state.get("epoch") != epoch or state.get("mode") != pending_mode:
                raise RuntimeError("Desktop ownership changed during control transfer")
            state["mode"] = {"take": "human", "release": "instructions",
                             "prepare_resume": "resuming", "resume": "agent"}[action]
            if action == "release":
                state["epoch"] = time.time_ns()
            result = dict(state)
        transfer_pending = False
        return result
    except BaseException as error:
        if transfer_pending:
            try:
                # Roll back even if this transfer no longer owns the epoch.
                result = subprocess.run(commands[0][0], capture_output=True, text=True,
                                        timeout=DESKTOP_VNC_COMMAND_TIMEOUT)
                answers = set(result.stdout.strip().splitlines()[-1].split(",")) if result.stdout.strip() else set()
                if result.returncode or answers != commands[0][1]:
                    raise RuntimeError("Native desktop revocation could not be verified")
            except Exception as cleanup_error:
                error.add_note(f"Native revocation also failed: {cleanup_error}")
        if epoch is not None and not isinstance(error, ApplicationError):
            with desktop_state() as state:
                if state.get("owner") == owner:
                    state["mode"] = "recovery"
        if isinstance(error, Exception):
            raise ApplicationError(str(error), non_retryable=True) from error
        raise
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


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
