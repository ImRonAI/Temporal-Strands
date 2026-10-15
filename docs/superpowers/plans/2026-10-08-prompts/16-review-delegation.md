# Review: delegation and the package audit

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

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task G — Delegation SOP", "Discovery inventory", "Task B — Formations", "Do not"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/hooks/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The installed `strands-agents-sops` format doc the SOP section was told to read
- The diff for the hook module, `orchestrator/sops/delegation.sop.md`, `orchestrator/tests/test_healthcare_forge_catalog.py`, `orchestrator/mcp.json`

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked.

**Subagent use.** Do not launch subagents. You are one of four reviewers. Do not re-call the forge, dicom, and fhir functions. The audit test is the record. You check that the test covers the inventory.

**Prompting.** Judge cancel rules against Task G. Judge the audit against the inventory lists. Do not add a rule that cancels a direct `swarm` call. Do not require pubmed or FDA tools.

**Scope.** You read the hook's cancel rules and the audit test's coverage. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: running forge invoke against a network, editing the SOP, editing the packages.

## Outcome

Write findings for any of these that are false:

- The hook cancels `graph`, `use_agent`, or `use_skill` for a string topology, a node `model_id` or `model_settings`, a `workflow` or `parallel` type, a `skill_agent` without `skill`, a graph swarm member that is not `agent` or `skill_agent`, and a `tools` entry that is neither on the session nor loaded.
- A direct call to the discovered `swarm` tool is not cancelled.
- The SOP file follows the installed format and does not inject `code-assist` or `pdd`.
- `test_healthcare_forge_catalog.py` names every forge, dicom, and fhir entry in the plan, asserts the three packages are discoverable, and asserts `pubmed` and `fda` are absent. You do not re-execute those functions.

No finding means you write that you checked each bullet and it holds, with the file you read.

## Ground in these resources

Read these before you judge the diff. A behavior you cannot point at in one of them is not a finding. Say you could not verify it.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task G — Delegation SOP", "Discovery inventory", "Task B — Formations", "Do not"
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `.agents/skills/strands-temporal/SKILL.md`
- `.agents/skills/strands-temporal/scripts/strands_plugin/hooks/workflow.py`
- https://docs.temporal.io/develop/python/integrations/strands-agents
- The installed `strands-agents-sops` format doc the SOP section was told to read
- The diff for the hook module, `orchestrator/sops/delegation.sop.md`, `orchestrator/tests/test_healthcare_forge_catalog.py`, `orchestrator/mcp.json`

## Requirements

**Code writing.** You do not write code on this pass. A finding names the file, the plan sentence it breaks, and the source you checked.

**Subagent use.** Do not launch subagents. You are one of four reviewers. Do not re-call the forge, dicom, and fhir functions. The audit test is the record. You check that the test covers the inventory.

**Prompting.** Judge cancel rules against Task G. Judge the audit against the inventory lists. Do not add a rule that cancels a direct `swarm` call. Do not require pubmed or FDA tools.

**Scope.** You read the hook's cancel rules and the audit test's coverage. You also reject, when you see them, `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. Out: running forge invoke against a network, editing the SOP, editing the packages.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
