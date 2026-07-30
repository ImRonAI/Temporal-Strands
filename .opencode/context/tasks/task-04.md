# Task 04: Contextualized LanceDB Memory

Canonical section: Task 4.

Allowed files:

- `orchestrator/memory.py`
- `orchestrator/tests/test_memory.py`

RED and GREEN: `.venv/bin/python -m pytest tests/test_memory.py -q`

Acceptance: embedding limit/dimension/encoding validation, signed int8 decoding, ordered nested input, tenant-first filtering, idempotent stable upserts, versioned tables, bounded entries without vectors, activity-backed memory tools, and retry-safe persistence hook.
