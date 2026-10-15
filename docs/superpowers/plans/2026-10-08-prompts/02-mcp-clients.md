# Section: MCP clients

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "MCP servers: connect all of them"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md` — `TemporalMCPClient`
- `.agents/skills/strands-temporal/scripts/strands_plugin/mcp/workflow.py`
- `.agents/skills/strands-temporal/scripts/strands_plugin/mcp/run_worker.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/mcp.json` — the server keys. Read it. Do not add `agent-sops` here. Another section adds that entry.
- `orchestrator/agent_tools.py` — `_eager_mcp_server_names`, `_server_env_missing`, `temporal_mcp_clients`

## Requirements

**Code writing.** Every server in `orchestrator/mcp.json` whose required `${VAR}` values are set becomes a `TemporalMCPClient`. `continue_on_error: true` means a failed handshake does not crash the worker. It does not mean the server is left out of the factory. `_server_env_missing` still drops a server when a variable is unset, and the log line names the server and the variable. Do not read `.env*`. Do not invent a new MCP client class. Use the `TemporalMCPClient` construction this file already uses for `shell`. Timeouts and queue names stay in `config.py`.

**Subagent use.** Do not launch subagents. This is one factory change and two tests. A per-server agent would edit the same function eleven times.

**Prompting.** The plan table is the server list. Do not add a server that is not in `mcp.json`. Do not drop `telnyx-readonly`, `telnyx-docs`, or `unsloth` because the plan's "what the agent gets" cell is short. They are in the file, so they are in the factory when their env is satisfied.

**Scope.** In: `orchestrator/agent_tools.py`, `orchestrator/tests/test_mcp_shell.py`. Out: `orchestrator/mcp.json`, `orchestrator/workflow.py`, `orchestrator/run_worker.py`, `.env*`. The `agent-sops` entry is not yours. If a change requires a file outside this list, stop and name the file.

## Outcome

With the env vars set, the factory returns every server key in the plan table. With `TELNYX_API_KEY` unset, `telnyx` is the only key absent, and the log names `telnyx` and `TELNYX_API_KEY`. `shell` stays mounted. `openapi` stays in the factory when its variables are present as empty strings; a later app test records it if the process exits. You do not start the servers.

Run from `orchestrator/`:

```bash
.venv/bin/python -m pytest tests/test_mcp_shell.py -q
```

Report a collection error that imports `TurnInput` or `_ToolResultHook`. Do not reintroduce those symbols.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "MCP servers: connect all of them"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md` — `TemporalMCPClient`
- `.agents/skills/strands-temporal/scripts/strands_plugin/mcp/workflow.py`
- `.agents/skills/strands-temporal/scripts/strands_plugin/mcp/run_worker.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/mcp.json` — the server keys. Read it. Do not add `agent-sops` here. Another section adds that entry.
- `orchestrator/agent_tools.py` — `_eager_mcp_server_names`, `_server_env_missing`, `temporal_mcp_clients`

## Requirements

**Code writing.** Every server in `orchestrator/mcp.json` whose required `${VAR}` values are set becomes a `TemporalMCPClient`. `continue_on_error: true` means a failed handshake does not crash the worker. It does not mean the server is left out of the factory. `_server_env_missing` still drops a server when a variable is unset, and the log line names the server and the variable. Do not read `.env*`. Do not invent a new MCP client class. Use the `TemporalMCPClient` construction this file already uses for `shell`. Timeouts and queue names stay in `config.py`.

**Subagent use.** Do not launch subagents. This is one factory change and two tests. A per-server agent would edit the same function eleven times.

**Prompting.** The plan table is the server list. Do not add a server that is not in `mcp.json`. Do not drop `telnyx-readonly`, `telnyx-docs`, or `unsloth` because the plan's "what the agent gets" cell is short. They are in the file, so they are in the factory when their env is satisfied.

**Scope.** In: `orchestrator/agent_tools.py`, `orchestrator/tests/test_mcp_shell.py`. Out: `orchestrator/mcp.json`, `orchestrator/workflow.py`, `orchestrator/run_worker.py`, `.env*`. The `agent-sops` entry is not yours. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
