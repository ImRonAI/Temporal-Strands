# Task 08: Durable Chat Workflow

Canonical section: Task 8.

Allowed files:

- `orchestrator/workflow.py`
- `orchestrator/tests/test_workflow.py`

RED and GREEN: `.venv/bin/python -m pytest tests/test_workflow.py -q`

Acceptance: serializable state, fixed model, serialized concurrent turns, durable approval, idempotent end, queryable completed history, disconnect-safe updates, surfaced failures, and Continue-As-New preserving identity, messages, tenant/session, and stream offsets without duplicates.
