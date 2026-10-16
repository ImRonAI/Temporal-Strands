# Section: forge, dicom, and fhir audit

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

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Discovery inventory", including the forge, dicom, fhir, pubmed, and fda subsections
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `orchestrator/load_tool.py` — `discover_tools_activity` and `_nested_tool_file`. Read them. Do not rewrite them if the catalog already lists the three packages.
- `orchestrator/strands-tools/forge/strands_tools.py` — `get_forge_strands_tools`
- `orchestrator/strands-tools/dicom/strands_tools.py` — `get_dicom_strands_tools`
- `orchestrator/strands-tools/fhir/fhir_tools.py` — `fhir_terminology_copilot`
- `orchestrator/strands-tools/fda/__init__.py` — confirm it defines no tool

## Requirements

**Code writing.** This section is a test and an audit record. Assert `forge`, `dicom`, and `fhir` appear in the discover catalog. Assert `pubmed` and `fda` do not. Import the three packages. Call the no-network entry points named in the plan. `forge.health` must succeed with no network. Invoke and webhook calls may fail closed; record the exception. Do not fake a success. File-based DICOM calls use a tiny fixture the test writes, or they record the `pydicom` import error. Orthanc, DICOMweb, and terminology-server actions are recorded as needing that server. Do not start a server. Do not call a public terminology server. Do not create a pubmed package. Do not invent FDA tools. Do not rewrite the loaders when the test shows the specs already load.

**Subagent use.** Do not launch subagents. The discoverability lines are a few assertions. The work is the audit, and it is one test module. One agent per package would split one file. A separate "confirm the names" agent is too small.

**Prompting.** The inventories in the plan are the names you assert. If a function's signature in the source disagrees with the plan, the source wins, and you say so in the test output. Do not add tools to the inventory to make the plan match a guess.

**Scope.** In: `orchestrator/tests/test_healthcare_forge_catalog.py`. Out: `orchestrator/load_tool.py`, the package sources, `orchestrator/strands-tools/pubmed`, `orchestrator/strands-tools/fda`. If a change requires a file outside this list, stop and name the file.

## Outcome

`orchestrator/tests/test_healthcare_forge_catalog.py` fails when `forge`, `dicom`, or `fhir` is missing from discovery, fails when `pubmed` or `fda` is present, and records a result for every name in the three inventories: passed with no network, or the reason it needs a server, a license, or a missing import.

Run from `orchestrator/`:

```bash
.venv/bin/python -m pytest tests/test_healthcare_forge_catalog.py -q
```

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Discovery inventory", including the forge, dicom, fhir, pubmed, and fda subsections
- `AGENTS.md`
- `orchestrator/AGENTS.md`
- `orchestrator/load_tool.py` — `discover_tools_activity` and `_nested_tool_file`. Read them. Do not rewrite them if the catalog already lists the three packages.
- `orchestrator/strands-tools/forge/strands_tools.py` — `get_forge_strands_tools`
- `orchestrator/strands-tools/dicom/strands_tools.py` — `get_dicom_strands_tools`
- `orchestrator/strands-tools/fhir/fhir_tools.py` — `fhir_terminology_copilot`
- `orchestrator/strands-tools/fda/__init__.py` — confirm it defines no tool

## Requirements

**Code writing.** This section is a test and an audit record. Assert `forge`, `dicom`, and `fhir` appear in the discover catalog. Assert `pubmed` and `fda` do not. Import the three packages. Call the no-network entry points named in the plan. `forge.health` must succeed with no network. Invoke and webhook calls may fail closed; record the exception. Do not fake a success. File-based DICOM calls use a tiny fixture the test writes, or they record the `pydicom` import error. Orthanc, DICOMweb, and terminology-server actions are recorded as needing that server. Do not start a server. Do not call a public terminology server. Do not create a pubmed package. Do not invent FDA tools. Do not rewrite the loaders when the test shows the specs already load.

**Subagent use.** Do not launch subagents. The discoverability lines are a few assertions. The work is the audit, and it is one test module. One agent per package would split one file. A separate "confirm the names" agent is too small.

**Prompting.** The inventories in the plan are the names you assert. If a function's signature in the source disagrees with the plan, the source wins, and you say so in the test output. Do not add tools to the inventory to make the plan match a guess.

**Scope.** In: `orchestrator/tests/test_healthcare_forge_catalog.py`. Out: `orchestrator/load_tool.py`, the package sources, `orchestrator/strands-tools/pubmed`, `orchestrator/strands-tools/fda`. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
