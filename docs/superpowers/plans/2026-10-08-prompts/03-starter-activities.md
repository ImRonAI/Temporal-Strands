# Do not build this

`03-starter-activities.md` is not a section. Do not launch it. Do not write `journal_activity.py`, `environment_activity.py`, or `http_request_activity.py`.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, and `http_request` use that path. The registry section calls it when the session starts. `load_tool` calls it later for every other community tool. `graph_activity` and the desktop activities already exist. Leave them.

The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is a teaching example. This repo already has the dynamic activity. Do not copy the sample into a new file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, and `http_request` use that path. The registry section calls it when the session starts. `load_tool` calls it later for every other community tool. `graph_activity` and the desktop activities already exist. Leave them.

The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is a teaching example. This repo already has the dynamic activity. Do not copy the sample into a new file.
