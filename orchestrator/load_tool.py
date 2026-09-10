"""Temporal wiring for ``load_tool``, hot-loaded I/O community tools, and ``mcp_client``.

``load_tool`` is I/O (``strands_tools.load_tool.load_tool`` runs sync file I/O
and Strands' ``stream()`` wraps sync tools in ``asyncio.to_thread``, which
Temporal workflows block), so ``load_tool_activity`` wraps the public
``strands_tools.load_tool.load_tool`` in ``@activity.defn(name="load_tool")``
and ``PERMANENT_COMMUNITY_TOOLS`` carries ``activity_as_tool(load_tool_activity)``.
Loaded I/O tools are wrapped with ``activity_as_tool`` here too, via
``_HotLoadHook`` after a successful load.
``mcp_client`` is I/O → ``activity_as_tool(mcp_client_activity)`` only.

https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from os.path import expanduser

import strands_tools
from strands.tools.loader import load_tools_from_file_path
from strands.tools.mcp import MCPClient
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolUse
from strands_tools.load_tool import load_tool as official_load_tool
from strands_tools.mcp_client import mcp_client as official_mcp_client
from temporalio import activity
from temporalio.common import RawValue
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.exceptions import ApplicationError
from temporalio.contrib.strands._temporal_mcp_client import (
    _CallToolArgs,
    _MCP_CONNECTION_IDLE,
    build_call_tool_activity,
    build_list_tools_activity,
)

from config import (
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
    MODEL_SCHEDULE_TO_CLOSE,
    MODEL_START_TO_CLOSE,
    closable_activity_options,
)

# Official load_tool loads an existing .py file (path + name).
# strands_tools is consumed as the installed package — no orchestrator/tools/
# symlink farm. Search roots (in order): the installed strands_tools package,
# official meta-tooling cwd()/tools, this package (skills_loader / Pattern 2),
# Agent Skills catalogs.
_ORCHESTRATOR_DIR = Path(__file__).resolve().parent
STRANDS_TOOLS_DIR = Path(strands_tools.__file__).resolve().parent
_STRANDS_TOOLS_REPO = _ORCHESTRATOR_DIR.parent.parent / "strands-tools"

_IMPLEMENTATIONS: dict[str, AgentTool] = {}
_LOADED_PATHS: dict[str, str] = {}


class _LoadRegistryShim:
    """Duck-typed ``agent`` for the public ``strands_tools.load_tool`` call.

    The workflow owns the real agent loop; the activity runs on the worker,
    which has no conversation agent. The official ``load_tool`` only touches
    ``agent.tool_registry``, so a plain registry holder satisfies its public
    contract. Loaded tools land in ``_IMPLEMENTATIONS`` for ``run_loaded_tool``;
    the workflow-side ``_HotLoadHook`` registers the same file on the live
    agent via ``register_community_tool``.
    """

    def __init__(self) -> None:
        from strands.tools.registry import ToolRegistry

        self.tool_registry = ToolRegistry()


_LOAD_SHIM: _LoadRegistryShim | None = None


def _load_target_agent() -> _LoadRegistryShim:
    """The shared worker-side registry holder for ``load_tool_activity``."""
    global _LOAD_SHIM
    if _LOAD_SHIM is None:
        _LOAD_SHIM = _LoadRegistryShim()
    return _LOAD_SHIM

# Temporal requires start_to_close or schedule_to_close on every activity;
# config leaves both unset ("we do not cap"), so apply the shared one-day
# schedule-to-close fallback (same behavior as workflow.py's _closable).
_ACTIVITY_OPTIONS = closable_activity_options(
    dict(
        start_to_close_timeout=MODEL_START_TO_CLOSE,
        schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE,
        heartbeat_timeout=MODEL_HEARTBEAT,
        retry_policy=MODEL_RETRY_POLICY,
    )
)

_SKIP_PARAMS = frozenset({"self", "cls", "agent", "tool"})


def load_tool_search_dirs() -> list[Path]:
    """Directories official ``load_tool`` may resolve ``path`` against."""
    from skills_config import skills_dir

    ordered = [
        STRANDS_TOOLS_DIR,
        Path.cwd() / "tools",
        _ORCHESTRATOR_DIR,
        skills_dir(),
        _STRANDS_TOOLS_REPO / "skills",
        _STRANDS_TOOLS_REPO / "src" / "skills",
    ]
    seen: set[Path] = set()
    dirs: list[Path] = []
    for raw in ordered:
        resolved = raw.expanduser().resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        dirs.append(resolved)
    return dirs


def _looks_in(root: Path, rel: Path) -> Path | None:
    name = rel.name if rel.suffix == ".py" else f"{rel.name}.py"
    candidates = [root / rel, root / name]
    if rel.suffix != ".py":
        candidates.append(root / f"{rel}.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for pattern in (f"*/{name}", f"*/scripts/{name}"):
        for match in root.glob(pattern):
            if match.is_file():
                return match
    return None


def tool_file_path(path: str) -> str:
    """Resolve a load_tool path the official tool can open.

    Official load_tool only accepts a file that exists. Bare names and
    ``tools/`` / ``orchestrator/`` prefixes are resolved against the installed
    ``strands_tools`` package, ``cwd()/tools`` (meta-tooling), this package
    (``skills_loader.py``), and the Agent Skills catalogs.
    """
    path = expanduser(path)
    if os.path.exists(path):
        return str(Path(path).absolute())
    rel = Path(path)
    parts = rel.parts
    if parts and parts[0] in {"tools", "orchestrator"}:
        rel = Path(*parts[1:]) if len(parts) > 1 else Path(rel.name)
    for root in load_tool_search_dirs():
        found = _looks_in(root, rel)
        if found is not None:
            return str(found)
    fallback = STRANDS_TOOLS_DIR / rel
    return str(fallback if fallback.suffix == ".py" else STRANDS_TOOLS_DIR / f"{rel.name}.py")


def _tool_input_signature(loaded: AgentTool) -> inspect.Signature:
    """Input signature for the activity, derived from the public ToolSpec.

    The registry's loaded tools are ``AgentTool`` instances (not all callable),
    so the signature comes from the tool's published inputSchema — never from
    private internals.
    """
    schema = loaded.tool_spec.get("inputSchema") or {}
    json_schema = schema.get("json", schema) if isinstance(schema, dict) else {}
    required = set(json_schema.get("required") or [])
    return inspect.Signature(
        [
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=inspect.Parameter.empty if name in required else None,
                annotation=Any,
            )
            for name in json_schema.get("properties") or {}
        ]
    )


def _as_activity(loaded: AgentTool):
    """Named ``@activity.defn`` so ``activity_as_tool`` can bind ``tool_use`` input.

    The worker does not register this function. ``run_loaded_tool`` (dynamic)
    runs the loaded ``AgentTool`` by ``activity.info().activity_type``.
    """
    signature = _tool_input_signature(loaded)

    async def _run(*args: Any, **kwargs: Any) -> Any:
        values = list(args) if args else list(kwargs.values())
        return await _invoke_loaded(_IMPLEMENTATIONS[loaded.tool_name], values)

    _run.__name__ = loaded.tool_name
    _run.__qualname__ = loaded.tool_name
    _run.__doc__ = loaded.__doc__ if isinstance(loaded.__doc__, str) else None
    _run.__signature__ = signature  # type: ignore[attr-defined]
    return activity.defn(name=loaded.tool_name)(_run)


def as_agent_tool(loaded: AgentTool, path: str | None = None) -> AgentTool:
    """Register an I/O @tool as activity_as_tool. Worker runs it via run_loaded_tool."""
    _IMPLEMENTATIONS[loaded.tool_name] = loaded
    if path:
        _LOADED_PATHS[loaded.tool_name] = path
    wrapped = activity_as_tool(_as_activity(loaded), **_ACTIVITY_OPTIONS)
    wrapped._spec = {**loaded.tool_spec, "name": loaded.tool_name}
    return wrapped


def wrap_loaded_io_tool(agent: Any, name: str, path: str | None = None) -> None:
    """Replace an officially loaded I/O tool with activity_as_tool."""
    loaded = agent.tool_registry.registry.get(name)
    if loaded is None or loaded.tool_type == "temporal_activity":
        return
    wrapped = as_agent_tool(loaded, path)
    wrapped.mark_dynamic()
    agent.tool_registry.replace(wrapped)


def _resolve_tool_file(path: str, name: str) -> str:
    """Resolve ``path`` for the official ``load_tool`` (bare name → package).

    A bare ``name`` (no directory separator) resolves inside the installed
    ``strands_tools`` package — e.g. ``load_tool(name="calculator")``; anything
    else goes through the ``tool_file_path`` search roots.
    """
    bare = Path(path).name in {path, name, f"{name}.py"} and "/" not in path
    if bare:
        candidate = STRANDS_TOOLS_DIR / f"{name}.py"
        if candidate.is_file():
            return str(candidate)
    return tool_file_path(path)


@activity.defn(name="load_tool")
async def load_tool_activity(path: str, name: str) -> dict:
    """Dynamically load a Python tool file and register it with the agent.

    The public ``strands_tools.load_tool.load_tool`` call, run on the worker
    (the workflow cannot run the official sync ``@tool`` — Strands wraps sync
    tools in ``asyncio.to_thread``, which Temporal workflows block). The loaded
    tool registers with the agent inside this worker process; ``_HotLoadHook``
    then records it on workflow state and wraps it as an activity for future
    turns and continue-as-new.

    Args:
        path: Path to the tool's ``.py`` file, or the bare module name of a
            community tool in the installed ``strands_tools`` package
            (``path="calculator"`` resolves ``strands_tools/calculator.py``).
        name: Name to register the tool under.
    """
    resolved = _resolve_tool_file(path, name)
    if Path(resolved).stem in {"cursor", "use_computer", "browser", "aws_virtual_desktop"}:
        raise ApplicationError(
            "Desktop tools are already registered on the isolated desktop worker; use those native tools.",
            non_retryable=True,
        )
    shim = _load_target_agent()
    result = official_load_tool(path=resolved, name=name, agent=shim)
    if result.get("status") == "error":
        text = next(
            (
                block.get("text", "")
                for block in result.get("content") or []
                if isinstance(block, dict)
            ),
            "",
        )
        raise ApplicationError(
            text or f"Failed to load tool {name!r} from {resolved}",
            type="LoadToolFailed",
            non_retryable=True,
        )
    loaded = shim.tool_registry.registry.get(name)
    if loaded is not None:
        _IMPLEMENTATIONS[loaded.tool_name] = loaded
        _LOADED_PATHS[loaded.tool_name] = resolved
    return result


@activity.defn(name="mcp_client")
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
    auth: Any = None,
    agent: Any = None,
) -> dict[str, Any]:
    """Official ``mcp_client``, registered on the worker per Temporal Strands README."""
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
        "agent": agent,
    }
    result = official_mcp_client(**{k: v for k, v in call.items() if v is not None})
    _track_extra_connection(call, result)
    return result


def register_community_tool(agent: Any, path: str, name: str) -> list[str]:
    path = tool_file_path(path)
    result = official_load_tool(path=path, name=name, agent=agent)
    if result.get("status") != "success":
        raise RuntimeError(result)
    wrap_loaded_io_tool(agent, name, path)
    return [name]


def unload_community_tool(agent: Any, name: str) -> None:
    registry = agent.tool_registry
    registry.registry.pop(name, None)
    registry.dynamic_tools.pop(name, None)
    _IMPLEMENTATIONS.pop(name, None)
    _LOADED_PATHS.pop(name, None)


def _reload_implementation(name: str) -> AgentTool:
    path = _LOADED_PATHS.get(name)
    if not path or not os.path.exists(path):
        raise KeyError(name)
    tools = load_tools_from_file_path(path)
    matching = [loaded for loaded in tools if loaded.tool_name == name]
    for loaded in matching or tools:
        _IMPLEMENTATIONS[loaded.tool_name] = loaded
        _LOADED_PATHS[loaded.tool_name] = path
    if name not in _IMPLEMENTATIONS:
        raise KeyError(name)
    return _IMPLEMENTATIONS[name]


def _bind_input(loaded: AgentTool, values: list[Any]) -> dict[str, Any]:
    names = [param.name for param in _tool_input_signature(loaded).parameters.values()]
    # ``TemporalActivityTool.stream`` applies signature defaults, so optional
    # params the model never sent arrive here as explicit None. Loaded tools
    # re-validate their input (calculator's pydantic model rejects ``None`` for
    # its string/int fields), so omit them and let the tool's own defaults run.
    return {
        name: values[index]
        for index, name in enumerate(names)
        if index < len(values) and values[index] is not None
    }


async def _invoke_loaded(loaded: AgentTool, values: list[Any]) -> Any:
    tool_use: ToolUse = {
        "toolUseId": activity.info().activity_id,
        "name": loaded.tool_name,
        "input": _bind_input(loaded, values),
    }
    result: Any = None
    async for event in loaded.stream(tool_use, {}):
        if isinstance(event, ToolResultEvent):
            result = event.tool_result
        elif isinstance(event, dict) and "tool_result" in event:
            result = event["tool_result"]
        elif isinstance(event, dict) and "status" in event:
            result = event
    return result


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
    """Execute a load_tool I/O tool or an extra MCP {server}-list-tools / {server}-call-tool."""
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
    loaded = _IMPLEMENTATIONS.get(activity_type)
    if loaded is None:
        loaded = _reload_implementation(activity_type)
    return await _invoke_loaded(loaded, values)
