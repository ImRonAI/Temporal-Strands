# Section: settings

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. Settings is the model allowlist and the default. The composer tool selector is a different section and a different contract. Do not build it.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task K — Settings, and a tool selector on the composer", the settings paragraphs only
- `AGENTS.md` — native AI Elements, model ids come from `/api/models`, the only literal id allowed in the client is `lib/perplexity.ts` `DEFAULT_MODEL`
- `components/v0/AGENTS.md`
- `components/v0/model-picker.tsx` and `components/v0/use-models.ts`
- `components/v0/agent-chat.tsx` — where the picker is mounted
- `app/api/models/route.ts` — read it. Do not edit it. The list is the worker catalog.

## Requirements

**Code writing.** Add a settings view the chat can open. It lists the models from `/api/models`. The user checks the ones this browser may use and picks one default. The picker and a new session offer only the checked models. The default is the model a new session starts on. Store the choice in the browser. Do not add a settings service, a database, or a new API route. Do not hardcode model ids. Compose the controls the app already uses. Do not build a second picker. Do not edit `composer.tsx`. The tool selector lands after you, and it is allowed to edit `agent-chat.tsx` then. Leave that file in a state where a later edit can pass a selection through without rewriting settings.

**Subagent use.** Do not launch subagents. An allowlist and a default are one view. Splitting "the checklist" from "the default" makes two agents edit `model-picker.tsx` and `agent-chat.tsx`.

**Prompting.** The settings half of Task K is the spec. The composer paragraphs are out. Do not forward tool names on the turn body. Do not change `reasoning-levels.json`.

**Scope.** In: a new settings component under `components/v0/`, `components/v0/model-picker.tsx`, `components/v0/agent-chat.tsx`, and the vitest you add for the filter. Out: `components/v0/composer.tsx`, `app/api/**`, `orchestrator/**`, `lib/reasoning-levels.json`. If a change requires a file outside this list, stop and name the file.

## Outcome

Opening settings shows the catalog. Unchecking a model removes it from the picker. Choosing a default makes a new session start on that model. Reloading the page keeps both choices. A model that is not checked cannot be selected.

From the repo root:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' components/v0/model-picker.test.tsx
```

If `http://localhost:3001` is already up, open settings and confirm the control renders and the choice survives reload. Do not restart the stack. If the server is down, say you could not verify it in the browser.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task K — Settings, and a tool selector on the composer", the settings paragraphs only
- `AGENTS.md` — native AI Elements, model ids come from `/api/models`, the only literal id allowed in the client is `lib/perplexity.ts` `DEFAULT_MODEL`
- `components/v0/AGENTS.md`
- `components/v0/model-picker.tsx` and `components/v0/use-models.ts`
- `components/v0/agent-chat.tsx` — where the picker is mounted
- `app/api/models/route.ts` — read it. Do not edit it. The list is the worker catalog.

## Requirements

**Code writing.** Add a settings view the chat can open. It lists the models from `/api/models`. The user checks the ones this browser may use and picks one default. The picker and a new session offer only the checked models. The default is the model a new session starts on. Store the choice in the browser. Do not add a settings service, a database, or a new API route. Do not hardcode model ids. Compose the controls the app already uses. Do not build a second picker. Do not edit `composer.tsx`. The tool selector lands after you, and it is allowed to edit `agent-chat.tsx` then. Leave that file in a state where a later edit can pass a selection through without rewriting settings.

**Subagent use.** Do not launch subagents. An allowlist and a default are one view. Splitting "the checklist" from "the default" makes two agents edit `model-picker.tsx` and `agent-chat.tsx`.

**Prompting.** The settings half of Task K is the spec. The composer paragraphs are out. Do not forward tool names on the turn body. Do not change `reasoning-levels.json`.

**Scope.** In: a new settings component under `components/v0/`, `components/v0/model-picker.tsx`, `components/v0/agent-chat.tsx`, and the vitest you add for the filter. Out: `components/v0/composer.tsx`, `app/api/**`, `orchestrator/**`, `lib/reasoning-levels.json`. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
