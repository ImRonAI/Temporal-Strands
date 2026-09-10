"""Native physical Linux input activities, sharing the browser worker/display.

Gemini's shipped action names and 0-999 coordinates remain its public contract.
Perplexity receives the same typed activity tools and full-display observations.
"""

from typing import Annotated, Any
import time

from temporalio import activity
from temporalio.exceptions import ApplicationError
from strands_tools.browser.models import BrowserInput

from browser_activity import _browser, capture_desktop, desktop_action, desktop_state

GEMINI_BROWSER_ACTIONS = {
    "click": "Left-click the Linux desktop at x/y (0-999 across the full screenshot).",
    "double_click": "Double-click at x/y (0-999).",
    "triple_click": "Triple-click at x/y (0-999).",
    "middle_click": "Middle-click at x/y (0-999).",
    "right_click": "Right-click at x/y (0-999).",
    "mouse_down": "Hold the left mouse button at x/y (0-999).",
    "mouse_up": "Release the left mouse button at x/y (0-999).",
    "move": "Move the desktop cursor to x/y (0-999).",
    "type": "Type text into the focused desktop application; optional x/y focuses and replaces a field.",
    "drag_and_drop": "Drag from start_x/start_y to end_x/end_y (0-999).",
    "wait": "Wait up to 5 seconds, then observe the desktop.",
    "press_key": "Press a desktop key (enter, tab, esc, etc).",
    "key_down": "Hold a desktop key.",
    "key_up": "Release a desktop key.",
    "hotkey": "Press a desktop key combination (for example ctrl+l).",
    "take_screenshot": "Observe full Linux desktop pixels, including browser chrome and other applications.",
    "scroll": "Scroll at x/y (0-999); direction up/down/left/right.",
    "go_back": "Navigate back in the shared browser.",
    "navigate": "Open a URL in the shared headed Linux browser.",
    "go_forward": "Navigate forward in the shared browser.",
}
LEGACY_ALIASES = {
    "click_at": "click", "hover_at": "move", "type_text_at": "type",
    "key_combination": "hotkey", "scroll_at": "scroll", "scroll_document": "scroll",
    "open_web_browser": "navigate", "wait_5_seconds": "wait",
}
COMPUTER_USE_TOOL_NAMES = frozenset({*GEMINI_BROWSER_ACTIONS, *LEGACY_ALIASES})


def denormalize(coord: int | float | None, size: int) -> int:
    if coord is None:
        return 0
    if not 0 <= coord <= 999:
        raise ValueError("Desktop coordinates must be between 0 and 999")
    return int(float(coord) / 1000 * size)


def execute_computer_use(action: str, args: dict[str, Any]) -> dict[str, Any]:
    canonical = LEGACY_ALIASES.get(action, action)
    with desktop_action() as epoch:
        # Imports stay inside the Linux activity, never on the credential host.
        import pyautogui
        from strands_tools.cursor import cursor

        width, height = pyautogui.size()
        x, y = denormalize(args.get("x"), width), denormalize(args.get("y"), height)
        key = str(args.get("key", "enter")).lower()
        key = {"escape": "esc", "control": "ctrl", "meta": "win"}.get(key, key)
        result = {"status": "success", "content": []}
        if canonical in {"navigate", "go_back", "go_forward"}:
            with desktop_state() as state:
                session = state.get("session_name")
                if not session:
                    session = "desktop-browser-session"
                    state["session_name"] = session
                    result = _browser.browser(browser_input=BrowserInput.model_validate({"action": {
                        "type": "init_session", "session_name": session,
                        "description": "Shared physical desktop browser",
                    }}))
            if result["status"] == "success":
                browser_args = {"type": {"go_back": "back", "go_forward": "forward"}.get(canonical, canonical),
                                "session_name": session}
                if canonical == "navigate":
                    browser_args["url"] = args.get("url", "about:blank")
                result = _browser.browser(browser_input=BrowserInput.model_validate({"action": browser_args}))
        elif canonical in {"click", "double_click", "triple_click", "middle_click", "right_click"}:
            result = cursor(action="click", x=x, y=y,
                            button={"middle_click": "middle", "right_click": "right"}.get(canonical, "left"),
                            clicks={"double_click": 2, "triple_click": 3}.get(canonical, 1))
        elif canonical == "move":
            result = cursor(action="move", x=x, y=y)
        elif canonical in {"mouse_down", "mouse_up"}:
            pyautogui.moveTo(x, y)
            (pyautogui.mouseDown if canonical == "mouse_down" else pyautogui.mouseUp)()
        elif canonical == "type":
            text = str(args.get("text", ""))
            if len(text) > 10_000:
                raise ValueError("Desktop typing is limited to 10000 characters per action")
            if args.get("x") is not None and args.get("y") is not None:
                pyautogui.click(x, y)
                pyautogui.hotkey("ctrl", "a")
            result = cursor(action="type_text", text=text)
            if args.get("press_enter"):
                pyautogui.press("enter")
        elif canonical == "drag_and_drop":
            pyautogui.moveTo(denormalize(args.get("start_x", args.get("x")), width),
                             denormalize(args.get("start_y", args.get("y")), height))
            result = cursor(action="drag", to_x=denormalize(args.get("end_x", args.get("destination_x")), width),
                            to_y=denormalize(args.get("end_y", args.get("destination_y")), height))
        elif canonical == "press_key":
            result = cursor(action="press_key", key=key)
        elif canonical in {"key_down", "key_up"}:
            (pyautogui.keyDown if canonical == "key_down" else pyautogui.keyUp)(key)
        elif canonical == "hotkey":
            keys = args.get("keys", args.get("key", []))
            if isinstance(keys, str):
                keys = keys.lower().replace("control", "ctrl").split("+")
            result = cursor(action="hotkey", keys=keys)
        elif canonical == "scroll":
            direction = args.get("direction", "down")
            amount = max(1, min(30, int(args.get("magnitude_in_pixels", args.get("magnitude", 300))) // 100))
            pyautogui.moveTo(x, y)
            if direction in {"left", "right"}:
                pyautogui.hscroll(amount if direction == "right" else -amount)
            else:
                result = cursor(action="scroll", amount=amount if direction == "up" else -amount)
        elif canonical == "wait":
            time.sleep(max(0, min(5, args.get("seconds", 5))))
        elif canonical != "take_screenshot":
            raise ValueError(f"Unsupported desktop action: {action}")
        if result.get("status") == "error":
            raise ApplicationError(str(result), non_retryable=True)
        return {**result, "action": canonical, "url": args.get("url", ""),
                "intent": args.get("intent", ""), "observation": capture_desktop(epoch)}


def _make_activity(name: str, description: str):
    def _impl(
        x: int | None = None, y: int | None = None,
        start_x: int | None = None, start_y: int | None = None,
        end_x: int | None = None, end_y: int | None = None,
        destination_x: int | None = None, destination_y: int | None = None,
        text: str | None = None, press_enter: bool | None = None,
        url: str | None = None, key: str | None = None,
        keys: list[str] | str | None = None, direction: str | None = None,
        magnitude: int | None = None, magnitude_in_pixels: int | None = None,
        seconds: int | None = None,
        intent: Annotated[str | None, "Brief explanation of this desktop action"] = None,
        safety_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return execute_computer_use(name, {k: v for k, v in locals().items() if v is not None and k != "name"})

    _impl.__name__ = _impl.__qualname__ = name
    _impl.__doc__ = description
    return activity.defn(name=name, no_thread_cancel_exception=True)(_impl)


COMPUTER_USE_ACTIVITIES = [
    _make_activity(name, description) for name, description in GEMINI_BROWSER_ACTIONS.items()
] + [_make_activity(alias, GEMINI_BROWSER_ACTIONS[name]) for alias, name in LEGACY_ALIASES.items()]
