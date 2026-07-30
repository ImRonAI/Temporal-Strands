# Task 14: Full Verification

Canonical section: Task 14.

Allowed files: none by default. A failing verification may authorize only the minimum proven fix through an explicit ledger amendment copied from the affected task's allowlist.

Required verification:

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m pytest tests/test_replay.py -q`
- `uv pip check --python .venv/bin/python`
- `pnpm vitest run app/api/orchestrator/route.test.ts`
- `pnpm lint`
- `pnpm build`
- `git status --short`
- `git diff --check`

Acceptance: complete current evidence, protected contracts pass, production build succeeds, dependencies are compatible, and final diff contains no secrets, runtime data, staging, or unrelated modifications made by this plan.
