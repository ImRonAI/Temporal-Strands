# Task 10: Worker And Live Model Catalog

Canonical section: Task 10.

Allowed files:

- `orchestrator/run_worker.py`
- `orchestrator/tests/test_run_worker.py`

RED and GREEN: `.venv/bin/python -m pytest tests/test_run_worker.py -q`

Acceptance: bounded live model discovery, duplicate/empty rejection, correctly captured factories, schema-validated native tools, closure-only secrets, all workflow/activity/MCP registration, and non-secret readiness lifecycle with fail-before-polling prerequisites.
