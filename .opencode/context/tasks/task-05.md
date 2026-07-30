# Task 05: Data Commons And Managed PopHIVE MCP

Canonical section: Task 5.

Allowed files:

- `orchestrator/mcp_config.py`
- `orchestrator/pophive_sync.py`
- `orchestrator/tests/test_mcp_config.py`
- `orchestrator/tests/test_pophive_sync.py`
- `scripts/sync-pophive.sh`
- `package.json`
- `.gitignore`

RED: `.venv/bin/python -m pytest tests/test_mcp_config.py tests/test_pophive_sync.py -q`

GREEN:

- `.venv/bin/python -m pytest tests/test_mcp_config.py tests/test_pophive_sync.py -q`
- `pnpm lint`

Acceptance: exactly two worker factories, serializable workflow handles, no workflow secrets/transports, pinned deterministic PopHIVE checkout and lockfile-aware install, startup sync, and preserved graph-state ignore.
