# app/api — Agent Notes

All seven route files here are **PROTECTED**: `orchestrator/route.ts`, `orchestrator/end/route.ts`, `orchestrator/approval/route.ts`, `orchestrator/file/route.ts`, `orchestrator/handoff/route.ts`, `orchestrator/desktop-image/route.ts`, `models/route.ts`. `compare/` holds only `route.test.ts` (multi-pane transport compatibility against `orchestrator/route.ts`) — there is no `compare/route.ts`. Change a route only when a failing compatibility test proves the backend cannot satisfy an existing contract. Read the root `AGENTS.md` first; this file only adds route-local detail.

| Route | Method | Upstream (`orchestrator/server.py`) | Test |
|-------|--------|------------------------------------|------|
| `orchestrator/route.ts` | POST | `POST /sessions` (first turn), `POST /sessions/{id}/turns/stream` | `route.test.ts` (1329 lines) |
| `orchestrator/approval/route.ts` | POST | `POST /sessions/{id}/approve` → `ChatWorkflow.approve` signal | via `route.test.ts` |
| `orchestrator/handoff/route.ts` | GET, POST | `GET /sessions/{id}/desktop-control`, `POST /sessions/{id}/handoff` (`take`/`release`/`give`, zod-validated) | `handoff/route.test.ts` |
| `orchestrator/desktop-image/route.ts` | GET | `GET /sessions/{id}/desktop-images/{artifact_id}` (PNG proxy) | `desktop-image/route.test.ts` |
| `orchestrator/end/route.ts` | POST | `POST /sessions/{id}/end` (`sendBeacon` from `pagehide`, always 204) | — |
| `orchestrator/file/route.ts` | GET | `GET /responses/{response_id}/files/{file_id}/content` | — |
| `models/route.ts` | GET | `GET /health` via `lib/perplexity.ts` `listModels` | `lib/perplexity.test.ts` |

## orchestrator/route.ts (SSE bridge, 1047 lines)

- Reaches the FastAPI bridge via `ORCHESTRATOR_URL` (default `http://localhost:8787`, line 32); sibling routes inline the same default.
- `ORCHESTRATOR_EVENT_SCHEMA` mirrors the backend SSE protocol; keep it in lockstep with `orchestrator/server.py`. Frames are read with the AI SDK's `parseJsonEventStream` (never a hand-rolled reader).
- `POST` (:376) folds topics into UI-message parts: `data-session` (:484); `data-skill-run` (:567, `thinking` + `tool_use.name == "use_skill"`); `use_agent` frames become labelled `reasoning-delta`s (:621); `data-graph-run` (:649, `thinking` + `graph`); `data-agent-run` (:745, `agent_runs` topic, deduped by `(response_id, sequence_number)`); `data-native-tool` (:767/:806 — `sandbox_results`, `mcp_call`, `mcp_list_tools`, `share_file`, `google_search`, `google_maps`); `tool-output-available`/`-error` (:822/:831, `tool_results`); `data-approval` (:853); `data-retry` (:904, a `messageStart` on an already-open topic = activity retry, rotates the text id); `reasoning-*`, `text-*`, `tool-input-*`, `error` (:1027).
- Native tool ids: `sandbox-{call_id}`, `mcp-{id}`, `file-{call_id}`; `share_file` URLs rewrite to `/api/orchestrator/file?path=...`.
- A placeholder `reasoning-start` (`turn-pending`) is written before any orchestrator I/O so `useChat` flips to `streaming` immediately; every real part closes it. Each contiguous reasoning run gets a fresh id so Chain of Thought interleaves correctly with tools.
- Session continuity: `data-session` is emitted on the first turn and reused by `AgentChat`; the turn body forwards `model` as `model_id` plus `reasoning_effort` for per-turn switching ("POST per-turn model switching" compatibility test).
- Per-turn tool/skill allowlist: the turn body also forwards `tools` and `skills` (`string[]`, both defaulting to `[]` when the client omits them) so each turn can restrict which native tools and skills the agent may use ("forwards the request body tools and skills arrays on the turn POST" compatibility test).

## Graph contract (implemented 2026-08-12)

The former `data-graph-event` envelope contract (protocol_version / run_id / node_path / dotted event_type) was stripped by user decision as invented complexity. The implemented contract, asserted by the "POST graph activity streaming" tests in `route.test.ts`: the workflow publishes the graph tool's **raw native `multiagent_*` events** (shapes: `docs/evidence/GWEN-30/event-log.json`) on the thinking topic with `tool_use: {name: "graph", toolUseId}`; `route.ts` folds them into ONE reconciled `data-graph-run` snapshot part per tool call (id `graph-{toolUseId}`, the same accumulation pattern as `data-agent-run`). Frontend consumer: `components/v0/graph-run.ts` → `use-graph-stream.ts` → `graph-canvas.tsx`/`graph-node.tsx`/`graph-artifacts.tsx`. The orchestrator side (publish sanitized native events on the thinking topic during graph execution) is GWEN-38's remaining scope.

## Running the frontend tests

`package.json` has no `test` script and vitest has no include/exclude config, so always scope:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts
```

Expected result: all tests pass (the 2 former expected-failing graph tests were replaced by the implemented native contract, 2026-08-12). `.kilo/worktrees/oasis-streetcar/` holds a duplicate `route.test.ts`, hence the second exclude.
