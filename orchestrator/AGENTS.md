# Orchestrator — Agent Notes

Python 3.13 Temporal/Strands stack. Read the root `AGENTS.md` first; this file adds orchestrator-local detail.

## Desktop implementation contract

Before editing `desktop_worker.py`, `browser_activity.py`, `computer_use_activity.py`, or desktop-related workflow, server, config, and provider code, read `desktop/AGENTS.md`. That document covers sibling backend files explicitly through this instruction, not directory inheritance alone. Preserve Think. It records the required noVNC iframe, native-only framework integration, screenshot delivery, human takeover, same-session Continue-As-New, known gaps, and mandatory live acceptance tests. Do not treat old inventory claims in this file as proof that the current provider or desktop implementation is complete.

## Running tests

Always run pytest from inside `orchestrator/` — no `conftest.py` or `__init__.py`. The first command is the adherence gate:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest tests/test_workflow.py -q   # needs `temporal` CLI on PATH
```

## Non-negotiable conventions

- Constants live in `config.py` (`TASK_QUEUE`, timeouts, `MODEL_RETRY_POLICY`, batch intervals) — never inline them at call sites.
- `StrandsPlugin` goes on the **Client**, never the Worker.
- Providers subclass concrete Strands providers. `GeminiModel` (`gemini_model.py`) subclasses Strands' `GeminiModel`; `PerplexityModel` (`perplexity_model.py:245`) subclasses `OpenAIResponsesModel` — the adherence gate rejects any bare `Model` subclass.
- Hooks subclass `HookProvider` on `HookRegistry` callbacks (`_ToolResultHook` on `BeforeToolCallEvent`/`AfterToolCallEvent`/`MessageAddedEvent`, `_HumanControlHook` on `BeforeToolCallEvent`/`BeforeModelCallEvent`/`AfterToolCallEvent`, `_HotLoadHook`/`_ComputerUseSafetyHook` on the tool-call events).
 - Community tools come from `strands_tools`, imported, never vendored. The former `orchestrator/tools/` symlink farm was removed; `load_tool` runs as the worker-registered `load_tool_activity` (`workflow.py` permanent tool) and resolves bare module names against the installed `strands_tools` package.
- `server.py`: never iterate a turn stream to exhaustion; cancel the consumer, never the workflow update.
- Live `Model` instances never go into workflow `__init__` — use `TemporalAgent` + named model factories on the worker.
- Graceful degradation for optional infra — `telemetry.py` is the reference (missing OTLP endpoint → log + empty list, never raise).

## Providers

Primary: Perplexity Agent API (`PerplexityModel`), six `preset:` ids plus every live catalog id. Secondary: Google AI Studio (`GeminiModel`, three ids). Roadmap, no code yet: OpenAI → `OpenAIResponsesModel`, Anthropic → `AnthropicModel`, Mistral → `MistralModel`, xAI → `OpenAIModel` (`base_url=https://api.x.ai/v1`). The single extension point is factory assembly in `run_worker.py` plus `config.py`.

## Think tool

Think is the installed `strands_tools.think` tool, **unmodified**, exposed to the session `TemporalAgent` as `THINK_TOOL = activity_as_tool(think_activity.think, ...)` — a thin `@activity.defn(name="think")` wrapper (Pattern 2 surface 3) that calls the installed callable on the activity executor. It cannot run workflow-side: it is synchronous (Strands runs sync tools via `asyncio.to_thread`, which Temporal's workflow loop blocks with `NotImplementedError`) and its nested `agent(prompt)` cycles need a real event loop. Agent objects cannot cross the activity boundary (official docs: hook events' Agent references "cannot cross the Activity boundary"), so there is no parent injection and no worker stand-in — nested cycles resolve their model through the tool's own custom-provider path, `orchestrator/.models/perplexity.py` (`create_model` → `$CWD/.models/<provider>.py` → `instance()`), which builds the same `PerplexityModel` (Agent API + native server-side tools) the session uses. The model decides when to think and for how many cycles (`cycle_count` 1-10, per `agent.json`'s Thinking section); no hook forces or gates tool order.

The think envelope (`THINK_START_TO_CLOSE` 10 min, `THINK_HEARTBEAT_TIMEOUT` 2 min, `THINK_RETRY_POLICY` 1 attempt) lives in `config.py`; the activity is registered in `run_worker.py`'s `WORKER_ACTIVITIES`. Nested-cycle output does not stream to the `thinking` topic in this architecture — the result returns as the activity's tool result.

**Replay:** histories that scheduled the retired workflow-side Think (patch markers `think-model-chosen-astra-v1`, `think-native-tool-v1`, both `deprecate_patch`ed in `run()`) must finish on their original worker or be explicitly migrated; a deprecated marker does not make a changed activity-command sequence replay-compatible. Never silently terminate sessions.

Note: `strands_tools.think` is deprecated upstream ("error log in v0.9.0; migration path: native extended thinking via provider reasoning config" — `strands-tools/think.py` docstring). A future migration to provider reasoning config is a product decision, not done here.

## Stream topics

`events`, `thinking`, `approval`, `handoff`, `tool_results`, `agent_runs` are a shared contract with `server.py` and the frontend route; a name change without the route breaks the UI silently.

## Batch intervals

Nested graph, use_agent, and use_skill streams batch at 2s; Think's nested model stream batches at 500ms so actual output appears sooner. Every flushed batch is a durable Signal in workflow history, and at 200ms a long run hit Temporal's 50 MB / 51,200-event cap. The outer model stream stays at 200ms (`streaming_batch_interval` in `workflow.py`).

## Model activity timeouts

`TemporalAgent.start_to_close_timeout` bounds each model-call activity, not the whole Think cycle. The example 60-second window cut off slow reasoning; `MODEL_START_TO_CLOSE` now allows 10 minutes per call, `MODEL_HEARTBEAT` is 2 minutes (the installed Strands plugin heartbeats at half that interval), and `MODEL_RETRY_POLICY` allows one attempt so a timed-out streamed completion is not silently duplicated. The Think cycle's separate activity timeout is still 10 minutes for pre-patch histories. Longer model calls still fail after their bounded attempt; a 500ms stream batch interval does not change that limit. Diagnose any `Activity not found on completion` warning with its actual workflow history before attributing it to this change.

## Graph tool

The formation graph is vendored in-repo as `orchestrator/graph_tool.py` (behavior-identical consolidation of the former sibling `strands_graph_tool` package; the external editable install is no longer imported). `graph_tool.graph` is an async streaming `@tool` compiling nested formations (agent/skill_agent/swarm/graph/workflow/parallel) to native Strands executors. `orchestrator/graph_activity.py` wraps it as a named `@activity.defn`, and `workflow.py` exposes it to the agent via `activity_as_tool(graph_activity, **_GRAPH_ACTIVITY_OPTIONS)`. `run_worker.py` calls `graph_tool.configure_models` at boot; skills are registered through `configure_skills` in `skills_config.py`. Streaming frames are published on `THINKING_TOPIC` as `{"tool_use", "data"}` envelopes (`ToolStreamEvent` payloads sanitized via `subagent_support.publishable`). Framework adherence (Strands `DecoratedFunctionTool`, Temporal `activity._Definition`, `activity_as_tool` structural input schema) is enforced by `tests/test_graph_tool_adherence.py`.

## Planned modules (do not import)

`memory.py`, `mcp_config.py`, `pophive_sync.py`, `agent_runtime.py`, `run_workflow.py`, `README.md`, `tests/histories/` are design intent only, not on disk.

## Module map (29 top-level modules)

| Group | Module (lines) | Key names | Imported by |
|-------|----------------|-----------|-------------|
| Core | `config.py` (390) | `TASK_QUEUE`, `DESKTOP_BROWSER_TASK_QUEUE`, `PROVIDER_*`, `RegisteredModel`, every timeout/`RetryPolicy`, `*_STREAM_BATCH_INTERVAL` | 22 modules |
| Core | `workflow.py` (1620) | `ChatWorkflow` :943, `TurnInput` :421, `ChatInput` :447, `EVENTS_TOPIC` :339 … `TOOL_RESULTS_TOPIC` :389 | `server`, `run_worker`, `*_activity` |
| Core | `server.py` (933) | `lifespan` :266, `health` :362, `start_session` :395, `turn_stream` :416, `approve` :654, `desktop_control_status` :708, `handoff` :741, `desktop_image` :789, `end_session` :806, `response_file_content` :825, `compare_stream` :877 | ASGI entry |
| Core | `run_worker.py` (532) | `assemble_model_factories` :280, `workflow_tool_specs` :338, `WORKER_ACTIVITIES` :404, `connect_included_mcp_servers` :430, `main` :464 | `server` |
| Core | `catalog_workflow.py`, `compare_workflow.py` | `ModelCatalogWorkflow`; `CompareWorkflow` (legacy — UI compare now runs N `AgentChat`s) | `run_worker`, `server` |
| Providers | `perplexity_model.py`, `gemini_model.py`, `agent_api_tools.py` | `PerplexityModel`, `GeminiModel`, `native_tools`, `_validate_tools` | `run_worker`, `workflow`, `server` |
| Tools | `think_activity.py`, `load_tool.py`, `graph_activity.py`, `graph_tool.py` (protected), `subagent_support.py`, `use_agent_activity.py`, `use_skill_activity.py`, `skills_config.py`, `skills_loader.py` | `think`, `load_tool_activity`, `mcp_client_activity`, `graph_activity` :328, `publishable`, `catalog_skills` | `run_worker`, `workflow` |
| Desktop | `desktop_worker.py`, `browser_activity.py`, `computer_use_activity.py`, `desktop_observation.py` | `main` :112, `browser_activity` :151, `desktop_control`, `COMPUTER_USE_ACTIVITIES`, `store_observation` | container worker only; `server` reads observations |
| State | `workspace_state.py` (579), `workspace_registry.py`, `workspace_api.py`, `workspace_files.py`, `workspace_service.py` | `ControllerLease`, `StateConflict` :75, `ScopeUnavailable` :79, `begin_control_transition` :220, `accept_operation` :352 | `server`, `workspace_api` |
| Misc | `telemetry.py` | `telemetry_plugins` | both workers |

`ChatWorkflow` handlers: updates `turn` :1173, `claim_control` :1386, `desktop_control` :1399, `relinquish_control` :1429; signals `approve` :1353, `take_control` :1367, `give_control` :1451, `end_chat` :1461; queries `control_status` :1443, `messages` :1466, `pending_approval` :1470, `model_id` :1474, `loaded_tools` :1478, `session_id` :1493, `turn_start_offset` :1497; `run` :1502.

Non-code: `agent.json` (identity `Orchestrator`, 9.3 KB prompt, no `tools` array), `agents/perplexity.json` + `agents/gemini.json` (provider prompt overrides), `mcp.json` (`shell`, `datacommons`, `pophive` — the only runtime MCP registry), `desktop/` (Dockerfile, `start-desktop.sh`, `seccomp.json`, `requirements.txt`).

## integrations/ is a staging lane, not runtime

`integrations/{google-tools,fun-tools,mcp,openapi}` hold isolated `.venv`/`venvs/`, manifests, probes, and lane tests for candidate tool packages and MCP servers. `run_worker.py`/`workflow.py`/`load_tool.py` never read this tree. Promotion = hand-merge a stanza from `mcp/config/mcpServers.fragment.json` or `openapi/mcp-fragment.json` into `mcp.json`, or add a pin to `requirements.txt`. Gotchas: fragments carry absolute `/Users/tims-stuff/...` paths and `${VAR}` placeholders; `openapi/specs/` is empty (specs live in a sibling checkout); mutating Google tools need `BYPASS_TOOL_CONSENT=true` off a TTY; importing `strands_fun_tools` litters cwd. Lane tests need their lane venv — `pytest tests` from `orchestrator/`, never bare `pytest`.

## Tests (38 test modules in `tests/`)

Live Temporal (`WorkflowEnvironment.start_local`, needs `temporal` CLI): `test_workflow.py`, `test_workflow_desktop_rollover.py`, `test_hot_load.py` (spawns `tests/echo_mcp_server.py`), `test_browser_activity.py` default path. Opt-in container test: `GWEN_DESKTOP_LIVE_TEST=1` + `GWEN_DESKTOP_TEST_TEMPORAL_ADDRESS`. Everything else uses `ActivityEnvironment`, `AsyncMock`, or FastAPI `TestClient`. `forbid_host_desktop` autouse fixtures in `test_desktop_runtime.py` / `test_desktop_native_contracts.py` block host GUI. Gates: `test_framework_adherence.py`, `test_graph_tool_adherence.py`. `tests/dump_tool_specs.py` is a debug helper, not a test.
