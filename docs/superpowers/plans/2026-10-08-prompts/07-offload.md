# Section: Telnyx offload and the shared constants

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task D — Two Telnyx buckets", and the `config.py` constant names in "Task F — Dream workflow" (`DREAM_MIN_INTERVAL` starting at 24 hours, `DREAM_MIN_SESSIONS` starting at 5). You add those dream constants. You do not write the dream workflow.
- `AGENTS.md` — do not lower stream batch intervals; no second payload store
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/streaming/workflow.py` — the sample returns `str(result)`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/graph_activity.py`, `orchestrator/use_agent_activity.py`, `orchestrator/use_skill_activity.py` — the return paths
- `orchestrator/config.py` — add constants here. Do not inline a timeout or a bucket name at the call site.
- The Telnyx MCP tool list. Confirm each argument from that server's tool schema before you call it. Documented names in the plan: `cloud_storage_list_buckets`, `cloud_storage_create_bucket`, `cloud_storage_upload_file`, `cloud_storage_download_file`, `cloud_storage_list_objects`, `cloud_storage_delete_object`, `create_embeddings`, `list_embedded_buckets`, and similarity search. If you cannot read the live schema, say which argument you could not confirm and do not invent it.

## Requirements

**Code writing.** Constants: `OFFLOAD_BUCKET = "gwen-offload"`, `MEMORY_BUCKET = "gwen-memory"`, `OFFLOAD_PAYLOAD_BYTES = 524_288`, `DREAM_MIN_INTERVAL` of 24 hours, `DREAM_MIN_SESSIONS = 5`. Reuse a bucket that exists. Create a missing one once. Offload keys are `sessions/{session_id}/{tool_name}/{activity_id}.json`. Memory keys, one file per memory, are `index.md`, `skills/{skill_name}.md`, `topics/{topic}/{date}-{slug}.md`, `dream-cursor.md`. Call `create_embeddings` once, only when `list_embedded_buckets` does not include `gwen-memory`. Do not call `embed_url`. Do not embed `gwen-offload`. Do not add LanceDB, Mem0, or a second store. Before `graph_activity`, `use_agent_activity`, and `use_skill_activity` return, a JSON body at or above `OFFLOAD_PAYLOAD_BYTES` is uploaded through the Telnyx `MCPClient` factory and the activity returns a short string: tool name, status, object key, and a preview of a few hundred characters. Smaller results return unchanged. Do not lower batch intervals. Do not pass a live `Model`.

**Subagent use.** Do not launch subagents. The bucket policy and the three return sites are one feature. The dream constants are a few lines in `config.py`, so they belong here. A separate constants agent is too small, and a separate agent per activity would fight over the helper.

**Prompting.** Task D is the spec. Task F's schedule and `DREAM_PROMPT` are not yours. You only publish the two dream constants so the dream section can import them. If those names are already in `config.py`, do not add a second copy.

**Scope.** In: `orchestrator/config.py`, one small upload helper next to the activities, `orchestrator/graph_activity.py`, `orchestrator/use_agent_activity.py`, `orchestrator/use_skill_activity.py`. Out: `orchestrator/dream_workflow.py`, `orchestrator/run_worker.py`, `orchestrator/workflow.py`. If a change requires a file outside this list, stop and name the file.

## Outcome

A result smaller than 524288 bytes is unchanged. A larger result from graph, use_agent, or use_skill becomes a short string that names the offload key, and the JSON is in `gwen-offload` at `sessions/{session_id}/{tool_name}/{activity_id}.json`. `gwen-memory` is created when it is missing and embedded once. `gwen-offload` is not embedded. The dream section can import `DREAM_MIN_INTERVAL` and `DREAM_MIN_SESSIONS`.

Run from `orchestrator/`:

```bash
.venv/bin/python -m pytest tests/test_graph_activity.py tests/test_use_agent_activity.py tests/test_use_skill_activity.py -q
```

Add a test for the size threshold if those files do not already cover it. Report a collection error that imports `TurnInput` or `_ToolResultHook`. Do not reintroduce those symbols.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task D — Two Telnyx buckets", and the `config.py` constant names in "Task F — Dream workflow" (`DREAM_MIN_INTERVAL` starting at 24 hours, `DREAM_MIN_SESSIONS` starting at 5). You add those dream constants. You do not write the dream workflow.
- `AGENTS.md` — do not lower stream batch intervals; no second payload store
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/streaming/workflow.py` — the sample returns `str(result)`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- `orchestrator/graph_activity.py`, `orchestrator/use_agent_activity.py`, `orchestrator/use_skill_activity.py` — the return paths
- `orchestrator/config.py` — add constants here. Do not inline a timeout or a bucket name at the call site.
- The Telnyx MCP tool list. Confirm each argument from that server's tool schema before you call it. Documented names in the plan: `cloud_storage_list_buckets`, `cloud_storage_create_bucket`, `cloud_storage_upload_file`, `cloud_storage_download_file`, `cloud_storage_list_objects`, `cloud_storage_delete_object`, `create_embeddings`, `list_embedded_buckets`, and similarity search. If you cannot read the live schema, say which argument you could not confirm and do not invent it.

## Requirements

**Code writing.** Constants: `OFFLOAD_BUCKET = "gwen-offload"`, `MEMORY_BUCKET = "gwen-memory"`, `OFFLOAD_PAYLOAD_BYTES = 524_288`, `DREAM_MIN_INTERVAL` of 24 hours, `DREAM_MIN_SESSIONS = 5`. Reuse a bucket that exists. Create a missing one once. Offload keys are `sessions/{session_id}/{tool_name}/{activity_id}.json`. Memory keys, one file per memory, are `index.md`, `skills/{skill_name}.md`, `topics/{topic}/{date}-{slug}.md`, `dream-cursor.md`. Call `create_embeddings` once, only when `list_embedded_buckets` does not include `gwen-memory`. Do not call `embed_url`. Do not embed `gwen-offload`. Do not add LanceDB, Mem0, or a second store. Before `graph_activity`, `use_agent_activity`, and `use_skill_activity` return, a JSON body at or above `OFFLOAD_PAYLOAD_BYTES` is uploaded through the Telnyx `MCPClient` factory and the activity returns a short string: tool name, status, object key, and a preview of a few hundred characters. Smaller results return unchanged. Do not lower batch intervals. Do not pass a live `Model`.

**Subagent use.** Do not launch subagents. The bucket policy and the three return sites are one feature. The dream constants are a few lines in `config.py`, so they belong here. A separate constants agent is too small, and a separate agent per activity would fight over the helper.

**Prompting.** Task D is the spec. Task F's schedule and `DREAM_PROMPT` are not yours. You only publish the two dream constants so the dream section can import them. If those names are already in `config.py`, do not add a second copy.

**Scope.** In: `orchestrator/config.py`, one small upload helper next to the activities, `orchestrator/graph_activity.py`, `orchestrator/use_agent_activity.py`, `orchestrator/use_skill_activity.py`. Out: `orchestrator/dream_workflow.py`, `orchestrator/run_worker.py`, `orchestrator/workflow.py`. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
