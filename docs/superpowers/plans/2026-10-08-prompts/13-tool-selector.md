# Section: composer tool and skill selector

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. Settings has already landed. You may edit `agent-chat.tsx` to pass the selection through. You add one sentence to `agent.json`. You do not rebuild settings.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — the composer paragraphs of "Task K — Settings, and a tool selector on the composer", and the one selection sentence in "What the system prompt tells the agent"
- `AGENTS.md` — `PromptInput` is native, protected routes, the turn body is `model_id` and `reasoning_effort` today
- `components/v0/AGENTS.md`
- `app/api/AGENTS.md`
- `components/v0/composer.tsx` — `PromptInput`
- `components/v0/agent-chat.tsx` — where the turn body is built
- `orchestrator/workflow.py` — where the session tool list is enforced. Read the current list. A checked discoverable tool is still loaded with `load_tool`. An unchecked name is not loaded and is not called.
- `app/api/orchestrator/route.ts` and `app/api/orchestrator/route.test.ts` — protected. This section is the request to forward two arrays, `tools` and `skills`, and nothing else. Read the test first. Do not redesign the stream.
- `orchestrator/agent.json` — add the one sentence. Load the JSON, replace the prompt, dump it. Do not hand-edit the escaped string. Do not rewrite the tool map.

## Requirements

**Code writing.** Add a selector on the existing prompt input. Tools come from `discover_tools` plus the tools already on the session. Skills come from the catalog `use_skill` already uses. The user checks the ones this session may call. The turn sends those names. The session may call a checked tool or skill and may not call an unchecked one. `load_tool` still attaches a checked community tool through `run_loaded_tool`. Do not build a second composer. Do not build a second tool runtime. Server-side Agent API tools are request types, not `load_tool` names. The selector must not present `web_search` as a community tool. The sentence you add to the prompt says: when the turn lists selected tools and skills, those names are the ones this turn may call, and a selected discoverable tool is still loaded with `load_tool` before it is called. Forward `tools` and `skills` on the turn body. Change the protected route only far enough to pass those two arrays. If the existing route test already carries unknown fields through, do not edit the route.

**Subagent use.** Do not launch subagents. The selector, the turn body, and the allowlist are one feature. Splitting the UI from the enforcement ships a control that does not constrain the agent.

**Prompting.** The composer half of Task K is the spec. The settings paragraphs are done. Do not change the model allowlist. Do not change reasoning levels.

**Scope.** In: `components/v0/composer.tsx`, `components/v0/agent-chat.tsx`, `orchestrator/agent.json` (one sentence), `orchestrator/workflow.py` (the allowlist only), and `app/api/orchestrator/route.ts` only if the two arrays are not already forwarded. Plus the vitest or pytest that proves an unchecked name is not called. Out: the settings component's behavior, `orchestrator/run_worker.py`, `orchestrator/graph_tool.py`, the stream part types. If a change requires a file outside this list, stop and name the file.

## Outcome

The composer shows a tool and skill selector. A turn with one tool and one skill checked sends those names. The session loads and calls the checked tool, and it does not call an unchecked one. The system prompt states that the turn's selection wins. Settings still filters models, and this section does not disturb that.

From the repo root, run the vitest you add, scoped with `--exclude '**/.worktrees/**' --exclude '**/.kilo/**'`. From `orchestrator/`, run the pytest you add with `.venv/bin/python -m pytest`. If `http://localhost:3001` is already up, open the composer and confirm the selector renders. Do not restart the stack. If the server is down, say you could not verify it in the browser.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — the composer paragraphs of "Task K — Settings, and a tool selector on the composer", and the one selection sentence in "What the system prompt tells the agent"
- `AGENTS.md` — `PromptInput` is native, protected routes, the turn body is `model_id` and `reasoning_effort` today
- `components/v0/AGENTS.md`
- `app/api/AGENTS.md`
- `components/v0/composer.tsx` — `PromptInput`
- `components/v0/agent-chat.tsx` — where the turn body is built
- `orchestrator/workflow.py` — where the session tool list is enforced. Read the current list. A checked discoverable tool is still loaded with `load_tool`. An unchecked name is not loaded and is not called.
- `app/api/orchestrator/route.ts` and `app/api/orchestrator/route.test.ts` — protected. This section is the request to forward two arrays, `tools` and `skills`, and nothing else. Read the test first. Do not redesign the stream.
- `orchestrator/agent.json` — add the one sentence. Load the JSON, replace the prompt, dump it. Do not hand-edit the escaped string. Do not rewrite the tool map.

## Requirements

**Code writing.** Add a selector on the existing prompt input. Tools come from `discover_tools` plus the tools already on the session. Skills come from the catalog `use_skill` already uses. The user checks the ones this session may call. The turn sends those names. The session may call a checked tool or skill and may not call an unchecked one. `load_tool` still attaches a checked community tool through `run_loaded_tool`. Do not build a second composer. Do not build a second tool runtime. Server-side Agent API tools are request types, not `load_tool` names. The selector must not present `web_search` as a community tool. The sentence you add to the prompt says: when the turn lists selected tools and skills, those names are the ones this turn may call, and a selected discoverable tool is still loaded with `load_tool` before it is called. Forward `tools` and `skills` on the turn body. Change the protected route only far enough to pass those two arrays. If the existing route test already carries unknown fields through, do not edit the route.

**Subagent use.** Do not launch subagents. The selector, the turn body, and the allowlist are one feature. Splitting the UI from the enforcement ships a control that does not constrain the agent.

**Prompting.** The composer half of Task K is the spec. The settings paragraphs are done. Do not change the model allowlist. Do not change reasoning levels.

**Scope.** In: `components/v0/composer.tsx`, `components/v0/agent-chat.tsx`, `orchestrator/agent.json` (one sentence), `orchestrator/workflow.py` (the allowlist only), and `app/api/orchestrator/route.ts` only if the two arrays are not already forwarded. Plus the vitest or pytest that proves an unchecked name is not called. Out: the settings component's behavior, `orchestrator/run_worker.py`, `orchestrator/graph_tool.py`, the stream part types. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
