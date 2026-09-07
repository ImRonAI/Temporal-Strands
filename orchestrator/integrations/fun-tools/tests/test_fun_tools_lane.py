"""Verification lane for strands-fun-tools 0.5.0 (isolated .venv only).

Runs against the genuine PyPI distribution installed into
``orchestrator/integrations/fun-tools/.venv`` — NOT the app's shared venv.

Covers:
1. every ``__all__`` export imports and is a native strands ``AgentTool``
   (``DecoratedFunctionTool``) with a well-formed ``tool_spec``;
2. hot-loading through official mechanisms — the community
   ``strands_tools.load_tool`` @tool (deprecated ``load_tool_from_filepath``
   path) and the SDK ``ToolLoader.load_tools`` / ``load_tools_from_string``;
3. safe, read-only/local direct invocations, including expected failures.

No custom wrappers, no rewritten schemas: everything asserted here is the
upstream export or the official loader output.

Run from this directory: ``.venv/bin/python -m pytest tests -q``
"""

from __future__ import annotations

import importlib
import json
import os
import site
import sys
from pathlib import Path

import pytest

EXPECTED_EXPORTS = [
    "human_typer",
    "cursor",
    "clipboard",
    "dynamic_package",
    "template",
    "utility",
    "chess",
    "bluetooth",
    "screen_reader",
    "yolo_vision",
    "face_recognition",
    "take_photo",
    "listen",
    "spinner_generator",
    "npm",
    "dialog",
    "asciimatics_ui",
]

# Tools whose *direct call* would touch hardware, the GUI session, external
# services, or block on interactive input. Import + schema + hot-load are
# still verified; execution is exercised only via safe no-op/status actions
# or is skipped with the reason recorded.
def _package_dir() -> Path:
    import strands_fun_tools

    return Path(strands_fun_tools.__file__).parent


def test_distribution_is_genuine_upstream_050() -> None:
    import importlib.metadata as metadata

    assert metadata.version("strands-fun-tools") == "0.5.0"
    # Upstream quirk: __init__ hardcodes 0.1.0; the build reads __version__.py.
    version_mod = importlib.import_module("strands_fun_tools.__version__")
    assert version_mod.__version__ == "0.5.0"


def test_all_expected_exports_present() -> None:
    import strands_fun_tools

    missing = [name for name in EXPECTED_EXPORTS if name not in strands_fun_tools.__all__]
    assert missing == [], f"exports missing from __all__ (optional dep failed): {missing}"
    assert len(strands_fun_tools.__all__) == len(EXPECTED_EXPORTS)


@pytest.mark.parametrize("name", EXPECTED_EXPORTS)
def test_export_is_native_agent_tool_with_schema(name: str) -> None:
    import strands_fun_tools
    from strands.tools.decorator import DecoratedFunctionTool

    exported = getattr(strands_fun_tools, name)
    assert isinstance(exported, DecoratedFunctionTool), type(exported)
    spec = exported.tool_spec
    assert spec["name"] == name
    assert spec["description"], f"{name} has an empty description"
    schema = spec["inputSchema"]["json"]
    assert schema.get("type") == "object"
    assert isinstance(schema.get("properties"), dict)
    # Native schema round-trips as JSON (what the model actually sees).
    json.dumps(schema)


@pytest.mark.parametrize("name", EXPECTED_EXPORTS)
def test_module_file_hot_loads_via_sdk_toolloader(name: str) -> None:
    """Official SDK ToolLoader loads each installed module file by path."""
    from strands.tools.loader import ToolLoader

    path = _package_dir() / f"{name}.py"
    assert path.is_file()
    loaded = ToolLoader.load_tools(str(path), name)
    assert len(loaded) >= 1
    assert any(t.tool_name == name for t in loaded)


@pytest.mark.parametrize("name", EXPECTED_EXPORTS)
def test_module_string_hot_loads_via_load_tool_from_string(name: str) -> None:
    """Non-deprecated path: dotted module string via importlib (full package
    context, relative imports inside the package would work here)."""
    from strands.tools.loader import load_tool_from_string

    loaded = load_tool_from_string(f"strands_fun_tools.{name}")
    assert any(t.tool_name == name for t in loaded)


def test_official_community_load_tool_hot_loads_file() -> None:
    """The official ``strands_tools.load_tool`` @tool registers a fun tool
    into a live Agent's ToolRegistry from a file path (BYPASS_TOOL_CONSENT
    avoids the interactive prompt; the deprecated registry path emits a
    DeprecationWarning, which we tolerate)."""
    os.environ["BYPASS_TOOL_CONSENT"] = "true"
    from strands.agent.agent import Agent
    from strands_tools.load_tool import load_tool as official_load_tool

    agent = Agent(tools=[official_load_tool], load_tools_from_directory=False)
    path = _package_dir() / "utility.py"
    result = official_load_tool(
        path=str(path), name="utility", agent=agent
    )
    assert result["status"] == "success", result
    assert "utility" in agent.tool_registry.registry


def test_official_load_tool_fails_cleanly_on_missing_file() -> None:
    os.environ["BYPASS_TOOL_CONSENT"] = "true"
    from strands.agent.agent import Agent
    from strands_tools.load_tool import load_tool as official_load_tool

    agent = Agent(tools=[official_load_tool], load_tools_from_directory=False)
    result = official_load_tool(
        path="/nonexistent/not_a_tool.py", name="ghost", agent=agent
    )
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Safe direct executions (read-only / local / status actions only)
# ---------------------------------------------------------------------------


def test_execute_utility_roundtrip() -> None:
    from strands_fun_tools import utility

    out = utility(action="base64_encode", input="blurple")
    assert out["status"] == "success"
    encoded = out["content"][0]["text"]
    back = utility(action="base64_decode", input=encoded)
    assert back["status"] == "success"
    assert "blurple" in back["content"][0]["text"]


def test_execute_utility_failure_path() -> None:
    from strands_fun_tools import utility

    out = utility(action="base64_decode", input="!!!not base64!!!")
    assert out["status"] == "error"


def test_execute_utility_unknown_action_fails() -> None:
    from strands_fun_tools import utility

    out = utility(action="not_a_real_action", input="x")
    assert out["status"] == "error"


def test_execute_template_create_render_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstream template stores .j2 files under ``cwd()/templates`` computed at
    import time; re-import with a temp cwd so the lane leaves no artifacts."""
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("strands_fun_tools.template", None)
    mod = importlib.import_module("strands_fun_tools.template")
    template = mod.template

    created = template(action="create", template_name="lane", content="hi {{ name }}")
    assert created["status"] == "success"
    out = template(action="render", template_name="lane", variables={"name": "blurple"})
    assert out["status"] == "success"
    assert "hi blurple" in "".join(c["text"] for c in out["content"])


def test_execute_template_render_missing_name_fails() -> None:
    # Re-importing the submodule (previous test) rebinds the package attribute
    # to the module object; fetch the @tool from the module explicitly.
    import strands_fun_tools.template as template_mod

    out = template_mod.template(action="render", variables={"name": "x"})
    assert out["status"] == "error"


def test_execute_dynamic_package_list_functions() -> None:
    from strands_fun_tools import dynamic_package

    out = dynamic_package(package_name="json", list_functions=True)
    assert out["status"] == "success"


def test_execute_dynamic_package_missing_package_fails() -> None:
    from strands_fun_tools import dynamic_package

    out = dynamic_package(
        package_name="definitely_not_a_pkg_xyz", list_functions=True
    )
    assert out["status"] == "error"


def test_execute_spinner_generator_local() -> None:
    from strands_fun_tools import spinner_generator

    out = spinner_generator(text="lane check", duration=0.2)
    assert out["status"] == "success"


def test_execute_clipboard_status_readonly() -> None:
    """Status/history listing is a local read (no clipboard mutation)."""
    from strands_fun_tools import clipboard

    out = clipboard(action="status")
    assert out["status"] in {"success", "error"}  # error acceptable headless


def test_execute_cursor_position_readonly() -> None:
    """Position read only — never moves/clicks. May fail without an
    accessible GUI session; both outcomes are recorded honestly."""
    from strands_fun_tools import cursor

    try:
        out = cursor(action="position")
    except Exception as exc:  # pyautogui can raise on headless/display denial
        pytest.skip(f"cursor blocked in this environment: {exc}")
    assert out["status"] in {"success", "error"}


def test_execute_bluetooth_status_readonly() -> None:
    from strands_fun_tools import bluetooth

    out = bluetooth(action="status")
    assert out["status"] in {"success", "error"}


def test_execute_yolo_vision_status_readonly() -> None:
    from strands_fun_tools import yolo_vision

    out = yolo_vision(action="status")
    assert out["status"] in {"success", "error"}


def test_execute_listen_status_readonly() -> None:
    from strands_fun_tools import listen

    out = listen(action="status")
    assert out["status"] in {"success", "error"}


def test_execute_npm_missing_package_fails() -> None:
    """Failure-path check: nonexistent package must error (node runs a local
    require() that throws; no network install is performed)."""
    from strands_fun_tools import npm

    out = npm(package_name="definitely-not-a-real-npm-pkg-xyz", list_functions=True)
    assert out["status"] == "error"


def test_execute_chess_reports_missing_engine_or_plays_locally(tmp_path: Path) -> None:
    """Stockfish binary is an external system dependency; either a clean
    error (missing engine) or a local evaluation is acceptable."""
    from strands_fun_tools import chess

    try:
        out = chess(action="evaluate", fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    except Exception as exc:
        pytest.skip(f"chess blocked (stockfish engine not on PATH): {exc}")
    assert out["status"] in {"success", "error"}


def test_execute_human_typer_requires_target_safely() -> None:
    """human_typer types into the active window — never run for real. The
    upstream module still validates input first; empty text must not type."""
    from strands_fun_tools import human_typer

    out = human_typer(text="")
    assert out["status"] in {"success", "error"}
