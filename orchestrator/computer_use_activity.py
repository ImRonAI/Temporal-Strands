"""Gemini Computer Use actions, executed on LocalChromiumBrowser Playwright.

Google's Computer Use built-in tool emits function calls (``click``, ``type``,
``navigate``, …). This module registers Temporal activities with those exact
names so Strands can dispatch them. The Chromium runtime is
``strands_tools.browser.LocalChromiumBrowser``; coordinates are Gemini's
0–999 grid, scaled to the Playwright viewport.

https://ai.google.dev/gemini-api/docs/generate-content/computer-use
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any

from temporalio import activity
from temporalio.exceptions import ApplicationError
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import InitSessionAction

logger = logging.getLogger(__name__)

# Gemini 3.x ENVIRONMENT_BROWSER commands, plus legacy 2.5 names.
GEMINI_BROWSER_ACTIONS: dict[str, str] = {
    "click": "Left-click at a coordinate on the page. x and y are 0-999.",
    "double_click": "Double-click at a coordinate on the page. x and y are 0-999.",
    "triple_click": "Triple-click at a coordinate on the page. x and y are 0-999.",
    "middle_click": "Middle-click at a coordinate on the page. x and y are 0-999.",
    "right_click": "Right-click at a coordinate on the page. x and y are 0-999.",
    "mouse_down": "Press and hold the mouse button at a coordinate. x and y are 0-999.",
    "mouse_up": "Release the mouse button at a coordinate. x and y are 0-999.",
    "move": "Move the cursor to a coordinate. x and y are 0-999.",
    "type": "Type text. Optional press_enter submits.",
    "drag_and_drop": "Drag from start_x/start_y to end_x/end_y (0-999).",
    "wait": "Pause for a number of seconds.",
    "press_key": "Press and release a key.",
    "key_down": "Press and hold a key.",
    "key_up": "Release a key.",
    "hotkey": "Press a key combination.",
    "take_screenshot": "Capture the current page.",
    "scroll": "Scroll at a coordinate. direction is up/down/left/right.",
    "go_back": "Navigate back in history.",
    "navigate": "Navigate directly to a URL.",
    "go_forward": "Navigate forward in history.",
}

LEGACY_ALIASES: dict[str, str] = {
    "click_at": "click",
    "hover_at": "move",
    "type_text_at": "type",
    "key_combination": "hotkey",
    "scroll_at": "scroll",
    "scroll_document": "scroll",
    "open_web_browser": "navigate",
    "wait_5_seconds": "wait",
}

COMPUTER_USE_TOOL_NAMES: frozenset[str] = frozenset(
    {*GEMINI_BROWSER_ACTIONS, *LEGACY_ALIASES}
)

# Chromium remote-debugging HTTP origin. Playwright connect_over_cdp and the
# CDP HTTP endpoints use this URL:
# https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp
# https://chromedevtools.github.io/devtools-protocol/
_DEFAULT_CDP_PORT = 9222

_browser: LocalChromiumBrowser | None = None
_pw_lock = threading.Lock()


def _viewport() -> tuple[int, int]:
    # Google's Computer Use Playwright example uses 1440×900
    # (https://ai.google.dev/gemini-api/docs/generate-content/computer-use).
    width = int(os.getenv("STRANDS_BROWSER_WIDTH", "1440"))
    height = int(os.getenv("STRANDS_BROWSER_HEIGHT", "900"))
    return width, height


def _cdp_port() -> int:
    return int(os.getenv("COMPUTER_USE_CDP_PORT", str(_DEFAULT_CDP_PORT)))


def _cdp_origin() -> str:
    return f"http://localhost:{_cdp_port()}"


def _headless() -> bool:
    # Headless keeps Chromium off-screen; WebPreview iframes the CDP inspector URL.
    return os.getenv("STRANDS_BROWSER_HEADLESS", "true").lower() != "false"


def _web_origin() -> str:
    return os.getenv("COMPUTER_USE_WEB_ORIGIN", "http://localhost:3000")


def _remote_allow_origins() -> str:
    """Origins allowed to attach to the CDP WebSocket (iframe + local UI)."""
    configured = os.getenv("COMPUTER_USE_REMOTE_ALLOW_ORIGINS")
    if configured:
        return configured
    return f"{_cdp_origin()},{_web_origin()},*"


def _cdp_page_target(page_url: str) -> dict[str, Any] | None:
    """Page target from Chromium ``/json/list`` for the active Playwright tab."""
    origin = _cdp_origin()
    try:
        with urllib.request.urlopen(f"{origin}/json/list", timeout=2) as response:
            targets = json.loads(response.read().decode())
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("type") != "page":
            continue
        if page_url and target.get("url") != page_url:
            continue
        return target
    return None


def _devtools_frontend_url(page_url: str) -> str:
    """Bundled Chromium DevTools inspector for the remote-debugging page target."""
    origin = _cdp_origin()
    target = _cdp_page_target(page_url)
    if not target:
        return origin

    frontend = target.get("devtoolsFrontendUrl")
    if isinstance(frontend, str) and frontend.startswith("/"):
        return f"{origin}{frontend}"

    ws = target.get("webSocketDebuggerUrl")
    if isinstance(ws, str) and ws.startswith("ws://"):
        ws_path = ws[len("ws://") :]
        query = urllib.parse.urlencode({"ws": ws_path})
        return f"{origin}/devtools/inspector.html?{query}"

    return origin


def _live_preview_url(page_url: str) -> str:
    """Viewport-only CDP screencast for WebPreview (no DevTools console)."""
    target = _cdp_page_target(page_url)
    if not target:
        return ""
    ws = target.get("webSocketDebuggerUrl")
    if not isinstance(ws, str) or not ws.startswith("ws://"):
        return ""
    query = urllib.parse.urlencode({"ws": ws})
    return f"{_web_origin()}/computer-use-live.html?{query}"


def denormalize(coord: int | float | None, size: int) -> int:
    """Official generateContent denormalize: ``int(x / 1000 * screen)``.

    https://ai.google.dev/gemini-api/docs/generate-content/computer-use
    """
    if coord is None:
        return 0
    return int(float(coord) / 1000 * size)


def _session_name() -> str:
    info = activity.info()
    raw = (info.workflow_id or "computer-use").lower()
    cleaned = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in raw)
    cleaned = cleaned.strip("-") or "computer-use"
    if len(cleaned) < 10:
        cleaned = f"{cleaned}-session"
    if len(cleaned) > 36:
        digest = hashlib.sha1(cleaned.encode()).hexdigest()[:32]
        cleaned = f"cu-{digest}"
    return cleaned


def _get_browser() -> LocalChromiumBrowser:
    global _browser
    if _browser is None:
        width, height = _viewport()
        port = _cdp_port()
        _browser = LocalChromiumBrowser(
            launch_options={
                "headless": _headless(),
                "args": [
                    f"--window-size={width},{height}",
                    f"--remote-debugging-port={port}",
                    f"--remote-allow-origins={_remote_allow_origins()}",
                ],
            },
            context_options={"viewport": {"width": width, "height": height}},
        )
        # Official @tool browser() auto-starts Playwright on first use.
        # Direct init_session skips that path.
        _browser._start()
    return _browser


def _ensure_session(browser: LocalChromiumBrowser, session_name: str) -> None:
    if session_name in browser._sessions:
        return
    result = browser.init_session(
        InitSessionAction(
            type="init_session",
            description="Gemini Computer Use session",
            session_name=session_name,
        )
    )
    if result.get("status") == "error":
        # Already-exists is recoverable; anything else is fatal for the action.
        text = ""
        content = result.get("content") or []
        if content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        if "already exists" not in text.lower():
            raise ApplicationError(
                text or "Failed to start Computer Use browser session",
                type="ComputerUseError",
                non_retryable=False,
            )


def capture_page_png_unlocked() -> tuple[bytes | None, str]:
    """Playwright capture. Caller must hold ``_pw_lock``."""
    browser = _browser
    if browser is None or not hasattr(browser, "_execute_async"):
        return None, ""

    async def _run() -> tuple[bytes | None, str]:
        for name in list(browser._sessions):
            page = browser.get_session_page(name)
            if page is None:
                continue
            png = await page.screenshot(type="png")
            return png, page.url
        return None, ""

    try:
        return browser._execute_async(_run())
    except Exception:
        logger.exception("Computer Use page capture failed")
        return None, ""


def capture_page_png() -> tuple[bytes | None, str]:
    """Current Playwright page image and URL for generateContent FunctionResponse.

    Official Computer Use loop: after each action, capture the page and send
    it back as a function_result image.
    https://ai.google.dev/gemini-api/docs/generate-content/computer-use
    """
    if not _pw_lock.acquire(timeout=5):
        return None, ""
    try:
        return capture_page_png_unlocked()
    finally:
        _pw_lock.release()


def execute_computer_use(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one Gemini Computer Use command against the workflow's Chromium page."""
    canonical = LEGACY_ALIASES.get(action, action)
    browser = _get_browser()
    session_name = _session_name()
    _ensure_session(browser, session_name)
    width, height = _viewport()

    async def _run() -> dict[str, Any]:
        page = browser.get_session_page(session_name)
        if page is None:
            return {
                "status": "error",
                "content": [{"text": "No active Computer Use page"}],
            }

        x = denormalize(args.get("x"), width)
        y = denormalize(args.get("y"), height)
        button = "left"
        click_count = 1
        if canonical == "right_click":
            button = "right"
        elif canonical == "middle_click":
            button = "middle"
        elif canonical == "double_click":
            click_count = 2
        elif canonical == "triple_click":
            click_count = 3

        if canonical in {
            "click",
            "double_click",
            "triple_click",
            "middle_click",
            "right_click",
        }:
            await page.mouse.click(x, y, button=button, click_count=click_count)
        elif canonical == "mouse_down":
            await page.mouse.move(x, y)
            await page.mouse.down()
        elif canonical == "mouse_up":
            await page.mouse.move(x, y)
            await page.mouse.up()
        elif canonical == "move":
            await page.mouse.move(x, y)
        elif canonical == "type":
            # Official execute_function_calls type / type_text_at:
            # click to focus when x/y are present, then Meta+A, Backspace, type.
            text = str(args.get("text") or "")
            if "x" in args and "y" in args:
                await page.mouse.click(x, y)
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(text)
            if args.get("press_enter"):
                await page.keyboard.press("Enter")
        elif canonical == "drag_and_drop":
            start_x = denormalize(args.get("start_x", args.get("x")), width)
            start_y = denormalize(args.get("start_y", args.get("y")), height)
            end_x = denormalize(args.get("end_x", args.get("destination_x")), width)
            end_y = denormalize(args.get("end_y", args.get("destination_y")), height)
            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            await page.mouse.move(end_x, end_y)
            await page.mouse.up()
        elif canonical == "wait":
            seconds = args.get("seconds", 5 if action == "wait_5_seconds" else 1)
            await page.wait_for_timeout(int(float(seconds) * 1000))
        elif canonical == "press_key":
            await page.keyboard.press(str(args.get("key") or "Enter"))
        elif canonical == "key_down":
            await page.keyboard.down(str(args.get("key") or "Shift"))
        elif canonical == "key_up":
            await page.keyboard.up(str(args.get("key") or "Shift"))
        elif canonical == "hotkey":
            keys = args.get("keys") or args.get("key") or []
            if isinstance(keys, str):
                combo = keys.replace("+", "+")
                await page.keyboard.press(combo)
            elif isinstance(keys, list) and keys:
                await page.keyboard.press("+".join(str(k) for k in keys))
        elif canonical == "scroll":
            direction = str(args.get("direction") or "down").lower()
            magnitude = int(args.get("magnitude_in_pixels") or args.get("magnitude") or 300)
            dx = dy = 0
            if direction == "down":
                dy = magnitude
            elif direction == "up":
                dy = -magnitude
            elif direction == "right":
                dx = magnitude
            elif direction == "left":
                dx = -magnitude
            await page.mouse.move(x, y)
            await page.mouse.wheel(dx, dy)
        elif canonical == "navigate":
            url = str(args.get("url") or "about:blank")
            await page.goto(url, wait_until="domcontentloaded")
        elif canonical == "go_back":
            await page.go_back()
        elif canonical == "go_forward":
            await page.go_forward()
        elif canonical == "take_screenshot":
            pass
        else:
            return {
                "status": "error",
                "content": [{"text": f"Unsupported Computer Use action: {action}"}],
            }

        # Official execute_function_calls: wait for load after every action.
        await page.wait_for_load_state(timeout=5000)

        payload: dict[str, Any] = {
            "action": canonical,
            "url": page.url,
            "intent": args.get("intent"),
            "livePreviewUrl": _live_preview_url(page.url),
            "devtoolsFrontendUrl": _devtools_frontend_url(page.url),
        }
        if args.get("safety_decision"):
            # Official generateContent: after user confirms, acknowledge.
            payload["safety_acknowledgement"] = True
        return {
            "status": "success",
            "content": [{"text": json.dumps(payload)}],
        }

    try:
        with _pw_lock:
            return browser._execute_async(_run())
    except ApplicationError:
        raise
    except Exception as error:
        logger.exception("Computer Use action %s failed", action)
        raise ApplicationError(
            f"Computer Use {action} failed: {error}",
            type="ComputerUseError",
            non_retryable=False,
        ) from error


def _make_activity(name: str, description: str):
    async def _impl(
        x: int | None = None,
        y: int | None = None,
        start_x: int | None = None,
        start_y: int | None = None,
        end_x: int | None = None,
        end_y: int | None = None,
        destination_x: int | None = None,
        destination_y: int | None = None,
        text: str | None = None,
        press_enter: bool | None = None,
        url: str | None = None,
        key: str | None = None,
        keys: list[str] | str | None = None,
        direction: str | None = None,
        magnitude: int | None = None,
        magnitude_in_pixels: int | None = None,
        seconds: int | None = None,
        intent: Annotated[
            str | None,
            "Brief natural-language explanation of why this browser action is being taken",
        ] = None,
        safety_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = {
            "x": x,
            "y": y,
            "start_x": start_x,
            "start_y": start_y,
            "end_x": end_x,
            "end_y": end_y,
            "destination_x": destination_x,
            "destination_y": destination_y,
            "text": text,
            "press_enter": press_enter,
            "url": url,
            "key": key,
            "keys": keys,
            "direction": direction,
            "magnitude": magnitude,
            "magnitude_in_pixels": magnitude_in_pixels,
            "seconds": seconds,
            "intent": intent,
            "safety_decision": safety_decision,
        }
        return execute_computer_use(
            name, {key: value for key, value in args.items() if value is not None}
        )

    _impl.__name__ = name
    _impl.__doc__ = description
    _impl.__qualname__ = name
    return activity.defn(name=name)(_impl)


COMPUTER_USE_ACTIVITIES: list[Any] = [
    _make_activity(name, description)
    for name, description in GEMINI_BROWSER_ACTIONS.items()
] + [
    _make_activity(alias, GEMINI_BROWSER_ACTIONS[canonical])
    for alias, canonical in LEGACY_ALIASES.items()
]
