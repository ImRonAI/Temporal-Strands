"""Tests for the thin ``python_repl`` activity (Pattern 2: installed tool as activity).

``python_repl_activity.python_repl`` owns no interpreter logic: it builds a
ToolUse from the model's input and delegates to the installed
``strands_tools.python_repl`` callable off the event loop. These tests pin the
wrapper contract -- delegation, the non-interactive/persistent environment,
the runtime state directory, and real execution with state carried between
calls -- using Temporal's ``ActivityEnvironment``.

Run from inside orchestrator/::

    .venv/bin/python -m pytest tests/test_python_repl_activity.py -q
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from temporalio.testing import ActivityEnvironment

import python_repl_activity
from python_repl_activity import python_repl


@pytest.fixture
def repl_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated persistence directory so tests never share or pollute state."""
    state_dir = tmp_path / "python-repl"
    state_dir.mkdir()
    monkeypatch.setenv("PYTHON_REPL_PERSISTENCE_DIR", str(state_dir))
    monkeypatch.delenv("PYTHON_REPL_INTERACTIVE", raising=False)
    monkeypatch.chdir(tmp_path)
    # The installed module caches its ReplState process-wide; start fresh.
    from strands_tools import python_repl as installed

    monkeypatch.setattr(installed, "_repl_state", None)
    return state_dir


def test_activity_delegates_to_the_installed_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    from strands_tools import python_repl as installed

    assert python_repl_activity.native_python_repl is installed.python_repl
    source = inspect.getsource(python_repl_activity)
    # Thin wrapper: no copied REPL loop, no private internals, not the AWS interpreter.
    for forbidden in ("ReplState(", "OutputCapture(", "PtyManager(", "code_interpreter", "._tool_func"):
        assert forbidden not in source, forbidden


@pytest.mark.asyncio
async def test_activity_builds_the_tool_use_and_forces_non_interactive(
    monkeypatch: pytest.MonkeyPatch, repl_dir: Path,
) -> None:
    seen: list[dict[str, Any]] = []

    def fake(tool_use: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        seen.append(tool_use)
        return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"text": "ok"}]}

    monkeypatch.setattr(python_repl_activity, "native_python_repl", fake)
    env = ActivityEnvironment()

    result = await env.run(python_repl, "print(1)", True)

    assert result == {"status": "success", "content": [{"text": "ok"}]}
    assert seen == [{
        "toolUseId": env.info.activity_id,
        "input": {"code": "print(1)", "interactive": False, "reset_state": True},
    }]
    import os
    assert os.environ["PYTHON_REPL_INTERACTIVE"] == "false"
    assert os.environ["BYPASS_TOOL_CONSENT"] == "true"
    assert os.environ["STRANDS_NON_INTERACTIVE"] == "true"
    assert os.environ["PYTHON_REPL_PERSISTENCE_DIR"] == str(repl_dir)


@pytest.mark.asyncio
async def test_activity_executes_code_with_state_between_calls(repl_dir: Path) -> None:
    env = ActivityEnvironment()

    first = await env.run(python_repl, "x = 20\nprint(x + 1)")
    second = await env.run(python_repl, "print(x * 2)")

    assert first["status"] == "success"
    assert "21" in first["content"][0]["text"]
    assert second["status"] == "success"
    assert "40" in second["content"][0]["text"]
    assert (repl_dir / "repl_state" / "repl_state.pkl").exists()


@pytest.mark.asyncio
async def test_activity_returns_errors_as_tool_errors_not_activity_failures(repl_dir: Path) -> None:
    env = ActivityEnvironment()

    result = await env.run(python_repl, "raise ValueError('boom')")

    assert result["status"] == "error"
    assert "ValueError" in result["content"][0]["text"]
    assert "boom" in result["content"][0]["text"]


def test_default_persistence_dir_is_runtime_data_not_source() -> None:
    from config import PYTHON_REPL_PERSISTENCE_DIR

    assert PYTHON_REPL_PERSISTENCE_DIR.parent.name == ".runtime"
    assert PYTHON_REPL_PERSISTENCE_DIR.parent.parent == Path(python_repl_activity.__file__).resolve().parent
