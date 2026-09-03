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

## Graph contract (implemented 2026-08-12)

The former `data-graph-event` envelope contract (protocol_version / run_id / node_path / dotted event_type) was stripped by user decision as invented complexity. The implemented contract, asserted by the "POST graph activity streaming" tests in `route.test.ts`: the workflow publishes the graph tool's **raw native `multiagent_*` events** (shapes: `docs/evidence/GWEN-30/event-log.json`) on the thinking topic with `tool_use: {name: "graph", toolUseId}`; `route.ts` folds them into ONE reconciled `data-graph-run` snapshot part per tool call (id `graph-{toolUseId}`, the same accumulation pattern as `data-agent-run`). Frontend consumer: `components/v0/graph-run.ts` → `use-graph-stream.ts` → `graph-canvas.tsx`/`graph-node.tsx`/`graph-artifacts.tsx`. The orchestrator side (publish sanitized native events on the thinking topic during graph execution) is GWEN-38's remaining scope.

## Running the frontend tests

`package.json` has no `test` script and vitest has no include/exclude config, so always scope:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts
```

Expected result: all tests pass (the 2 former expected-failing graph tests were replaced by the implemented native contract, 2026-08-12). `.kilo/worktrees/oasis-streetcar/` holds a duplicate `route.test.ts`, hence the second exclude.
