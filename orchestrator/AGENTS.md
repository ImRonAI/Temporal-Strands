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
- Model factory: `run_worker.py` registers `GeminiModel` for `gemini-3.8-flash` (`GEMINI_MODEL_ID` in `config.py`) and dynamic alias `gemini-flash-latest`.
- `run_worker.py:22-27`: `StrandsPlugin` goes on the **Client**, never the Worker.
- `workflow.py:58-61`: streaming topics `events` / `thinking` / `approval` / `tool_results` are a shared contract with `server.py` and the frontend.
- The `think` community tool is discontinued. Do not register `activity_as_tool(think)` or add `think` to the worker. Reasoning is Gemini thought parts (`include_thoughts=True`) streamed as `reasoningContent` on `events`. `AgentActivity` still hides a tool card named `"think"` if one appears.
- `server.py`: never iterate a turn stream to exhaustion; cancel the consumer, never the workflow update.
- Live `Model` instances never go into workflow `__init__` — use Temporal's official Strands integration (`TemporalAgent` + named model factories on the worker).
- Graceful degradation for optional infra — `telemetry.py` is the reference (missing OTLP endpoint → log + empty plugin list, never raise).

## Graph tool

- Formation graph: **`strands_graph_tool.graph`**, wired as ``activity_as_tool(graph_activity)`` in ``workflow.py`` (Temporal Strands README tools pattern). The activity publishes ``ToolStreamEvent`` envelopes on ``THINKING_TOPIC``; the frontend consumes ``data-graph-run`` via ``route.ts``.
- Package: sibling repo ``../../strands-tools`` (`strands-heterogeneous-graph-tool` 0.1.0). Node types: ``agent``, ``skill_agent``, ``swarm``, ``graph``, ``workflow``, ``parallel``.

## Agent Skills (present)

- Catalog: ``../../strands-tools/skills`` (``SKILLS_DIR`` override). Worker: ``ensure_skills_configured`` + ``configure_skills`` for graph ``skill_agent`` nodes.
- Pattern 2: ``skills_loader.py`` — ``load_tool(..., name="list_skills"|"skill")``.
- Pattern 3: ``use_skill_activity`` — permanent ``activity_as_tool``; streams on ``THINKING_TOPIC``.
- Install: ``pnpm skills:add owner/repo`` (``npx skills`` CLI).
- ``orchestrator/graph_tool.py`` remains **absent and protected** — do not add a duplicate tool implementation.

## Planned modules (do not import)

`memory.py`, `mcp_config.py`, `pophive_sync.py`, `agent_runtime.py`, `run_workflow.py`, `README.md`, `tests/histories/` are design intent only — not on disk. `compare_workflow.py`, `run_worker.py`, and `server.py` have no test suites yet (GWEN-12/13/14).
