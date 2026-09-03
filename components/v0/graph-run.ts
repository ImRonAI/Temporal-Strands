import type { ToolPart } from "@/components/ai-elements/tool"

// Formation graph stream contract (frontend side).
//
// app/api/orchestrator/route.ts folds the graph tool's raw native
// multiagent_* events (published on the thinking topic with the graph
// tool_use) into ONE reconciled `data-graph-run` snapshot part per tool
// call — the same accumulation pattern as data-agent-run. This module just
// types that snapshot and derives what the canvas needs: node list, edges
// from handoffs, and the ToolPart state mapping for getStatusBadge.
// Contract: app/api/orchestrator/route.test.ts ("POST graph activity
// streaming"); native shapes: docs/evidence/GWEN-30/event-log.json.

export type GraphNodeStatus = "pending" | "running" | "streaming" | "done" | "failed"

// Mirrors GraphRunSnapshot in app/api/orchestrator/route.ts.
export type GraphRunSnapshot = {
  toolUseId: string
  status: "running" | "done" | "failed"
  nodes: Record<string, { status: GraphNodeStatus; kind: string; text: string }>
  handoffs: Array<{ from: string[]; to: string[] }>
  resultText: string | null
}

export type GraphNodeState = {
  id: string
  kind: string
  status: GraphNodeStatus
  text: string
}

export type GraphEdgeState = {
  from: string
  to: string
  status: "pending" | "active" | "done"
}

export type GraphRunState = {
  runId: string
  status: "running" | "done" | "failed"
  nodes: GraphNodeState[]
  edges: GraphEdgeState[]
  resultText: string | null
}

type GraphRunPart = {
  type: "data-graph-run"
  id?: string
  data: GraphRunSnapshot
}

export function isGraphRunPart(part: { type: string }): part is GraphRunPart {
  return part.type === "data-graph-run"
}

// Node lifecycle mapped onto ToolPart["state"] so the vendored
// getStatusBadge (components/ai-elements/tool.tsx:67) renders it natively.
export function nodeToolState(status: GraphNodeStatus): ToolPart["state"] {
  switch (status) {
    case "pending":
      return "input-streaming"
    case "running":
    case "streaming":
      return "input-available"
    case "done":
      return "output-available"
    case "failed":
      return "output-error"
  }
}

/** Derive render state from the route's reconciled snapshot parts. */
export function buildGraphRuns(
  parts: ReadonlyArray<{ type: string; id?: string; data?: unknown }>
): GraphRunState[] {
  const runs: GraphRunState[] = []
  for (const part of parts) {
    if (!isGraphRunPart(part)) continue
    const snap = part.data
    if (!snap || typeof snap.toolUseId !== "string") continue

    const nodes = Object.entries(snap.nodes ?? {}).map(([id, n]) => ({
      id,
      kind: n.kind,
      status: n.status,
      text: n.text,
    }))
    const nodeStatus = new Map(nodes.map((n) => [n.id, n.status]))

    // Edges come from observed handoffs (the tool reports actual execution
    // flow); edge state follows the source node's lifecycle.
    const edges: GraphEdgeState[] = []
    for (const handoff of snap.handoffs ?? []) {
      for (const from of handoff.from) {
        for (const to of handoff.to) {
          if (edges.some((e) => e.from === from && e.to === to)) continue
          const source = nodeStatus.get(from)
          edges.push({
            from,
            to,
            status:
              source === "done"
                ? "done"
                : source === "running" || source === "streaming"
                  ? "active"
                  : "pending",
          })
        }
      }
    }

    runs.push({
      runId: snap.toolUseId,
      status: snap.status,
      nodes,
      edges,
      resultText: snap.resultText,
    })
  }
  return runs
}

const COL = 340
const ROW = 200

/** Topological layers for Canvas positions: no-incoming nodes at x=0. */
export function layerNodes(run: GraphRunState): GraphNodeState[][] {
  const layerOf = new Map<string, number>()
  const incoming = (id: string) => run.edges.filter((e) => e.to === id)

  const resolve = (id: string, seen: Set<string>): number => {
    const cached = layerOf.get(id)
    if (cached !== undefined) return cached
    if (seen.has(id)) return 0
    seen.add(id)
    const preds = incoming(id)
    const layer = preds.length
      ? Math.max(...preds.map((e) => resolve(e.from, seen))) + 1
      : 0
    layerOf.set(id, layer)
    return layer
  }

  const layers: GraphNodeState[][] = []
  for (const node of run.nodes) {
    const layer = resolve(node.id, new Set())
    ;(layers[layer] ??= []).push(node)
  }
  return layers.filter(Boolean)
}

export type FlowNode = {
  id: string
  type: "formation"
  position: { x: number; y: number }
  data: {
    label: string
    kind: string
    status: GraphNodeStatus
    text: string
    handles: { target: boolean; source: boolean }
  }
}

export type FlowEdge = {
  id: string
  source: string
  target: string
  type: "animated" | "temporary"
}

/** Map a run onto AI Elements Canvas nodes/edges (demo-workflow shape). */
export function toFlowElements(run: GraphRunState): {
  nodes: FlowNode[]
  edges: FlowEdge[]
} {
  const incoming = new Set(run.edges.map((e) => e.to))
  const outgoing = new Set(run.edges.map((e) => e.from))
  const isolated = run.edges.length === 0
  const nodes: FlowNode[] = []
  layerNodes(run).forEach((layer, x) => {
    layer.forEach((node, y) => {
      nodes.push({
        id: node.id,
        type: "formation",
        position: { x: x * COL, y: y * ROW },
        data: {
          label: node.id,
          kind: node.kind,
          status: node.status,
          text: node.text,
          handles: {
            target: incoming.has(node.id),
            source: isolated || outgoing.has(node.id),
          },
        },
      })
    })
  })
  const edges: FlowEdge[] = run.edges.map((edge) => ({
    id: `${edge.from}->${edge.to}`,
    source: edge.from,
    target: edge.to,
    type: edge.status === "pending" ? "temporary" : "animated",
  }))
  return { nodes, edges }
}
