# Coding agent

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the parent. You do not implement a section yourself while a section prompt exists for it. You launch the section prompts, review what came back, and run the app test last.

## Ground in these resources

Read these before you launch anyone. If a section prompt and the plan disagree, the plan section named in that prompt wins, and you amend the launch instruction in one sentence. Do not invent a third version.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "How you must execute this", "Do not", "Done when"
- `docs/superpowers/plans/2026-10-08-prompts/` — the section files below
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents

## Requirements

**Code writing.** You do not write the section's code. When a section agent stops because a signature is not in the sources it was given, you find the source or you record the gap. You do not guess the signature and send the agent back with an invented one. Protected paths stay protected: `.env*`, `orchestrator/graph_tool.py`, and the route files under `app/api/**`. A section prompt names the only route edit that is allowed, and only that edit. Do not restart `pnpm dev:all` until `18-app-test.md`. Do not commit.

**Subagent use.** Launch the section file as the subagent's entire prompt. Do not summarize, merge, or split a section file. Do not launch one subagent per task heading. Task A, Task B, and Task E are not launches. They are inside the section files that name them.

Batch 1. Launch together: `01-session-registry.md`, `02-mcp-clients.md`, `04-package-audit.md`, `05-think-chain.md`. Do not launch `03-starter-activities.md`. `journal`, `environment`, and `http_request` are `load_tool` registrations inside `01-session-registry.md`.

Batch 2, after batch 1 has finished and you have checked that no two agents edited the same file. Launch together: `06-in-activity-models.md`, `07-offload.md`, `08-dream.md`, `09-delegation-sop.md`, `10-handoff-and-truncate.md`, `11-settings.md`.

Batch 3, after batch 2. Launch together: `12-worker-wiring.md`, `13-tool-selector.md`. They do not share files.

Review, after batch 3. Launch together: `14-review-registry-mcp.md`, `15-review-memory.md`, `16-review-delegation.md`, `17-review-ui.md`. Reviewers write findings. They do not edit. Send each finding back to the section agent that owns the file, with that section's original prompt plus the finding. Then run the four reviewers again on the fix. Do not start the app test while a finding is open.

App test. You run `18-app-test.md` yourself. Do not hand the browser session to a subagent.

**Prompting.** A subagent's prompt is the section file, unchanged. When you send a fix, the message is the original file plus the finding and the file paths the reviewer named. Do not add a new design in the fix message.

**Scope.** Your scope is ordering, file-overlap checks, and the app test. A section's file list is that section's scope. If two returned diffs touch one file, stop and send the second agent back. Do not merge them yourself.

## Outcome

The plan's "Done when" list is true, and `docs/superpowers/plans/2026-10-08-app-test.md` records one clean pass of the thirteen browser steps.

The product that exists at the end:

- A session starts with `load_tool`, `discover_tools`, `unload_tools`, `use_skill`, `environment`, `http_request`, `journal`, and one `TemporalMCPClient` per connected MCP server.
- `agent.json` tells the model those registered tools, the server-side Agent API tools, the discoverable tools, each MCP server, and the ten chains.
- Community tools, including forge, dicom, fhir, swarm, graph, and think, are discovered and loaded. Graph loads `graph_activity`. Swarm, forge, dicom, and fhir run through `run_loaded_tool`.
- `journal`, `environment`, and `http_request` are on the session because `register_community_tool` registered them. `run_loaded_tool` runs them. There is no activity file per tool.
- Large activity results go to `gwen-offload`. Durable memories go to `gwen-memory`. A dream workflow reads both plus the journal and writes separate memory files. The schedule uses overlap `SKIP`.
- In-activity models keep Agent API tools on the request.
- Take Over and Relinquish run on `ChatWorkflow` through `desktop_control`. There is no second computer agent.
- The live Agent API catalog adds and removes reasoning levels. Settings picks the visible models and the default. The composer picks the tools and skills for the turn.
- Think stays one chain of thought.

You are finished when "Done when" is true and the app-test file has no open failure. A green pytest run is not finished.

## Ground in these resources

Read these before you launch anyone. If a section prompt and the plan disagree, the plan section named in that prompt wins, and you amend the launch instruction in one sentence. Do not invent a third version.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "How you must execute this", "Do not", "Done when"
- `docs/superpowers/plans/2026-10-08-prompts/` — the section files below
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents

## Requirements

**Code writing.** You do not write the section's code. When a section agent stops because a signature is not in the sources it was given, you find the source or you record the gap. You do not guess the signature and send the agent back with an invented one. Protected paths stay protected: `.env*`, `orchestrator/graph_tool.py`, and the route files under `app/api/**`. A section prompt names the only route edit that is allowed, and only that edit. Do not restart `pnpm dev:all` until `18-app-test.md`. Do not commit.

**Subagent use.** Launch the section file as the subagent's entire prompt. Do not summarize, merge, or split a section file. Do not launch one subagent per task heading. Task A, Task B, and Task E are not launches. They are inside the section files that name them.

Batch 1. Launch together: `01-session-registry.md`, `02-mcp-clients.md`, `04-package-audit.md`, `05-think-chain.md`. Do not launch `03-starter-activities.md`. `journal`, `environment`, and `http_request` are `load_tool` registrations inside `01-session-registry.md`.

Batch 2, after batch 1 has finished and you have checked that no two agents edited the same file. Launch together: `06-in-activity-models.md`, `07-offload.md`, `08-dream.md`, `09-delegation-sop.md`, `10-handoff-and-truncate.md`, `11-settings.md`.

Batch 3, after batch 2. Launch together: `12-worker-wiring.md`, `13-tool-selector.md`. They do not share files.

Review, after batch 3. Launch together: `14-review-registry-mcp.md`, `15-review-memory.md`, `16-review-delegation.md`, `17-review-ui.md`. Reviewers write findings. They do not edit. Send each finding back to the section agent that owns the file, with that section's original prompt plus the finding. Then run the four reviewers again on the fix. Do not start the app test while a finding is open.

App test. You run `18-app-test.md` yourself. Do not hand the browser session to a subagent.

**Prompting.** A subagent's prompt is the section file, unchanged. When you send a fix, the message is the original file plus the finding and the file paths the reviewer named. Do not add a new design in the fix message.

**Scope.** Your scope is ordering, file-overlap checks, and the app test. A section's file list is that section's scope. If two returned diffs touch one file, stop and send the second agent back. Do not merge them yourself.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
