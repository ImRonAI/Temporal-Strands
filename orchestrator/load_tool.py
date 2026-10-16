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

Workflow side: the hook registers the activity-returned tool schema through
native ``@tool(context=True)`` and dispatches its ToolUse to the dynamic
activity. New loads and Continue-As-New never import tool files on replay.
Path-only records retain the old loading behavior for persisted histories.

https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
"""

from __future__ import annotations

import importlib.util
import asyncio
import inspect
import json
import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

import strands_tools
from strands import Agent, tool
from strands.hooks import AfterToolCallEvent, HookProvider
from strands.tools.mcp import MCPClient
from strands.types._events import ToolResultEvent
from strands.types.tools import ToolContext, ToolResult, ToolSpec, ToolUse
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
    GRAPH_START_TO_CLOSE,
    GRAPH_HEARTBEAT_TIMEOUT,
    GRAPH_RETRY_POLICY,
    GRAPH_QUIET_HEARTBEAT_INTERVAL,
    MCP_START_TO_CLOSE,
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
)

STRANDS_TOOLS_DIR = Path(strands_tools.__file__).resolve().parent
_MCP_CONFIG_PATH = Path(__file__).resolve().parent / "mcp.json"

# Tool activity. Start-to-close matches the installed Strands guide's
# activity_as_tool(..., start_to_close_timeout=timedelta(seconds=30)).
_ACTIVITY_OPTIONS = dict(
    start_to_close_timeout=MCP_START_TO_CLOSE,
    heartbeat_timeout=MODEL_HEARTBEAT,
    retry_policy=MODEL_RETRY_POLICY,
)


def _bound_tool(name: str) -> Any | None:
    """Existing bindings whose native invocation contracts must be preserved.

    Desktop tools (browser + computer-use) are bound to the calling session's
    own desktop task queue; the module-level tuples only serve schemas. Only the
    requested activity is bound: ``TemporalActivityTool`` extracts a schema on
    construction, and this runs in workflow code, so on every replay.
    """
    from agent_tools import (
        COMPUTER_USE_TOOLS, DESKTOP_ACTIVITIES, DESKTOP_TOOL_NAMES, PERMANENT_COMMUNITY_TOOLS,
        desktop_activity_options,
    )
    from strands_tools.think import think
    from temporalio.contrib.strands.workflow import activity_as_tool

    if name == "think":
        return think
    if name in DESKTOP_TOOL_NAMES and workflow.in_workflow():
        return activity_as_tool(
            DESKTOP_ACTIVITIES[name], **desktop_activity_options(workflow.info().workflow_id)
        )
    for bound in (*COMPUTER_USE_TOOLS, *PERMANENT_COMMUNITY_TOOLS):
        if bound.tool_name == name and (
            bound in COMPUTER_USE_TOOLS or name in {"graph", "browser", "use_agent"}
        ):
            return bound
    return None


def tool_file_path(path: str) -> str:
    """The file the official ``load_tool`` opens.

    The path as given when it exists; otherwise the installed ``strands_tools``
    module of that name (``path="current_time"`` -> ``strands_tools/current_time.py``,
    resolved by ``importlib``). A bare package id resolves to the nested file
    that defines its tools (``dicom`` -> ``dicom/strands_tools.py``). Anything
    else is returned unchanged for the official tool to report in its own words.
    """
    expanded = os.path.expanduser(path)
    if os.path.isfile(expanded):
        return expanded
    stem = Path(path).stem
    spec = importlib.util.find_spec(f"strands_tools.{stem}")
    if spec is not None and spec.origin and Path(spec.origin).name != "__init__.py":
        return spec.origin
    nested = _nested_tool_file(path)
    if nested:
        return nested
    return spec.origin if spec is not None and spec.origin else path


def _nested_tool_file(path: str) -> str | None:
    stem = Path(path).stem
    for candidate in (
        STRANDS_TOOLS_DIR / path,
        STRANDS_TOOLS_DIR / stem / "strands_tools.py",
        STRANDS_TOOLS_DIR / stem / f"{stem}_tools.py",
    ):
        if candidate.is_file() and candidate.resolve().is_relative_to(STRANDS_TOOLS_DIR):
            return str(candidate)
    return None


def _ok(text: str) -> dict[str, Any]:
    return {"status": "success", "content": [{"text": text}]}


def _mcp_config() -> dict[str, Any]:
    if not _MCP_CONFIG_PATH.is_file():
        return {}
    return json.loads(_MCP_CONFIG_PATH.read_text())


def _expand_env(env: dict[str, Any]) -> tuple[dict[str, str] | None, list[str]]:
    expanded: dict[str, str] = {}
    missing: list[str] = []
    for key, value in env.items():
        text = "" if value is None else str(value)
        variables = re.findall(r"\$\{(?:env:)?([^}]+)\}", text)
        missing.extend(var for var in variables if not os.environ.get(var))
        expanded[key] = re.sub(
            r"\$\{(?:env:)?([^}]+)\}",
            lambda match: os.environ.get(match[1], match[0]),
            text,
        )
    return (None if missing else expanded), missing


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
    """Load one community tool and register it on this session.

    Call this once per tool. Several calls in one step all register, and each
    loaded name is callable on the next step. Call it again with the same name
    to reload.

    Args:
        path: Bare module name (current_time, dicom, fhir, forge) or a Python file path.
        name: Registry name for the loaded tool.
    """
    bound = _bound_tool(name)
    if bound is not None:
        return {**_ok(f"Loaded {name}"), "path": name, "tool_spec": bound.tool_spec}
    resolved = tool_file_path(path)
    if name in _SESSION_TOOLS and (
        name not in {"environment", "http_request", "journal"}
        or Path(resolved).resolve() != STRANDS_TOOLS_DIR / f"{name}.py"
    ):
        return {"status": "error", "content": [{"text": f"Cannot replace starter tool {name}"}]}
    result = official_load_tool(path=resolved, name=name, agent=_worker_agent())
    if result.get("status") == "success":
        return {
            **result,
            "path": resolved,
            "tool_spec": _worker_agent().tool_registry.registry[name].tool_spec,
        }
    return result


async def mcp_client_activity(
    action: str,
    server_config: dict[str, Any] | None = None,
    connection_id: str | None = None,
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    transport: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    server_url: str | None = None,
    arguments: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    timeout: float | None = None,
    sse_read_timeout: float | None = None,
    terminate_on_close: bool | None = None,
    auth: Any | None = None,
) -> dict[str, Any]:
    """Forward one ``mcp_client`` call to the installed tool.

    ``agent`` stays on the worker. It is not an activity argument.
    """
    call = {
        "action": action,
        "server_config": server_config,
        "connection_id": connection_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "server_url": server_url,
        "arguments": arguments,
        "headers": headers,
        "timeout": timeout,
        "sse_read_timeout": sse_read_timeout,
        "terminate_on_close": terminate_on_close,
        "auth": auth,
        "agent": _worker_agent(),
    }
    result = official_mcp_client(**call)
    _track_extra_connection(call, result)
    return result


mcp_client_activity.__doc__ = inspect.unwrap(official_mcp_client).__doc__
mcp_client_activity = activity.defn(name="mcp_client")(mcp_client_activity)


async def _loaded_tool_path(name: str) -> str | None:
    """Path of a loaded tool from the parent workflow's ``loaded_tools`` query.

    A restarted worker uses that path to reload. If the query is unavailable,
    the bare module name resolves to the installed catalog file.
    """
    info = activity.info()
    if not info.workflow_id:
        return tool_file_path(name)
    handle = activity.client().get_workflow_handle(info.workflow_id)
    try:
        records = await handle.query("loaded_tools")
    except Exception as error:  # noqa: BLE001 - query is optional after a worker restart
        activity.logger.warning("loaded_tools query unavailable: %s", error)
        return tool_file_path(name)
    for rec in records or []:
        if isinstance(rec, dict) and rec.get("name") == name and rec.get("path"):
            return str(rec["path"])
    return tool_file_path(name)


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
    heartbeat_task = None
    if name == "swarm":
        from subagent_support import (
            in_process_model, parent_agent, quiet_heartbeat_ticker, session_model,
        )

        agent = await parent_agent(in_process_model(await session_model(name)))
        heartbeat_task = asyncio.create_task(quiet_heartbeat_ticker(GRAPH_QUIET_HEARTBEAT_INTERVAL.total_seconds()))
    result: ToolResult | None = None
    try:
        async for event in loaded.stream(tool_use, {"agent": agent}):
            if isinstance(event, ToolResultEvent):
                result = event.tool_result
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
    return result


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


def register_community_tool(agent: Any, path: str, name: str, spec: ToolSpec | None = None) -> dict[str, Any]:
    """Register the activity-returned schema without importing tool files on replay.

    The path-only branch supports persisted sessions created before schemas were
    carried. New sessions use only serialized metadata from the load activity.
    """
    bound = _bound_tool(name)
    if bound is not None:
        if name in agent.tool_registry.registry:
            agent.tool_registry.replace(bound)
        else:
            agent.tool_registry.register_tool(bound)
        return _ok(f"Loaded {name}")
    if spec is None:
        result = official_load_tool(path=tool_file_path(path), name=name, agent=agent)
        if result.get("status") != "success":
            return result
        spec = agent.tool_registry.registry[name].tool_spec

    @tool(name=name, description=spec["description"], inputSchema=spec["inputSchema"], context=True)
    async def dispatch(tool_context: ToolContext) -> ToolResult:
        # Named legacy activities may still serve older histories. A distinct
        # activity type ensures loaded community tools reach the dynamic handler.
        options = _ACTIVITY_OPTIONS
        if name == "swarm":
            options = dict(
                start_to_close_timeout=GRAPH_START_TO_CLOSE,
                heartbeat_timeout=GRAPH_HEARTBEAT_TIMEOUT,
                retry_policy=GRAPH_RETRY_POLICY,
            )
        return await workflow.execute_activity(f"community.{name}", tool_context.tool_use, **options)

    if name in agent.tool_registry.registry:
        agent.tool_registry.replace(dispatch)
    else:
        agent.tool_registry.register_tool(dispatch)
    return _ok(f"Loaded {name}")


@activity.defn(name="discover_tools")
async def discover_tools_activity() -> dict[str, Any]:
    """List community tool names that can be loaded. Does not register them."""
    names: list[str] = []
    for item in sorted(STRANDS_TOOLS_DIR.glob("*.py")):
        if item.is_file() and not item.name.startswith("_"):
            names.append(item.stem)
    for child in sorted(STRANDS_TOOLS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if (child / "strands_tools.py").is_file() or (child / f"{child.name}_tools.py").is_file():
            names.append(child.name)
    from agent_tools import COMPUTER_USE_TOOLS

    names.extend(bound.tool_name for bound in COMPUTER_USE_TOOLS)
    names.extend(("graph", "browser", "use_agent"))
    names = sorted(set(names))
    return {"status": "success", "content": [{"text": "\n".join(names)}]}


def unregistered_names(catalog: str, registered: set[str]) -> list[str]:
    """Catalog names that are not already on the session registry."""
    return [line for line in catalog.splitlines() if line and line not in registered]


def _result_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    content = result.get("content") or []
    if not content or not isinstance(content[0], dict):
        return None
    text = content[0].get("text") or ""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def apply_tool_result(agent: Any, tool_use: dict[str, Any], result: dict[str, Any], paths: dict[str, str]) -> None:
    """Register a successful ``load_tool`` or drop names from ``unload_tools``.

    One call handles one tool result. The model may emit many in one step;
    each result updates the registry on its own.
    """
    if tool_use.get("name") == "load_tool":
        payload = _result_payload(result)
        if not payload or payload.get("status") != "success" or not payload.get("tool_spec"):
            return
        name = (tool_use.get("input") or {}).get("name")
        if not isinstance(name, str) or not name or name in _SESSION_TOOLS:
            return
        path = payload.get("path") or name
        register_community_tool(agent, str(path), name, payload["tool_spec"])
        paths[name] = str(path)
        return
    if tool_use.get("name") != "unload_tools":
        return
    requested = (tool_use.get("input") or {}).get("names") or []
    if isinstance(requested, list):
        drop_loaded(agent, [str(name) for name in requested], paths)


def drop_loaded(agent: Any, names: list[str], paths: dict[str, str]) -> list[str]:
    """Pop every requested name that this session loaded. Leave the rest."""
    gone: list[str] = []
    registry = agent.tool_registry.registry
    dynamic = getattr(agent.tool_registry, "dynamic_tools", None)
    for name in names:
        if name in _SESSION_TOOLS or name not in paths:
            continue
        registry.pop(name, None)
        if isinstance(dynamic, dict):
            dynamic.pop(name, None)
        paths.pop(name, None)
        gone.append(name)
    return gone


def replay_catalog(agent: Any, messages: Sequence[Any], paths: dict[str, str]) -> None:
    """Re-apply load and unload results already stored in the conversation.

    Continue-as-new rebuilds the agent from its static tool list. The messages
    are the record of what was loaded afterward.
    """
    uses: dict[str, dict[str, Any]] = {}
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            use = block.get("toolUse")
            if isinstance(use, dict) and use.get("toolUseId"):
                uses[str(use["toolUseId"])] = use
            result = block.get("toolResult")
            if not isinstance(result, dict):
                continue
            matched = uses.get(str(result.get("toolUseId")))
            if matched:
                apply_tool_result(agent, matched, result, paths)


class LoadedTools(HookProvider):
    """Register each successful ``load_tool`` before the next model call."""

    def __init__(self) -> None:
        self.paths: dict[str, str] = {}

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._after)

    def _after(self, event: AfterToolCallEvent) -> None:
        if event.result is None:
            return
        apply_tool_result(event.agent, dict(event.tool_use), dict(event.result), self.paths)


def catalog_tools(paths: dict[str, str]) -> tuple[Any, Any]:
    """``discover_tools`` and ``unload_tools`` bound to this session's loads."""

    @tool(context=True)
    async def discover_tools(tool_context: ToolContext) -> dict[str, Any]:
        """List community tools that exist and are not on this session.

        Returns names only. Load any of them with load_tool, as many as you
        want in this step or the next. Call this again after loading or unloading.
        """
        catalog = await workflow.execute_activity(
            discover_tools_activity,
            start_to_close_timeout=MCP_START_TO_CLOSE,
            heartbeat_timeout=MODEL_HEARTBEAT,
            retry_policy=MODEL_RETRY_POLICY,
        )
        text = (catalog.get("content") or [{}])[0].get("text") or ""
        names = unregistered_names(text, set(tool_context.agent.tool_registry.registry))
        return {"status": "success", "content": [{"text": "\n".join(names)}]}

    @tool(context=True)
    def unload_tools(names: list[str], tool_context: ToolContext) -> dict[str, Any]:
        """Remove loaded community tools from this session.

        ``names`` may list as many loaded tools as you want. Tools that were
        not loaded stay. Call load_tool again to bring one back.
        """
        gone = drop_loaded(tool_context.agent, names, paths)
        return {"status": "success", "content": [{"text": "\n".join(gone)}]}

    return discover_tools, unload_tools


_SESSION_TOOLS = frozenset({
    "load_tool",
    "discover_tools",
    "unload_tools",
    "use_skill",
    "environment",
    "http_request",
    "journal",
})


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
        for key in ("transport", "command", "args", "env", "server_url", "headers"):
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
    configured = (_mcp_config().get("mcpServers") or {}).get(server)
    if configured and configured.get("url"):
        # Resolve configured credentials on the worker, including after restart.
        return lambda: MCPClient.load_servers({
            "mcpServers": {server: {**configured, "continue_on_error": False}},
        })[0]
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
            return MCPClient(lambda: sse_client(params["server_url"], headers=params.get("headers")))
        return MCPClient(lambda: streamablehttp_client(params["server_url"], headers=params.get("headers")))

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
