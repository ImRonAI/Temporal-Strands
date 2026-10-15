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
import subprocess
import sys
from pathlib import Path

from strands.tools.mcp import MCPClient
from strands.tools.registry import ToolRegistry
from strands_tools.mcp_client import mcp_client

from load_tool import (
    STRANDS_TOOLS_DIR,
    register_community_tool,
    tool_file_path,
)
from strands_tools.load_tool import load_tool as _official_load_tool  # noqa: F401
from run_worker import MCP_CONFIG_PATH, _ROOT
from agent_tools import (
    COMPUTER_USE_TOOLS,
    PERMANENT_COMMUNITY_TOOLS,
    THINK_TOOL,
    mcp_client_factories,
    temporal_mcp_clients,
)


def test_permanent_registry_includes_skills_and_graph() -> None:
    registry = ToolRegistry()
    names = registry.process_tools(list(PERMANENT_COMMUNITY_TOOLS))
    assert names == ["load_tool", "browser", "mcp_client", "graph", "use_agent", "use_skill", "python_repl"]
    assert sorted(registry.registry) == [
        "browser", "graph", "load_tool", "mcp_client", "python_repl", "use_agent", "use_skill",
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

    identity = json.loads(Path(__file__).resolve().parents[1].joinpath("agent.json").read_text())
    prompt = identity["prompt"]
    for marker in ("strands_tools.", "skills_loader.py", "list_skills"):
        assert marker not in prompt, marker

    tools = {
        tool.tool_name: tool
        for tool in (
            *PERMANENT_COMMUNITY_TOOLS,
            THINK_TOOL,
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
    assert "current_time" in load_props["path"]["description"]
    assert "strands_tools.current_time" not in load_props["path"]["description"]

    use_agent_tools = tools["use_agent"].tool_spec["inputSchema"]["json"]["properties"]["tools"]
    assert "Registry names" in use_agent_tools["description"]


def test_graph_tool_exposes_formation_schema() -> None:
    graph_tool = next(tool for tool in PERMANENT_COMMUNITY_TOOLS if tool.tool_name == "graph")
    spec = graph_tool.tool_spec["inputSchema"]["json"]["properties"]
    description = graph_tool.tool_spec["description"]
    for marker in ("system_prompt", "model_settings", "multiagent_handoff"):
        assert marker in description or marker in spec["topology"]["description"]
    assert "required for execute" in spec["task"]["description"]
    assert "nodes" in spec["topology"]["description"]
    assert "edges" in spec["topology"]["description"]


def test_mcp_json_servers_use_temporal_mcp_client(monkeypatch) -> None:
    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://api.datacommons.org/mcp")
    monkeypatch.setenv("DC_API_KEY", "test-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://mcp.pophive.org/mcp")
    monkeypatch.setenv("TELNYX_API_KEY", "test-key")
    expected = set(json.loads(MCP_CONFIG_PATH.read_text())["mcpServers"])
    factories = mcp_client_factories()
    assert set(factories) == expected
    assert {"shell", "agent-sops", "telnyx-readonly", "telnyx-docs", "cms-coverage"} <= expected
    # Handles capture the worker environment at import, before workflow replay.
    result = subprocess.run(
        [sys.executable, "-c", """
import json
from agent_tools import temporal_mcp_clients
handles = temporal_mcp_clients()
print(json.dumps({"names": [handle.server for handle in handles],
                  "cached": all(handle._cache_tools for handle in handles)}))
"""],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    handles = json.loads(result.stdout)
    assert set(handles["names"]) == expected
    assert handles["cached"] is True


def test_strands_tools_dir_is_the_installed_package() -> None:
    """A path is used as given; a strands_tools module name resolves via importlib."""
    import strands_tools

    assert STRANDS_TOOLS_DIR == Path(strands_tools.__file__).resolve().parent
    assert not (_ROOT / "tools").exists()
    assert (STRANDS_TOOLS_DIR / "shell.py").is_file()
    existing = str(STRANDS_TOOLS_DIR / "shell.py")
    assert tool_file_path(existing) == existing
    assert tool_file_path("shell") == existing
    assert tool_file_path("shell.py") == existing
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
    # The schema catalog is separate from executable MCP server registrations.
    assert servers["telnyx"]["headers"]["Authorization"] == "Bearer ${TELNYX_API_KEY}"
    for name in ("telnyx", "telnyx-readonly", "telnyx-docs"):
        assert servers[name]["continue_on_error"] is True
        assert servers[name]["url"].startswith("https://")
        assert "command" not in servers[name]
    assert any(schema["name"] == "telnyx" for schema in config["openapiSchemas"])


def test_startup_connect_skips_streamable_http_catalog(monkeypatch) -> None:
    """Worker boot only pre-connects stdio. HTTP catalog servers stay on-demand."""
    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://api.datacommons.org/mcp")
    monkeypatch.setenv("DC_API_KEY", "test-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://mcp.pophive.org/mcp")
    monkeypatch.setenv("TELNYX_API_KEY", "test-key")
    calls: list[dict] = []

    def fake_mcp_client(**kwargs):
        calls.append(kwargs)
        return {"status": "success"}

    monkeypatch.setattr("strands_tools.mcp_client.mcp_client", fake_mcp_client)
    from run_worker import connect_included_mcp_servers

    names = connect_included_mcp_servers()
    assert names == ["agent-sops", "shell"]
    assert [call.get("transport") for call in calls] == ["stdio", "stdio"]
    assert [call["connection_id"] for call in calls] == ["agent-sops", "shell"]


def test_mcp_client_factories_omit_only_telnyx_without_its_required_key(
    monkeypatch, caplog,
) -> None:
    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://api.datacommons.org/mcp")
    monkeypatch.setenv("DC_API_KEY", "test-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://mcp.pophive.org/mcp")
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    expected = set(json.loads(MCP_CONFIG_PATH.read_text())["mcpServers"])
    assert set(mcp_client_factories()) == expected - {"telnyx"}
    assert any("telnyx" in record.getMessage() and "TELNYX_API_KEY" in record.getMessage()
               for record in caplog.records)


def test_load_servers_skips_optional_servers_without_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATACOMMONS_MCP_URL", raising=False)
    monkeypatch.delenv("DC_API_KEY", raising=False)
    monkeypatch.delenv("POPHIVE_MCP_URL", raising=False)
    monkeypatch.setenv("TELNYX_API_KEY", "test-key")
    clients = MCPClient.load_servers(str(MCP_CONFIG_PATH))
    names = {client._application_name for client in clients}
    assert "shell" in names
    assert "datacommons" not in names
    assert "pophive" not in names


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
    path = str(STRANDS_TOOLS_DIR / "current_time.py")
    raw_agent = _Agent()
    assert _official_load_tool(path=path, name="current_time", agent=raw_agent)["status"] == "success"
    raw_spec = raw_agent.tool_registry.registry["current_time"].tool_spec

    result = register_community_tool(agent, "current_time", "current_time")
    assert result["status"] == "success"
    registered = agent.tool_registry.registry["current_time"]
    from strands.tools.decorator import DecoratedFunctionTool
    assert isinstance(registered, DecoratedFunctionTool)
    assert registered.tool_name == "current_time"
    assert registered.tool_spec == raw_spec

    # Re-registering (continue-as-new rebuild) is idempotent.
    assert register_community_tool(agent, path, "current_time")["status"] == "success"
    assert isinstance(agent.tool_registry.registry["current_time"], DecoratedFunctionTool)

    missing = register_community_tool(_Agent(), "does/not/exist.py", "x")
    assert missing["status"] == "error"


def test_register_serialized_tool_never_opens_the_tool_file(monkeypatch):
    agent = _Agent()
    spec = {"name": "remote_tool", "description": "Worker tool", "inputSchema": {
        "json": {"type": "object", "properties": {"value": {"type": "string"}}},
    }}
    monkeypatch.setattr("load_tool.official_load_tool", lambda **kwargs:
        pytest.fail("workflow registration must not import files"))
    assert register_community_tool(agent, "/not/on/workflow/host.py", "remote_tool", spec)["status"] == "success"
    assert agent.tool_registry.registry["remote_tool"].tool_spec == spec
