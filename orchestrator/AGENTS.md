# Orchestrator — Agent Notes

Python 3.13 Temporal/Strands stack. Read the root `AGENTS.md` first; this file adds orchestrator-local detail.

## Running tests

Always run pytest from inside `orchestrator/` — no `conftest.py` or `__init__.py`. The first command is the adherence gate:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest tests/test_workflow.py -q   # needs `temporal` CLI on PATH
```

## Non-negotiable conventions

- Constants live in `config.py` (`TASK_QUEUE`, timeouts, `MODEL_RETRY_POLICY`, batch intervals) — never inline them at call sites.
- `StrandsPlugin` goes on the **Client**, never the Worker.
- Providers subclass concrete Strands providers. `GeminiModel` subclasses Strands' `GeminiModel`; `PerplexityModel` is a standalone `Model` subclass, planned to rebase onto `OpenAIResponsesModel`.
- Hooks subclass `HookProvider` on `HookRegistry` callbacks (`_ThinkFirstHook` on `BeforeInvocationEvent`, `_ToolResultHook` on `AfterToolCallEvent`).
- Community tools come from `strands_tools`, imported, never vendored. The `orchestrator/tools/` symlink farm still exists for `load_tool`; marked for removal.
- `server.py`: never iterate a turn stream to exhaustion; cancel the consumer, never the workflow update.
- Live `Model` instances never go into workflow `__init__` — use `TemporalAgent` + named model factories on the worker.
- Graceful degradation for optional infra — `telemetry.py` is the reference (missing OTLP endpoint → log + empty list, never raise).

## Providers

Primary: Perplexity Agent API (`PerplexityModel`), six `preset:` ids plus every live catalog id. Secondary: Google AI Studio (`GeminiModel`, three ids). Roadmap, no code yet: OpenAI → `OpenAIResponsesModel`, Anthropic → `AnthropicModel`, Mistral → `MistralModel`, xAI → `OpenAIModel` (`base_url=https://api.x.ai/v1`). The single extension point is factory assembly in `run_worker.py` plus `config.py`.

## Think tool

The `think` tool is **live**, falsifying the root `AGENTS.md` claim that it is discontinued. `THINK_TOOL` is built in `workflow.py`, added to the agent's tools, and `_ThinkFirstHook` forces it before the model on each new prompt, folding notes in as a `<think_notes>` block. `think_activity.py` is registered and configured in `run_worker.py`.

## Stream topics

`events`, `thinking`, `approval`, `handoff`, `tool_results`, `agent_runs` are a shared contract with `server.py` and the frontend route; a name change without the route breaks the UI silently.

## Batch intervals

Nested activity streams (`think`, `graph`, `use_agent`, `use_skill`, `agent_runs`) batch at 2s: every flushed batch is a durable Signal in the workflow's history, and at 200ms a long run hit Temporal's 50 MB / 51,200-event cap. The outer model stream stays at 200ms (`streaming_batch_interval` in `workflow.py`).

## Graph tool

The formation graph is vendored in-repo as `orchestrator/graph_tool.py` (behavior-identical consolidation of the former sibling `strands_graph_tool` package; the external editable install is no longer imported). `graph_tool.graph` is an async streaming `@tool` compiling nested formations (agent/skill_agent/swarm/graph/workflow/parallel) to native Strands executors. `orchestrator/graph_activity.py` wraps it as a named `@activity.defn`, and `workflow.py` exposes it to the agent via `activity_as_tool(graph_activity, **_GRAPH_ACTIVITY_OPTIONS)`. `run_worker.py` calls `graph_tool.configure_models` at boot; skills are registered through `configure_skills` in `skills_config.py`. Streaming frames are published on `THINKING_TOPIC` as `{"tool_use", "data"}` envelopes (`ToolStreamEvent` payloads sanitized via `subagent_support.publishable`). Framework adherence (Strands `DecoratedFunctionTool`, Temporal `activity._Definition`, `activity_as_tool` structural input schema) is enforced by `tests/test_graph_tool_adherence.py`.

## Planned modules (do not import)

`memory.py`, `mcp_config.py`, `pophive_sync.py`, `agent_runtime.py`, `run_workflow.py`, `README.md`, `tests/histories/` are design intent only, not on disk.