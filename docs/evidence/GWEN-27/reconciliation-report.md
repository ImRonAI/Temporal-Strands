# GWEN-27 — Graph event shape reconciliation report

Date: 2026-08-11
Inputs:
- **Captured events**: `docs/evidence/GWEN-30/event-log.json` (153 events from a live 2-node formation run of the `graph` tool in `strands-tools/src/strands_graph_tool/graph.py`, captured 2026-08-11)
- **Frontend contract**: `app/api/orchestrator/route.test.ts` lines 5–127 (the two expected-failing `data-graph-event` tests)

## Vitest baseline (mandatory gate)

```
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts
→ Tests  2 failed | 4 passed (6)
```

Exactly the 2 expected `data-graph-event` failures; no new failures. Neither `app/api/orchestrator/route.ts` nor the test file was modified.

## What the contract expects (per test assertions)

The route receives backend SSE lines of shape:

```json
{
  "topic": "thinking",
  "tool_use": { "name": "graph", "toolUseId": "graph-tool-1" },
  "data": { …envelope… }
}
```

and must emit `data-graph-event` UI parts. The envelope (`data`) fields asserted:

| Field | Test 1 (`node.started`) | Test 2 (`model.delta`) |
| --- | --- | --- |
| `protocol_version` | `"1.0"` | `"1.0"` |
| `event_id` | — | `"graph-run-1:{sequence}"` |
| `sequence` | — | monotonically increasing int |
| `run_id` | `"graph-run-1"` | `"graph-run-1"` |
| `node_path` | `[{"id": "research", "kind": "agent"}]` | same |
| `formation_kind` | `"graph"` | `"graph"` |
| `node_kind` | `"agent"` | `"agent"` |
| `event_type` | `"node.started"` (dotted taxonomy) | `"model.delta"` |
| `provisional` | `false` | `true` |
| `payload.event` | raw native event verbatim (`{"type": "multiagent_node_start", "node_id": "research"}`) | `{"event": {"data": "first"}}` |
| `payload.native_nesting` | `["multiagent_node_start"]` | — |

Reconciliation requirement: repeated `model.delta` for one node produce UI parts sharing the stable id `graph-{run_id}-{leaf node id}-{event_type}` (e.g. `graph-graph-run-1-research-model.delta`).

## What the tool actually emits (captured, 153 events)

The `graph` tool async generator yields the **raw native SDK events**, flat, with no envelope:

| Captured shape | Count | Notes |
| --- | --- | --- |
| `{"type": "multiagent_node_start", "node_id", "node_type"}` | 2 | `node_type` is `"agent"` even for the skill_agent node |
| `{"type": "multiagent_node_stream", "node_id", "event": {…raw agent-loop event…}}` | 144 | inner shapes: bare `{"event": …}` (72), model deltas `{"data", "delta", "agent", "event_loop_cycle_id", …}` (62), `start`/`start_event_loop`/`message`/`result`/`init_event_loop` lifecycle (10) |
| `{"type": "multiagent_node_stop", "node_id", "node_result": NodeResult}` | 2 | `NodeResult` is a non-JSON-serializable dataclass |
| `{"type": "multiagent_handoff", "from_node_ids", "to_node_ids"}` | 1 | |
| `{"type": "multiagent_result", "result": GraphResult}` | 1 | non-serializable dataclass |
| `{"status", "content"}` (official tool result) | 3 | create / execute-final / delete |

Event ordering was correct and fully `node_id`-tagged: `node_start(research)` → 74 streams → `node_stop` → `handoff` → `node_start(expert)` → 70 streams → `node_stop` → `multiagent_result` → final result.

## Field-by-field mismatch table

| Contract field | Present in tool output? | Source in captured data | Gap |
| --- | --- | --- | --- |
| `protocol_version` | ❌ | none | must be stamped by a mapping layer |
| `event_id` | ❌ | none | derivable as `{run_id}:{sequence}` |
| `sequence` | ❌ | none (capture script added its own `seq`) | mapping layer must number events per run |
| `run_id` | ❌ | none — tool has `graph_id`, not a per-execution run id | mapping layer must mint a run id per `execute` |
| `node_path` | ⚠️ partial | flat `node_id` on start/stream/stop; handoff has `from_node_ids`/`to_node_ids`; **no nesting path** — a node inside a nested formation reports only its leaf id | mapping layer must maintain the path from topology + start/stop nesting; tool-side: nested formations' inner events are re-tagged with only the inner node id (outer wrapper id available from stream context) |
| `formation_kind` | ⚠️ partial | recoverable from the `topology` passed at `create` | mapping layer must retain topology; tool does not echo it on events |
| `node_kind` | ⚠️ partial | `node_type` on `multiagent_node_start` only — and it reports `"agent"` for skill_agent nodes (the SDK sees the built sub-agent as an Agent) | mapping layer can carry kind from topology; **tool-side gap**: `skill_agent`/`swarm`/`workflow`/`parallel` provenance is not distinguishable from the native event alone |
| `event_type` (dotted: `node.started`, `model.delta`, …) | ❌ | native `type` values are `multiagent_node_start`, `multiagent_node_stream`, etc.; model deltas are only identifiable by inner keys (`data` + `delta`) | mapping layer must classify: `multiagent_node_start`→`node.started`, `node_stream` with `data`→`model.delta`, `node_stop`→`node.completed`, `handoff`→`edge.handoff`, `multiagent_result`→`run.completed` (taxonomy to be finalized in follow-up) |
| `provisional` | ❌ | none | mapping: `true` for deltas/streams, `false` for lifecycle events |
| `payload.event` (verbatim native event) | ⚠️ partial | the native event **is** the tool output, but model-delta events embed non-serializable objects (`Agent`, `UUID`, spans, traces) and `node_stop`/`multiagent_result` carry `NodeResult`/`GraphResult` dataclasses | mapping layer must strip/serialize before crossing the SSE boundary (Temporal activity boundary also requires JSON-safe payloads) |
| `payload.native_nesting` | ❌ | none | mapping layer records the native type chain |
| SSE wrapper (`topic: "thinking"`, `tool_use: {name: "graph", toolUseId}`) | ❌ (out of tool scope) | produced by orchestrator streaming (workflow thinking topic), not the tool | belongs to the durable graph activity / workflow streaming path (GWEN-10 scope, `orchestrator/graph_activity.py`) |

## Conclusion

**No mismatch requires changing the graph tool's yield contract.** The tool's job — stream every native `multiagent_*` event, tagged and ordered — is verified working. The contract's envelope is a **separate mapping layer** that must live orchestrator-side (the planned durable graph activity, `orchestrator/graph_activity.py`, GWEN-10 scope), because it needs state the tool deliberately does not own: run ids, sequence numbers, retained topology for `node_path`/`formation_kind`/`node_kind`, JSON serialization across the activity boundary, and the `thinking`-topic SSE wrapper.

Two genuine tool-side observations (filed as follow-up):

1. **`node_type` fidelity**: `multiagent_node_start` reports `"agent"` for `skill_agent` nodes and would report the executor class for nested formations — the declared topology `type` is lost. The mapping layer can recover it from retained topology, so no tool change is required, but this must be documented in the mapping design.
2. **Nested `node_path`**: native events from inside a nested formation carry only the inner node id. The envelope's `node_path` array is exactly the right representation; the mapping layer must reconstruct nesting from start/stop bracketing plus topology.

## Verification summary

- vitest: 2 failed (expected graph contract) / 4 passed — unchanged
- strands-tools pytest: 12/12 passed
- No protected files modified (`app/api/orchestrator/route.ts`, `route.test.ts`, `orchestrator/graph_tool.py` untouched)
