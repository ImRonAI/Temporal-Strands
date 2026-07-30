# Repository Guidelines

v0-style chat product: Next.js UI streams durable agent turns from a Python Temporal/Strands orchestrator backed by Perplexity’s Agent API.

## Project Structure & Module Organization

Request path: `app/page.tsx` (`useChat` → `/api/orchestrator`) converts FastAPI SSE into AI SDK UI-message parts; the bridge is `orchestrator/server.py` (`POST /sessions`, `/turns/stream`, `/end`, `/compare/stream`, `/approve`, `GET /health`) talking to Temporal workflow `ChatWorkflow` in `workflow.py`. Workers (`run_worker.py`) register one `PerplexityModel` factory per live `GET /v1/models` id on task queue `perplexity-orchestrator`.

- `components/ai-elements/` — vendored AI Elements primitives; treat as library code.
- `components/v0/` — app UI that **composes** those primitives (`composer`, `agent-activity`, `model-picker`, `compare-view`).
- `components/ui/` — shadcn/base-ui primitives (`Button`/`Select` use `@base-ui/react`, not Radix `asChild`).
- `lib/perplexity.ts` — `DEFAULT_MODEL` + unauthenticated model listing only; all inference goes through the orchestrator.
- `orchestrator/` — Python stack (`requirements.txt`, local `.venv`). Agent identity lives in `agent.json`.

**Hard rule:** every AI Elements surface must use native subcomponents, props, and animations (`Conversation` scroll, `MessageResponse`/Streamdown, `PromptInput*` submit/attachments, `ChainOfThought*`). Do not reimplement those in `components/v0/`. `reasoning.tsx` is intentionally not vendored — `ChainOfThought` is the only reasoning UI. Model ids are never hardcoded lists; pickers use `/api/models`. A session’s model is fixed at start; switching models ends the session.

## Build, Test, and Development Commands

Use **pnpm** (lockfile present). Root `.env.local` needs `PERPLEXITY_API_KEY` (loaded by worker and FastAPI).

```bash
pnpm install
cd orchestrator && uv venv .venv && uv pip install -r requirements.txt   # once
pnpm dev:all          # frees ports, then Temporal :7233, worker, API :8787, Next :3000
pnpm dev:clean        # scripts/free-ports.sh (3000, 7233, 8233, 8787 + stale worker/uvicorn)
pnpm build && pnpm start
pnpm lint
cd orchestrator && .venv/bin/python run_workflow.py "prompt" [model_id]  # smoke test, no UI
```

## Coding Style & Naming Conventions

TypeScript **strict** (`tsconfig.json`), path alias `@/*`. Prefer existing composition patterns in `components/v0/*`. Orchestrator code must stay on Temporal’s official Strands integration (`TemporalAgent` + model factories on the worker); do not pass live `Model` instances into workflow `__init__`.

## Commit & Pull Request Guidelines

History is sparse (`Initial commit: v0-clone-blurple`). Prefer short, imperative subjects describing the change. PRs should note frontend vs orchestrator impact and any env/Temporal process requirements for reviewers.
