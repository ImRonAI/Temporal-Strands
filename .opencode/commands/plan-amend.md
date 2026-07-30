---
description: Record an explicit user decision and resume a blocked plan without inferred scope changes.
agent: plan-controller
---

Follow `.opencode/workflows/block-and-amend.md`. Require an existing `blocked_user` record and treat the following text as the explicit user decision: $ARGUMENTS

Append the decision to `amendments`, update only directly authorized contracts or allowlists, clear the blocker, and restore the prior safe state. If the text does not resolve the recorded blocker, ask one precise question and keep execution frozen.
