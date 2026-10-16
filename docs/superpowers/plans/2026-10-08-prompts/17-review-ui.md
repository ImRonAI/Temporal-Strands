# Review: UI, handoff, and the catalog the picker reads

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

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task H", "Task I", "Task E", "Task J", "Task K", "Do not"
- `AGENTS.md` — native `ChainOfThought` and `PromptInput`, protected routes
- `components/v0/AGENTS.md`
- `orchestrator/desktop/AGENTS.md` — the mode order
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The diff for `components/v0/agent-activity.tsx`, the settings component, `components/v0/model-picker.tsx`, `components/v0/composer.tsx`, `components/v0/agent-chat.tsx`, `orchestrator/server.py` `handoff`, `orchestrator/workflow.py` handoff and truncate, `lib/reasoning-levels.json`, `orchestrator/run_worker.py` catalog sync

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked. You do not browse the app. The parent runs the browser script after findings are closed.

**Subagent use.** Do not launch subagents. You are one of four reviewers. The Strands-Temporal checks below are part of your review. They are not a fifth reviewer.

**Prompting.** Judge Think against Task H, handoff against Task I, truncate against Task E, reasoning levels against Task J, and the two controls against Task K. Do not ask for a new reasoning renderer or a second composer.

**Scope.** You read the Think chain, the settings filter, the composer selector, and the handoff path. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: clicking through localhost, editing the route, restarting the stack.

## Outcome

Write findings for any of these that are false:

- One think invocation stays one `ChainOfThought` across text, tool calls, tool results, and native-tool events. `unstreamedThinkOutput` does not hide the step once text has streamed. There is no new reasoning renderer.
- Settings stores an allowlist and a default in the browser. The picker offers only the checked models.
- The composer selector sends tool and skill names. An unchecked name is not called. `web_search` is not offered as a `load_tool` name. The route, if it changed, only forwards the two arrays.
- `handoff` returns 204 by calling `ChatWorkflow`, which calls `desktop_control`. The mode order is `agent`, `stopping`, `human`, `relinquishing`, `instructions`, `resuming`. There is no second computer agent. Gemini factories do not attach `computer_use`.
- Truncate runs before `get_state`. Part types in the protected orchestrator route are unchanged.
- Agent API catalog ids in `reasoning-levels.json` use the six efforts, ids the fetch dropped are gone, and Gemini entries are still `low`, `medium`, `high`.

No finding means you write that you checked each bullet and it holds, with the file you read.

## Ground in these resources

Read these before you judge the diff. A behavior you cannot point at in one of them is not a finding. Say you could not verify it.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task H", "Task I", "Task E", "Task J", "Task K", "Do not"
- `AGENTS.md` — native `ChainOfThought` and `PromptInput`, protected routes
- `components/v0/AGENTS.md`
- `orchestrator/desktop/AGENTS.md` — the mode order
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The diff for `components/v0/agent-activity.tsx`, the settings component, `components/v0/model-picker.tsx`, `components/v0/composer.tsx`, `components/v0/agent-chat.tsx`, `orchestrator/server.py` `handoff`, `orchestrator/workflow.py` handoff and truncate, `lib/reasoning-levels.json`, `orchestrator/run_worker.py` catalog sync

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked. You do not browse the app. The parent runs the browser script after findings are closed.

**Subagent use.** Do not launch subagents. You are one of four reviewers. The Strands-Temporal checks below are part of your review. They are not a fifth reviewer.

**Prompting.** Judge Think against Task H, handoff against Task I, truncate against Task E, reasoning levels against Task J, and the two controls against Task K. Do not ask for a new reasoning renderer or a second composer.

**Scope.** You read the Think chain, the settings filter, the composer selector, and the handoff path. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: clicking through localhost, editing the route, restarting the stack.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
