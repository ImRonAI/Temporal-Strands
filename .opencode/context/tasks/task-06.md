# Task 06: Graph Activity Boundary

Canonical section: Task 6.

Allowed files:

- `orchestrator/graph_activity.py`
- `orchestrator/tests/test_graph_activity.py`
- `orchestrator/tests/fixtures/graph_events.json`

Prerequisite: `.venv/bin/python -c "from graph_tool import graph; print(graph.tool_name, graph.tool_spec)"`

Do not enter RED unless the prerequisite exits 0 and exposes the public `graph` name and native schema.

RED: `.venv/bin/python -m pytest tests/test_graph_activity.py -q`

GREEN:

- `.venv/bin/python -m pytest tests/test_graph_activity.py -q`
- `pnpm vitest run app/api/orchestrator/route.test.ts`

Acceptance: unchanged public graph schema, exact durable envelope, stable IDs and sequence, heartbeat/progress publication, serializable result, and cancellation propagation. Never edit `orchestrator/graph_tool.py`.
