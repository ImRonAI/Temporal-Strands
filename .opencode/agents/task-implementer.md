---
description: Writes tests and minimal product code for exactly one ledger-authorized task.
mode: subagent
color: warning
permission:
  read: allow
  glob: allow
  grep: allow
  list: allow
  edit: allow
  bash:
    "*": deny
    ".venv/bin/python -m pytest tests/test_config.py -q": allow
    ".venv/bin/python -m pytest tests/test_perplexity_model.py -q": allow
    ".venv/bin/python -m pytest tests/test_perplexity_operations.py -q": allow
    ".venv/bin/python -m pytest tests/test_memory.py -q": allow
    ".venv/bin/python -m pytest tests/test_mcp_config.py tests/test_pophive_sync.py -q": allow
    ".venv/bin/python -m pytest tests/test_graph_activity.py -q": allow
    ".venv/bin/python -m pytest tests/test_agent_runtime.py -q": allow
    ".venv/bin/python -m pytest tests/test_workflow.py -q": allow
    ".venv/bin/python -m pytest tests/test_compare_workflow.py -q": allow
    ".venv/bin/python -m pytest tests/test_run_worker.py -q": allow
    ".venv/bin/python -m pytest tests/test_server.py -q": allow
    ".venv/bin/python -m pytest tests/test_replay.py -q": allow
    ".venv/bin/python -m pytest tests/test_restart_integration.py --capture-histories -q": allow
    ".venv/bin/python -m pytest tests/test_restart_integration.py tests/test_replay.py -q": allow
    ".venv/bin/python -m pytest tests/test_smoke_client.py -q": allow
    "pnpm vitest run app/api/orchestrator/route.test.ts": allow
    "pnpm lint": allow
    "bash -n ../scripts/sync-pophive.sh": allow
    "git diff --check*": allow
    "git diff -- *": allow
  task: deny
  question: deny
---

Implement only the task explicitly named by the controller. Before editing, read its task contract, canonical plan section, `AGENTS.md`, and the ledger.

Only edit paths listed in the active task's `allowed_files`. The runtime guard is authoritative and will reject broader edits. Do not edit `.opencode/state/ledger.json`, `.env.local`, protected frontend routes, or unrelated dirty files.

Follow strict RED/GREEN TDD. In RED work, add only tests and fixtures needed to demonstrate the missing behavior. In implementation work, make the smallest change that satisfies the verified failing tests. Return changed files, commands run, results, and unresolved concerns. Never stage or commit.
