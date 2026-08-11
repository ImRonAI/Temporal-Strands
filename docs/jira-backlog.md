# Jira Backlog: Durable Strands/Temporal Orchestrator

Source of truth for migrating the retired `.opencode/` ledger into Jira (2026-08-04).
Canonical requirements remain in
`docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md`.
Status below reflects the actual working tree, not the ledger's last state — Tasks 8–11
implementations landed outside the ledger; only Task 8 has its test suite.

Suggested epic: **Durable Orchestrator** (all issues below).
A second epic, **Coding Agent Product Reset**, covers
`docs/superpowers/plans/2026-07-30-coding-agent-product-reset.md` and must be sequenced
after the orchestrator epic completes.

Run all pytest commands from `orchestrator/` with `.venv/bin/python -m pytest …`.

---

## Done (create closed or skip)

### Task 1 — Orchestrator foundation ✅
`config.py`, `telemetry.py`, `agent.json`, `requirements.txt`. Tests: `tests/test_config.py`, `tests/test_telemetry.py`.

### Task 2 — PerplexityModel provider ✅
`perplexity_model.py`. Tests: `tests/test_perplexity_model.py` (45 tests).

### Task 8 — Durable chat workflow ✅ (completed 2026-08-04)
`workflow.py` + `tests/test_workflow.py` (9 tests): serialized concurrent turns, durable
approval (approve/deny/stale-rejection), idempotent end, queryable history, disconnect-safe
updates, surfaced failures, Continue-As-New preserving model/session/messages/offsets
without duplicate frames.

### Task 15 — Root AGENTS.md refresh ✅ (superseded)
Delivered under ledger amendment A-003; spec-approved, quality re-review (B1/B2 ratified+fixed)
was pending when the ledger was retired. AGENTS.md has since been updated again for the
Jira migration.

---

## Open issues

### ORCH-A · Task 3 — Bounded Agent API operations
**Files:** `orchestrator/perplexity_operations.py`, `orchestrator/tests/test_perplexity_operations.py`
**Gate:** `pytest tests/test_perplexity_operations.py -q` (TDD: RED then GREEN)
**Acceptance:** exact lifecycle/file endpoints; serializable bounded projections; stable
request correlation; safe ambiguous-create retry policy; binary storage outside workflow
payloads; only intended activity-backed tools.
**Dependencies:** none. Ready now.

### ORCH-B · Task 4 — Contextualized LanceDB memory
**Files:** `orchestrator/memory.py`, `orchestrator/tests/test_memory.py`
**Gate:** `pytest tests/test_memory.py -q`
**Acceptance:** embedding limit/dimension/encoding validation (see `EMBEDDING_GENERATIONS`
in `config.py`); signed int8 decoding; ordered nested input; tenant-first filtering;
idempotent stable upserts; versioned tables; bounded entries without vectors;
activity-backed memory tools; retry-safe persistence hook.
**Dependencies:** Task 3.

### ORCH-C · Task 5 — Data Commons and managed PopHIVE MCP
**Files:** `orchestrator/mcp_config.py`, `orchestrator/pophive_sync.py`,
`orchestrator/tests/test_mcp_config.py`, `orchestrator/tests/test_pophive_sync.py`,
`scripts/sync-pophive.sh`, `package.json`, `.gitignore`
**Gate:** `pytest tests/test_mcp_config.py tests/test_pophive_sync.py -q` + `pnpm lint`
**Acceptance:** exactly two worker factories; serializable workflow handles; no workflow
secrets/transports; pinned deterministic PopHIVE checkout and lockfile-aware install;
startup sync; preserved graph-state ignore.
**Note:** `run_worker.py` currently wires both MCP servers as native Agent API
`{"type": "mcp"}` tools (with a documented `allowed_tools` workaround for Data Commons'
malformed `get_multi_entity_observations` schema). Reconcile this task's Strands-MCP
design against that working approach before implementing.
**Dependencies:** Task 4.

### ORCH-D · Task 6 — Graph activity boundary ⛔ BLOCKED
**Files:** `orchestrator/graph_activity.py`, `orchestrator/tests/test_graph_activity.py`,
`orchestrator/tests/fixtures/graph_events.json`
**Prerequisite (hard):** `python -c "from graph_tool import graph; print(graph.tool_name, graph.tool_spec)"`
must exit 0. `orchestrator/graph_tool.py` is absent and protected — never create or edit it.
A more capable graph tool will be designed with the user first.
**Gate:** `pytest tests/test_graph_activity.py -q` + `pnpm exec vitest run app/api/orchestrator/route.test.ts`
**Acceptance:** unchanged public graph schema; exact durable envelope; stable IDs and
sequence; heartbeat/progress publication; serializable result; cancellation propagation.
**Note:** the two currently-failing tests in `app/api/orchestrator/route.test.ts` assert
`data-graph-event` handling and belong to this issue's scope.
**Dependencies:** graph tool design decision (user), Task 5.

### ORCH-E · Task 7 — Shared agent runtime and approval
**Files:** `orchestrator/agent_runtime.py`, `orchestrator/tests/test_agent_runtime.py`
**Gate:** `pytest tests/test_agent_runtime.py -q`
**Acceptance:** one serializable shared builder for chat and compare; Temporal model handle
only (never live Model/MCP/LanceDB/HTTP objects in workflow code); complete tool/MCP/memory
parity between chat and compare; deterministic bounded result stream; approval only for
configured side effects with correct denial cancellation.
**Note:** `workflow.py` and `compare_workflow.py` currently build their agents inline;
this task extracts that into the shared builder. Graph-dependent parity assertions wait
on ORCH-D.
**Dependencies:** Task 6 (graph parity only); otherwise Task 5.

### ORCH-F · Task 9 — Independent model comparison: test suite
**Files:** `orchestrator/tests/test_compare_workflow.py` (implementation `compare_workflow.py` already exists)
**Gate:** `pytest tests/test_compare_workflow.py -q`
**Acceptance:** fresh agent and messages per model; complete shared runtime; distinct
per-model stream topics; concurrent execution; no mutable state sharing; one model's
failure does not remove the other replies.

### ORCH-G · Task 10 — Worker and live model catalog: test suite
**Files:** `orchestrator/tests/test_run_worker.py` (implementation `run_worker.py` already exists)
**Gate:** `pytest tests/test_run_worker.py -q`
**Acceptance:** bounded live model discovery; duplicate/empty rejection; correctly captured
factories (default-arg closure bind); schema-validated native tools; closure-only secrets;
all workflow/activity/MCP registration; non-secret readiness lifecycle with
fail-before-polling prerequisites.

### ORCH-H · Task 11 — FastAPI and SSE bridge: test suite
**Files:** `orchestrator/tests/test_server.py` (implementation `server.py` already exists)
**Protected:** the four `app/api/**/route.ts` files change only with a failing
compatibility test proving the backend cannot satisfy the existing contract.
**Gate:** `pytest tests/test_server.py -q` + `pnpm exec vitest run --exclude '**/.worktrees/**' app/api/orchestrator/route.test.ts`
**Acceptance:** six validated endpoints; readiness-based model checks; fixed session
models; exact protected SSE contracts; subscriber-before-update ordering; disconnect-safe
update lifecycle; explicit status mapping; readiness-aware health.

### ORCH-I · Task 12 — Replay and restart coverage
**Files:** `orchestrator/tests/test_replay.py`, `orchestrator/tests/test_restart_integration.py`,
`orchestrator/tests/histories/{chat,tool,approval,graph,continue_as_new}.json`
**Gate:** capture histories via `pytest tests/test_restart_integration.py --capture-histories -q`,
then `pytest tests/test_restart_integration.py tests/test_replay.py -q`
**Acceptance:** fixtures generated from real Temporal test runs, never hand-authored; all
representative histories replay; worker/API restart and Continue-As-New retain stable
workflow behavior and completed messages. (`graph.json` waits on ORCH-D.)
**Dependencies:** ORCH-F/G/H.

### ORCH-J · Task 13 — Operations documentation and smoke client
**Files:** `orchestrator/run_workflow.py`, `orchestrator/README.md`, `orchestrator/tests/test_smoke_client.py`
**Gate:** `pytest tests/test_smoke_client.py -q` + `bash -n scripts/sync-pophive.sh`
**Acceptance:** current Temporal plugin/update contracts; fixed selected model; reply
output; guaranteed end signal; exact setup/runtime/persistence/MCP/health/live-test docs.
**Dependencies:** ORCH-I.

### ORCH-K · Task 14 — Full verification
**Gate:** `pytest -q`; `pytest tests/test_replay.py -q`; `uv pip check --python .venv/bin/python`;
`pnpm exec vitest run --exclude '**/.worktrees/**' app/api/orchestrator/route.test.ts`; `pnpm lint`;
`pnpm build`; `git status --short`; `git diff --check`
**Acceptance:** complete current evidence; protected contracts pass; production build
succeeds; dependencies compatible; final diff contains no secrets, runtime data, or
unrelated modifications.
**Dependencies:** everything above.

---

## Standing constraints (carry into every issue description)

- Protected paths: `.env*`, `orchestrator/graph_tool.py`, and the four existing
  `app/api/**/route.ts` files.
- Orchestrator code stays on Temporal's official Strands integration (`TemporalAgent` +
  named model factories on the worker); never pass live `Model` instances into workflows.
- No hardcoded model-id lists anywhere; the catalog is fetched live.
- A session's model is fixed at start.
