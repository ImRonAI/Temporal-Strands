"""Permanent load_tool + mcp_client registry, shell connection, tool discovery.

Docs:
- https://github.com/strands-agents/tools
- https://strandsagents.com/docs/user-guide/concepts/tools/community-tools-package/
- https://strandsagents.com/docs/user-guide/concepts/tools/
- https://strandsagents.com/docs/user-guide/shell/mcp-server/
"""

from __future__ import annotations

import asyncio
import json
import os

from strands.tools.mcp import MCPClient
from strands.tools.registry import ToolRegistry
from strands_tools.mcp_client import mcp_client

from load_tool import (
    STRANDS_TOOLS_DIR,
    _IMPLEMENTATIONS,
    tool_file_path,
    unload_community_tool,
    wrap_loaded_io_tool,
)
from strands_tools.load_tool import load_tool as _official_load_tool
from run_worker import MCP_CONFIG_PATH, _ROOT, ensure_strands_tools_dir
from workflow import PERMANENT_COMMUNITY_TOOLS, mcp_client_factories, temporal_mcp_clients


def test_permanent_registry_includes_skills_and_graph() -> None:
    registry = ToolRegistry()
    names = registry.process_tools(list(PERMANENT_COMMUNITY_TOOLS))
    assert names == ["load_tool", "mcp_client", "graph", "use_skill"]
    assert sorted(registry.registry) == ["graph", "load_tool", "mcp_client", "use_skill"]
    assert registry.registry["load_tool"].tool_type != "temporal_activity"
    assert registry.registry["use_skill"].tool_type == "temporal_activity"
    assert registry.registry["mcp_client"].tool_type == "temporal_activity"


def test_mcp_json_servers_use_temporal_mcp_client() -> None:
    factories = mcp_client_factories()
    assert set(factories) == {"shell"}
    handles = temporal_mcp_clients()
    assert {handle.server for handle in handles} == {"shell"}
    assert all(handle._cache_tools for handle in handles)


def test_strands_tools_dir_exposes_the_installed_package() -> None:
    tools_dir = ensure_strands_tools_dir()
    assert tools_dir == _ROOT / "tools"
    assert tools_dir == STRANDS_TOOLS_DIR
    assert (tools_dir / "file_read.py").is_file()
    assert (tools_dir / "shell.py").is_file()
    assert tool_file_path("file_read.py") == str(tools_dir / "file_read.py")
    assert tool_file_path("tools/file_read.py") == str(tools_dir / "file_read.py")
    assert os.path.isfile(tool_file_path("file_read"))


def test_mcp_json_catalog_keeps_shell_and_optional_servers() -> None:
    config = json.loads(MCP_CONFIG_PATH.read_text())
    servers = config["mcpServers"]
    assert servers["shell"] == {
        "command": ".venv/bin/strands-shell",
        "args": ["--mcp"],
    }
    assert servers["datacommons"]["continue_on_error"] is True
    assert servers["pophive"]["continue_on_error"] is True


def test_mcp_client_factories_skips_temporal_list_tools_for_remote_catalog(
    monkeypatch,
) -> None:
    """Remote mcp.json servers stay catalogued but never register {name}-list-tools."""
    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://api.datacommons.org/mcp")
    monkeypatch.setenv("DC_API_KEY", "test-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://example.com/mcp")
    assert set(mcp_client_factories()) == {"shell"}


def test_load_servers_skips_optional_servers_without_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATACOMMONS_MCP_URL", raising=False)
    monkeypatch.delenv("DC_API_KEY", raising=False)
    monkeypatch.delenv("POPHIVE_MCP_URL", raising=False)
    clients = MCPClient.load_servers(str(MCP_CONFIG_PATH))
    assert len(clients) == 1


def test_mcp_client_connects_shell_and_accepts_another_connection() -> None:
    binary = _ROOT / ".venv/bin/strands-shell"
    assert binary.is_file()
    command = str(binary.resolve())
    try:
        shell = mcp_client(
            action="connect",
            connection_id="shell",
            transport="stdio",
            command=command,
            args=["--mcp"],
        )
        assert shell["status"] == "success"
        extra = mcp_client(
            action="connect",
            connection_id="shell-extra",
            transport="stdio",
            command=command,
            args=["--mcp"],
        )
        assert extra["status"] == "success"
        listed = mcp_client(action="list_connections")
        ids = {
            conn["connection_id"]
            for conn in listed["content"][1]["json"]["connections"]
        }
        assert "shell" in ids
        assert "shell-extra" in ids
        assert "datacommons" not in ids
        assert "pophive" not in ids
    finally:
        mcp_client(action="disconnect", connection_id="shell-extra")
        mcp_client(action="disconnect", connection_id="shell")


class _Agent:
    def __init__(self) -> None:
        self.tool_registry = ToolRegistry()


def test_permanent_load_tool_is_the_official_strands_tool() -> None:
    assert PERMANENT_COMMUNITY_TOOLS[0] is _official_load_tool
    assert PERMANENT_COMMUNITY_TOOLS[0].tool_type != "temporal_activity"


def test_load_tool_registers_io_community_tool_as_activity() -> None:
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    tools_dir = ensure_strands_tools_dir()
    agent = _Agent()
    path = str(tools_dir / "file_read.py")
    result = _official_load_tool(path=path, name="file_read", agent=agent)
    assert result["status"] == "success"
    # The official loader registers the raw I/O tool; the workflow hook then
    # swaps it for the activity-backed one, as _HotLoadHook does after success.
    assert agent.tool_registry.registry["file_read"].tool_type != "temporal_activity"
    wrap_loaded_io_tool(agent, "file_read", path)
    registered = agent.tool_registry.registry["file_read"]
    assert registered.tool_type == "temporal_activity"
    assert registered.tool_name == "file_read"
    assert "file_read" in _IMPLEMENTATIONS

    unload_community_tool(agent, "file_read")
    assert "file_read" not in agent.tool_registry.registry
    assert "file_read" not in _IMPLEMENTATIONS
