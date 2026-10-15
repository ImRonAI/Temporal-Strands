"""Native Temporal tool bindings and worker-side MCP configuration."""

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from strands.tools.mcp import MCPClient
from temporalio import workflow
from temporalio.contrib.strands import TemporalMCPClient
from temporalio.contrib.strands.workflow import activity_as_tool

from browser_activity import browser_activity
from computer_use_activity import COMPUTER_USE_ACTIVITIES
from graph_activity import graph_activity
from load_tool import load_tool_activity, mcp_client_activity
from python_repl_activity import python_repl as python_repl_activity
from use_agent_activity import use_agent_activity
from use_skill_activity import use_skill_activity
import think_activity
from config import (
    BROWSER_RETRY_POLICY, DESKTOP_BROWSER_TASK_QUEUE, DESKTOP_TASK_TIMEOUT, desktop_task_queue,
    DESKTOP_JOB_HEARTBEAT_TIMEOUT, GRAPH_START_TO_CLOSE, GRAPH_HEARTBEAT_TIMEOUT,
    GRAPH_RETRY_POLICY, MCP_START_TO_CLOSE, MODEL_HEARTBEAT, MODEL_RETRY_POLICY,
    PYTHON_REPL_START_TO_CLOSE, PYTHON_REPL_HEARTBEAT_TIMEOUT, PYTHON_REPL_RETRY_POLICY,
    THINK_START_TO_CLOSE, THINK_HEARTBEAT_TIMEOUT, THINK_RETRY_POLICY,
    USE_AGENT_START_TO_CLOSE, USE_AGENT_HEARTBEAT_TIMEOUT, USE_AGENT_RETRY_POLICY,
)

_MCP_ACTIVITY_OPTIONS = dict(
    start_to_close_timeout=MCP_START_TO_CLOSE,
    heartbeat_timeout=MODEL_HEARTBEAT,
    retry_policy=MODEL_RETRY_POLICY,
)
_GRAPH_ACTIVITY_OPTIONS = dict(
    start_to_close_timeout=GRAPH_START_TO_CLOSE,
    heartbeat_timeout=GRAPH_HEARTBEAT_TIMEOUT,
    retry_policy=GRAPH_RETRY_POLICY,
)
_USE_AGENT_ACTIVITY_OPTIONS = dict(
    start_to_close_timeout=USE_AGENT_START_TO_CLOSE,
    heartbeat_timeout=USE_AGENT_HEARTBEAT_TIMEOUT,
    retry_policy=USE_AGENT_RETRY_POLICY,
)
_DESKTOP_ACTIVITY_OPTIONS = dict(
    task_queue=DESKTOP_BROWSER_TASK_QUEUE,
    start_to_close_timeout=DESKTOP_TASK_TIMEOUT,
    heartbeat_timeout=DESKTOP_JOB_HEARTBEAT_TIMEOUT,
    retry_policy=BROWSER_RETRY_POLICY,
    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
)


def desktop_activity_options(session_id: str) -> dict[str, Any]:
    """``_DESKTOP_ACTIVITY_OPTIONS`` routed to the session's own desktop queue."""
    return dict(
        task_queue=desktop_task_queue(session_id),
        start_to_close_timeout=DESKTOP_TASK_TIMEOUT,
        heartbeat_timeout=DESKTOP_JOB_HEARTBEAT_TIMEOUT,
        retry_policy=BROWSER_RETRY_POLICY,
        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
    )


DESKTOP_TOOL_NAMES = frozenset(
    {"browser", *(activity.__name__ for activity in COMPUTER_USE_ACTIVITIES)}
)


def desktop_tools(session_id: str) -> tuple[Any, ...]:
    """Browser + computer-use tools bound to the session's desktop container."""
    return (
        activity_as_tool(browser_activity, **desktop_activity_options(session_id)),
        *(activity_as_tool(action, **desktop_activity_options(session_id)) for action in COMPUTER_USE_ACTIVITIES),
    )
_PYTHON_REPL_ACTIVITY_OPTIONS = dict(
    start_to_close_timeout=PYTHON_REPL_START_TO_CLOSE,
    heartbeat_timeout=PYTHON_REPL_HEARTBEAT_TIMEOUT,
    retry_policy=PYTHON_REPL_RETRY_POLICY,
)

# Schemas are process-wide; rebuilding them during replay delays activation.
COMPUTER_USE_TOOLS = tuple(activity_as_tool(action, **_DESKTOP_ACTIVITY_OPTIONS)
                          for action in COMPUTER_USE_ACTIVITIES)
PERMANENT_COMMUNITY_TOOLS = (
    activity_as_tool(load_tool_activity, **_MCP_ACTIVITY_OPTIONS),
    activity_as_tool(browser_activity, **_DESKTOP_ACTIVITY_OPTIONS),
    activity_as_tool(mcp_client_activity, **_MCP_ACTIVITY_OPTIONS),
    activity_as_tool(graph_activity, **_GRAPH_ACTIVITY_OPTIONS),
    activity_as_tool(use_agent_activity, **_USE_AGENT_ACTIVITY_OPTIONS),
    activity_as_tool(use_skill_activity, **_MCP_ACTIVITY_OPTIONS),
    activity_as_tool(python_repl_activity, **_PYTHON_REPL_ACTIVITY_OPTIONS),
)
# Retained only for histories predating think-durable-native-async-v2.
THINK_TOOL = activity_as_tool(think_activity.think,
    start_to_close_timeout=THINK_START_TO_CLOSE,
    heartbeat_timeout=THINK_HEARTBEAT_TIMEOUT,
    retry_policy=THINK_RETRY_POLICY)

_MCP_CONFIG_PATH = Path(__file__).resolve().parent / "mcp.json"
_SHELL_MCP = Path(__file__).resolve().parent / ".venv/bin/strands-shell"
_ENV_REF = re.compile(r"\$\{(?:env:)?([^}]+)\}")
_LOGGER = logging.getLogger(__name__)


def _server_env_missing(cfg: dict[str, Any], *, server_name: str) -> bool:
    blobs = [str(value) for value in (cfg.get("env") or {}).values()]
    blobs.extend(str(value) for value in (cfg.get("headers") or {}).values())
    blobs.extend(cfg[key] for key in ("command", "url") if isinstance(cfg.get(key), str))
    missing = sorted({name for blob in blobs for name in _ENV_REF.findall(blob)
                      if not os.environ.get(name)})
    for name in missing:
        _LOGGER.warning("Skipping MCP server %s: required environment variable %s is unset",
                        server_name, name)
    return bool(missing)


def mcp_client_factories() -> dict[str, Callable[[], MCPClient]]:
    """Worker factories for every configured server with its required environment."""
    raw = json.loads(_MCP_CONFIG_PATH.read_text())
    servers = raw.get("mcpServers", {})
    for name, cfg in list(servers.items()):
        if not isinstance(cfg, dict):
            continue
        if _server_env_missing(cfg, server_name=name):
            del servers[name]
            continue
        args = cfg.get("args")
        if not isinstance(args, list):
            continue
        rewritten = []
        for index, item in enumerate(args):
            text = str(item)
            candidate = _MCP_CONFIG_PATH.parent / "strands-tools" / text
            rewritten.append(str(candidate.resolve()) if index and args[index - 1] == "--from"
                             and candidate.exists() else text)
        cfg["args"] = rewritten
    if _SHELL_MCP.is_file():
        raw["mcpServers"]["shell"]["command"] = str(_SHELL_MCP.resolve())
    return {client._application_name: (lambda c=client: c)
            for client in MCPClient.load_servers(raw)}


# Read configuration once outside workflow execution. A workflow handle must
# never create a live MCP client or inspect the host environment on replay.
_MCP_SERVER_NAMES = tuple(mcp_client_factories())


def temporal_mcp_clients() -> tuple[TemporalMCPClient, ...]:
    return tuple(TemporalMCPClient(server=name, cache_tools=True, **_MCP_ACTIVITY_OPTIONS)
                 for name in _MCP_SERVER_NAMES)
