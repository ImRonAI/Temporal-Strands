# Section: Take Over, Relinquish, and stream truncate

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. Task I and Task E share `workflow.py` and `server.py`, so they are one agent. Neither is large enough, alone, to justify a second writer on those files.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task I — Take Over and Relinquish, on the same agent" and "Task E — Truncate the stream before continue-as-new"
- `AGENTS.md` — desktop handoff order, do not lower batch intervals, protected routes
- `orchestrator/AGENTS.md`
- `orchestrator/desktop/AGENTS.md` — the takeover order and the live acceptance sequence. You are not running that sequence. The app test is.
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/streaming/workflow.py` and `continue_as_new/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/browser_activity.py` — `desktop_control`. The allowed modes are the order. Do not add phases.
- `orchestrator/server.py` — `handoff` currently returns HTTP 410. `desktop_control_status` already reads the mode file.
- `orchestrator/workflow.py` — `ChatWorkflow`. There is no handoff update and no truncate update yet.
- The installed `temporalio/contrib/workflow_streams/_stream.py` — `truncate` and `get_state`. Truncating past the log raises `ApplicationError` type `TruncateOutOfRange`.
- `components/v0/agent-chat.tsx` — `handoff`, `takeControl`, `giveControl`. Read them. Do not redesign them.
- `app/api/orchestrator/handoff/route.ts` and its test. Read them. This route is protected. Change it only if the test shows it cannot forward `action` and `message`.

## Requirements

**Code writing.** Put one update on `ChatWorkflow` that calls the existing `desktop_control` activity with the desktop activity options (`task_queue=DESKTOP_BROWSER_TASK_QUEUE`). The server `handoff` endpoint calls that update and returns 204. One action moves one step: `take` from `agent` goes `stopping` then `human`; `release` from `human` goes `relinquishing` then `instructions`; `give` from `instructions` is `prepare_resume` then `resume`. The steering text is the next turn on this same session. `giveControl` already sends that message. A call in the wrong mode returns the activity's existing error. Do not add a phase. Do not add a computer-use agent, a second `TemporalAgent`, or a Gemini `computer_use` tool. Removing that Gemini tool is the wiring section, because it lives in `run_worker.py`. Also add `truncate_stream` as the plan shows, call it from `server.py` with the offset already forwarded, and truncate in `run` before `get_state()`. Do not change part types in `app/api/orchestrator/route.ts`. Do not lower batch intervals. The computer stays this session: same workflow id, same messages, same model. Desktop tools run on `desktop-browser` because the screen is in the Linux container.

**Subagent use.** Do not launch subagents. Handoff and truncate are each one update plus the server call that starts it. Two agents would edit `workflow.py` and `server.py` at the same time.

**Prompting.** Task I is the handoff spec. Task E is the truncate spec. The UI in `computer-use-preview.tsx` already has Take control, Relinquish, and the steering composer. Do not rebuild it. After the server returns 204, the existing `refreshControl` must be able to read the mode the activity wrote. If it cannot, stop and name the line. Do not invent a status channel.

**Scope.** In: `orchestrator/workflow.py`, `orchestrator/server.py`, and a test for the handoff update and the truncate update. Out: `orchestrator/run_worker.py`, `components/v0/computer-use-preview.tsx`, `app/api/orchestrator/route.ts`, `orchestrator/graph_tool.py`. The handoff route is out unless its existing test fails to forward `action` and `message`. If a change requires a file outside this list, stop and name the file.

## Outcome

`POST /sessions/{id}/handoff` no longer returns 410. Take, release, and give move the desktop through `agent`, `stopping`, `human`, `relinquishing`, `instructions`, `resuming`, and back to `agent`, on `ChatWorkflow`, by calling `desktop_control`. The stream truncates to the offset the server has forwarded before continue-as-new copies state. A subscriber that reconnects below the new base follows the base offset the stream reports.

Run the tests you add from `orchestrator/` with `.venv/bin/python -m pytest`. The visible takeover is the app test. Do not restart the stack.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task I — Take Over and Relinquish, on the same agent" and "Task E — Truncate the stream before continue-as-new"
- `AGENTS.md` — desktop handoff order, do not lower batch intervals, protected routes
- `orchestrator/AGENTS.md`
- `orchestrator/desktop/AGENTS.md` — the takeover order and the live acceptance sequence. You are not running that sequence. The app test is.
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/streaming/workflow.py` and `continue_as_new/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/browser_activity.py` — `desktop_control`. The allowed modes are the order. Do not add phases.
- `orchestrator/server.py` — `handoff` currently returns HTTP 410. `desktop_control_status` already reads the mode file.
- `orchestrator/workflow.py` — `ChatWorkflow`. There is no handoff update and no truncate update yet.
- The installed `temporalio/contrib/workflow_streams/_stream.py` — `truncate` and `get_state`. Truncating past the log raises `ApplicationError` type `TruncateOutOfRange`.
- `components/v0/agent-chat.tsx` — `handoff`, `takeControl`, `giveControl`. Read them. Do not redesign them.
- `app/api/orchestrator/handoff/route.ts` and its test. Read them. This route is protected. Change it only if the test shows it cannot forward `action` and `message`.

## Requirements

**Code writing.** Put one update on `ChatWorkflow` that calls the existing `desktop_control` activity with the desktop activity options (`task_queue=DESKTOP_BROWSER_TASK_QUEUE`). The server `handoff` endpoint calls that update and returns 204. One action moves one step: `take` from `agent` goes `stopping` then `human`; `release` from `human` goes `relinquishing` then `instructions`; `give` from `instructions` is `prepare_resume` then `resume`. The steering text is the next turn on this same session. `giveControl` already sends that message. A call in the wrong mode returns the activity's existing error. Do not add a phase. Do not add a computer-use agent, a second `TemporalAgent`, or a Gemini `computer_use` tool. Removing that Gemini tool is the wiring section, because it lives in `run_worker.py`. Also add `truncate_stream` as the plan shows, call it from `server.py` with the offset already forwarded, and truncate in `run` before `get_state()`. Do not change part types in `app/api/orchestrator/route.ts`. Do not lower batch intervals. The computer stays this session: same workflow id, same messages, same model. Desktop tools run on `desktop-browser` because the screen is in the Linux container.

**Subagent use.** Do not launch subagents. Handoff and truncate are each one update plus the server call that starts it. Two agents would edit `workflow.py` and `server.py` at the same time.

**Prompting.** Task I is the handoff spec. Task E is the truncate spec. The UI in `computer-use-preview.tsx` already has Take control, Relinquish, and the steering composer. Do not rebuild it. After the server returns 204, the existing `refreshControl` must be able to read the mode the activity wrote. If it cannot, stop and name the line. Do not invent a status channel.

**Scope.** In: `orchestrator/workflow.py`, `orchestrator/server.py`, and a test for the handoff update and the truncate update. Out: `orchestrator/run_worker.py`, `components/v0/computer-use-preview.tsx`, `app/api/orchestrator/route.ts`, `orchestrator/graph_tool.py`. The handoff route is out unless its existing test fails to forward `action` and `message`. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
