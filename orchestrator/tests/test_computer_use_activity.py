"""Physical desktop action contracts, with no host input or browser execution."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError

import browser_activity as browser
import computer_use_activity as cua


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser.platform, "system", lambda: "Linux")
    monkeypatch.setattr(browser.activity, "info", lambda: SimpleNamespace(
        namespace="test", workflow_id="desktop-owner", activity_id="click-1",
    ))
    browser.initialize_desktop()
    # Import substitutes are installed before the activity's lazy imports. Even
    # importing the real PyAutoGUI module could connect to the host display.
    gui = MagicMock()
    gui.size.return_value = (1440, 900)
    monkeypatch.setitem(sys.modules, "pyautogui", gui)
    cursor_module = ModuleType("strands_tools.cursor")
    cursor_module.cursor = MagicMock(return_value={"status": "success", "content": []})
    monkeypatch.setitem(sys.modules, "strands_tools.cursor", cursor_module)
    stock = MagicMock()
    stock.browser.return_value = {"status": "success", "content": []}
    monkeypatch.setattr(cua, "_browser", stock)
    observation = {"artifact_id": "test-artifact", "desktop_epoch": 123}
    capture = MagicMock(return_value=observation)
    monkeypatch.setattr(cua, "capture_desktop", capture)
    sleep = MagicMock()
    monkeypatch.setattr(cua.time, "sleep", sleep)
    return SimpleNamespace(gui=gui, cursor=cursor_module.cursor, browser=stock,
                           capture=capture, observation=observation, sleep=sleep)


@pytest.mark.parametrize("coord,size,expected", [
    (0, 1440, 0), (999, 1440, 1438), (999, 900, 899),
    (500, 1440, 720), (125.5, 1000, 125), (None, 900, 0),
])
def test_denormalize_matches_official_generate_content_formula(coord, size, expected):
    # https://ai.google.dev/gemini-api/docs/generate-content/computer-use
    assert cua.denormalize(coord, size) == expected


@pytest.mark.parametrize("coord", [-1, 1000, float("nan"), float("inf")])
def test_denormalize_rejects_coordinates_outside_the_shipped_grid(coord):
    with pytest.raises(ValueError, match="between 0 and 999"):
        cua.denormalize(coord, 1440)


def test_shipped_names_and_legacy_aliases_remain_native_activities():
    canonical = {
        "click", "double_click", "triple_click", "middle_click", "right_click",
        "mouse_down", "mouse_up", "move", "type", "drag_and_drop", "wait",
        "press_key", "key_down", "key_up", "hotkey", "take_screenshot", "scroll",
        "go_back", "navigate", "go_forward",
    }
    aliases = {
        "click_at": "click", "hover_at": "move", "type_text_at": "type",
        "key_combination": "hotkey", "scroll_at": "scroll", "scroll_document": "scroll",
        "open_web_browser": "navigate", "wait_5_seconds": "wait",
    }
    assert cua.LEGACY_ALIASES == aliases
    assert cua.COMPUTER_USE_TOOL_NAMES == canonical | aliases.keys()
    definitions = [activity._Definition.must_from_callable(fn) for fn in cua.COMPUTER_USE_ACTIVITIES]
    assert {definition.name for definition in definitions} == cua.COMPUTER_USE_TOOL_NAMES
    assert len(definitions) == len(cua.COMPUTER_USE_TOOL_NAMES)
    assert all(definition.no_thread_cancel_exception for definition in definitions)


@pytest.mark.parametrize("action,button,clicks", [
    ("click", "left", 1), ("click_at", "left", 1), ("double_click", "left", 2),
    ("triple_click", "left", 3), ("middle_click", "middle", 1), ("right_click", "right", 1),
])
def test_click_uses_stock_cursor_and_full_display_coordinates(desktop, action, button, clicks):
    result = cua.execute_computer_use(action, {"x": 500, "y": 999, "intent": "Open editor"})

    desktop.cursor.assert_called_once_with(action="click", x=720, y=899, button=button, clicks=clicks)
    desktop.browser.browser.assert_not_called()
    with browser.desktop_state() as state:
        desktop.capture.assert_called_once_with(state["epoch"])
    assert result == {"status": "success", "content": [], "action": cua.LEGACY_ALIASES.get(action, action),
                      "url": "", "intent": "Open editor", "observation": desktop.observation}


@pytest.mark.parametrize("action", ["move", "hover_at"])
def test_move_uses_stock_cursor(desktop, action):
    cua.execute_computer_use(action, {"x": 0, "y": 500})
    desktop.cursor.assert_called_once_with(action="move", x=0, y=450)


@pytest.mark.parametrize("action,method", [("mouse_down", "mouseDown"), ("mouse_up", "mouseUp")])
def test_held_mouse_input_is_physical(desktop, action, method):
    cua.execute_computer_use(action, {"x": 250, "y": 500})
    desktop.gui.moveTo.assert_called_once_with(360, 450)
    getattr(desktop.gui, method).assert_called_once_with()
    desktop.cursor.assert_not_called()


@pytest.mark.parametrize("action", ["type", "type_text_at"])
def test_type_focuses_and_replaces_with_linux_shortcut(desktop, action):
    cua.execute_computer_use(action, {"x": 500, "y": 250, "text": "search", "press_enter": True})
    assert desktop.gui.method_calls == [call.size(), call.click(720, 225),
                                       call.hotkey("ctrl", "a"), call.press("enter")]
    desktop.cursor.assert_called_once_with(action="type_text", text="search")


def test_type_without_coordinates_preserves_focused_desktop_application(desktop):
    cua.execute_computer_use("type", {"text": "note", "press_enter": False})
    desktop.cursor.assert_called_once_with(action="type_text", text="note")
    assert desktop.gui.method_calls == [call.size()]
    desktop.browser.browser.assert_not_called()


@pytest.mark.parametrize("args", [
    {"start_x": 100, "start_y": 200, "end_x": 500, "end_y": 999},
    {"x": 100, "y": 200, "destination_x": 500, "destination_y": 999},
])
def test_drag_accepts_shipped_coordinate_names(desktop, args):
    cua.execute_computer_use("drag_and_drop", args)
    desktop.gui.moveTo.assert_called_once_with(144, 180)
    desktop.cursor.assert_called_once_with(action="drag", to_x=720, to_y=899)


@pytest.mark.parametrize("key,expected", [("Escape", "esc"), ("Control", "ctrl"), ("Meta", "win")])
@pytest.mark.parametrize("action", ["press_key", "key_down", "key_up"])
def test_keyboard_names_map_to_linux_keys(desktop, action, key, expected):
    cua.execute_computer_use(action, {"key": key})
    if action == "press_key":
        desktop.cursor.assert_called_once_with(action="press_key", key=expected)
    else:
        getattr(desktop.gui, "keyDown" if action == "key_down" else "keyUp").assert_called_once_with(expected)


@pytest.mark.parametrize("action,args", [
    ("hotkey", {"keys": ["ctrl", "l"]}),
    ("key_combination", {"keys": "Control+L"}),
    ("hotkey", {"key": "CTRL+L"}),
])
def test_hotkey_preserves_list_and_legacy_string_inputs(desktop, action, args):
    cua.execute_computer_use(action, args)
    desktop.cursor.assert_called_once_with(action="hotkey", keys=["ctrl", "l"])


@pytest.mark.parametrize("direction,expected", [("up", 3), ("down", -3), ("left", -3), ("right", 3)])
def test_scroll_dispatches_physical_vertical_and_horizontal_input(desktop, direction, expected):
    cua.execute_computer_use("scroll_at", {"direction": direction, "x": 500, "y": 500, "magnitude_in_pixels": 300})
    desktop.gui.moveTo.assert_called_once_with(720, 450)
    if direction in {"left", "right"}:
        desktop.gui.hscroll.assert_called_once_with(expected)
        desktop.cursor.assert_not_called()
    else:
        desktop.cursor.assert_called_once_with(action="scroll", amount=expected)


@pytest.mark.parametrize("magnitude,expected", [(0, -1), (100_000, -30)])
def test_scroll_magnitude_is_bounded(desktop, magnitude, expected):
    cua.execute_computer_use("scroll_document", {"magnitude": magnitude})
    desktop.cursor.assert_called_once_with(action="scroll", amount=expected)


@pytest.mark.parametrize("action,args,seconds", [
    ("wait_5_seconds", {}, 5), ("wait", {"seconds": -2}, 0), ("wait", {"seconds": 100}, 5),
])
def test_wait_is_bounded_and_observes_after_waiting(desktop, action, args, seconds):
    desktop.sleep.side_effect = lambda _: desktop.capture.assert_not_called()
    result = cua.execute_computer_use(action, args)
    desktop.sleep.assert_called_once_with(seconds)
    assert result["observation"] == desktop.observation


def test_screenshot_returns_observation_without_host_browser_or_input(desktop):
    result = cua.execute_computer_use("take_screenshot", {"intent": "See the editor"})
    desktop.cursor.assert_not_called()
    desktop.browser.browser.assert_not_called()
    assert desktop.gui.method_calls == [call.size()]
    assert result["observation"] == desktop.observation
    assert not {"screenshot", "livePreviewUrl", "devtoolsFrontendUrl"} & result.keys()


@pytest.mark.parametrize("action,expected", [
    ("navigate", "navigate"), ("open_web_browser", "navigate"),
    ("go_back", "back"), ("go_forward", "forward"),
])
def test_navigation_reuses_the_shared_browser_session(desktop, action, expected):
    with browser.desktop_state() as state:
        state["session_name"] = "shared-browser-session"
    cua.execute_computer_use(action, {"url": "https://example.com/"})
    request = desktop.browser.browser.call_args.kwargs["browser_input"]
    assert request.action.type == expected
    assert request.action.session_name == "shared-browser-session"
    desktop.browser.browser.assert_called_once()
    desktop.cursor.assert_not_called()


def test_navigation_initializes_stock_session_only_once(desktop):
    cua.execute_computer_use("navigate", {"url": "https://example.com/"})
    cua.execute_computer_use("navigate", {"url": "https://example.com/next"})
    actions = [item.kwargs["browser_input"].action for item in desktop.browser.browser.call_args_list]
    assert [action.type for action in actions] == ["init_session", "navigate", "navigate"]
    assert len({action.session_name for action in actions}) == 1
    assert actions[-1].url == "https://example.com/next"


@pytest.mark.parametrize("action,args,match", [
    ("click", {"x": 1000, "y": 0}, "coordinates"),
    ("type", {"text": "x" * 10_001}, "10000"),
    ("unsupported", {}, "Unsupported desktop action"),
])
def test_invalid_input_never_reaches_physical_input(desktop, action, args, match):
    with pytest.raises(ValueError, match=match):
        cua.execute_computer_use(action, args)
    desktop.cursor.assert_not_called()
    desktop.browser.browser.assert_not_called()
    desktop.capture.assert_not_called()
    assert desktop.gui.method_calls == [call.size()]


@pytest.mark.parametrize("action,backend", [("click", "cursor"), ("navigate", "browser")])
def test_stock_tool_errors_are_non_retryable_and_fence_following_actions(desktop, action, backend):
    target = desktop.cursor if backend == "cursor" else desktop.browser.browser
    target.return_value = {"status": "error", "content": [{"text": "uncertain native input"}]}
    with pytest.raises(ApplicationError, match="uncertain native input") as error:
        cua.execute_computer_use(action, {})
    assert error.value.non_retryable
    desktop.capture.assert_not_called()
    with browser.desktop_state() as state:
        assert state["mode"] == "recovery"
    with pytest.raises(ApplicationError, match="owned, paused, or requires recovery"):
        cua.execute_computer_use("click", {})
    target.assert_called_once()


def test_native_activity_signature_filters_only_none_and_preserves_false_and_zero(monkeypatch):
    execute = MagicMock(return_value={"status": "success"})
    monkeypatch.setattr(cua, "execute_computer_use", execute)
    for fn in cua.COMPUTER_USE_ACTIVITIES:
        result = fn(x=0, press_enter=False, text="", intent="explain", safety_decision={})
        execute.assert_called_with(fn.__name__, {
            "x": 0, "press_enter": False, "text": "", "intent": "explain", "safety_decision": {},
        })
        assert result == {"status": "success"}


def test_computer_tool_uses_the_same_browser_and_ownership_fence():
    assert cua._browser is browser._browser
    assert cua.desktop_action is browser.desktop_action
    assert cua.desktop_state is browser.desktop_state
    assert cua.capture_desktop is browser.capture_desktop
