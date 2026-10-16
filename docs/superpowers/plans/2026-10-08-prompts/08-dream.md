# Section: dream workflow

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. You write the workflow module and `DREAM_PROMPT`. The schedule is four lines in `run_worker.py` and belongs to `12-worker-wiring.md`.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task F — Dream workflow". The "Schedule in `run_worker.py`" bullets are not this section.
- `AGENTS.md` — delegated model policy, no live `Model` in a workflow
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md` — `TemporalAgent`, `model=` is a factory name, no `context_manager="auto"`
- `.agents/skills/strands-temporal/scripts/strands_plugin/hello_world/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/config.py` — import `DREAM_MIN_INTERVAL`, `DREAM_MIN_SESSIONS`, `MODEL_START_TO_CLOSE`, `MODEL_HEARTBEAT`, `MODEL_RETRY_POLICY`. The offload section adds the two dream constants. If they are missing, stop. Do not add them yourself.
- `orchestrator/load_tool.py` — `register_community_tool` and `run_loaded_tool`. The dream agent gets `journal` through that path. Do not write a journal activity.
- `orchestrator/agent_tools.py` — `temporal_mcp_clients()`, read only

## Requirements

**Code writing.** `orchestrator/dream_workflow.py` defines `DreamWorkflow`. `run` takes `model_id: str` and returns `str`. The agent is a `TemporalAgent` with `model=model_id`, because the worker sets `StrandsPlugin(models=...)`. Tools are `journal`, registered with `register_community_tool` so `run_loaded_tool` runs it, and `temporal_mcp_clients()`. Do not write a journal activity. Timeouts and the retry policy come from `config.py`. `model_id` is passed in. Do not write a model id literal. The delegated set the caller may pass is Fable 5.1, GPT 6, DeepSeek V4 Flash, DeepSeek V4 Pro, GLM 5.3, GLM 5.3 Flash, Gemini 3.8 Flash, Grok 4.6, and Kimi K3. Do not pass a live `Model`. Do not set `context_manager="auto"`. Do not take a snapshot. The workflow does not read the journal directory itself. The journal tool does.

**Subagent use.** Do not launch subagents. The prompt and the one-shot agent are the work. The schedule is too small to be its own agent and it lives in a file this section must not touch.

**Prompting.** `DREAM_PROMPT` is the eight steps in Task F, in that order. One memory file is one decision, one preference, one skill reflection, or one verified fact. A session may produce several files, or none. Do not tell the dream to modify `cwd()/journal` or `gwen-offload`. Do not tell it to promote a to-do or a raw tool body. Do not tell it to call `create_embeddings` per file. The embed-once step is only when `gwen-memory` has never been embedded.

**Scope.** In: `orchestrator/dream_workflow.py`. Out: `orchestrator/run_worker.py`, `orchestrator/config.py`, the journal activity, the offload helper. If a change requires a file outside this list, stop and name the file.

## Outcome

`DreamWorkflow.run(model_id)` builds one `TemporalAgent`, invokes it once, and returns `str` of the result. The prompt makes that agent read `gwen-memory/index.md`, journal entries newer than `dream-cursor.md`, and new `gwen-offload/sessions/` prefixes; stop when the cursor is inside `DREAM_MIN_INTERVAL` and the new session count is below `DREAM_MIN_SESSIONS`; otherwise write or replace separate memory files, replace a file the new sessions contradict, and rewrite `index.md` and `dream-cursor.md`. The schedule that starts this workflow does not exist yet. That is correct.

A unit test can construct the prompt and assert the eight rules are in it, and can assert the workflow code passes `model=model_id` and does not contain a model id literal. Run that test from `orchestrator/` with `.venv/bin/python -m pytest`. Do not start a Temporal server for this section.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task F — Dream workflow". The "Schedule in `run_worker.py`" bullets are not this section.
- `AGENTS.md` — delegated model policy, no live `Model` in a workflow
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md` — `TemporalAgent`, `model=` is a factory name, no `context_manager="auto"`
- `.agents/skills/strands-temporal/scripts/strands_plugin/hello_world/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/config.py` — import `DREAM_MIN_INTERVAL`, `DREAM_MIN_SESSIONS`, `MODEL_START_TO_CLOSE`, `MODEL_HEARTBEAT`, `MODEL_RETRY_POLICY`. The offload section adds the two dream constants. If they are missing, stop. Do not add them yourself.
- `orchestrator/load_tool.py` — `register_community_tool` and `run_loaded_tool`. The dream agent gets `journal` through that path. Do not write a journal activity.
- `orchestrator/agent_tools.py` — `temporal_mcp_clients()`, read only

## Requirements

**Code writing.** `orchestrator/dream_workflow.py` defines `DreamWorkflow`. `run` takes `model_id: str` and returns `str`. The agent is a `TemporalAgent` with `model=model_id`, because the worker sets `StrandsPlugin(models=...)`. Tools are `journal`, registered with `register_community_tool` so `run_loaded_tool` runs it, and `temporal_mcp_clients()`. Do not write a journal activity. Timeouts and the retry policy come from `config.py`. `model_id` is passed in. Do not write a model id literal. The delegated set the caller may pass is Fable 5.1, GPT 6, DeepSeek V4 Flash, DeepSeek V4 Pro, GLM 5.3, GLM 5.3 Flash, Gemini 3.8 Flash, Grok 4.6, and Kimi K3. Do not pass a live `Model`. Do not set `context_manager="auto"`. Do not take a snapshot. The workflow does not read the journal directory itself. The journal tool does.

**Subagent use.** Do not launch subagents. The prompt and the one-shot agent are the work. The schedule is too small to be its own agent and it lives in a file this section must not touch.

**Prompting.** `DREAM_PROMPT` is the eight steps in Task F, in that order. One memory file is one decision, one preference, one skill reflection, or one verified fact. A session may produce several files, or none. Do not tell the dream to modify `cwd()/journal` or `gwen-offload`. Do not tell it to promote a to-do or a raw tool body. Do not tell it to call `create_embeddings` per file. The embed-once step is only when `gwen-memory` has never been embedded.

**Scope.** In: `orchestrator/dream_workflow.py`. Out: `orchestrator/run_worker.py`, `orchestrator/config.py`, the journal activity, the offload helper. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
