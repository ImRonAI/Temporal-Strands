# Task 09: Independent Model Comparison

Canonical section: Task 9.

Allowed files:

- `orchestrator/compare_workflow.py`
- `orchestrator/tests/test_compare_workflow.py`

RED and GREEN: `.venv/bin/python -m pytest tests/test_compare_workflow.py -q`

Acceptance: fresh agent and messages per model, complete shared runtime, distinct model topics, concurrent execution, no mutable state sharing, and partial failure preservation.
