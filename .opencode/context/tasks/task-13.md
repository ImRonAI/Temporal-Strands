# Task 13: Operations Documentation And Smoke Client

Canonical section: Task 13.

Allowed files:

- `orchestrator/run_workflow.py`
- `orchestrator/README.md`
- `orchestrator/tests/test_smoke_client.py`

RED: `.venv/bin/python -m pytest tests/test_smoke_client.py -q`

GREEN:

- `.venv/bin/python -m pytest tests/test_smoke_client.py -q`
- `bash -n ../scripts/sync-pophive.sh`

Acceptance: current Temporal plugin/update contracts, fixed selected model, reply output, guaranteed end signal, and exact setup/runtime/persistence/MCP/health/live-test documentation.
