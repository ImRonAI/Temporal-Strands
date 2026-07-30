# Task 12: Replay And Restart Coverage

Canonical section: Task 12.

Allowed files:

- `orchestrator/tests/test_replay.py`
- `orchestrator/tests/test_restart_integration.py`
- `orchestrator/tests/histories/chat.json`
- `orchestrator/tests/histories/tool.json`
- `orchestrator/tests/histories/approval.json`
- `orchestrator/tests/histories/graph.json`
- `orchestrator/tests/histories/continue_as_new.json`

RED: `.venv/bin/python -m pytest tests/test_replay.py -q`

GREEN:

- `.venv/bin/python -m pytest tests/test_restart_integration.py --capture-histories -q`
- `.venv/bin/python -m pytest tests/test_restart_integration.py tests/test_replay.py -q`

Acceptance: fixtures are generated from real Temporal test runs, never hand-authored; all representative histories replay; worker/API restart and Continue-As-New retain stable workflow behavior and completed messages.
