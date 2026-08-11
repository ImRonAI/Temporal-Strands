# Orchestrator — Agent Notes

Python 3.13 Temporal/Strands stack. Read the root `AGENTS.md` first; this file only adds orchestrator-local detail.

## Running tests

Always run pytest from inside `orchestrator/` — there is no `conftest.py` or `__init__.py`:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest tests/test_workflow.py -q   # needs `temporal` CLI on PATH (WorkflowEnvironment.start_local())
```

Fake models in tests are driven by a module-level `SCRIPTS: deque` — `ModelActivity` caches model factories, so scripts must be loaded before the worker starts.

## Non-negotiable conventions

- Constants live in `config.py` (`TASK_QUEUE`, timeouts, `MODEL_RETRY_POLICY`, `EMBEDDING_GENERATIONS`) — never inline them at call sites.
- `run_worker.py:7-20`: model ids like `openai/gpt-5.6-sol` are REAL live catalog ids — pass them verbatim, never "correct" them.
- `run_worker.py:22-27`: `StrandsPlugin` goes on the **Client**, never the Worker.
- `workflow.py:58-61`: streaming topics `events` / `thinking` / `approval` / `tool_results` are a shared contract with `server.py` and the frontend.
- `workflow.py:293`: the reasoning stage is registered as the tool named `"think"` — `components/v0/agent-activity.tsx:887` keys Chain-of-Thought suppression off that exact name. Never rename it.
- `server.py`: never iterate a turn stream to exhaustion; cancel the consumer, never the workflow update.
- Live `Model` instances never go into workflow `__init__` — use Temporal's official Strands integration (`TemporalAgent` + named model factories on the worker).
- Graceful degradation for optional infra — `telemetry.py` is the reference (missing OTLP endpoint → log + empty plugin list, never raise).

## Graph tool (critical)

- `orchestrator/graph_tool.py` and `graph_activity.py` are **ABSENT and PROTECTED**. Never create or edit them opportunistically.
- The actual formation graph tool lives in a **separate repo**: `/Users/tims-stuff/Desktop/strands-tools` — package `strands-heterogeneous-graph-tool` 0.1.0. Public API (`src/strands_graph_tool/__init__.py`): `GraphManager, build_graph, configure_skills, graph`. `graph` is an async-generator `@tool` (`graph.py`); skill-agent nodes come from `skill_nodes.py` (`configure_skills` + `build_skill_agent`). Its `tests/test_graph_tool.py` has 12 passing tests. `examples/basic_graph_agent.py` is stale (legacy API) — do not use it.
- Integration is tracked in Jira: Feature GWEN-21, Epics GWEN-22 (frontend canvas) / GWEN-23 (tool build + validation), Stories/Tasks GWEN-24..30. Two validation gaps remain: live end-to-end stream capture (GWEN-30) and event-shape reconciliation against the `data-graph-event` contract (GWEN-27).
- The two failing tests in `app/api/orchestrator/route.test.ts` assert that contract and are **expected to fail** — never fix or delete them.

## Planned modules (do not import)

`perplexity_operations.py`, `memory.py`, `mcp_config.py`, `pophive_sync.py`, `agent_runtime.py`, `run_workflow.py`, `README.md`, `tests/histories/` are design intent only — not on disk. `compare_workflow.py`, `run_worker.py`, and `server.py` have no test suites yet (GWEN-12/13/14).
