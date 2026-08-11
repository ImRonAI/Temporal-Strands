# app/api — Agent Notes

All six route files here are **PROTECTED**: `orchestrator/route.ts`, `orchestrator/end/route.ts`, `orchestrator/approval/route.ts`, `orchestrator/file/route.ts`, `compare/route.ts`, `models/route.ts`. Change a route only when a failing compatibility test proves the backend cannot satisfy an existing contract. Read the root `AGENTS.md` first; this file only adds route-local detail.

## orchestrator/route.ts (SSE bridge, 526 lines)

- Reaches the FastAPI bridge via `ORCHESTRATOR_URL` (default `http://localhost:8787`, line 15).
- Line ~78 mirrors the backend SSE protocol; keep it in lockstep with `orchestrator/server.py`.
- Conversion map (~lines 318-521) translates backend events into AI SDK UI-message parts:
  - native tool ids: `sandbox-{call_id}`, `mcp-{id}`, `file-{call_id}`
  - `share_file` URLs are rewritten to `/api/orchestrator/file?path=...` (~line 354)
  - reasoning deltas handled at ~lines 231-262
- Session continuity: a `data-session` part is emitted and scanned by `app/page.tsx`.

## Graph contract (expected-failing tests)

`app/api/orchestrator/route.test.ts` lines 5-127 contain **two intentionally failing tests** asserting `data-graph-event` emission and per-node graph-progress reconciliation (part ids like `graph-graph-run-1-research-model.delta`). `route.ts` has zero graph handling today — that is correct. Never fix, delete, or satisfy these tests opportunistically; they are the contract source of truth for the formation graph tool built in `/Users/tims-stuff/Desktop/strands-tools` (Jira: GWEN-21 feature; validation GWEN-27/GWEN-30; frontend GWEN-24/26/28/29).

## Running the frontend tests

`package.json` has no `test` script and vitest has no include/exclude config, so always scope:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts
```

Expected result: exactly the 2 graph-contract failures, nothing else. `.kilo/worktrees/oasis-streetcar/` holds a duplicate `route.test.ts`, hence the second exclude.
