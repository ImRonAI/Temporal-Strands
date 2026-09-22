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
from pathlib import Path

from strands.tools.mcp import MCPClient
from strands.tools.registry import ToolRegistry
from strands_tools.mcp_client import mcp_client

from load_tool import (
    STRANDS_TOOLS_DIR,
    LoadedActivityTool,
    register_community_tool,
    tool_file_path,
)
from strands_tools.load_tool import load_tool as _official_load_tool  # noqa: F401
from run_worker import MCP_CONFIG_PATH, _ROOT
from workflow import PERMANENT_COMMUNITY_TOOLS, mcp_client_factories, temporal_mcp_clients


def test_permanent_registry_includes_skills_and_graph() -> None:
    registry = ToolRegistry()
    names = registry.process_tools(list(PERMANENT_COMMUNITY_TOOLS))
    assert names == ["load_tool", "browser", "mcp_client", "graph", "use_agent", "use_skill"]
    assert sorted(registry.registry) == [
        "browser", "graph", "load_tool", "mcp_client", "use_agent", "use_skill",
    ]
    assert registry.registry["load_tool"].tool_type == "temporal_activity"
    assert registry.registry["use_skill"].tool_type == "temporal_activity"
    assert registry.registry["use_agent"].tool_type == "temporal_activity"
    assert registry.registry["mcp_client"].tool_type == "temporal_activity"


_PACKAGE_PATH_MARKERS = (
    "strands_tools.",
    "skills_loader.py",
    "module path",
    ".py file path",
)


def _schema_text(tool) -> str:
    spec = tool.tool_spec
    return json.dumps(spec, default=str)


def test_registry_contract_is_activity_names_not_package_paths() -> None:
    """Pattern 2: the model sees Temporal activity names, not import paths."""
    from pathlib import Path

    from workflow import AGENT_API_TOOLS, COMPUTER_USE_TOOLS, THINK_TOOL

    identity = json.loads(Path(__file__).resolve().parents[1].joinpath("agent.json").read_text())
    prompt = identity["prompt"]
    for marker in ("strands_tools.", "skills_loader.py", "list_skills"):
        assert marker not in prompt, marker

    tools = {
        tool.tool_name: tool
        for tool in (
            *PERMANENT_COMMUNITY_TOOLS,
            THINK_TOOL,
            *AGENT_API_TOOLS,
            *COMPUTER_USE_TOOLS,
        )
    }
    assert "load_tool" in tools
    assert "graph" in tools
    assert "use_agent" in tools
    assert "use_skill" in tools
    for name in ("load_tool", "graph", "use_agent", "use_skill"):
        text = _schema_text(tools[name])
        for marker in _PACKAGE_PATH_MARKERS:
            assert marker not in text, f"{name} schema still contains {marker!r}"

    load_props = tools["load_tool"].tool_spec["inputSchema"]["json"]["properties"]
    assert "calculator" in load_props["path"]["description"]
    assert "strands_tools.calculator" not in load_props["path"]["description"]

    use_agent_tools = tools["use_agent"].tool_spec["inputSchema"]["json"]["properties"]["tools"]
    assert "registry" in use_agent_tools["description"]


def test_graph_tool_exposes_formation_schema() -> None:
    graph_tool = next(tool for tool in PERMANENT_COMMUNITY_TOOLS if tool.tool_name == "graph")
    spec = graph_tool.tool_spec["inputSchema"]["json"]["properties"]
    description = graph_tool.tool_spec["description"]
    for marker in ("system_prompt", "model_settings", "multiagent_handoff"):
        assert marker in description or marker in spec["topology"]["description"]
    assert "required for execute" in spec["task"]["description"]
    assert "nodes" in spec["topology"]["description"]
    assert "edges" in spec["topology"]["description"]


def test_mcp_json_servers_use_temporal_mcp_client() -> None:
    factories = mcp_client_factories()
    assert set(factories) == {"shell"}
    handles = temporal_mcp_clients()
    assert {handle.server for handle in handles} == {"shell"}
    assert all(handle._cache_tools for handle in handles)


def test_strands_tools_dir_is_the_installed_package() -> None:
    """A path is used as given; a strands_tools module name resolves via importlib."""
    import strands_tools

    assert STRANDS_TOOLS_DIR == Path(strands_tools.__file__).resolve().parent
    assert not (_ROOT / "tools").exists()
    assert (STRANDS_TOOLS_DIR / "file_read.py").is_file()
    assert (STRANDS_TOOLS_DIR / "shell.py").is_file()
    existing = str(STRANDS_TOOLS_DIR / "file_read.py")
    assert tool_file_path(existing) == existing
    assert tool_file_path("file_read") == existing
    assert tool_file_path("file_read.py") == existing
    assert tool_file_path("does/not/exist.py") == "does/not/exist.py"


def test_mcp_json_catalog_keeps_shell_and_optional_servers() -> None:
    config = json.loads(MCP_CONFIG_PATH.read_text())
    servers = config["mcpServers"]
    assert servers["shell"] == {
        "command": ".venv/bin/strands-shell",
        "args": ["--mcp"],
    }
    assert servers["datacommons"]["continue_on_error"] is True
    assert servers["pophive"]["continue_on_error"] is True


def test_startup_connect_skips_streamable_http_catalog(monkeypatch) -> None:
    """Worker boot only pre-connects stdio. HTTP catalog servers stay on-demand."""
    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://api.datacommons.org/mcp")
    monkeypatch.setenv("DC_API_KEY", "test-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://mcp.pophive.org/mcp")
    calls: list[dict] = []

    def fake_mcp_client(**kwargs):
        calls.append(kwargs)
        return {"status": "success"}

    monkeypatch.setattr("strands_tools.mcp_client.mcp_client", fake_mcp_client)
    from run_worker import connect_included_mcp_servers

    names = connect_included_mcp_servers()
    assert names == ["shell"]
    assert [call.get("transport") for call in calls] == ["stdio"]
    assert calls[0]["connection_id"] == "shell"


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


def test_permanent_load_tool_is_the_public_activity_wrapper() -> None:
    """The permanent ``load_tool`` is the public official call as an activity."""
    permanent = PERMANENT_COMMUNITY_TOOLS[0]
    assert permanent.tool_name == "load_tool"
    assert permanent.tool_type == "temporal_activity"
    assert permanent._activity_name == "load_tool"


def test_load_tool_registers_io_community_tool_as_activity() -> None:
    """``register_community_tool`` = official load_tool on the live agent, then the
    SDK ``replace`` with the activity-dispatching stub carrying the tool's own spec."""
    os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
    agent = _Agent()
    path = str(STRANDS_TOOLS_DIR / "file_read.py")
    raw_agent = _Agent()
    assert _official_load_tool(path=path, name="file_read", agent=raw_agent)["status"] == "success"
    raw_spec = raw_agent.tool_registry.registry["file_read"].tool_spec

    result = register_community_tool(agent, "file_read", "file_read")
    assert result["status"] == "success"
    registered = agent.tool_registry.registry["file_read"]
    assert isinstance(registered, LoadedActivityTool)
    assert registered.tool_type == "temporal_activity"
    assert registered.tool_name == "file_read"
    assert registered.tool_spec == raw_spec

    # Re-registering (continue-as-new rebuild) is idempotent.
    assert register_community_tool(agent, path, "file_read")["status"] == "success"
    assert isinstance(agent.tool_registry.registry["file_read"], LoadedActivityTool)

    missing = register_community_tool(_Agent(), "does/not/exist.py", "x")
    assert missing["status"] == "error"
