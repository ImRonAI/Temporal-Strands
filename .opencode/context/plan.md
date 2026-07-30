# Durable Orchestrator Execution Contract

Canonical plan: `docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md`.

## Controller Loop

1. Read `ledger.json`, the active task contract, the canonical task section, and `AGENTS.md`.
2. Refuse to start when another task is active or the ledger is `blocked_user`.
3. Delegate RED-test creation to `task-implementer` with only the task allowlist.
4. Run the task's RED command directly. Record command, exit code, and concise output in `evidence.red`.
5. Delegate the minimal implementation to `task-implementer`.
6. Run all focused verification commands directly. Record results in `evidence.green`.
7. Delegate a read-only requirement check to `spec-reviewer`.
8. Delegate a separate read-only maintainability and risk review to `quality-reviewer`.
9. Resolve findings through the implementer and repeat controller verification and reviews.
10. Mark the task complete only after both reviews approve and all required commands pass.

## State Ownership

The controller alone updates task state, evidence, review decisions, blockers, amendments, and `active_task`. Implementers and reviewers return structured reports but do not edit the ledger.

Every evidence item records:

- UTC timestamp
- exact command
- working directory
- exit code
- result summary

Every amendment records the user decision, affected tasks, and resulting contract change. A user decision must be explicit; silence is not approval.

## Scope Rules

- `.opencode/**` maintenance is control-plane work and may be performed by the controller.
- Product edits are limited to the active task's `allowed_files` patterns.
- Protected files are denied even if a broad pattern would otherwise match.
- Task 6 cannot enter RED until its graph prerequisite command passes.
- Graph-dependent portions of Tasks 7-14 remain blocked while Task 6 is blocked.
- Existing frontend route files are protected unless a recorded failing compatibility test and user-approved amendment explicitly allow a change.
- Commit steps in the canonical plan are intentionally disabled. The user owns staging and commits.

## Blocked User

Enter `blocked_user` only for material ambiguity, conflicting requirements, unsafe actions, unavailable required dependencies, graph prerequisite failure, or requested scope changes. Record:

- prior state
- precise blocker
- options and consequences
- exact decision requested

Resume only through `/plan-amend`, after recording the user's decision in `amendments` and restoring the prior safe state.
