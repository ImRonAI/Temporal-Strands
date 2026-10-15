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
- `orchestrator/` — Python stack (`requirements.txt`, local `.venv`). Agent identity and system prompt live in `agent.json` (name `Orchestrator`; tools are described in the prompt, not a `tools` array); per-provider prompt overrides in `agents/perplexity.json` and `agents/gemini.json`. Active MCP servers come only from `orchestrator/mcp.json` (`shell` stdio, `datacommons`, `pophive`).
- `orchestrator/graph_tool.py` — formation graph tool mirroring the official community `strands_tools.graph` contract on the real SDK `GraphBuilder`/`Graph`/`Swarm` primitives; node types agent/skill_agent/swarm/graph (nested formations; workflow/parallel not supported); Temporal-safe async streaming via `stream_async`; exposed via `graph_activity.py` and `activity_as_tool`.
- `orchestrator/strands-tools` — a **tracked symlink** into the venv's installed `strands_tools` package (`.venv/lib/python3.13/site-packages/strands_tools`), which carries the community tools and the ~700-entry Agent Skills catalog (`strands-tools/skills`, 699 on disk). Excluded from vitest, ESLint, and tsconfig — it is not app source. The graph implementation is in-repo (`graph_tool.py`); `requirements.txt` line 3 records that no sibling checkout is needed any more.
- `orchestrator/integrations/` — **staging lanes, not runtime**: `google-tools/`, `fun-tools/`, `mcp/`, `openapi/` each hold their own `.venv`/`venvs/`, manifests, probes, and lane tests. Nothing here is imported or auto-loaded by the worker; promotion means hand-merging a fragment into `orchestrator/mcp.json`. Fragments contain absolute `/Users/tims-stuff/...` paths. Never run pytest recursively over `orchestrator/` (lane tests need lane venvs) — `pytest tests` only.
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

Use **pnpm** (lockfile present). Root `.env.local` holds `PERPLEXITY_API_KEY`, `DATACOMMONS_MCP_URL`, `DC_API_KEY`, and `POPHIVE_MCP_URL`; it is a protected path — never read or edit it. Next routes reach the orchestrator through `ORCHESTRATOR_URL`, defaulting to `http://localhost:8787` (`app/api/orchestrator/route.ts:32`; each sibling route repeats the same default inline).

```bash
pnpm install
cd orchestrator && uv venv .venv && uv pip install -r requirements.txt   # once; .venv is already provisioned (Python 3.13)
pnpm dev:all          # resets this checkout's stack, then Temporal :7233, worker, API :8787, desktop container, Next :3001
pnpm dev:clean        # scripts/free-ports.sh (3001, 7233, 8233, 8787 + stale worker/uvicorn + orphaned gwen-desktop container)
pnpm build:desktop    # build the gwen-desktop:native image (scripts/run-desktop.sh build, Docker context "colima")
pnpm build && pnpm start
pnpm lint
```

Individual services: `pnpm dev:temporal` (dev server, needs the `temporal` CLI on PATH), `pnpm dev:worker`, `pnpm dev:api`, `pnpm dev:desktop`, `pnpm dev:web`. `pnpm dev:all` enters `start-all.sh` once: ensure Docker is available, stop the previous checkout-owned process tree and exact `gwen-desktop` container, clear allowlisted transient state via `reset-dev-state.mjs`, reconcile JS/Python dependencies, prepare the source-hashed desktop image, then launch fresh processes. Temporal is in-memory, so this deliberately discards local test sessions/history. Cleared state is the selected `.next` cache, runtime Python bytecode, legacy browser observations, and desktop ownership/locks/screenshots; source `.models` providers, dependencies, credentials, journals and generated reports are preserved. `dev:all` pins local service endpoints; individual service commands retain environment overrides. Concurrently uses bounded shutdown and a desktop-only teardown through `free-ports.sh`; `run-desktop.sh` never stops or removes containers. Do not kill unrelated servers or nested worktree services (port 3000 is often another app; this checkout's Next is permanently :3001). `pnpm test:dev-scripts` covers cleanup, startup order, cache safety, image preparation, and real subprocess supervisor shutdown.

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

### Binding Strands tools to Temporal (mandatory)

- Deterministic, I/O-free helpers may use `@tool` in workflow context. Installed ecosystem tools that perform I/O or invoke agents must use **thin `@activity.defn` → `activity_as_tool(...)` → worker activity registration**. Async alone does not make an ecosystem tool workflow-safe.
- The activity imports and calls the installed tool's public callable. Do not copy its agent/cycle loop, access private tool internals, add compatibility frameworks, or inject a replacement parent agent into workflow invocation state.
- Bind activities with explicit `start_to_close_timeout` and applicable heartbeat/retry options from `/Users/tims-stuff/Desktop/v0-clone-blurple/orchestrator/config.py`. Register the callable on the worker polling its queue; desktop tools remain on the desktop worker.
- Think is the installed `strands_tools.think` from the pinned `strands-agents-tools` package. Do not patch or rewrite that file. The old activity binding remains only for histories without `think-durable-native-async-v2`.
- Before changing bindings, read Pattern 2 in `/Applications/strands-temporal/references/strands_temporal_agent_guide.md` and the installed SDK source at `/Users/tims-stuff/Desktop/v0-clone-blurple/orchestrator/.venv/lib/python3.13/site-packages/temporalio/contrib/strands/workflow.py`. Verify activity dispatch, installed-tool delegation, selected-model inheritance, and worker registration in tests. See also https://docs.temporal.io/develop/python/integrations/strands-agents.
- Replay is a separate gate: a retained/deprecated patch marker does not make a changed activity-command sequence compatible. Runs that already executed the removed workflow-side Think must finish on their original worker before deploying this binding to their queue, or be explicitly migrated. Never silently reset/end user sessions or claim replay coverage without evidence.

TypeScript **strict** (`tsconfig.json`), path alias `@/`*. `next.config.mjs` sets `typescript.ignoreBuildErrors: true`, so `pnpm build` is not a type gate — run `npx tsc --noEmit` for that. It also pins the Turbopack workspace root to this directory (a stray lockfile in `$HOME` otherwise hijacks resolution), allows LAN dev origins, supports `NEXT_DIST_DIR` for concurrent acceptance servers (`.next-desktop-acceptance/`, `.next-obsidian-inspect/`), and disables image optimization. Lint is flat-config ESLint (`eslint.config.mjs`: `eslint-config-next` core-web-vitals + typescript, ignoring `.next*/`, `**/.worktrees/**`, `**/.kilo/worktrees/**`, `orchestrator/.venv/`, `orchestrator/strands-tools/`, `next-env.d.ts`); there is no Prettier or `.editorconfig`, so match surrounding style. Tailwind v4 is CSS-first (`postcss.config.mjs` only, no `tailwind.config`). There is no `hooks/` directory — React hooks live beside their feature in `components/v0/use-*.ts`. Prefer existing composition patterns in `components/v0/`*.

Orchestrator code must stay on Temporal's official Strands integration (`TemporalAgent` + model factories on the worker); do not pass live `Model` instances into workflow `__init__`. Stream topics (`events`, `thinking`, `approval`, `handoff`, `tool_results`, `agent_runs`) are a shared contract between `workflow.py`, `server.py`, and the frontend route — renaming one without the others breaks the UI silently.

Python targets 3.13, uses 4-space indent and `snake_case` modules, and keeps module-level constants in `config.py` rather than inlining timeouts or queue names at call sites. Prefer graceful degradation over hard failure for optional infrastructure — `telemetry.py` is the reference: a missing endpoint or missing import logs and returns an empty plugin list instead of raising. Key pinned dependencies (`orchestrator/requirements.txt`): `strands-agents[gemini]`, `temporalio[strands-agents,pydantic]`, `perplexityai`, `fastapi`/`uvicorn`, `mcp`, the graph package from the sibling `strands-tools` checkout, and the Agent Skills catalog pinned to a git commit.

## Commit & Pull Request Guidelines

Subjects are capitalized, imperative, and unprefixed ("Implement durable orchestrator foundation", "Plan coding agent product reset"); a `feat:` prefix appears in history but is not required. Keep that form and commit once per completed task. PRs should note frontend vs orchestrator impact and any env/Temporal process requirements for reviewers.

Protected paths bind regardless of committing: `.env*`, `orchestrator/graph_tool.py`, and the seven route files under `app/api/**` (`orchestrator/route.ts`, `orchestrator/end`, `orchestrator/approval`, `orchestrator/file`, `orchestrator/handoff`, `orchestrator/desktop-image`, `models`; `app/api/compare/` holds only a compatibility test — there is no `compare/route.ts`). Change a route only when a failing compatibility test proves the backend cannot satisfy an existing contract. The former `.opencode/` ledger/guard system is retired; scope and sequencing now live in Jira (`docs/jira-backlog.md` is the migration source).

## Where To Look

| Task | Location | Notes |
|------|----------|-------|
| Change turn streaming / SSE → UI parts | `app/api/orchestrator/route.ts` (`POST` :376) ↔ `orchestrator/server.py` (`turn_stream` :416) | Protected; lockstep pair. Part types listed in `app/api/AGENTS.md` |
| Workflow state, signals, updates, queries | `orchestrator/workflow.py` (`ChatWorkflow` :943) | Handler index in `orchestrator/AGENTS.md` |
| Register a model / provider / activity | `orchestrator/run_worker.py` (`assemble_model_factories` :280, `WORKER_ACTIVITIES` :404) + `config.py` | The only provider extension point |
| Perplexity Agent API native tools / catalog | `orchestrator/agent_api_tools.py` (`AGENT_API_TOOLS`, `SANDBOX_TOOL`, `native_tools`, `_validate_tools`, `_validate_skills`), `orchestrator/catalog_workflow.py` (`model_catalog`) | Full documented union on every request: web/image/people/finance search, fetch_url, sandbox, mcp, connector + `skills`; `background`/`store`/`max_steps`/`max_output_tokens` from `run_worker.model_params`. Delegation runs through `use_agent` / graph nodes on registered model factories |
| Local Python execution | `orchestrator/python_repl_activity.py` → `agent_tools.PERMANENT_COMMUNITY_TOOLS` | Thin activity over installed `strands_tools.python_repl` (not the AWS code interpreter); the API `sandbox` is the server-side alternative |
| Timeouts, queues, batch intervals, provider ids | `orchestrator/config.py` | `timedelta(` anywhere else fails the adherence gate |
| Desktop / browser / computer-use | `orchestrator/desktop_worker.py`, `browser_activity.py`, `computer_use_activity.py`, `desktop_observation.py`, `workspace_state.py` | Read `orchestrator/desktop/AGENTS.md` first — binding contract |
| Human takeover / handoff | `server.py` `handoff` :741 → `ChatWorkflow.desktop_control` :1399 → `components/v0/computer-use-preview.tsx` | Order: agent → stopping → human → relinquishing → steering → resuming |
| Chat UI, composer, activity timeline | `components/v0/agent-chat.tsx` (:151), `composer.tsx`, `agent-activity.tsx` (`AgentActivity` :1585) | Feature→file map in `components/v0/AGENTS.md` |
| Model picker / per-turn switching | `components/v0/model-picker.tsx`, `use-models.ts`, `app/api/models/route.ts`, `lib/perplexity.ts` | Only `DEFAULT_MODEL = "preset:high"` may be a literal id |
| Graph formations | `orchestrator/graph_tool.py` (protected) → `graph_activity.py` → `components/v0/graph-run.ts` → `graph-canvas.tsx` | Raw `multiagent_*` events fold into one `data-graph-run` part |
| Skills catalog / `use_skill` / `use_agent` | `orchestrator/skills_config.py`, `use_skill_activity.py`, `use_agent_activity.py`, `subagent_support.py` | Skills are the pinned `strands_agentskills` git commit |
| Dev stack lifecycle | `scripts/free-ports.sh` → `start-all.sh` → `run-desktop.sh` → `wait-for-stack.mjs` | Only `free-ports.sh` ever stops the `gwen-desktop` container |
| Production VM deploy | `scripts/gce-startup.sh`, `validate_desktop_release.py` | systemd units `gwen-*`; web on :3000 there, :3001 locally |
| Product requirements / backlog | `docs/superpowers/plans/*.md`, `docs/jira-backlog.md`, `docs/evidence/<issue>/` | Plans in `.omo/plans/`, `.kilo/plans/` are context, not completion evidence |

## Code Map

Centrality from CodeGraph (LSP daemon was unavailable at generation time). Refs = distinct call sites.

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| `ChatWorkflow` | Temporal workflow | `orchestrator/workflow.py:943` | ~198 / 15 files | Owns a session: turn update, approval/handoff signals, model rebuild, Continue-As-New |
| `TurnInput` | dataclass | `orchestrator/workflow.py:421` | ~54 | Prompt + images/documents/videos per turn |
| `lifespan` | FastAPI lifespan | `orchestrator/server.py:266` | ~243 (tests) | Temporal client + `StrandsPlugin` on the **client** |
| `turn_stream` | endpoint | `orchestrator/server.py:416` | — | Subscribes to topics before starting the update; cancels the consumer, never the update |
| `set_catalog` / `model_catalog` | fn / `@activity.defn` | `orchestrator/catalog_workflow.py:36,47` | — | The worker's provider-declaring model registry; the `<agent_api_models>` prompt block reads it |
| `graph_activity` | `@activity.defn` | `orchestrator/graph_activity.py:328` | ~49 | Streams formation runs onto `thinking` as `{tool_use, data}` |
| `StateConflict` / `ScopeUnavailable` | exceptions | `orchestrator/workspace_state.py:75,79` | ~83 / ~49 | 409 / 404 for desktop ownership transitions |
| `POST` | route handler | `app/api/orchestrator/route.ts:376` | ~220 (tests) | Folds SSE frames into `text-*`, `reasoning-*`, `tool-*`, `data-*` parts |
| `AgentChat` | component | `components/v0/agent-chat.tsx:151` | 6 callers | `useChat` → `/api/orchestrator`; hosts composer, picker, IDE sheet |
| `AgentActivity` | component | `components/v0/agent-activity.tsx:1585` | ~29 | Dispatches `reasoning`, `dynamic-tool`, `data-native-tool`, `data-agent-run`, `data-skill-run`, `data-retry`, computer-use segments |
| `projectIdePreview` | fn | `components/v0/project-ide.ts:348` | ~55 | Projects shell tool history into file tree + terminal + preview URL |
| `foldGraphEvent` | fn | `components/v0/graph-run.ts:288` | ~22 | Reconciles graph frames into React-Flow nodes/edges |
| `parseDataObservation` | fn | `components/v0/data-observation.ts:424` | ~27 | Turns tabular MCP results into chart series |

**Turn flow:** `AgentChat` (`useChat`) → `POST /api/orchestrator` → `POST /sessions` (first turn only, emits `data-session`) → `POST /sessions/{id}/turns/stream` (body carries `model_id`, `reasoning_effort`) → `server.py` subscribes to `events`/`thinking`/`approval`/`handoff`/`tool_results`/`agent_runs` then starts `ChatWorkflow.turn` update → host worker on `perplexity-orchestrator` runs `TemporalAgent`; desktop tools hop to `desktop-browser` → frames flushed as durable signals (200 ms outer, 500 ms Think, 2 s nested/graph/agent_runs) → SSE → `route.ts` → UI parts. Closing the tab never aborts the turn; `pagehide` beacons `/api/orchestrator/end`.

## Anti-Patterns (This Project)

Enforced by `app/framework-adherence.test.ts`, `orchestrator/tests/test_framework_adherence.py`, `test_graph_tool_adherence.py`, and `scripts/desktop-guard.mjs` / `check_desktop_python.py`:

- Frontend: no `new ReadableStream(` / `TextEncoder` / `.getReader(` / `EventSource` — use AI SDK stream helpers; no hardcoded model ids except `lib/perplexity.ts` `DEFAULT_MODEL`; no `asChild=` on `@base-ui/react`; no local `reasoning.tsx`; no `as any` / `@ts-ignore`.
- Orchestrator: no bare `strands.models.Model` subclass (`PerplexityModel` extends `OpenAIResponsesModel`, `perplexity_model.py:245`); no hand-rolled `_format_chunk`; `think_activity` imports installed `strands_tools.think`; hooks never call `workflow.execute_activity` (use `activity_as_hook`); no `._tool_func`; no `except: pass`; no live `Model` in workflow `__init__`; `StrandsPlugin` on Client not Worker.
- Desktop: no `headless=True` / `STRANDS_BROWSER_HEADLESS=1` / `--no-sandbox`; no `Browser`/`LocalChromiumBrowser`/`TemporalAgent`/`TemporalActivityTool` subclasses; no desktop activities on the host worker; no CDP screencast player (`computer-use-live.html`, `Page.startScreencast`) — noVNC in `WebPreviewBody` only; Dockerfile never `COPY . .`; Continue-As-New must carry `session_id`, `messages`, `stream_state`, `resume_prompt`.
- Streaming: never lower batch intervals (200 ms outer hit Temporal's 50 MB / 51,200-event history cap on long runs); never iterate a turn stream to exhaustion in `server.py`; never retry computer-use actions after an ambiguous disconnect.
- Repo hygiene: root-level `*.png`, `compute.json`, `gwen-*.json`, `fair-expanse-*.json`, `--full-page` are local debris/credentials — never read, commit, or reference them. `.playwright-mcp/`, `.playwright-cli/`, `.firecrawl/`, `output/`, `orchestrator/.runtime/` are tool output, not source.
