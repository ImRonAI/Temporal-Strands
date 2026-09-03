import { describe, expect, it } from "vitest"

import {
  buildGraphRuns,
  nodeToolState,
  toFlowElements,
  type GraphRunSnapshot,
} from "./graph-run"

// Snapshot shape mirrors GraphRunSnapshot produced by
// app/api/orchestrator/route.ts (data-graph-run parts).
const snapshot = (overrides: Partial<GraphRunSnapshot>): GraphRunSnapshot => ({
  toolUseId: "graph-tool-1",
  status: "running",
  nodes: {},
  handoffs: [],
  resultText: null,
  ...overrides,
})

const part = (data: GraphRunSnapshot) => ({
  type: "data-graph-run" as const,
  id: `graph-${data.toolUseId}`,
  data,
})

describe("buildGraphRuns", () => {
  it("derives node list and run status from a snapshot", () => {
    const runs = buildGraphRuns([
      part(
        snapshot({
          nodes: {
            research: { status: "streaming", kind: "agent", text: "first second" },
            expert: { status: "running", kind: "agent", text: "" },
          },
        })
      ),
    ])
    expect(runs).toHaveLength(1)
    expect(runs[0].runId).toBe("graph-tool-1")
    expect(runs[0].status).toBe("running")
    expect(runs[0].nodes.map((n) => n.id)).toEqual(["research", "expert"])
    expect(runs[0].nodes[0].text).toBe("first second")
  })

  it("derives edges from handoffs with state following the source node", () => {
    const runs = buildGraphRuns([
      part(
        snapshot({
          nodes: {
            research: { status: "done", kind: "agent", text: "out" },
            expert: { status: "streaming", kind: "agent", text: "" },
            final: { status: "running", kind: "agent", text: "" },
          },
          handoffs: [
            { from: ["research"], to: ["expert"] },
            { from: ["expert"], to: ["final"] },
            // Duplicate handoff must not duplicate the edge.
            { from: ["research"], to: ["expert"] },
          ],
        })
      ),
    ])
    expect(runs[0].edges).toEqual([
      { from: "research", to: "expert", status: "done" },
      { from: "expert", to: "final", status: "active" },
    ])
  })

  it("carries completion status and result text", () => {
    const runs = buildGraphRuns([
      part(
        snapshot({
          status: "done",
          nodes: { research: { status: "done", kind: "agent", text: "out" } },
          resultText: "Graph g executed in 3724ms (completed).",
        })
      ),
    ])
    expect(runs[0].status).toBe("done")
    expect(runs[0].resultText).toContain("3724ms")
  })

  it("ignores non-graph parts and malformed snapshots", () => {
    const runs = buildGraphRuns([
      { type: "text" },
      { type: "data-graph-run", data: {} },
      part(snapshot({})),
    ])
    expect(runs).toHaveLength(1)
  })
})

describe("toFlowElements", () => {
  it("lays out Canvas nodes and maps edge types for AI Elements Workflow", () => {
    const [run] = buildGraphRuns([
      part(
        snapshot({
          nodes: {
            research: { status: "done", kind: "agent", text: "out" },
            expert: { status: "streaming", kind: "swarm", text: "" },
          },
          handoffs: [{ from: ["research"], to: ["expert"] }],
        })
      ),
    ])
    const { nodes, edges } = toFlowElements(run)
    expect(nodes.map((n) => n.id)).toEqual(["research", "expert"])
    expect(nodes.every((n) => n.type === "formation")).toBe(true)
    expect(nodes[0].position.x).toBe(0)
    expect(nodes[1].position.x).toBe(340)
    expect(nodes[0].data.handles).toEqual({ target: false, source: true })
    expect(nodes[1].data.handles).toEqual({ target: true, source: false })
    expect(edges).toEqual([
      {
        id: "research->expert",
        source: "research",
        target: "expert",
        type: "animated",
      },
    ])
  })
})

describe("nodeToolState", () => {
  it("maps node lifecycle onto ToolPart states for getStatusBadge", () => {
    expect(nodeToolState("pending")).toBe("input-streaming")
    expect(nodeToolState("running")).toBe("input-available")
    expect(nodeToolState("streaming")).toBe("input-available")
    expect(nodeToolState("done")).toBe("output-available")
    expect(nodeToolState("failed")).toBe("output-error")
  })
})
