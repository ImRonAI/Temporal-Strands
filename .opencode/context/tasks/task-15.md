# Task 15: Root Repository Guidance Refresh

Origin: amendment A-003 (user decision, 2026-08-01). Not derived from the canonical plan.

Allowed files:

- `AGENTS.md`

## Gates

RED: not applicable. The deliverable is repository-guidance prose; no executable test asserts its
content. A-003 authorizes `pending -> implementing` directly. Spec and quality reviews remain
mandatory.

Controller verification (no shell available this session; see Constraints):

1. Read `AGENTS.md` in full.
2. Cross-check every concrete path claim against `Glob`/`Read` of the real tree.
3. Confirm each of the Acceptance items below.

## Acceptance

1. Every forward-looking architectural claim currently in `AGENTS.md` is preserved. Tasks 4-14
   depend on it as design intent.
2. Files that do not yet exist are marked as planned, not described as present. Corrected absent
   set (see Errata): `orchestrator/server.py`, `orchestrator/workflow.py`,
   `orchestrator/run_worker.py`, `orchestrator/run_workflow.py`,
   `orchestrator/compare_workflow.py`, `orchestrator/graph_activity.py`,
   `orchestrator/memory.py`, `orchestrator/mcp_config.py`.
3. Modules that do exist are stated accurately: `orchestrator/config.py`,
   `orchestrator/telemetry.py`, `orchestrator/perplexity_model.py`, `orchestrator/agent.json`,
   `orchestrator/requirements.txt`, and the three suites under `orchestrator/tests/`.
4. The AI Elements hard rule, the no-hardcoded-model-ids rule, the fixed-model-per-session rule,
   and the Temporal/Strands integration rule are retained verbatim in substance.
5. No secrets, no `.env.local` contents, no invented commands.
6. Additive and corrective only. Do not delete a section to shorten the file.

## Errata

Corrected 2026-08-01 during controller verification. The original Acceptance item 2 listed
`orchestrator/agent.json` and `orchestrator/requirements.txt` as absent. Both exist and are
tracked; they are Task 1 deliverables and Task 1 is `completed`. The controller's scan used the
glob `orchestrator/**/*.py`, which matched only Python files and hid both. `task-implementer`
detected the discrepancy, documented both as present, and reported the conflict instead of
following the erroneous contract. Controller re-verified with `orchestrator/*` and a direct read
of `agent.json`. The implementer's deviation was correct and is ratified.

## Constraints

- Root `AGENTS.md` only. No subdirectory `AGENTS.md`. No `CLAUDE.md`.
- Hand-authored from direct file reads. `/init-deep` discovery is not used: no `explore` subagent
  is registered and shell execution is unavailable, so its scale-scoring phase cannot run.
- Protected paths remain denied regardless of this task: `.env*`, `orchestrator/graph_tool.py`,
  and the four existing `app/api/**/route.ts` files.
