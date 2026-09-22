# Repository Guidelines

Multi-provider agent gateway: a Next.js 16 UI streams durable agent turns from a Python 3.13 Temporal/Strands orchestrator. Perplexity's Agent API is the primary provider; Google AI Studio connects directly. Any other provider (OpenAI, Anthropic, Mistral, xAI, and so on) is roadmap-only and must not be described as implemented.

> **Read this first.** This file describes the verified current state of the working tree. Where a behavior is policy (the agent model policy below, the provider-declaration rule, protected paths), treat it as binding.

**Nested agent notes.** Four scoped files extend this one; read them before touching their areas:

- `orchestrator/AGENTS.md` — Temporal/Strands conventions, providers, Think tool, stream topics, batch intervals, graph tool.
- `orchestrator/desktop/AGENTS.md` — the browser/computer-use acceptance contract (September 10 user requirements). It supersedes older conflicting desktop proposals and covers sibling backend files, routes, and startup scripts. Historical success reports are not verification of the current implementation.
- `components/v0/AGENTS.md` — native AI Elements composition contracts and desktop UI rules.
- `app/api/AGENTS.md` — route-local protocol detail for the SSE bridge.

**Desktop continuation:** `.kilo/plans/2026-09-10-desktop-completion-handoff.md` records the native migration's remaining gaps, completed guard tooling, restored full Perplexity picker, and latest runtime/test evidence. Recheck current state; do not repeat obsolete demos or remove the restored catalog fix.

## Project Structure & Module Organization

Request path: `app/page.tsx` (`AgentChat` → `useChat` → `/api/orchestrator`) converts FastAPI SSE into AI SDK UI-message parts; the bridge is `orchestrator/server.py` (`POST /sessions`, `/sessions/{id}/turns/stream`, `/approve`, `/handoff`, `/end`, `GET /health`, `/desktop-control`, `/desktop-images/{artifact_id}`, `/compare/stream`, response file content) talking to Temporal workflow `ChatWorkflow` in `workflow.py`. Workers (`run_worker.py`) register provider model factories on task queue `perplexity-orchestrator`; desktop browser actions run on a separate worker (`desktop_worker.py`) polling queue `desktop-browser` inside a headed Linux desktop container.

- `components/ai-elements/` — vendored AI Elements primitives; treat as library code. `reasoning.tsx` is intentionally not vendored — `ChainOfThought` is the only reasoning UI.
- `components/v0/` — app UI that **composes** those primitives (`agent-chat`, `composer`, `agent-activity`, `model-picker`, `compare-view`, `computer-use-`*, `graph-`*).
- `components/ui/` — shadcn/base-ui primitives (`Button`/`Select` use `@base-ui/react`, not Radix `asChild`; `components.json` style `base-nova`).
- `lib/perplexity.ts` — model listing helpers only (reads the orchestrator `/health` payload); all inference goes through the orchestrator. Session default is `preset:high`.
- `orchestrator/` — Python stack (`requirements.txt`, local `.venv`). Agent identity and system prompt live in `agent.json`; registered agent definitions in `agents/`.
- `orchestrator/graph_tool.py` — vendored formation graph (agent/skill_agent/swarm/graph/workflow/parallel); exposed via `graph_activity.py` and `activity_as_tool`.
- `orchestrator/strands-tools` — a **tracked symlink** into the venv's installed `strands_tools` package (`.venv/lib/python3.13/site-packages/strands_tools`), which carries the community tools and the ~700-entry Agent Skills catalog (`strands-tools/skills`). Excluded from vitest, ESLint, and tsconfig — it is not app source. Separately, `requirements.txt` installs the graph package (`strands-heterogeneous-graph-tool`) from a sibling checkout at `../../strands-tools` outside the repo — the desktop contract flags this full-host dependency as a known gap.
- `scripts/` — dev orchestration (`free-ports.sh`, `run-desktop.sh`, `worker_supervisor.py`, `start-all.sh`, `gce-startup.sh`) and the desktop guard tooling (`desktop-guard.mjs`, `framework-contracts.mjs`, `check_desktop_python.py`, `validate_desktop_release.py`) with their node/unittest suites.

**Hard rule:** every AI Elements surface must use native subcomponents, props, and animations (`Conversation` scroll, `MessageResponse`/Streamdown, `PromptInput`* submit/attachments, `ChainOfThought`*). Do not reimplement those in `components/v0/`. Model ids are never hardcoded lists; pickers use `/api/models`. Models switch per turn: the picker stays live mid-conversation, each turn body carries the selected model, and the orchestrator rebuilds the session's agent on the new model without ending the session.

**Provider declaration.** Providers are declared by the worker, never inferred client-side. The readiness lease and `/health` carry provider metadata (`config.py`: `PROVIDER_PERPLEXITY_AGENT_API`, `PROVIDER_GOOGLE_AI_STUDIO`); pickers group models by the declared owner, not by guessing from the id shape. This closes the gap where a `gemini`* id could mean "Google AI Studio (direct)" or "google/* via Perplexity gateway" and both collapsed into one group.

**Agent model policy.** Delegated/subagent work may run ONLY on: Fable 5.1, GPT 6, DeepSeek V4 Flash, DeepSeek V4 Pro, GLM 5.3, GLM 5.3 Flash, Gemini 3.8 Flash, Grok 4.6, Kimi K3. Haiku, Sonnet, and any other model are prohibited.

### Code intelligence (CodeGraph)

The workspace is indexed by **CodeGraph** (OhMyOpenCode's code-intelligence graph). `.codegraph` at the repo root is a symlink to `~/.omo/codegraph/projects/v0-clone-blurple-5c4264970e8c4939/`, holding a SQLite index auto-synced by a file watcher (~1s lag). Agents with `codegraph_`* tools should reach for `codegraph_explore` first when asking about TypeScript or Python source — one call returns verbatim line-numbered source plus callers/callees and blast radius, replacing grep/read loops. CodeGraph is developer tooling only; it is unrelated to the product graph work.

### Work tracking

Remaining work is tracked in **Jira** (Atlassian MCP is configured in `.kilo/kilo.json`). `docs/jira-backlog.md` is the migration source: it maps every remaining task, its files, verification gates, acceptance criteria, and dependencies. Canonical requirements remain in `docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md`. A second plan, `2026-07-30-coding-agent-product-reset.md`, renames `components/v0/` to `components/coding-agent/` and `blurple-background.tsx` to `app-background.tsx`; it lives on branch `feature/coding-agent-product-reset` (worktree `.worktrees/coding-agent-product-reset`). Sequencing: the framework-adherence reset (`.omo/plans/framework-adherence-reset.md`) precedes the coding-agent product reset.

### Jira documentation rules (project GWEN)

Site `cato-labs.atlassian.net` (usable directly as `cloudId`), project key **GWEN**, team-managed. Assignee for all issues: accountId `712020:9708152a-7fe5-4de5-a6a5-0003c1678f72`.

**Hierarchy.** Feature → Epic → Story → Task → Sub-task. There is no Initiative type; Feature is the top. Epics are created with `parent` = the Feature key; Stories with `parent` = their Epic key. Tasks are NOT parented to Stories — link each Task to its Story with the **"Polaris work item link"** type (inward = Story, outward = Task; renders as implements / is implemented by). Sub-tasks use `parent` = the Task key. Bugs may implement a Story the same way Tasks do.

**Links.** Every Epic, Story, and Task gets a **Relates** link to its Feature (inward = Feature, outward = child). Real dependencies use **Blocks** (inward = blocker, outward = blocked). Never invent issue keys — discover them with `getVisibleJiraProjects` / JQL search first, and check for existing issues before creating to avoid duplicates.

**Fields — every issue, no exceptions.** `priority` (Highest = release gates, High = critical path, Medium = blocked/downstream), `assignee`, 4–7 specific `labels`, Start date (`customfield_10015`, `YYYY-MM-DD`), and `duedate`. Completed work uses the dates it actually happened. Bugs additionally fill `environment` exhaustively (OS, stack versions, ports, model ids, repro workflow ids). The project has no Sprints, Components, or Fix versions — skip those. Descriptions use `contentFormat: "markdown"`.

**Mandatory description sections.** Features: Capability, Scope, discovery/decision status, standing constraints, DoD. Stories: user-story sentence ("As a … I want … so that …"), Context, Scope (exact file paths), Acceptance criteria, Links. Tasks: `## Objective`, `## Mandatory Tool Usage` (exact commands), `## Implementation Requirements` (files, patterns), `## Documentation References` (real URLs or repo paths — never invented), `## Definition of Done` (checkboxes). Bugs: Objective, Reproduction Steps (numbered, from a live repro — never hypothesized), Expected Behavior, Root Cause Analysis (SDK/source citations with file:line), Fix (candidates evaluated, chosen one justified), Implementation Requirements, Mandatory Tool Usage, Documentation References, Environment, Severity, DoD.

**Process gates.** Product discovery precedes decomposition: a Feature may exist with explicit "DISCOVERY REQUIRED — no children yet" status, and Epics/Stories/Tasks are created only after decisions are recorded on the Feature. Documentation of already-built work is written as an honest engineering record (real paths, real commands, actual dates) with incomplete verification left as open Tasks, not marked done. Status changes go through `getTransitionsForJiraIssue` → `transitionJiraIssue`. Evidence (screenshots, captures) is attached to the issue itself and mirrored under `docs/evidence/<issue-key>/` in-repo (existing: `GWEN-27`, `GWEN-30`, `gwen-6`). Verification gates cited in DoDs: `npx tsc --noEmit`, `pnpm lint`, scoped vitest (`--exclude '**/.worktrees/**' --exclude '**/.kilo/**'`), and orchestrator pytest run from inside `orchestrator/`.

## Build, Test, and Development Commands

Use **pnpm** (lockfile present). Root `.env.local` holds `PERPLEXITY_API_KEY`, `DATACOMMONS_MCP_URL`, `DC_API_KEY`, and `POPHIVE_MCP_URL`; it is a protected path — never read or edit it. Next routes reach the orchestrator through `ORCHESTRATOR_URL`, defaulting to `http://localhost:8787` (`app/api/orchestrator/route.ts:15`).

```bash
pnpm install
cd orchestrator && uv venv .venv && uv pip install -r requirements.txt   # once; .venv is already provisioned (Python 3.13)
pnpm dev:all          # resets this checkout's stack, then Temporal :7233, worker, API :8787, desktop container, Next :3001
pnpm dev:clean        # scripts/free-ports.sh (3001, 7233, 8233, 8787 + stale worker/uvicorn + orphaned gwen-desktop container)
pnpm build:desktop    # build the gwen-desktop:native image (scripts/run-desktop.sh build, Docker context "colima")
pnpm build && pnpm start
pnpm lint
```

Individual services: `pnpm dev:temporal` (dev server, needs the `temporal` CLI on PATH), `pnpm dev:worker`, `pnpm dev:api`, `pnpm dev:desktop`, `pnpm dev:web`. Every `pnpm dev:all` run resets first (`pnpm dev:clean`, then `start-all.sh` resets again before image prepare) — including a `gwen-desktop` container left over from a killed run (`run-desktop.sh` itself never stops or removes containers; only `free-ports.sh` does, and only that exact name in `DESKTOP_DOCKER_CONTEXT`, default `colima`). Do not kill unrelated servers (port 3000 is often another app, e.g. Electron; this checkout's Next is permanently :3001). `pnpm test:dev-scripts` covers both startup scripts with a mocked `docker`.

Desktop guard tooling (see `orchestrator/desktop/AGENTS.md` for scope and limits):

```bash
pnpm check:desktop                                  # all structural findings
pnpm check:contracts <files...>                     # compiler/Python-boundary diagnostics for a source and its callers
pnpm test:desktop-guard                             # node --test fixtures for the guards
pnpm test:python-contracts                          # unittest suite for the Python contract checker
```

Python tests, always from inside `orchestrator/` (no `conftest.py`, no `__init__.py`):

```bash
.venv/bin/python -m pytest tests -q                       # all suites (~38 test modules)
.venv/bin/python -m pytest tests/test_workflow.py -q      # needs local Temporal via `temporal` CLI on PATH
```

Vitest is configured (`vitest.config.ts`, aliasing `@` to the repo root, excluding `orchestrator/.venv/**` and `orchestrator/strands-tools/**`). `package.json` defines no `test` script, and the config still does not exclude `.worktrees/` or `.kilo/worktrees/` (which contain duplicate suites, e.g. `.kilo/worktrees/oasis-streetcar/`), so always scope it:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts   # one suite
```

Skills management (agent-facing catalog, not app runtime): `pnpm skills:list`, `pnpm skills:find`, `pnpm skills:add owner/repo`. 

## Gates

Verification before claiming work complete: `npx tsc --noEmit`, `pnpm lint`, scoped vitest (`pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'`), and orchestrator pytest run from inside `orchestrator/` (`.venv/bin/python -m pytest tests -q`). The adherence suites `orchestrator/tests/test_framework_adherence.py` and `app/framework-adherence.test.ts` exist and count as gates. Desktop work additionally requires the live acceptance sequence in `orchestrator/desktop/AGENTS.md` — mocked tests cannot replace it. Report every failing/timed-out/skipped test and its scope; a known unrelated failure is not a green gate.

## Coding Style & Naming Conventions

TypeScript **strict** (`tsconfig.json`), path alias `@/`*. `next.config.mjs` sets `typescript.ignoreBuildErrors: true`, so `pnpm build` is not a type gate — run `npx tsc --noEmit` for that. It also pins the Turbopack workspace root to this directory (a stray lockfile in `$HOME` otherwise hijacks resolution), allows LAN dev origins, supports `NEXT_DIST_DIR` for concurrent acceptance servers (`.next-desktop-acceptance/`), and disables image optimization. Lint is flat-config ESLint (`eslint.config.mjs`: `eslint-config-next` core-web-vitals + typescript, ignoring `.next/`, `.next-desktop-acceptance/`, `orchestrator/.venv/`, `next-env.d.ts`); there is no Prettier config, so match surrounding style. Prefer existing composition patterns in `components/v0/`*.

Orchestrator code must stay on Temporal's official Strands integration (`TemporalAgent` + model factories on the worker); do not pass live `Model` instances into workflow `__init__`. Stream topics (`events`, `thinking`, `approval`, `handoff`, `tool_results`, `agent_runs`) are a shared contract between `workflow.py`, `server.py`, and the frontend route — renaming one without the others breaks the UI silently.

Python targets 3.13, uses 4-space indent and `snake_case` modules, and keeps module-level constants in `config.py` rather than inlining timeouts or queue names at call sites. Prefer graceful degradation over hard failure for optional infrastructure — `telemetry.py` is the reference: a missing endpoint or missing import logs and returns an empty plugin list instead of raising. Key pinned dependencies (`orchestrator/requirements.txt`): `strands-agents[gemini]`, `temporalio[strands-agents,pydantic]`, `perplexityai`, `fastapi`/`uvicorn`, `mcp`, the graph package from the sibling `strands-tools` checkout, and the Agent Skills catalog pinned to a git commit.

## Commit & Pull Request Guidelines

Subjects are capitalized, imperative, and unprefixed ("Implement durable orchestrator foundation", "Plan coding agent product reset"); a `feat:` prefix appears in history but is not required. Keep that form and commit once per completed task. PRs should note frontend vs orchestrator impact and any env/Temporal process requirements for reviewers.

Protected paths bind regardless of committing: `.env*`, `orchestrator/graph_tool.py`, and the route files under `app/api/**` (`orchestrator/route.ts`, `orchestrator/end`, `orchestrator/approval`, `orchestrator/file`, `orchestrator/handoff`, `orchestrator/desktop-image`, `compare`, `models`). Change a route only when a failing compatibility test proves the backend cannot satisfy an existing contract. The former `.opencode/` ledger/guard system is retired; scope and sequencing now live in Jira (`docs/jira-backlog.md` is the migration source).