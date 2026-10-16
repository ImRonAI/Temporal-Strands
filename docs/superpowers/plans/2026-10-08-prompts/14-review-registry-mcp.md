# Review: registry and MCP

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are a reviewer. You write findings. You do not edit until the parent has sent a finding back to the section that owns the file, that section has fixed it, and the parent has asked you to read the fix. On this pass, write the finding and stop.

## Ground in these resources

Read these before you judge the diff. A behavior you cannot point at in one of them is not a finding. Say you could not verify it.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "What is registered when a session starts", "What the system prompt tells the agent", "load_tool already converts a loaded tool into an activity", "MCP servers: connect all of them", "Do not"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The diff for `orchestrator/workflow.py`, `orchestrator/load_tool.py`, `orchestrator/agent.json`, `orchestrator/agent_tools.py`, `orchestrator/mcp.json`

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked.

**Subagent use.** Do not launch subagents. You are one of four reviewers. The Strands-Temporal checks below are part of your review. They are not a fifth reviewer.

**Prompting.** Judge the diff against the plan sections above. Do not add a requirement the plan does not have.

**Scope.** You read the session `tools` list, the `load_tool` exceptions, one `TemporalMCPClient` per connected server, and the `agent.json` tool map. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: rewriting the prompt, connecting a server, editing tests.

## Outcome

Write findings for any of these that are false:

- The `TemporalAgent` tools list is only `load_tool`, `discover_tools`, `unload_tools`, `use_skill`, `environment`, `http_request`, `journal`, and one `TemporalMCPClient` per connected server.
- `graph`, `swarm`, `use_agent`, `think`, `python_repl`, `browser`, and the computer-use actions are absent from that list.
- `load_tool("graph")` dispatches `graph_activity`. Desktop names use the desktop queue. Other loaded tools use `run_loaded_tool`. There is no `swarm_activity.py`.
- `agent.json` keeps the three groups separate and states the ten chains. Server-side names are not registry names. `pubmed` and `fda` are absent.
- Every `mcp.json` server whose env is set is in the factory. `continue_on_error` is not used as "never mount". A missing variable is logged by server name.

No finding means you write that you checked each bullet and it holds, with the file you read.

## Ground in these resources

Read these before you judge the diff. A behavior you cannot point at in one of them is not a finding. Say you could not verify it.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "What is registered when a session starts", "What the system prompt tells the agent", "load_tool already converts a loaded tool into an activity", "MCP servers: connect all of them", "Do not"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The diff for `orchestrator/workflow.py`, `orchestrator/load_tool.py`, `orchestrator/agent.json`, `orchestrator/agent_tools.py`, `orchestrator/mcp.json`

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked.

**Subagent use.** Do not launch subagents. You are one of four reviewers. The Strands-Temporal checks below are part of your review. They are not a fifth reviewer.

**Prompting.** Judge the diff against the plan sections above. Do not add a requirement the plan does not have.

**Scope.** You read the session `tools` list, the `load_tool` exceptions, one `TemporalMCPClient` per connected server, and the `agent.json` tool map. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: rewriting the prompt, connecting a server, editing tests.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
