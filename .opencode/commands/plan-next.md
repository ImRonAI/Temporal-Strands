---
description: Execute the next legal gate for the active orchestrator plan task.
agent: plan-controller
---

Follow `.opencode/workflows/execute-task.md` for exactly one legal gate transition of the active task. Read current state first, perform direct controller verification where required, update the ledger after evidence exists, and stop at any blocker. Do not stage or commit. User context: $ARGUMENTS
