# Repository Guidelines

v0-style chat product: Next.js UI streams durable agent turns from a Python Temporal/Strands orchestrator backed by Perplexity’s Agent API.

> **Read this first.** The architecture below is the *target* design. The frontend largely exists; the orchestrator is being built incrementally and most of its modules are **not on disk yet**. See [Current state](#current-state) for the exact split between what exists and what is planned. Treat planned modules as binding design intent — they are the contract the remaining work builds against — but do not assume you can import or run them.

## Project Structure & Module Organization

Request path: `app/page.tsx` (`useChat` → `/api/orchestrator`) converts FastAPI SSE into AI SDK UI-message parts; the bridge is `orchestrator/server.py` (`POST /sessions`, `/turns/stream`, `/end`, `/compare/stream`, `/approve`, `GET /health`) talking to Temporal workflow `ChatWorkflow` in `workflow.py`. Workers (`run_worker.py`) register one `PerplexityModel` factory per live `GET /v1/models` id on task queue `perplexity-orchestrator`.

- `components/ai-elements/` — vendored AI Elements primitives; treat as library code.
- `components/v0/` — app UI that **composes** those primitives (`composer`, `agent-activity`, `model-picker`, `compare-view`).
- `components/ui/` — shadcn/base-ui primitives (`Button`/`Select` use `@base-ui/react`, not Radix `asChild`).
- `lib/perplexity.ts` — `DEFAULT_MODEL` + unauthenticated model listing only; all inference goes through the orchestrator.
- `orchestrator/` — Python stack (`requirements.txt`, local `.venv`). Agent identity lives in `agent.json`.

**Hard rule:** every AI Elements surface must use native subcomponents, props, and animations (`Conversation` scroll, `MessageResponse`/Streamdown, `PromptInput*` submit/attachments, `ChainOfThought*`). Do not reimplement those in `components/v0/`. `reasoning.tsx` is intentionally not vendored — `ChainOfThought` is the only reasoning UI. Model ids are never hardcoded lists; pickers use `/api/models`. A session’s model is fixed at start; switching models ends the session.

### Current state

Verified against the working tree. Anything not listed as present is planned.

**Frontend — present.** `app/page.tsx`, `app/compare/page.tsx`, `app/layout.tsx`, `app/globals.css`. Routes: `app/api/orchestrator/route.ts`, `app/api/orchestrator/end/route.ts`, `app/api/orchestrator/approval/route.ts`, `app/api/compare/route.ts`, `app/api/models/route.ts`. `app/page.tsx` uses `useChat` with `DefaultChatTransport({ api: "/api/orchestrator" })` and calls `/api/orchestrator/end` and `/api/orchestrator/approval` directly. `components/ai-elements/` is vendored (and correctly has **no** `reasoning.tsx`); `components/ui/` is present with 18 files importing `@base-ui/react`; `components/v0/` holds `composer`, `agent-activity`, `model-picker`, `compare-view`, plus `site-header`, `blurple-background`, and the `use-models` hook. `lib/perplexity.ts` and `lib/utils.ts` are present.

**Orchestrator — present.** Only five files, plus their tests:

| File | Contents |
| --- | --- |
| `orchestrator/config.py` | `TASK_QUEUE = "perplexity-orchestrator"`, activity timeouts, `MODEL_RETRY_POLICY`, `EMBEDDING_GENERATIONS` |
| `orchestrator/telemetry.py` | `telemetry_plugins()` — opt-in Temporal OTel wiring, a no-op unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set |
| `orchestrator/perplexity_model.py` | the `PerplexityModel` implementation |
| `orchestrator/agent.json` | agent identity: `name` + system `prompt` |
| `orchestrator/requirements.txt` | pinned deps (`strands-agents`, `temporalio[strands-agents,pydantic]`, `perplexityai`, `lancedb`, `fastapi`, `mcp`, `pytest`, …) |

Tests live in `orchestrator/tests/`: `test_config.py`, `test_telemetry.py`, `test_perplexity_model.py`. There is no `conftest.py` or `__init__.py` — run pytest from inside `orchestrator/`.

**Orchestrator — planned, not yet on disk.** In plan/ledger order: `perplexity_operations.py` (Task 3, active), `memory.py` (4), `mcp_config.py` + `pophive_sync.py` + `scripts/sync-pophive.sh` (5), `graph_activity.py` (6), `agent_runtime.py` (7), `workflow.py` (8), `compare_workflow.py` (9), `run_worker.py` (10), `server.py` (11), replay fixtures under `tests/histories/` (12), `run_workflow.py` + `orchestrator/README.md` (13). Every reference to these above describes intended structure, not current fact. Notably: **`pnpm dev:all`, `dev:worker`, and `dev:api` cannot succeed yet**, because they invoke `run_worker.py` and `uvicorn server:app`. The first end-to-end turn becomes possible only after Task 11.

**`orchestrator/graph_tool.py` is absent and protected.** The guard denies writes to it in every state, so never create or edit it opportunistically. Task 6's prerequisite (`from graph_tool import graph`) therefore cannot pass today; plan line 41 and `task-06.md` direct you to pause Task 6 and the graph-dependent portions of Tasks 7-14 while Tasks 3-5 proceed independently. A more capable graph tool will be designed with the user when the build reaches that point; until then `graph_activity.py` remains a durable activity wrapper around that tool's public `graph` name and native schema, not a reimplementation of it.

### Two active plans

`docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md` is the plan `.opencode/state/ledger.json` executes, and it governs everything under `orchestrator/`. A second plan, `2026-07-30-coding-agent-product-reset.md`, renames `components/v0/` to `components/coding-agent/` and `blurple-background.tsx` to `app-background.tsx`, resets product metadata and copy, and rewrites this file's framing; it lives on branch `feature/coding-agent-product-reset` (worktree `.worktrees/coding-agent-product-reset`, currently identical to `main` apart from this file). **Order is orchestrator plan first, product reset second.** The ledger does not track the reset, so the `components/v0/` paths above stay current — do not apply the rename until the orchestrator plan completes.

## Build, Test, and Development Commands

Use **pnpm** (lockfile present). Root `.env.local` holds `PERPLEXITY_API_KEY`, `DATACOMMONS_MCP_URL`, `DC_API_KEY`, and `POPHIVE_MCP_URL`; it is a protected path — never read or edit it. Next routes reach the orchestrator through `ORCHESTRATOR_URL`, defaulting to `http://localhost:8787` (`app/api/orchestrator/route.ts:15`).

```bash
pnpm install
cd orchestrator && uv venv .venv && uv pip install -r requirements.txt   # once
pnpm dev:all          # frees ports, then Temporal :7233, worker, API :8787, Next :3000
pnpm dev:clean        # scripts/free-ports.sh (3000, 7233, 8233, 8787 + stale worker/uvicorn)
pnpm build && pnpm start
pnpm lint
cd orchestrator && .venv/bin/python run_workflow.py "prompt" [model_id]  # smoke test, no UI
```

Which of those work **today**:

- Working now: `pnpm install`, `pnpm dev` (plain `next dev`), `pnpm build`, `pnpm start`, `pnpm lint`, `pnpm dev:clean`. The `orchestrator/.venv` is already provisioned.
- Blocked until the orchestrator lands: `pnpm dev:all`, `pnpm dev:worker`, `pnpm dev:api`, and the `run_workflow.py` smoke test. `pnpm dev:web` additionally waits on `http-get://127.0.0.1:8787/health`, so it blocks too.

Python tests, from `orchestrator/`:

```bash
.venv/bin/python -m pytest tests -q                       # all three suites
.venv/bin/python -m pytest tests/test_perplexity_model.py -q   # one suite
```

Vitest is configured (`vitest.config.ts`, aliasing `@` to the repo root) and one frontend suite exists, `app/api/orchestrator/route.test.ts`. `package.json` defines no `test` script, and the config sets no `include`/`exclude`, so an unscoped run collects `.worktrees/` duplicates plus `.opencode/tests/plan-guard.test.ts`, which imports `bun:test` and errors. Always scope it:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.opencode/**'
pnpm exec vitest run --exclude '**/.worktrees/**' app/api/orchestrator/route.test.ts   # one suite
```

## Coding Style & Naming Conventions

TypeScript **strict** (`tsconfig.json`), path alias `@/*`. `next.config.mjs` sets `typescript.ignoreBuildErrors: true`, so `pnpm build` is not a type gate — run `npx tsc --noEmit` for that. Lint is flat-config ESLint (`eslint-config-next` core-web-vitals + typescript, ignoring `.next/`, `orchestrator/.venv/`, `next-env.d.ts`); there is no Prettier config, so match surrounding style. Prefer existing composition patterns in `components/v0/*`. Orchestrator code must stay on Temporal’s official Strands integration (`TemporalAgent` + model factories on the worker); do not pass live `Model` instances into workflow `__init__`.

Python here targets 3.13, uses 4-space indent and `snake_case` modules, and keeps module-level constants in `config.py` rather than inlining timeouts or queue names at call sites. Prefer graceful degradation over hard failure for optional infrastructure — `telemetry.py` is the reference: a missing endpoint or missing import logs and returns an empty plugin list instead of raising.

## Commit & Pull Request Guidelines

Subjects are capitalized, imperative, and unprefixed ("Implement durable orchestrator foundation", "Plan coding agent product reset"); keep that form and commit once per completed task. PRs should note frontend vs orchestrator impact and any env/Temporal process requirements for reviewers.

Two constraints still bind regardless of committing. Edits are gated to the active task's `allowed_files` in `.opencode/state/ledger.json`, and `.env*`, `orchestrator/graph_tool.py`, and the four existing `app/api/**/route.ts` files are protected paths denied regardless of that allowlist — change a route only when a failing compatibility test proves the backend cannot satisfy an existing contract. Read `.opencode/context/README.md` before editing anything under `orchestrator/`. Note that `.opencode/plugins/plan-guard.ts:92` still blocks `git add`/`commit`/`push` for OpenCode agents, and ledger amendment A-001 still records the older "user owns Git" decision; the user has since superseded A-001, but that plugin has not been updated.
