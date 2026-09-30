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

import ast
import functools
import html
import importlib.util
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
_MCP_CONFIG_PATH = Path(__file__).resolve().parent / "mcp.json"
_PACKAGES_PATH = Path(__file__).resolve().parent / "subagent_packages.json"
_SKIP_DIRS = {"node_modules", "__pycache__", "tests", "test", "skills"}

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
    resolved by ``importlib``). A bare package id resolves to the nested file
    that defines its tools (``dicom`` -> ``dicom/strands_tools.py``). Anything
    else is returned unchanged for the official tool to report in its own words.
    """
    expanded = os.path.expanduser(path)
    if os.path.exists(expanded):
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


def _err(text: str) -> dict[str, Any]:
    return {"status": "error", "content": [{"text": text}]}


def _xml(tag: str, body: str) -> str:
    return f"<{tag}>\n{body}\n</{tag}>"


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _py_tools() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for py in STRANDS_TOOLS_DIR.rglob("*.py"):
        if _SKIP_DIRS.intersection(py.parts) or py.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py.read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                tool_name = _decorated_tool_name(dec, node.name)
                if tool_name:
                    found.append((tool_name, str(py)))
    found.sort()
    return found


def _decorated_tool_name(dec: ast.expr, fallback: str) -> str | None:
    fid = getattr(dec, "id", None) or getattr(getattr(dec, "func", None), "id", None)
    attr = getattr(getattr(dec, "func", None), "attr", None)
    if fid not in {"tool", "strands_tool"} and attr not in {"tool", "strands_tool"}:
        return None
    if isinstance(dec, ast.Call):
        for kw in dec.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
    return fallback


def _mcp_config() -> dict[str, Any]:
    if not _MCP_CONFIG_PATH.is_file():
        return {}
    return json.loads(_MCP_CONFIG_PATH.read_text())


def _disk_mcp_servers() -> list[str]:
    names: list[str] = []
    for child in STRANDS_TOOLS_DIR.iterdir():
        if child.is_dir() and "mcp" in child.name.lower() and child.name not in _SKIP_DIRS:
            names.append(child.name)
    return sorted(names)


def _resolve_arg(arg: str) -> str:
    if arg.startswith("-"):
        return arg
    candidate = (STRANDS_TOOLS_DIR / arg).resolve()
    if str(candidate).startswith(str(STRANDS_TOOLS_DIR)) and candidate.exists():
        return str(candidate)
    return arg


def _server_line(name: str, cfg: dict[str, Any], status: str) -> str:
    if not cfg:
        return f'<server name="{_esc(name)}" status="{status}"/>'
    if cfg.get("url"):
        return (
            f'<server name="{_esc(name)}" status="{status}" '
            f'transport="streamable_http" server_url="{_esc(str(cfg["url"]))}" '
            f'credentials="{"worker-configured" if cfg.get("headers") else "none"}"/>'
        )
    args = [_resolve_arg(str(item)) for item in (cfg.get("args") or [])]
    env = cfg.get("env") or {}
    missing = [
        value[2:-1]
        for value in env.values()
        if isinstance(value, str) and value.startswith("${") and value.endswith("}")
        and not os.environ.get(value[2:-1])
    ]
    attrs = (
        f' transport="stdio" command="{_esc(str(cfg.get("command") or ""))}" '
        f'args="{_esc(json.dumps(args))}"'
    )
    if env:
        attrs += f' env="{_esc(json.dumps(env))}"'
    if missing:
        attrs += f' env_missing="{_esc(",".join(missing))}"'
    return f'<server name="{_esc(name)}" status="{status}"{attrs}/>'


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


def _server_status(name: str, configured: dict[str, Any]) -> str:
    if name in _EXTRA_MCP_PARAMS or (name in configured and not configured[name].get("continue_on_error")):
        return "connected"
    if name in configured:
        return "started"
    return "off"


def _schemas() -> list[tuple[str, str]]:
    used = {
        str((params.get("env") or {}).get("OPENAPI_SPEC_PATH") or "")
        for params in _EXTRA_MCP_PARAMS.values()
    }
    rows: list[tuple[str, str]] = []
    for entry in _mcp_config().get("openapiSchemas") or []:
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path") or "")
        full = str((STRANDS_TOOLS_DIR / rel).resolve()) if rel else ""
        if rel and full not in used and not any(item.endswith(rel) for item in used if item):
            rows.append((str(entry.get("name") or rel), rel))
    return rows


def _read_packages() -> dict[str, Any]:
    if not _PACKAGES_PATH.is_file():
        return {}
    data = json.loads(_PACKAGES_PATH.read_text())
    return data if isinstance(data, dict) else {}


def _write_packages(data: dict[str, Any]) -> None:
    _PACKAGES_PATH.write_text(json.dumps(data, indent=2) + "\n")


def _inventory(query: str = "") -> str:
    needle = query.strip().lower()

    def keep(text: str) -> bool:
        return not needle or needle in text.lower()

    loaded = {
        getattr(tool, "tool_name", "")
        for tool in _worker_agent().tool_registry.registry.values()
    }
    tools = []
    seen: set[str] = set()
    for tool_name, path in _py_tools():
        seen.add(tool_name)
        line = (
            f'<tool name="{_esc(tool_name)}" path="{_esc(path)}" '
            f'loaded="{str(tool_name in loaded).lower()}"/>'
        )
        if keep(line):
            tools.append(line)
    for tool_name in sorted(loaded - seen):
        if not tool_name:
            continue
        line = f'<tool name="{_esc(tool_name)}" path="meta" loaded="true"/>'
        if keep(line):
            tools.append(line)
    skills = []
    try:
        from skills_config import discovered_skills

        for skill in discovered_skills():
            desc = (getattr(skill, "description", "") or "").replace("\n", " ").strip()[:160]
            line = f'<skill name="{_esc(skill.name)}" description="{_esc(desc)}"/>'
            if keep(line):
                skills.append(line)
    except Exception as error:  # noqa: BLE001 - catalog is optional
        skills.append(f"<!-- skills unavailable: {_esc(str(error))} -->")
    configured = _mcp_config().get("mcpServers") or {}
    servers = []
    names = sorted(set(configured) | set(_disk_mcp_servers()) | set(_EXTRA_MCP_PARAMS))
    for server in names:
        status = _server_status(server, configured)
        line = _server_line(server, configured.get(server) or {}, status)
        if keep(line):
            servers.append(line)
    schemas = [
        f'<schema name="{_esc(label)}" path="{_esc(rel)}"/>'
        for label, rel in _schemas()
        if keep(label + rel)
    ]
    packages = []
    for pkg_name, pkg in _read_packages().items():
        line = f'<package name="{_esc(pkg_name)}">{_esc(json.dumps(pkg))}</package>'
        if keep(line):
            packages.append(line)
    return "\n".join([
        _xml("tools", "\n".join(tools)),
        _xml("skills", "\n".join(skills)),
        _xml("mcp_servers", "\n".join(servers)),
        _xml("openapi_schemas", "\n".join(schemas)),
        _xml("packages", "\n".join(packages)),
    ])


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
async def load_tool_activity(
    path: str = "",
    name: str = "",
    action: str = "load",
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    query: str = "",
) -> dict:
    """Load, unload, or list tools, skills, MCP servers, schemas, and packages.

    Default action is load: ``path`` is a bare module name or a ``.py`` file,
    ``name`` is the registry name. ``list_tools`` returns tools (including
    meta-tools), skills, MCP servers, unconverted OpenAPI schemas, and
    subagent packages. ``unload`` removes a name this tool loaded.

    Args:
        path: Bare community module name (``calculator``, ``dicom``, ``fhir``,
            ``forge``) or a ``.py`` file you created.
        name: Registry name to load or unload, or the subagent package name.
        action: load (default), unload, list_tools, list_mcp_servers,
            list_schemas, list_skills, list_packages, save_package, or
            delete_package.
        tools: Tool names stored on a subagent package.
        skills: Catalog skill names stored on a subagent package.
        mcp_servers: MCP server names stored on a subagent package.
        query: Optional substring filter for list_tools.
    """
    if action == "load":
        return official_load_tool(path=tool_file_path(path), name=name, agent=_worker_agent())
    if action == "unload":
        return await _unload(name)
    if action in {"list_tools", "list_mcp_servers", "list_schemas", "list_skills", "list_packages"}:
        text = _inventory(query)
        if action != "list_tools":
            tag = {
                "list_mcp_servers": "mcp_servers",
                "list_schemas": "openapi_schemas",
                "list_skills": "skills",
                "list_packages": "packages",
            }[action]
            start = text.index(f"<{tag}>")
            end = text.index(f"</{tag}>") + len(f"</{tag}>")
            text = text[start:end]
        return _ok(text)
    if action == "save_package":
        if not name:
            return _err("package name is required")
        packages = _read_packages()
        packages[name] = {
            "tools": list(tools or []),
            "skills": list(skills or []),
            "mcp_servers": list(mcp_servers or []),
        }
        _write_packages(packages)
        return _ok(f"saved package {name}")
    if action == "delete_package":
        packages = _read_packages()
        if name not in packages:
            return _err(f"no package {name}")
        del packages[name]
        _write_packages(packages)
        return _ok(f"deleted package {name}")
    return _err(f"unknown load_tool action {action!r}")


async def _unload(name: str) -> dict:
    if not name:
        return _err("name is required")
    loaded_path = await _loaded_tool_path(name)
    if loaded_path is None and name not in _worker_agent().tool_registry.registry:
        return _err(f"{name} is not a loaded tool")
    if loaded_path is None:
        return _err(f"{name} is not a loaded tool")
    _worker_agent().tool_registry.registry.pop(name, None)
    return _ok(f"unloaded {name}")


@activity.defn(name="mcp_client")
@functools.wraps(official_mcp_client, updated=())
async def mcp_client_activity(*args: Any, **kwargs: Any) -> dict[str, Any]:
    bound = inspect.signature(official_mcp_client).bind_partial(*args, **kwargs)
    if bound.arguments.get("action") == "connect":
        kwargs = dict(bound.arguments)
        params = dict(kwargs.get("server_config") or {})
        configured = (_mcp_config().get("mcpServers") or {}).get(kwargs.get("connection_id"), {})
        if configured.get("url"):
            resolved, missing = _expand_env({"server_url": configured["url"]})
            if resolved is None:
                return _err(f"cannot connect: missing environment {', '.join(missing)}")
            configured_url = resolved["server_url"]
            supplied_url = kwargs.get("server_url") or params.get("server_url")
            if supplied_url and supplied_url not in (configured["url"], configured_url):
                return _err("configured connection_id must use its configured server URL")
            kwargs["server_url"] = configured_url
            kwargs["transport"] = configured.get("transport", "streamable_http").replace("-", "_")
            if configured.get("headers"):
                kwargs["headers"] = configured["headers"]
        for field in ("env", "headers"):
            values = kwargs.get(field, params.get(field))
            if isinstance(values, dict):
                expanded, missing = _expand_env(values)
                if missing:
                    return _err(f"cannot connect: missing environment {', '.join(missing)}")
                kwargs[field] = expanded
        args = ()
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
