# Task 11: FastAPI And SSE Bridge

Canonical section: Task 11.

Allowed files:

- `orchestrator/server.py`
- `orchestrator/tests/test_server.py`

Protected unless an explicit amendment exists:

- `app/api/orchestrator/route.ts`
- `app/api/orchestrator/approval/route.ts`
- `app/api/orchestrator/end/route.ts`
- `app/api/compare/route.ts`

RED: `.venv/bin/python -m pytest tests/test_server.py -q`

GREEN:

- `.venv/bin/python -m pytest tests/test_server.py -q`
- `pnpm vitest run app/api/orchestrator/route.test.ts`

Acceptance: six validated endpoints, readiness-based model checks, fixed session models, exact protected SSE contracts, subscriber-before-update ordering, disconnect-safe update lifecycle, explicit status mapping, and readiness-aware health.
