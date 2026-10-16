# Section: worker wiring and the Agent API catalog

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. You own `orchestrator/run_worker.py`. The edits are the dream schedule, the SOP append inside `agent_identity()`, the catalog sync, and deleting the Gemini `computer_use` tool. `run_loaded_tool` is already on the worker. Do not register a new tool activity. Task J is this section. It is not its own launch.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — the batch-3 "Worker wiring" row, "Task J — Reasoning levels follow the Agent API catalog", the schedule bullets in "Task F — Dream workflow", the `agent_identity()` sentence in "Task G — Delegation SOP", and the Gemini `computer_use` sentence in "Task I"
- `AGENTS.md` — provider declaration, `REASONING_EFFORTS` behavior, no hardcoded model list on the client
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The Temporal Python SDK source for `ScheduleActionStartWorkflow`, `ScheduleSpec`, `ScheduleIntervalSpec`, `SchedulePolicy`, and `ScheduleOverlapPolicy.SKIP`. Read the signatures. Do not guess the constructor.
- `orchestrator/run_worker.py` — `fetch_model_ids`, `build_perplexity_factories`, `build_model_factory`, `agent_identity`, the worker activity list
- `orchestrator/config.py` — `REASONING_EFFORTS`, `DREAM_MIN_INTERVAL`, `DREAM_MIN_SESSIONS`
- `lib/reasoning-levels.json` and `components/v0/model-picker.tsx` `reasoningLevels` — read them. The picker returns `[]` for a missing key.
- `orchestrator/load_tool.py` `run_loaded_tool` — already registered. Confirm it stays. Do not add a tool activity.
- `orchestrator/dream_workflow.py` — schedule it. Do not rewrite it.
- The SOP string exported by the delegation section. Append it. Do not rewrite the SOP file.
- `orchestrator/server.py` — it already rejects a `reasoning_effort` missing from `REASONING_LEVELS`. Read that. Do not edit it.

## Requirements

**Code writing.** Do not register `journal`, `environment`, or `http_request` as activities. `run_loaded_tool` already runs them. Create the dream schedule if it is absent: `ScheduleActionStartWorkflow` for `DreamWorkflow`, `ScheduleSpec(intervals=[ScheduleIntervalSpec(every=DREAM_MIN_INTERVAL)])`, `SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP)`. `SKIP` is the lock. Pass a `model_id` that is already a registered factory and is one of the delegated models the plan names. Do not add a new model id literal. Append the SOP string inside `agent_identity()`. The workflow does not read the file. In the same function that registers Perplexity factories, every live catalog id and every preset gets `REASONING_EFFORTS` (`minimal`, `low`, `medium`, `high`, `xhigh`, `max`) written into `lib/reasoning-levels.json`. An id that was in the previous catalog and is absent from this fetch is not registered and is removed from that JSON. Gemini entries stay `low`, `medium`, `high`. Do not keep a second model list. Delete the `genai.types.ComputerUse` tool from `build_model_factory`. Gemini uses the same desktop tools as every other model. Do not pass a live `Model`. Do not set `context_manager="auto"`.

**Subagent use.** Do not launch subagents. One agent owns `run_worker.py`. An agent per edit would serialize on that file and race the JSON rewrite.

**Prompting.** Task J is the catalog spec. The schedule bullets are the schedule spec. The `agent_identity()` sentence is the SOP append. The Gemini sentence is the deletion. Do not re-open the tool map in `agent.json`. Do not change the session `tools=` list.

**Scope.** In: `orchestrator/run_worker.py`, `lib/reasoning-levels.json`, and a pytest that uses a fake catalog. Out: `orchestrator/workflow.py`, `orchestrator/agent.json`, `orchestrator/dream_workflow.py`, any new `*_activity.py`, `orchestrator/sops/delegation.sop.md`, `components/v0/**`. If a change requires a file outside this list, stop and name the file.

## Outcome

The worker already runs loaded tools through `run_loaded_tool`. This section ensures the dream schedule exists with overlap `SKIP`, and puts the delegation SOP on the system prompt `agent_identity()` returns. A fake catalog of two Agent API ids writes both with the six efforts. A second fetch with one id deletes the other from the JSON and from the factory map. `gemini-3.8-flash` is still `low`, `medium`, `high`. Gemini model factories do not attach `computer_use`.

Run the new test from `orchestrator/` with `.venv/bin/python -m pytest`. Report a collection error that imports `TurnInput` or `_ToolResultHook`. Do not reintroduce those symbols. Do not restart the worker.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — the batch-3 "Worker wiring" row, "Task J — Reasoning levels follow the Agent API catalog", the schedule bullets in "Task F — Dream workflow", the `agent_identity()` sentence in "Task G — Delegation SOP", and the Gemini `computer_use` sentence in "Task I"
- `AGENTS.md` — provider declaration, `REASONING_EFFORTS` behavior, no hardcoded model list on the client
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The Temporal Python SDK source for `ScheduleActionStartWorkflow`, `ScheduleSpec`, `ScheduleIntervalSpec`, `SchedulePolicy`, and `ScheduleOverlapPolicy.SKIP`. Read the signatures. Do not guess the constructor.
- `orchestrator/run_worker.py` — `fetch_model_ids`, `build_perplexity_factories`, `build_model_factory`, `agent_identity`, the worker activity list
- `orchestrator/config.py` — `REASONING_EFFORTS`, `DREAM_MIN_INTERVAL`, `DREAM_MIN_SESSIONS`
- `lib/reasoning-levels.json` and `components/v0/model-picker.tsx` `reasoningLevels` — read them. The picker returns `[]` for a missing key.
- `orchestrator/load_tool.py` `run_loaded_tool` — already registered. Confirm it stays. Do not add a tool activity.
- `orchestrator/dream_workflow.py` — schedule it. Do not rewrite it.
- The SOP string exported by the delegation section. Append it. Do not rewrite the SOP file.
- `orchestrator/server.py` — it already rejects a `reasoning_effort` missing from `REASONING_LEVELS`. Read that. Do not edit it.

## Requirements

**Code writing.** Do not register `journal`, `environment`, or `http_request` as activities. `run_loaded_tool` already runs them. Create the dream schedule if it is absent: `ScheduleActionStartWorkflow` for `DreamWorkflow`, `ScheduleSpec(intervals=[ScheduleIntervalSpec(every=DREAM_MIN_INTERVAL)])`, `SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP)`. `SKIP` is the lock. Pass a `model_id` that is already a registered factory and is one of the delegated models the plan names. Do not add a new model id literal. Append the SOP string inside `agent_identity()`. The workflow does not read the file. In the same function that registers Perplexity factories, every live catalog id and every preset gets `REASONING_EFFORTS` (`minimal`, `low`, `medium`, `high`, `xhigh`, `max`) written into `lib/reasoning-levels.json`. An id that was in the previous catalog and is absent from this fetch is not registered and is removed from that JSON. Gemini entries stay `low`, `medium`, `high`. Do not keep a second model list. Delete the `genai.types.ComputerUse` tool from `build_model_factory`. Gemini uses the same desktop tools as every other model. Do not pass a live `Model`. Do not set `context_manager="auto"`.

**Subagent use.** Do not launch subagents. One agent owns `run_worker.py`. An agent per edit would serialize on that file and race the JSON rewrite.

**Prompting.** Task J is the catalog spec. The schedule bullets are the schedule spec. The `agent_identity()` sentence is the SOP append. The Gemini sentence is the deletion. Do not re-open the tool map in `agent.json`. Do not change the session `tools=` list.

**Scope.** In: `orchestrator/run_worker.py`, `lib/reasoning-levels.json`, and a pytest that uses a fake catalog. Out: `orchestrator/workflow.py`, `orchestrator/agent.json`, `orchestrator/dream_workflow.py`, any new `*_activity.py`, `orchestrator/sops/delegation.sop.md`, `components/v0/**`. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
