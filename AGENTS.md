# Repository Guidelines

v0-style chat product: Next.js UI streams durable agent turns from a Python Temporal/Strands orchestrator backed by Perplexity’s Agent API.

> **Read this first.** The architecture below is the *target* design. The frontend and the orchestrator's core runtime exist; several supporting modules are still planned. See [Current state](#current-state) for the exact split. Treat planned modules as binding design intent — they are the contract the remaining work builds against — but do not assume you can import or run them.

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

**Frontend — present.** `app/page.tsx`, `app/compare/page.tsx`, `app/layout.tsx`, `app/globals.css`. Routes: `app/api/orchestrator/route.ts`, `app/api/orchestrator/end/route.ts`, `app/api/orchestrator/approval/route.ts`, `app/api/orchestrator/file/route.ts` (streams sandbox-produced `share_file` output through the orchestrator, resolving relative Agent API file paths `/v1/{responses|agent}/{id}/files/{id}/content`; `app/api/orchestrator/route.ts` rewrites native tool file URLs to `/api/orchestrator/file?path=...` around line 354), `app/api/compare/route.ts`, `app/api/models/route.ts`. `app/page.tsx` uses `useChat` with `DefaultChatTransport({ api: "/api/orchestrator" })` and calls `/api/orchestrator/end` and `/api/orchestrator/approval` directly. `components/ai-elements/` is vendored (and correctly has **no** `reasoning.tsx`); `components/ui/` is present with 18 files importing `@base-ui/react`; `components/v0/` holds `composer`, `agent-activity`, `model-picker`, `compare-view`, plus `site-header`, `blurple-background`, and the `use-models` hook. `lib/perplexity.ts` and `lib/utils.ts` are present.

**Orchestrator — present.** Core runtime plus foundation:

| File | Contents |
| --- | --- |
| `orchestrator/config.py` | `TASK_QUEUE = "perplexity-orchestrator"`, activity timeouts, `MODEL_RETRY_POLICY`, `EMBEDDING_GENERATIONS` |
| `orchestrator/telemetry.py` | `telemetry_plugins()` — opt-in Temporal OTel wiring, a no-op unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set |
| `orchestrator/perplexity_model.py` | the `PerplexityModel` implementation |
| `orchestrator/workflow.py` | `ChatWorkflow`: durable session, `turn` update, HITL approval, streaming topics, continue-as-new; reasoning stage registered as the `think` tool via `Agent.as_tool` — the UI keys Chain-of-Thought suppression off that exact tool name (`components/v0/agent-activity.tsx:887`), so never rename it |
| `orchestrator/compare_workflow.py` | `CompareWorkflow`: independent per-model comparison |
| `orchestrator/run_worker.py` | worker: live model catalog → named factories, native Agent API tools + remote MCP, readiness file |
| `orchestrator/server.py` | FastAPI SSE bridge (`POST /sessions`, `/turns/stream`, `/end`, `/compare/stream`, `/approve`, `GET /health`) |
| `orchestrator/agent.json` | agent identity: `name` + system `prompt` |
| `orchestrator/requirements.txt` | pinned deps (`strands-agents`, `temporalio[strands-agents,pydantic]`, `perplexityai`, `lancedb`, `fastapi`, `mcp`, `pytest`, …) |

Tests live in `orchestrator/tests/`: `test_config.py`, `test_telemetry.py`, `test_perplexity_model.py`, `test_workflow.py`. There is no `conftest.py` or `__init__.py` — run pytest from inside `orchestrator/`. `compare_workflow.py`, `run_worker.py`, and `server.py` do **not** have test suites yet; writing them is tracked in Jira (see below).

**Orchestrator — planned, not yet on disk.** `perplexity_operations.py`, `memory.py`, `mcp_config.py` + `pophive_sync.py` + `scripts/sync-pophive.sh`, `graph_activity.py`, `agent_runtime.py`, replay fixtures under `tests/histories/`, `run_workflow.py` + `orchestrator/README.md`. References to these describe intended structure, not current fact.

**`orchestrator/graph_tool.py` is absent and protected.** Never create or edit it opportunistically. The graph-activity work (`graph_activity.py`) is blocked until a more capable graph tool is designed with the user; `graph_activity.py` remains a durable activity wrapper around that tool's public `graph` name and native schema, not a reimplementation of it. The two currently-failing tests in `app/api/orchestrator/route.test.ts` assert `data-graph-event` handling and belong to that blocked work.

### Code intelligence (CodeGraph)

The workspace is indexed by **CodeGraph** (OhMyOpenCode's code-intelligence graph). `.codegraph` at the repo root is a symlink to `~/.omo/codegraph/projects/v0-clone-blurple-5c4264970e8c4939/`, which holds a SQLite index (`codegraph.db`) created 2026-07-31 and auto-synced by a file watcher (~1s lag). Agents with `codegraph_*` tools should reach for `codegraph_explore` first when asking about TypeScript or Python source — one call returns verbatim line-numbered source plus callers/callees and blast radius, replacing grep/read loops. CodeGraph is developer tooling only: it is unrelated to the product graph work (`orchestrator/graph_tool.py` / `graph_activity.py`), which remains absent and blocked as described above.

### Work tracking

Remaining work is tracked in **Jira** (Atlassian MCP is configured in `.kilo/kilo.json`). `docs/jira-backlog.md` is the migration source: it maps every remaining task, its files, verification gates, acceptance criteria, and dependencies. Canonical requirements remain in `docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md`. A second plan, `2026-07-30-coding-agent-product-reset.md`, renames `components/v0/` to `components/coding-agent/` and `blurple-background.tsx` to `app-background.tsx`; it lives on branch `feature/coding-agent-product-reset` (worktree `.worktrees/coding-agent-product-reset`). **Order is orchestrator work first, product reset second** — do not apply the rename until the orchestrator epic completes, so the `components/v0/` paths above stay current.

### Jira documentation rules (project GWEN)

Site `cato-labs.atlassian.net` (usable directly as `cloudId`), project key **GWEN**, team-managed. Assignee for all issues: accountId `712020:9708152a-7fe5-4de5-a6a5-0003c1678f72`.

**Hierarchy.** Feature → Epic → Story → Task → Sub-task. There is no Initiative type; Feature is the top. Epics are created with `parent` = the Feature key; Stories with `parent` = their Epic key. Tasks are NOT parented to Stories — link each Task to its Story with the **"Polaris work item link"** type (inward = Story, outward = Task; renders as implements / is implemented by). Sub-tasks use `parent` = the Task key. Bugs may implement a Story the same way Tasks do.

**Links.** Every Epic, Story, and Task gets a **Relates** link to its Feature (inward = Feature, outward = child). Real dependencies use **Blocks** (inward = blocker, outward = blocked). Never invent issue keys — discover them with `getVisibleJiraProjects` / JQL search first, and check for existing issues before creating to avoid duplicates.

**Fields — every issue, no exceptions.** `priority` (Highest = release gates, High = critical path, Medium = blocked/downstream), `assignee`, 4–7 specific `labels`, Start date (`customfield_10015`, `YYYY-MM-DD`), and `duedate`. Completed work uses the dates it actually happened. Bugs additionally fill `environment` exhaustively (OS, stack versions, ports, model ids, repro workflow ids). The project has no Sprints, Components, or Fix versions — skip those. Descriptions use `contentFormat: "markdown"`.

**Mandatory description sections.** Features: Capability, Scope, discovery/decision status, standing constraints, DoD. Stories: user-story sentence ("As a … I want … so that …"), Context, Scope (exact file paths), Acceptance criteria, Links. Tasks: `## Objective`, `## Mandatory Tool Usage` (exact commands), `## Implementation Requirements` (files, patterns), `## Documentation References` (real URLs or repo paths — never invented), `## Definition of Done` (checkboxes). Bugs: Objective, Reproduction Steps (numbered, from a live repro — never hypothesized), Expected Behavior, Root Cause Analysis (SDK/source citations with file:line), Fix (candidates evaluated, chosen one justified), Implementation Requirements, Mandatory Tool Usage, Documentation References, Environment, Severity, DoD.

**Process gates.** Product discovery precedes decomposition: a Feature may exist with explicit "DISCOVERY REQUIRED — no children yet" status, and Epics/Stories/Tasks are created only after decisions are recorded on the Feature. Documentation of already-built work is written as an honest engineering record (real paths, real commands, actual dates) with incomplete verification left as open Tasks, not marked done. Status changes go through `getTransitionsForJiraIssue` → `transitionJiraIssue`. Evidence (screenshots, captures) is attached to the issue itself and mirrored under `docs/evidence/<issue-key>/` in-repo. Verification gates cited in DoDs: `npx tsc --noEmit`, `pnpm lint`, scoped vitest (`--exclude '**/.worktrees/**'`), and orchestrator pytest run from inside `orchestrator/`.

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

The `orchestrator/.venv` is already provisioned. The `run_workflow.py` smoke test remains planned (see `docs/jira-backlog.md`).

Python tests, from `orchestrator/`:

```bash
.venv/bin/python -m pytest tests -q                       # all suites
.venv/bin/python -m pytest tests/test_workflow.py -q      # one suite (needs local Temporal via `temporal` CLI on PATH)
```

Vitest is configured (`vitest.config.ts`, aliasing `@` to the repo root) and one frontend suite exists, `app/api/orchestrator/route.test.ts`. `package.json` defines no `test` script, and the config sets no `include`/`exclude`, so an unscoped run collects `.worktrees/` duplicates. Always scope it:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**'
pnpm exec vitest run --exclude '**/.worktrees/**' app/api/orchestrator/route.test.ts   # one suite
```

The two failures in that suite are expected: they assert `data-graph-event` handling that belongs to the blocked graph work. A second worktree lives at `.kilo/worktrees/oasis-streetcar/`, which `--exclude '**/.worktrees/**'` does not cover — add `--exclude '**/.kilo/**'` to avoid collecting its duplicate `route.test.ts`.

## Coding Style & Naming Conventions

TypeScript **strict** (`tsconfig.json`), path alias `@/*`. `next.config.mjs` sets `typescript.ignoreBuildErrors: true`, so `pnpm build` is not a type gate — run `npx tsc --noEmit` for that. Lint is flat-config ESLint (`eslint-config-next` core-web-vitals + typescript, ignoring `.next/`, `orchestrator/.venv/`, `next-env.d.ts`); there is no Prettier config, so match surrounding style. Prefer existing composition patterns in `components/v0/*`. Orchestrator code must stay on Temporal’s official Strands integration (`TemporalAgent` + model factories on the worker); do not pass live `Model` instances into workflow `__init__`.

Python here targets 3.13, uses 4-space indent and `snake_case` modules, and keeps module-level constants in `config.py` rather than inlining timeouts or queue names at call sites. Prefer graceful degradation over hard failure for optional infrastructure — `telemetry.py` is the reference: a missing endpoint or missing import logs and returns an empty plugin list instead of raising.

## Commit & Pull Request Guidelines

Subjects are capitalized, imperative, and unprefixed ("Implement durable orchestrator foundation", "Plan coding agent product reset"); keep that form and commit once per completed task. PRs should note frontend vs orchestrator impact and any env/Temporal process requirements for reviewers.

Protected paths bind regardless of committing: `.env*`, `orchestrator/graph_tool.py`, and the six existing `app/api/**/route.ts` files (orchestrator, orchestrator/end, orchestrator/approval, orchestrator/file, compare, models). Change a route only when a failing compatibility test proves the backend cannot satisfy an existing contract. The former `.opencode/` ledger/guard system is retired; scope and sequencing now live in Jira (`docs/jira-backlog.md` is the migration source).
