"""Thin Temporal activity around the installed ``strands_tools.python_repl``.

Client-side Python execution for the orchestrator's own agent. The Agent API's
``sandbox`` tool runs the model's code in Perplexity's container; this activity
runs code on the host worker, where it can reach the session workspace, the
loaded community tools' Python environment, and local files. It imports and
calls the installed tool's public callable -- no copied REPL loop, no private
internals -- and is bound to the workflow via ``activity_as_tool`` in
``agent_tools.py`` with the ``PYTHON_REPL_*`` envelope from ``config.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from temporalio import activity
from strands_tools.python_repl import python_repl as native_python_repl

from config import PYTHON_REPL_PERSISTENCE_DIR

logger = logging.getLogger(__name__)


def _configure_environment() -> None:
    """Non-interactive, persistent REPL in a runtime directory.

    The installed tool reads these at call time. ``BYPASS_TOOL_CONSENT`` and
    ``STRANDS_NON_INTERACTIVE`` are the tool's documented consent skips (the
    worker sets them at boot too; repeated here so a direct activity run
    behaves the same). ``PYTHON_REPL_INTERACTIVE=false`` selects the captured
    stdout/stderr path -- the PTY path needs a controlling terminal the worker
    does not have.
    """
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    os.environ.setdefault("PYTHON_REPL_INTERACTIVE", "false")
    if "PYTHON_REPL_PERSISTENCE_DIR" not in os.environ:
        PYTHON_REPL_PERSISTENCE_DIR.mkdir(parents=True, exist_ok=True)
        os.environ["PYTHON_REPL_PERSISTENCE_DIR"] = str(PYTHON_REPL_PERSISTENCE_DIR)


@activity.defn(name="python_repl")
async def python_repl(code: str, reset_state: bool = False) -> dict[str, Any]:
    """Execute Python code on the worker with state that persists between calls.

    Runs in the orchestrator's own Python 3.13 environment, not in the Agent
    API sandbox: use it for computation over local files, the session
    workspace, or data returned by other tools. Variables and imports defined
    in one call are available in the next. stdout, stderr, and the repr of the
    last expression are returned as text.

    Args:
        code: The Python code to execute.
        reset_state: Clear the persisted namespace before running this code.

    Returns:
        Dict with "status" ("success" or "error") and "content", a list with the
        captured output text or the formatted traceback on failure.
    """
    _configure_environment()
    try:
        activity.heartbeat({"code_length": len(code)})
        tool_use_id = activity.info().activity_id
    except RuntimeError:  # pragma: no cover - direct invocation outside Temporal
        logger.debug("python_repl heartbeat skipped outside activity context")
        tool_use_id = "python_repl"
    tool_use = {
        "toolUseId": tool_use_id,
        "input": {"code": code, "interactive": False, "reset_state": reset_state},
    }
    # The installed callable is synchronous and blocks on the interpreter; run
    # it off the event loop so the worker keeps heartbeating other activities.
    result = await asyncio.to_thread(native_python_repl, tool_use)
    return {"status": result["status"], "content": list(result["content"])}
