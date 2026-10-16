# Review: memory

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

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task C", "Task D — Two Telnyx buckets", "Task F — Dream workflow", "Task A", "Do not"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/streaming/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The diff for `orchestrator/config.py`, the upload helper, `orchestrator/graph_activity.py`, `orchestrator/use_agent_activity.py`, `orchestrator/use_skill_activity.py`, `orchestrator/dream_workflow.py`, `orchestrator/load_tool.py`, `orchestrator/run_worker.py` (the schedule only), `orchestrator/subagent_support.py`

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked.

**Subagent use.** Do not launch subagents. You are one of four reviewers. The Strands-Temporal checks below are part of your review. They are not a fifth reviewer.

**Prompting.** Judge the diff against Task C, Task D, Task F, and Task A. Do not require a payload store the plan does not name.

**Scope.** You read offload keys, embed-once, the dream prompt, the `SKIP` schedule, and `params["tools"]` kept by `in_process_model`. A new `*_activity.py` for `journal`, `environment`, `http_request`, or any other community tool is a finding. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: re-running the Telnyx API, editing the dream prompt, starting a worker.

## Outcome

Write findings for any of these that are false:

- Bodies at or above `OFFLOAD_PAYLOAD_BYTES` return a short string and are stored at `sessions/{session_id}/{tool_name}/{activity_id}.json` in `gwen-offload`. Smaller bodies return unchanged.
- `create_embeddings` runs once, when `gwen-memory` is not already embedded. `embed_url` is not called. `gwen-offload` is not embedded.
- `DREAM_PROMPT` has the eight steps. It does not modify the journal or the offload bucket. The schedule overlap is `SKIP`. `model=` is a factory name argument, not a live model.
- `journal`, `environment`, and `http_request` are registered with `register_community_tool` and run by `run_loaded_tool`. There is no wrapper activity.
- `in_process_model` no longer strips `tools` or `skills`. `background` and `store` are still stripped.

No finding means you write that you checked each bullet and it holds, with the file you read.

## Ground in these resources

Read these before you judge the diff. A behavior you cannot point at in one of them is not a finding. Say you could not verify it.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task C", "Task D — Two Telnyx buckets", "Task F — Dream workflow", "Task A", "Do not"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/streaming/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The diff for `orchestrator/config.py`, the upload helper, `orchestrator/graph_activity.py`, `orchestrator/use_agent_activity.py`, `orchestrator/use_skill_activity.py`, `orchestrator/dream_workflow.py`, `orchestrator/load_tool.py`, `orchestrator/run_worker.py` (the schedule only), `orchestrator/subagent_support.py`

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked.

**Subagent use.** Do not launch subagents. You are one of four reviewers. The Strands-Temporal checks below are part of your review. They are not a fifth reviewer.

**Prompting.** Judge the diff against Task C, Task D, Task F, and Task A. Do not require a payload store the plan does not name.

**Scope.** You read offload keys, embed-once, the dream prompt, the `SKIP` schedule, and `params["tools"]` kept by `in_process_model`. A new `*_activity.py` for `journal`, `environment`, `http_request`, or any other community tool is a finding. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: re-running the Telnyx API, editing the dream prompt, starting a worker.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
