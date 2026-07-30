# Task 07: Shared Agent Runtime And Approval

Canonical section: Task 7.

Allowed files:

- `orchestrator/agent_runtime.py`
- `orchestrator/tests/test_agent_runtime.py`

RED and GREEN: `.venv/bin/python -m pytest tests/test_agent_runtime.py -q`

Dependency: Task 6 must be completed for graph-dependent acceptance.

Acceptance: one serializable shared builder for chat and compare, Temporal model handle only, complete tool/MCP/memory parity, deterministic bounded result stream, and approval only for configured side effects with correct denial cancellation.
