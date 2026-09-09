# Repository Guidelines

Multi-provider agent gateway: a Next.js UI streams durable agent turns from a Python Temporal/Strands orchestrator. Perplexity's Agent API is the primary provider; Google AI Studio connects directly. Any other provider (OpenAI, Anthropic, Mistral, xAI, and so on) is roadmap-only and must not be described as implemented.

> **Read this first.** This file describes the verified current state of the working tree. Where a behavior is policy (the agent model policy below, the provider-declaration rule), treat it as binding.

## Project Structure & Module Organization

Request path: `app/page.tsx` (`useChat` → `/api/orchestrator`) converts FastAPI SSE into AI SDK UI-message parts; the bridge is `orchestrator/server.py` (`POST /sessions`, `/turns/stream`, `/end`, `/compare/stream`, `/approve`, `GET /health`) talking to Temporal workflow `ChatWorkflow` in `workflow.py`. Workers (`run_worker.py`) register provider model factories on task queue `perplexity-orchestrator`.

- `components/ai-elements/` — vendored AI Elements primitives; treat as library code.
- `components/v0/` — app UI that **composes** those primitives (`composer`, `agent-activity`, `model-picker`, `compare-view`).
- `components/ui/` — shadcn/base-ui primitives (`Button`/`Select` use `@base-ui/react`, not Radix `asChild`).
- `lib/perplexity.ts` — model listing helpers only; all inference goes through the orchestrator.
- `orchestrator/` — Python stack (`requirements.txt`, local `.venv`). Agent identity lives in `agent.json`.

**Hard rule:** every AI Elements surface must use native subcomponents, props, and animations (`Conversation` scroll, `MessageResponse`/Streamdown, `PromptInput*` submit/attachments, `ChainOfThought*`). Do not reimplement those in `components/v0/`. `reasoning.tsx` is intentionally not vendored — `ChainOfThought` is the only reasoning UI. Model ids are never hardcoded lists; pickers use `/api/models`. Models switch per turn: the picker stays live mid-conversation, each turn body carries the selected model, and the orchestrator rebuilds the session's agent on the new model without ending the session.

**Provider declaration.** Providers are declared by the worker, never inferred client-side. The readiness lease and `/health` carry provider metadata; pickers group models by the declared owner, not by guessing from the id shape. This closes the gap where a `gemini*` id could mean "Google AI Studio (direct)" or "google/* via Perplexity gateway" and both collapsed into one group.

**Agent model policy.** Delegated/subagent work may run ONLY on: Fable 5.1, GPT 6, DeepSeek V4 Flash, DeepSeek V4 Pro, GLM 5.3, GLM 5.3 Flash, Gemini 3.8 Flash, Grok 4.6, Kimi K3. Haiku, Sonnet, and any other model are prohibited.

### Code intelligence (CodeGraph)

The workspace is indexed by **CodeGraph** (OhMyOpenCode's code-intelligence graph). `.codegraph` at the repo root is a symlink to `~/.omo/codegraph/projects/v0-clone-blurple-5c4264970e8c4939/`, holding a SQLite index auto-synced by a file watcher (~1s lag). Agents with `codegraph_*` tools should reach for `codegraph_explore` first when asking about TypeScript or Python source — one call returns verbatim line-numbered source plus callers/callees and blast radius, replacing grep/read loops. CodeGraph is developer tooling only; it is unrelated to the product graph work.

### Work tracking

Remaining work is tracked in **Jira** (Atlassian MCP is configured in `.kilo/kilo.json`). `docs/jira-backlog.md` is the migration source: it maps every remaining task, its files, verification gates, acceptance criteria, and dependencies. Canonical requirements remain in `docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md`. A second plan, `2026-07-30-coding-agent-product-reset.md`, renames `components/v0/` to `components/coding-agent/` and `blurple-background.tsx` to `app-background.tsx`; it lives on branch `feature/coding-agent-product-reset` (worktree `.worktrees/coding-agent-product-reset`). Sequencing: this plan (`.omo/plans/framework-adherence-reset.md`) precedes the coding-agent product reset.

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
```

The `orchestrator/.venv` is already provisioned.

Python tests, from `orchestrator/`:

```bash
.venv/bin/python -m pytest tests -q                       # all suites
.venv/bin/python -m pytest tests/test_workflow.py -q      # one suite (needs local Temporal via `temporal` CLI on PATH)
```

Vitest is configured (`vitest.config.ts`, aliasing `@` to the repo root) and one frontend suite exists, `app/api/orchestrator/route.test.ts`. `package.json` defines no `test` script, and the config sets no `include`/`exclude`, so an unscoped run collects `.worktrees/` and `.kilo/` duplicates. Always scope it:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts   # one suite
```

## Gates

Verification before claiming work complete: `npx tsc --noEmit`, `pnpm lint`, scoped vitest (`pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'`), and orchestrator pytest run from inside `orchestrator/` (`.venv/bin/python -m pytest tests -q`). Two adherence suites are being added by the active plan and count as planned gates: `orchestrator/tests/test_framework_adherence.py` and `app/framework-adherence.test.ts`.

## Coding Style & Naming Conventions

TypeScript **strict** (`tsconfig.json`), path alias `@/*`. `next.config.mjs` sets `typescript.ignoreBuildErrors: true`, so `pnpm build` is not a type gate — run `npx tsc --noEmit` for that. Lint is flat-config ESLint (`eslint-config-next` core-web-vitals + typescript, ignoring `.next/`, `orchestrator/.venv/`, `next-env.d.ts`); there is no Prettier config, so match surrounding style. Prefer existing composition patterns in `components/v0/*`. Orchestrator code must stay on Temporal’s official Strands integration (`TemporalAgent` + model factories on the worker); do not pass live `Model` instances into workflow `__init__`.

Python here targets 3.13, uses 4-space indent and `snake_case` modules, and keeps module-level constants in `config.py` rather than inlining timeouts or queue names at call sites. Prefer graceful degradation over hard failure for optional infrastructure — `telemetry.py` is the reference: a missing endpoint or missing import logs and returns an empty plugin list instead of raising.

## Commit & Pull Request Guidelines

Subjects are capitalized, imperative, and unprefixed ("Implement durable orchestrator foundation", "Plan coding agent product reset"); a `feat:` prefix appears in history but is not required. Keep that form and commit once per completed task. PRs should note frontend vs orchestrator impact and any env/Temporal process requirements for reviewers.

Protected paths bind regardless of committing: `.env*`, `orchestrator/graph_tool.py`, and the six existing `app/api/**/route.ts` files (orchestrator, orchestrator/end, orchestrator/approval, orchestrator/file, compare, models). Change a route only when a failing compatibility test proves the backend cannot satisfy an existing contract. The former `.opencode/` ledger/guard system is retired; scope and sequencing now live in Jira (`docs/jira-backlog.md` is the migration source).