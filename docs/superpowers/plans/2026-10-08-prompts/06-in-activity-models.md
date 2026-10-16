# Section: in-activity models

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. It covers Task A, the Task B constraint, and the last paragraph of Task C. Those are one agent because they meet in `subagent_support.py`. Task A alone is a frozenset edit and is not its own launch. Task B is a constraint and is not its own launch.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task A — Keep Agent API tools on in-activity models", "Task B — Formations", the last paragraph of "Task C — Journal, environment, and http_request", and "What the system prompt tells the agent" for the server-side names. Do not edit `agent.json`.
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/subagent_support.py` — `in_process_model`, `_OUTER_MODEL_PARAMS`
- The installed `strands/models/openai_responses.py` `_format_request`, the block that spreads `request["tools"]` and then sets `store` from `self.stateful`
- The installed `strands_tools.journal`, `strands_tools.environment`, and `strands_tools.http_request` callables. The in-activity parent is already inside an activity, so it receives these callables, not `activity_as_tool`.
- `orchestrator/graph_tool.py` — read it only to check the four node types. Do not rewrite it.
- `orchestrator/tests/test_subagent_support.py`

## Requirements

**Code writing.** Change `_OUTER_MODEL_PARAMS` from `{"background", "store", "tools", "skills"}` to `{"background", "store"}`. Leave `background` and `store` stripped. `store` remains set from `self.stateful`. Replace the comment that cites `chat-975a5afb58315ff9`. If a request fails, record the response body. Do not put `tools` back into the strip set. Give the in-activity parent the installed `journal`, `environment`, and `http_request` callables. Do not pass a live `Model` into a workflow. Do not enable `context_manager="auto"`. Do not edit `graph_tool.py`. Formations stay as that file already implements them: `agent`, a batch of nodes with no edges, `swarm` whose members are `agent` or `skill_agent`, and nested `graph`. Omitted `tools` inherits. Omitted model is the parent model. `skill_agent` uses `build_skill_agent` and does not call `use_skill_activity`.

**Subagent use.** Do not launch subagents. Task A, Task B, and the parent-tool paragraph are one file. Splitting them is how two agents would edit `in_process_model`.

**Prompting.** Task A is the strip-set spec. Task B is the check you run, not a rewrite. The server-side tool names live in the system prompt, which another section owns. This section's test proves those names survive `in_process_model`. It does not restate them in `agent.json`.

**Scope.** In: `orchestrator/subagent_support.py`, `orchestrator/tests/test_subagent_support.py`. Out: `orchestrator/graph_tool.py`, `orchestrator/agent.json`, `orchestrator/workflow.py`, any new `*_activity.py`. The in-activity parent calls the installed `journal`, `environment`, and `http_request` functions because it is already inside an activity. That is not a new activity file. If a change requires a file outside this list, stop and name the file.

## Outcome

A model whose `params["tools"]` contains `web_search` still has that entry after `in_process_model`. `background` and `store` are gone. `stateful` is false. Graph nodes, `use_agent`, and `use_skill` go through that function, so they keep the server-side tools. Think's nested `TemporalAgent` does not call `in_process_model`, and you do not change Think to make it do so.

The in-activity parent used by graph and `use_skill` can call the same `journal`, `environment`, and `http_request` functions the session activities call.

Run from `orchestrator/`:

```bash
.venv/bin/python -m pytest tests/test_subagent_support.py tests/test_graph_tool_adherence.py -q
```

Report a collection error that imports `TurnInput` or `_ToolResultHook`. Do not reintroduce those symbols.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task A — Keep Agent API tools on in-activity models", "Task B — Formations", the last paragraph of "Task C — Journal, environment, and http_request", and "What the system prompt tells the agent" for the server-side names. Do not edit `agent.json`.
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/subagent_support.py` — `in_process_model`, `_OUTER_MODEL_PARAMS`
- The installed `strands/models/openai_responses.py` `_format_request`, the block that spreads `request["tools"]` and then sets `store` from `self.stateful`
- The installed `strands_tools.journal`, `strands_tools.environment`, and `strands_tools.http_request` callables. The in-activity parent is already inside an activity, so it receives these callables, not `activity_as_tool`.
- `orchestrator/graph_tool.py` — read it only to check the four node types. Do not rewrite it.
- `orchestrator/tests/test_subagent_support.py`

## Requirements

**Code writing.** Change `_OUTER_MODEL_PARAMS` from `{"background", "store", "tools", "skills"}` to `{"background", "store"}`. Leave `background` and `store` stripped. `store` remains set from `self.stateful`. Replace the comment that cites `chat-975a5afb58315ff9`. If a request fails, record the response body. Do not put `tools` back into the strip set. Give the in-activity parent the installed `journal`, `environment`, and `http_request` callables. Do not pass a live `Model` into a workflow. Do not enable `context_manager="auto"`. Do not edit `graph_tool.py`. Formations stay as that file already implements them: `agent`, a batch of nodes with no edges, `swarm` whose members are `agent` or `skill_agent`, and nested `graph`. Omitted `tools` inherits. Omitted model is the parent model. `skill_agent` uses `build_skill_agent` and does not call `use_skill_activity`.

**Subagent use.** Do not launch subagents. Task A, Task B, and the parent-tool paragraph are one file. Splitting them is how two agents would edit `in_process_model`.

**Prompting.** Task A is the strip-set spec. Task B is the check you run, not a rewrite. The server-side tool names live in the system prompt, which another section owns. This section's test proves those names survive `in_process_model`. It does not restate them in `agent.json`.

**Scope.** In: `orchestrator/subagent_support.py`, `orchestrator/tests/test_subagent_support.py`. Out: `orchestrator/graph_tool.py`, `orchestrator/agent.json`, `orchestrator/workflow.py`, any new `*_activity.py`. The in-activity parent calls the installed `journal`, `environment`, and `http_request` functions because it is already inside an activity. That is not a new activity file. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
