# Plan Control Context

This directory turns the durable orchestrator plan into a guarded task-state machine.

## Authority

1. `docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md` defines product requirements.
2. `.opencode/context/tasks/task-NN.md` defines the executable scope and gates for one task.
3. `.opencode/state/ledger.json` is the sole mutable runtime authority.
4. `AGENTS.md` remains binding repository guidance.

When sources conflict, stop in `blocked_user`. Never silently broaden scope.

## Non-Negotiable Controls

- The `plan-controller` never edits product code.
- Only `task-implementer` edits files, and only files allowlisted for the active task.
- The controller directly runs every required verification command.
- `spec-reviewer` and `quality-reviewer` are independent and read-only.
- Commits, staging, destructive Git operations, and `.env.local` access are prohibited.
- Unrelated dirty changes are preserved.
- A task advances only through:
  `pending -> red_verified -> implementing -> controller_verified -> spec_approved -> quality_approved -> completed`.
- Material ambiguity, conflicting requirements, unavailable dependencies, or requested scope changes enter `blocked_user`.
- Routine implementation or test failures return to `implementing`; they do not require user approval.

## Runtime Files

- `plan.md`: controller protocol and invariants.
- `tasks/`: exact task contracts derived from the canonical plan.
- `../workflows/`: reusable gate procedures.
- `../commands/`: user-facing controlled entry points.
- `../state/ledger.json`: state, evidence, reviews, blockers, and amendments.
- `../plugins/plan-guard.ts`: runtime file and shell enforcement.

Restart OpenCode after changing any agent, command, plugin, or config-time file.
