# Execute Active Task

1. Load the ledger and require `blocked` to be null.
2. Load only `active_task`; require all earlier tasks to be `completed`.
3. Read the task contract and canonical plan section.
4. For `pending`, delegate only RED tests/fixtures, run RED directly, and record expected failure before `red_verified`.
5. For `red_verified` or `implementing`, delegate minimal implementation, then run focused GREEN commands directly.
6. On routine failure, record evidence, keep `implementing`, and return findings to the implementer.
7. On GREEN success, set `controller_verified` and invoke `review-task.md`.
8. Mark complete only after both independent approvals.
9. Set the next task active, or leave Task 14 complete with `active_task: null`.

Never combine multiple tasks into one implementation delegation.
