"""``load_tool`` and ``mcp_client`` per the Temporal Strands plugin README, "Tools":

    Decorate non-deterministic tools with ``@activity.defn``, or if you're
    importing tools from ``strands_tools``, wrap them in a thin async function.
    Then register the activity on the worker via ``Worker(activities=[...])``
    and pass it to the agent with ``workflow.activity_as_tool(activity, ...)``.

Worker side: ``load_tool_activity`` and ``mcp_client_activity`` are those thin
async functions around the official ``strands_tools.load_tool.load_tool`` and
``strands_tools.mcp_client.mcp_client``. ``load_tool`` mutates the registry of
the ``agent`` it is handed, so the worker hands it a Strands ``Agent``. A loaded
tool then executes on the worker through Temporal's dynamic activity
(``@activity.defn(dynamic=True)``), which runs the loaded ``AgentTool`` with the
SDK's own ``stream()``.

Workflow side: after a successful ``load_tool`` the hook in ``workflow.py``
calls ``register_community_tool`` -- the same official ``load_tool`` against the
live ``TemporalAgent`` -- then swaps the loaded tool for ``LoadedActivityTool``,
which dispatches to that dynamic activity exactly as the plugin's own
``TemporalMCPTool`` dispatches to ``{server}-call-tool``.

https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
"""

from __future__ import annotations

import functools
import importlib.util
import inspect
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

import strands_tools
from strands import Agent
from strands.tools.mcp import MCPClient
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolGenerator, ToolResult, ToolSpec, ToolUse
from strands_tools.load_tool import load_tool as official_load_tool
from strands_tools.mcp_client import mcp_client as official_mcp_client
from temporalio import activity, workflow
from temporalio.common import RawValue
from temporalio.contrib.strands._temporal_mcp_client import (
    _CallToolArgs,
    _MCP_CONNECTION_IDLE,
    build_call_tool_activity,
    build_list_tools_activity,
)

from config import (
    MCP_START_TO_CLOSE,
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
)

STRANDS_TOOLS_DIR = Path(strands_tools.__file__).resolve().parent

# Tool activity. Start-to-close matches the installed Strands guide's
# activity_as_tool(..., start_to_close_timeout=timedelta(seconds=30)).
_ACTIVITY_OPTIONS = dict(
    start_to_close_timeout=MCP_START_TO_CLOSE,
    heartbeat_timeout=MODEL_HEARTBEAT,
    retry_policy=MODEL_RETRY_POLICY,
)


def tool_file_path(path: str) -> str:
    """The file the official ``load_tool`` opens.

    The path as given when it exists; otherwise the installed ``strands_tools``
    module of that name (``path="calculator"`` -> ``strands_tools/calculator.py``,
    resolved by ``importlib``). Anything else is returned unchanged for the
    official tool to report in its own words.
    """
    expanded = os.path.expanduser(path)
    if os.path.exists(expanded):
        return expanded
    spec = importlib.util.find_spec(f"strands_tools.{Path(path).stem}")
    return spec.origin if spec is not None and spec.origin else path


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

_WORKER_AGENT: Agent | None = None


def _worker_agent() -> Agent:
    """The Strands ``Agent`` whose registry the official ``load_tool`` mutates
    on this worker; the dynamic activity runs the loaded tools from it."""
    global _WORKER_AGENT
    if _WORKER_AGENT is None:
        _WORKER_AGENT = Agent(tools=[], callback_handler=None)
    return _WORKER_AGENT


@activity.defn(name="load_tool")
async def load_tool_activity(path: str, name: str) -> dict:
    """Load a community tool onto this session's TemporalAgent registry.

    After a successful load the model calls the registered ``name`` — the
    Temporal activity name — never a package import path.

    Args:
        path: Bare community module name the worker remaps
            (``path="calculator"``), or a ``.py`` file you created.
        name: Registry name the model will call after load.
    """
    return official_load_tool(path=tool_file_path(path), name=name, agent=_worker_agent())


@activity.defn(name="mcp_client")
@functools.wraps(official_mcp_client, updated=())
async def mcp_client_activity(*args: Any, **kwargs: Any) -> dict[str, Any]:
    result = official_mcp_client(*args, **kwargs)
    call = inspect.signature(official_mcp_client).bind_partial(*args, **kwargs).arguments
    _track_extra_connection(call, result)
    return result


async def _loaded_tool_path(name: str) -> str | None:
    """Path of a loaded tool from the parent workflow's ``loaded_tools`` query
    (durable workflow state, so a restarted worker can reload it)."""
    info = activity.info()
    if not info.workflow_id:
        return None
    handle = activity.client().get_workflow_handle(info.workflow_id)
    records = await handle.query("loaded_tools")
    for rec in records or []:
        if isinstance(rec, dict) and rec.get("name") == name and rec.get("path"):
            return str(rec["path"])
    return None


async def _run_loaded_tool(tool_use: ToolUse) -> ToolResult | None:
    agent = _worker_agent()
    name = tool_use["name"]
    loaded = agent.tool_registry.registry.get(name)
    if loaded is None:
        path = await _loaded_tool_path(name)
        if path is None:
            raise KeyError(name)
        official_load_tool(path=path, name=name, agent=agent)
        loaded = agent.tool_registry.registry[name]
    result: ToolResult | None = None
    async for event in loaded.stream(tool_use, {}):
        if isinstance(event, ToolResultEvent):
            result = event.tool_result
    return result


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class LoadedActivityTool(AgentTool):
    """Workflow-side stub for a ``load_tool``-loaded tool; dispatches to the
    worker's dynamic activity of the same name (the plugin's ``TemporalMCPTool``
    shape, with the SDK ``ToolUse`` as the activity input)."""

    def __init__(self, spec: ToolSpec, options: dict[str, Any]) -> None:
        super().__init__()
        self._spec = spec
        self._options = options

    @property
    def tool_name(self) -> str:
        return self._spec["name"]

    @property
    def tool_spec(self) -> ToolSpec:
        return self._spec

    @property
    def tool_type(self) -> str:
        return "temporal_activity"

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> ToolGenerator:
        result: ToolResult = await workflow.execute_activity(
            self.tool_name, tool_use, **self._options
        )
        yield ToolResultEvent(result)


def register_community_tool(agent: Any, path: str, name: str) -> dict[str, Any]:
    """Official ``load_tool`` against the live agent, then ``replace`` the loaded
    tool with its activity-dispatching stub. Returns the official result."""
    result = official_load_tool(path=tool_file_path(path), name=name, agent=agent)
    if result.get("status") != "success":
        return result
    loaded = agent.tool_registry.registry[name]
    if not isinstance(loaded, LoadedActivityTool):
        agent.tool_registry.replace(LoadedActivityTool(loaded.tool_spec, _ACTIVITY_OPTIONS))
    return result


# ---------------------------------------------------------------------------
# Runtime MCP servers: mcp_client connect -> TemporalMCPClient on the rebuilt
# agent, served by the plugin's own list-tools / call-tool activity builders.
# ---------------------------------------------------------------------------

_EXTRA_LIST_TOOLS: dict[str, Any] = {}
_EXTRA_CALL_TOOL: dict[str, Any] = {}
# connection_id -> the transport params the official mcp_client connect call
# was made with, recorded when that activity runs on this worker.
_EXTRA_MCP_PARAMS: dict[str, dict[str, Any]] = {}


def _track_extra_connection(inp: dict[str, Any], result: Any) -> None:
    """Record/forget connect params from official mcp_client calls."""
    connection_id = inp.get("connection_id")
    if not connection_id:
        return
    action = inp.get("action")
    if (
        action == "connect"
        and isinstance(result, dict)
        and result.get("status") == "success"
    ):
        params = dict(inp.get("server_config") or {})
        for key in ("transport", "command", "args", "env", "server_url"):
            if inp.get(key) is not None:
                params[key] = inp[key]
        _EXTRA_MCP_PARAMS[connection_id] = params
    elif action == "disconnect":
        _EXTRA_MCP_PARAMS.pop(connection_id, None)
        _EXTRA_LIST_TOOLS.pop(connection_id, None)
        _EXTRA_CALL_TOOL.pop(connection_id, None)


def _mcp_factory(server: str) -> Callable[[], MCPClient]:
    """MCPClient factory from recorded connect params.

    Same construction the Temporal Strands README uses for
    ``StrandsPlugin(mcp_clients=...)`` factories.
    """
    params = _EXTRA_MCP_PARAMS[server]
    transport = params.get("transport") or "stdio"

    def make() -> MCPClient:
        if transport == "stdio":
            server_params = StdioServerParameters(
                command=params["command"],
                args=params.get("args") or [],
                env=params.get("env"),
            )
            return MCPClient(lambda: stdio_client(server_params))
        if transport == "sse":
            return MCPClient(lambda: sse_client(params["server_url"]))
        return MCPClient(lambda: streamablehttp_client(params["server_url"]))

    return make


@activity.defn(dynamic=True)
async def run_loaded_tool(args: Sequence[RawValue]) -> Any:
    """Execute a load_tool-loaded tool or an extra MCP {server}-list-tools / {server}-call-tool."""
    activity_type = activity.info().activity_type
    conv = activity.payload_converter()
    values = [
        conv.from_payload(a.payload) if isinstance(a, RawValue) else a for a in args
    ]
    if activity_type.endswith("-list-tools"):
        server = activity_type[: -len("-list-tools")]
        fn = _EXTRA_LIST_TOOLS.get(server)
        if fn is None:
            fn = build_list_tools_activity(server, _mcp_factory(server), _MCP_CONNECTION_IDLE)
            _EXTRA_LIST_TOOLS[server] = fn
        return await fn()
    if activity_type.endswith("-call-tool"):
        server = activity_type[: -len("-call-tool")]
        fn = _EXTRA_CALL_TOOL.get(server)
        if fn is None:
            fn = build_call_tool_activity(server, _mcp_factory(server), _MCP_CONNECTION_IDLE)
            _EXTRA_CALL_TOOL[server] = fn
        payload = values[0] if values else {}
        call_args = (
            payload
            if isinstance(payload, _CallToolArgs)
            else _CallToolArgs(**payload)
        )
        return await fn(call_args)
    return await _run_loaded_tool(values[0])
