"""Computer Use activity helpers: Gemini 0–999 grid and action dispatch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from computer_use_activity import (
    COMPUTER_USE_TOOL_NAMES,
    LEGACY_ALIASES,
    denormalize,
    execute_computer_use,
)
import json


def test_denormalize_matches_official_generate_content_formula() -> None:
    # Official Python tab: int(x / 1000 * screen_width)
    # https://ai.google.dev/gemini-api/docs/generate-content/computer-use
    assert denormalize(0, 1280) == 0
    assert denormalize(999, 1280) == int(999 / 1000 * 1280)
    assert denormalize(None, 800) == 0


def test_legacy_aliases_and_tool_names_cover_browser_actions() -> None:
    assert LEGACY_ALIASES["click_at"] == "click"
    assert LEGACY_ALIASES["type_text_at"] == "type"
    assert "click" in COMPUTER_USE_TOOL_NAMES
    assert "click_at" in COMPUTER_USE_TOOL_NAMES
    assert "navigate" in COMPUTER_USE_TOOL_NAMES


def test_get_browser_starts_playwright(monkeypatch) -> None:
    import computer_use_activity as cua

    created: dict = {}

    class FakeBrowser:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs

        def _start(self):
            created["started"] = True

    monkeypatch.setattr(cua, "_browser", None)
    monkeypatch.setattr(cua, "LocalChromiumBrowser", FakeBrowser)
    browser = cua._get_browser()
    assert created["started"] is True
    assert created["kwargs"]["launch_options"]["headless"] is True
    assert created["kwargs"]["launch_options"]["args"] == [
        "--window-size=1440,900",
        "--remote-debugging-port=9222",
        "--remote-allow-origins=http://localhost:9222,http://localhost:3000,*",
    ]
    assert created["kwargs"]["context_options"]["viewport"] == {"width": 1440, "height": 900}
    assert browser is cua._get_browser()


def test_ensure_session_sends_official_init_session_action(monkeypatch) -> None:
    from computer_use_activity import _ensure_session
    from strands_tools.browser.models import InitSessionAction

    captured: dict = {}
    browser = MagicMock()
    browser._sessions = {}

    def init_session(action):
        captured["action"] = action
        return {"status": "success", "content": []}

    browser.init_session.side_effect = init_session
    _ensure_session(browser, "cu-session-1")
    action = captured["action"]
    assert isinstance(action, InitSessionAction)
    assert action.type == "init_session"
    assert action.session_name == "cu-session-1"


def _page_browser(monkeypatch, page) -> None:
    browser = MagicMock()
    browser._sessions = {"cu-session": object()}
    browser.get_session_page.return_value = page
    browser._execute_async.side_effect = asyncio.run
    monkeypatch.setattr("computer_use_activity._get_browser", lambda: browser)
    monkeypatch.setattr("computer_use_activity._session_name", lambda: "cu-session")
    monkeypatch.setattr("computer_use_activity._viewport", lambda: (1000, 1000))
    monkeypatch.setattr(
        "computer_use_activity._devtools_frontend_url",
        lambda _url: (
            "http://localhost:9222/devtools/inspector.html"
            "?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC"
        ),
    )
    monkeypatch.setattr(
        "computer_use_activity._live_preview_url",
        lambda _url: (
            "http://localhost:3000/computer-use-live.html"
            "?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC"
        ),
    )


def test_execute_click_uses_denormalized_coordinates(monkeypatch) -> None:
    page = AsyncMock()
    page.url = "https://example.com"
    page.mouse = AsyncMock()
    _page_browser(monkeypatch, page)

    result = execute_computer_use("click", {"x": 0, "y": 999, "intent": "Open the result"})

    page.mouse.click.assert_awaited_once_with(0, 999, button="left", click_count=1)
    page.wait_for_load_state.assert_awaited_once_with(timeout=5000)
    payload = json.loads(result["content"][0]["text"])
    assert payload == {
        "action": "click",
        "url": "https://example.com",
        "intent": "Open the result",
        "livePreviewUrl": (
            "http://localhost:3000/computer-use-live.html"
            "?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC"
        ),
        "devtoolsFrontendUrl": (
            "http://localhost:9222/devtools/inspector.html"
            "?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC"
        ),
    }
    assert result["status"] == "success"
    assert len(result["content"]) == 1


def test_type_follows_official_execute_function_calls(monkeypatch) -> None:
    page = AsyncMock()
    page.url = "https://example.com"
    page.mouse = AsyncMock()
    page.keyboard = AsyncMock()
    _page_browser(monkeypatch, page)

    result = execute_computer_use(
        "type", {"x": 400, "y": 250, "text": "search", "press_enter": False}
    )

    page.mouse.click.assert_awaited_once_with(400, 250)
    assert [call.args[0] for call in page.keyboard.press.await_args_list] == [
        "Meta+A",
        "Backspace",
    ]
    page.keyboard.type.assert_awaited_once_with("search")
    page.wait_for_load_state.assert_awaited_once_with(timeout=5000)
    assert result["status"] == "success"


def test_take_screenshot_does_not_put_pixels_in_temporal_payload(monkeypatch) -> None:
    page = AsyncMock()
    page.url = "https://example.com"
    _page_browser(monkeypatch, page)

    result = execute_computer_use("take_screenshot", {"intent": "See the form"})

    payload = json.loads(result["content"][0]["text"])
    assert payload == {
        "action": "take_screenshot",
        "url": "https://example.com",
        "intent": "See the form",
        "livePreviewUrl": (
            "http://localhost:3000/computer-use-live.html"
            "?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC"
        ),
        "devtoolsFrontendUrl": (
            "http://localhost:9222/devtools/inspector.html"
            "?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC"
        ),
    }
    assert "screenshot" not in payload
    assert len(result["content"]) == 1


def test_live_preview_url_uses_screencast_page(monkeypatch) -> None:
    import computer_use_activity as cua

    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps(
                [
                    {
                        "id": "ABC",
                        "title": "Example",
                        "type": "page",
                        "url": "https://example.com/",
                        "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/ABC",
                    }
                ]
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: FakeResponse()
    )
    assert cua._live_preview_url("https://example.com/") == (
        "http://localhost:3000/computer-use-live.html"
        "?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC"
    )


def test_devtools_frontend_url_uses_official_json_list(monkeypatch) -> None:
    import computer_use_activity as cua

    class FakeResponse:
        def read(self) -> bytes:
            # Exact /json/list shape from
            # https://chromedevtools.github.io/devtools-protocol/
            return json.dumps(
                [
                    {
                        "description": "",
                        "devtoolsFrontendUrl": (
                            "/devtools/inspector.html"
                            "?ws=localhost:9222/devtools/page/DAB7FB6187B554E10B0BD18821265734"
                        ),
                        "id": "DAB7FB6187B554E10B0BD18821265734",
                        "title": "Yahoo",
                        "type": "page",
                        "url": "https://www.yahoo.com/",
                        "webSocketDebuggerUrl": (
                            "ws://localhost:9222/devtools/page/"
                            "DAB7FB6187B554E10B0BD18821265734"
                        ),
                    }
                ]
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: FakeResponse()
    )
    assert cua._devtools_frontend_url("https://www.yahoo.com/") == (
        "http://localhost:9222/devtools/inspector.html"
        "?ws=localhost:9222/devtools/page/DAB7FB6187B554E10B0BD18821265734"
    )


def test_devtools_frontend_url_uses_local_inspector_from_websocket(monkeypatch) -> None:
    import computer_use_activity as cua

    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps(
                [
                    {
                        "devtoolsFrontendUrl": (
                            "https://chrome-devtools-frontend.appspot.com/serve_rev/@abc/"
                            "inspector.html?ws=localhost:9222/devtools/page/ABC"
                        ),
                        "id": "ABC",
                        "type": "page",
                        "url": "https://example.com/",
                        "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/ABC",
                    }
                ]
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: FakeResponse()
    )
    assert cua._devtools_frontend_url("https://example.com/") == (
        "http://localhost:9222/devtools/inspector.html"
        "?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC"
    )
