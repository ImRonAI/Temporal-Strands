# Block And Amend

1. Preserve the current task state as `blocked.prior_state`.
2. Set the task and global execution state to `blocked_user`.
3. Record the exact ambiguity, conflict, unsafe action, unavailable dependency, or scope request.
4. Ask one precise question with concrete options and consequences.
5. Freeze product edits, task delegation, and gate progression.
6. After an explicit user answer, append an amendment with timestamp, decision, and affected tasks.
7. Update only the contracts/allowlists directly authorized by the amendment.
8. Clear `blocked` and restore the prior safe state; never infer approval.
