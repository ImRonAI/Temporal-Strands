# Section: delegation SOP and the cancel hook

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. The one-line append inside `run_worker.agent_identity()` is `12-worker-wiring.md`. You write the SOP file, the hook, the package dependency, and the `mcp.json` entry.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task G — Delegation SOP". The sentence that appends the SOP inside `agent_identity()` is not this section. "What the system prompt tells the agent" stays in `agent.json` and you do not edit that file.
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md` — hooks use `activity_as_hook` only when the hook does I/O. This hook does no I/O.
- `.agents/skills/strands-temporal/scripts/strands_plugin/hooks/workflow.py` — the `HookProvider` / `BeforeToolCallEvent` pattern
- https://docs.temporal.io/develop/python/integrations/strands-agents
- After you install `strands-agents-sops`, read that package's `rules/agent-sop-format.md` and match it. Do not invent the section headings. Do not copy the package's git tree into the repo.
- `orchestrator/mcp.json` — add the `agent-sops` entry only
- The installed `strands_tools.swarm` signature, so the hook can tell a direct `swarm` call from a graph node. Read it. Do not wrap it.

## Requirements

**Code writing.** Add `strands-agents-sops` to `orchestrator/requirements.txt` and install it into `orchestrator/.venv`. Write `orchestrator/sops/delegation.sop.md` in the format you just read. It tells the orchestrator: `discover_tools` before naming a tool that is not on the session; `load_tool` it; `graph` loaded that way is `graph_activity`; a direct swarm is the discovered `swarm` tool; a swarm inside a graph is a node whose members are `agent` or `skill_agent`; omit `model_id`; omit `tools` to inherit; Agent API names are not registry names. Do not put `code-assist` or `pdd` into the orchestrator prompt. Add the MCP entry with command `.venv/bin/strands-agents-sops` and args `mcp`, `--sop-paths`, `sops`. Do not set that entry up to be skipped for `continue_on_error`. The server registers prompts, and `TemporalMCPClient` only lists tools. Mount it anyway. The hook's callback only reads `event.tool_use` and may set `event.cancel_tool`. It cancels `graph`, `use_agent`, or `use_skill` for the five cases in Task G. It does not cancel a call to the discovered `swarm` tool. Do not extend `LoadedTools` for this. That hook is `AfterToolCallEvent`.

**Subagent use.** Do not launch subagents. The SOP text and the hook implement the same rules. Installing the package is part of this section. A separate installer agent is too small.

**Prompting.** Task G is the spec for the rules. The ten chains in the system-prompt section are a different document. Do not paste them into the SOP, and do not edit `agent.json`. The workflow must not read the SOP file. You export a string loaded once at import inside `workflow.unsafe.imports_passed_through()` so the wiring section can append it. If that load cannot live without editing `workflow.py` or `run_worker.py`, stop and name the file. Do not take those files.

**Scope.** In: `orchestrator/requirements.txt`, `orchestrator/sops/delegation.sop.md`, the new hook module, the `agent-sops` entry in `orchestrator/mcp.json`, and a pytest for the cancel rules. Out: `orchestrator/agent.json`, `orchestrator/run_worker.py`, `orchestrator/workflow.py`, `orchestrator/load_tool.py`. If a change requires a file outside this list, stop and name the file.

## Outcome

The SOP file matches the installed format and states the delegation rules above. The hook cancels a string topology, a node `model_id` or `model_settings`, a `workflow` or `parallel` node, a `skill_agent` with no `skill`, a graph swarm member that is not `agent` or `skill_agent`, and a `tools` entry that is neither on the session nor in the loaded paths. A direct `swarm` call is not cancelled. `mcp.json` contains `agent-sops`. The package is installed. The wiring section can append the SOP string later.

Run the new hook test from `orchestrator/` with `.venv/bin/python -m pytest`. Report a collection error that imports `TurnInput` or `_ToolResultHook`. Do not reintroduce those symbols.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task G — Delegation SOP". The sentence that appends the SOP inside `agent_identity()` is not this section. "What the system prompt tells the agent" stays in `agent.json` and you do not edit that file.
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md` — hooks use `activity_as_hook` only when the hook does I/O. This hook does no I/O.
- `.agents/skills/strands-temporal/scripts/strands_plugin/hooks/workflow.py` — the `HookProvider` / `BeforeToolCallEvent` pattern
- https://docs.temporal.io/develop/python/integrations/strands-agents
- After you install `strands-agents-sops`, read that package's `rules/agent-sop-format.md` and match it. Do not invent the section headings. Do not copy the package's git tree into the repo.
- `orchestrator/mcp.json` — add the `agent-sops` entry only
- The installed `strands_tools.swarm` signature, so the hook can tell a direct `swarm` call from a graph node. Read it. Do not wrap it.

## Requirements

**Code writing.** Add `strands-agents-sops` to `orchestrator/requirements.txt` and install it into `orchestrator/.venv`. Write `orchestrator/sops/delegation.sop.md` in the format you just read. It tells the orchestrator: `discover_tools` before naming a tool that is not on the session; `load_tool` it; `graph` loaded that way is `graph_activity`; a direct swarm is the discovered `swarm` tool; a swarm inside a graph is a node whose members are `agent` or `skill_agent`; omit `model_id`; omit `tools` to inherit; Agent API names are not registry names. Do not put `code-assist` or `pdd` into the orchestrator prompt. Add the MCP entry with command `.venv/bin/strands-agents-sops` and args `mcp`, `--sop-paths`, `sops`. Do not set that entry up to be skipped for `continue_on_error`. The server registers prompts, and `TemporalMCPClient` only lists tools. Mount it anyway. The hook's callback only reads `event.tool_use` and may set `event.cancel_tool`. It cancels `graph`, `use_agent`, or `use_skill` for the five cases in Task G. It does not cancel a call to the discovered `swarm` tool. Do not extend `LoadedTools` for this. That hook is `AfterToolCallEvent`.

**Subagent use.** Do not launch subagents. The SOP text and the hook implement the same rules. Installing the package is part of this section. A separate installer agent is too small.

**Prompting.** Task G is the spec for the rules. The ten chains in the system-prompt section are a different document. Do not paste them into the SOP, and do not edit `agent.json`. The workflow must not read the SOP file. You export a string loaded once at import inside `workflow.unsafe.imports_passed_through()` so the wiring section can append it. If that load cannot live without editing `workflow.py` or `run_worker.py`, stop and name the file. Do not take those files.

**Scope.** In: `orchestrator/requirements.txt`, `orchestrator/sops/delegation.sop.md`, the new hook module, the `agent-sops` entry in `orchestrator/mcp.json`, and a pytest for the cancel rules. Out: `orchestrator/agent.json`, `orchestrator/run_worker.py`, `orchestrator/workflow.py`, `orchestrator/load_tool.py`. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
