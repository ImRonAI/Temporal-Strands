# App test

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the parent agent. This is the last gate. Pytest is not a substitute. Do not hand the browser session to a subagent.

## Ground in these resources

Read these before you restart anything. A step you did not exercise in the browser is a failure, even if a unit test covers it.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "App test, last, on the main agent", "App test script", "Done when", "Do not"
- `orchestrator/desktop/AGENTS.md` — the takeover sequence for step 11. Historical success reports are not this run.
- `AGENTS.md` — this checkout's Next is `http://localhost:3001`. Do not kill port 3000.
- `components/v0/AGENTS.md`
- The thirteen steps themselves, in the plan. If this prompt and a step disagree, the plan step wins.

## Requirements

**Code writing.** Fix a failure by sending it back to the section that owns the file, with that section's original prompt plus the exact UI text. Do not invent a new design during the test. Do not edit `app/api/**` routes, `orchestrator/graph_tool.py`, or `.env*` from this phase unless the owning section's prompt already allowed that file and the failure is in that scope.

**Subagent use.** You click, type, and read. The only subagents you launch from here are the original section agents, and only to fix a failure you just saw. Do not launch a new "app test helper". Do not split the thirteen steps across agents.

**Prompting.** For each step write one line: worked, or the exact text the UI showed. A missing-env MCP server is a note, not a product bug. Name the server and the missing variable or the process error. A swarm member that builds `Agent()` with `BedrockModel` because `model` is `None` is a defect: fix the parent passed into `strands_tools.swarm.swarm` so the member uses the session model. Do not rewrite swarm. The installed assignment is `strands/agent/agent.py`.

**Scope.** In: the running app at `http://localhost:3001`, and `docs/superpowers/plans/2026-10-08-app-test.md` as the record. Restart only this checkout's stack, then wait until `http://localhost:3001` and `http://localhost:8787/health` answer. Out: other worktrees, port 3000, claiming a step passed because the unit test passed.

## Outcome

Restart this checkout's stack so the worker loads the code. Open `http://localhost:3001`. Use the chat the way a person does: type, send, read the transcript, open the tool trace.

Walk all thirteen steps in "App test script":

1. The page loads, the composer accepts text, and a short prompt returns an assistant message.
2. The reply names the registered tools, the server-side tools, the discoverable tools, the connected MCP servers, and the chains. The trace shows the starter tools and the connected MCP tools. It does not show `graph`, `swarm`, or `think` yet.
3. Discover lists `forge`, `dicom`, and `fhir`, and does not list `pubmed` or `fda`.
4. A journal note is in `orchestrator/journal/<today>.md` and the chat shows the read-back.
5. `environment` and `http_request` against `http://localhost:8787/health` both return in the trace.
6. `load_tool` `forge`, then `forge.health`.
7. `load_tool` `swarm`, then a two-member swarm on a one-sentence task.
8. `load_tool` `graph`, one `agent` node with no `tools` key and no `model_id`. A second call with a string topology is cancelled and the chat shows that cancellation.
9. `load_tool` `think`. The thought text and any tool call inside it stay in one chain.
10. Each connected MCP server shows at least one tool. Each server that did not connect is named with the reason.
11. Take control, confirm the human has the screen, Relinquish, send a steering sentence, and confirm the same session id.
12. Settings: turn one model off, set another as the default, reload, and confirm the picker and the new session. On the composer, select one tool and one skill, send a message, and confirm the trace uses those names and does not call an unselected tool.
13. A model that was missing from `reasoning-levels.json` before this work shows minimal, low, medium, high, xhigh, and max.

Fix any step that fails and repeat from step 1 until all thirteen succeed in a single browser session. Write the thirteen results at `docs/superpowers/plans/2026-10-08-app-test.md`. You are finished when that file has no open failure and "Done when" in the plan is true.

## Ground in these resources

Read these before you restart anything. A step you did not exercise in the browser is a failure, even if a unit test covers it.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "App test, last, on the main agent", "App test script", "Done when", "Do not"
- `orchestrator/desktop/AGENTS.md` — the takeover sequence for step 11. Historical success reports are not this run.
- `AGENTS.md` — this checkout's Next is `http://localhost:3001`. Do not kill port 3000.
- `components/v0/AGENTS.md`
- The thirteen steps themselves, in the plan. If this prompt and a step disagree, the plan step wins.

## Requirements

**Code writing.** Fix a failure by sending it back to the section that owns the file, with that section's original prompt plus the exact UI text. Do not invent a new design during the test. Do not edit `app/api/**` routes, `orchestrator/graph_tool.py`, or `.env*` from this phase unless the owning section's prompt already allowed that file and the failure is in that scope.

**Subagent use.** You click, type, and read. The only subagents you launch from here are the original section agents, and only to fix a failure you just saw. Do not launch a new "app test helper". Do not split the thirteen steps across agents.

**Prompting.** For each step write one line: worked, or the exact text the UI showed. A missing-env MCP server is a note, not a product bug. Name the server and the missing variable or the process error. A swarm member that builds `Agent()` with `BedrockModel` because `model` is `None` is a defect: fix the parent passed into `strands_tools.swarm.swarm` so the member uses the session model. Do not rewrite swarm. The installed assignment is `strands/agent/agent.py`.

**Scope.** In: the running app at `http://localhost:3001`, and `docs/superpowers/plans/2026-10-08-app-test.md` as the record. Restart only this checkout's stack, then wait until `http://localhost:3001` and `http://localhost:8787/health` answer. Out: other worktrees, port 3000, claiming a step passed because the unit test passed.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
