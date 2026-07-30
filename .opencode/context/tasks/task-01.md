# Task 01: Reproducible Python Baseline

Canonical section: Task 1.

Allowed files:

- `orchestrator/requirements.txt`
- `orchestrator/agent.json`
- `orchestrator/telemetry.py`
- `orchestrator/config.py`
- `orchestrator/tests/test_config.py`

RED: `.venv/bin/python -m pytest tests/test_config.py -q`

GREEN:

- `uv pip check --python .venv/bin/python`
- `.venv/bin/python -m pytest tests/test_config.py -q`

Acceptance: pinned compatible dependencies, restored identity and optional telemetry, versioned non-secret constants, and no `.env.local` change.
