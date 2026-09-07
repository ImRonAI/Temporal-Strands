import { describe, expect, it } from "vitest"

import {
  buildGraphRuns,
  createGraphRunSnapshot,
  foldGraphEvent,
  layerMembers,
  nodeToolState,
  toFlowElements,
  type GraphNodeSnapshot,
  type GraphRunSnapshot,
} from "./graph-run"

// Snapshot shape mirrors GraphRunSnapshot shared with
// app/api/orchestrator/route.ts (data-graph-run parts).
const node = (overrides: Partial<GraphNodeSnapshot>): GraphNodeSnapshot => ({
  status: "pending",
  label: "node",
  parentId: null,
  kind: "agent",
  text: "",
  tools: [],
  ...overrides,
})

const snapshot = (overrides: Partial<GraphRunSnapshot>): GraphRunSnapshot => ({
  toolUseId: "graph-tool-1",
  graphId: null,
  status: "running",
  nodes: {},
  edges: [],
  handoffs: [],
  resultText: null,
  ...overrides,
})

const part = (data: GraphRunSnapshot) => ({
  type: "data-graph-run" as const,
  id: `graph-${data.toolUseId}`,
  data,
})

describe("foldGraphEvent", () => {
  it("folds the graph_topology frame into pending nodes with containment and structural edges", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, {
      type: "graph_topology",
      graph_id: "demo",
      nodes: [
        { node_id: "research", label: "research", parent_id: null, node_type: "agent" },
        { node_id: "team", label: "team", parent_id: null, node_type: "swarm" },
        {
          node_id: "team/coder",
          label: "coder",
          parent_id: "team",
          node_type: "agent",
          model: "fake/text",
        },
        {
          node_id: "team/writer",
          label: "writer",
          parent_id: "team",
          node_type: "skill_agent",
          skill: "wf-skill",
        },
      ],
      edges: [{ from: "research", to: "team" }],
    })

    expect(snap.graphId).toBe("demo")
    expect(snap.nodes["research"].status).toBe("pending")
    expect(snap.nodes["team"].kind).toBe("swarm")
    expect(snap.nodes["team/coder"]).toMatchObject({
      label: "coder",
      parentId: "team",
      model: "fake/text",
    })
    expect(snap.nodes["team/writer"]).toMatchObject({
      kind: "skill_agent",
      skill: "wf-skill",
    })
    expect(snap.edges).toEqual([{ from: "research", to: "team" }])
  })

  it("keeps duplicate leaf ids under different parents distinct", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, {
      type: "multiagent_node_start",
      node_id: "team/coder",
      node_type: "agent",
      label: "coder",
      parent_id: "team",
    })
    foldGraphEvent(snap, {
      type: "multiagent_node_start",
      node_id: "pipeline/coder",
      node_type: "agent",
      label: "coder",
      parent_id: "pipeline",
    })
    expect(Object.keys(snap.nodes).sort()).toEqual([
      "pipeline/coder",
      "team/coder",
    ])
    expect(snap.nodes["team/coder"].label).toBe("coder")
    expect(snap.nodes["pipeline/coder"].parentId).toBe("pipeline")
  })

  it("accumulates leaf text deltas and flips the node to streaming", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, {
      type: "multiagent_node_start",
      node_id: "research",
      node_type: "agent",
    })
    foldGraphEvent(snap, {
      type: "multiagent_node_stream",
      node_id: "research",
      event: { data: "first", delta: { text: "first" } },
    })
    foldGraphEvent(snap, {
      type: "multiagent_node_stream",
      node_id: "research",
      event: { data: " second", delta: { text: " second" } },
    })
    expect(snap.nodes["research"]).toMatchObject({
      status: "streaming",
      text: "first second",
    })
  })

  it("reconciles tool actions by toolUseId across start, fragments, and result", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    const stream = (event: unknown) =>
      foldGraphEvent(snap, {
        type: "multiagent_node_stream",
        node_id: "coder",
        event,
      })

    stream({
      event: {
        contentBlockStart: {
          start: { toolUse: { name: "file_write", toolUseId: "t1" } },
        },
      },
    })
    stream({
      event: { contentBlockDelta: { delta: { toolUse: { input: '{"path":' } } } },
    })
    stream({
      type: "tool_use_stream",
      delta: { toolUse: { input: '"a.txt"}' } },
      current_tool_use: {
        toolUseId: "t1",
        name: "file_write",
        input: '{"path":"a.txt"}',
      },
    })
    stream({
      message: {
        role: "user",
        content: [
          {
            toolResult: {
              toolUseId: "t1",
              status: "success",
              content: [{ text: "wrote a.txt" }],
            },
          },
        ],
      },
    })

    const tools = snap.nodes["coder"].tools
    expect(tools).toHaveLength(1)
    expect(tools[0]).toMatchObject({
      id: "t1",
      name: "file_write",
      status: "output-available",
      input: '{"path":"a.txt"}',
      output: "wrote a.txt",
    })
  })

  it("marks failed tool results as output-error", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, {
      type: "multiagent_node_stream",
      node_id: "coder",
      event: {
        type: "tool_result",
        tool_result: {
          toolUseId: "t9",
          status: "error",
          content: [{ text: "boom" }],
        },
      },
    })
    expect(snap.nodes["coder"].tools[0]).toMatchObject({
      id: "t9",
      status: "output-error",
      output: "boom",
    })
  })

  it("maps NodeResult stop statuses (completed/failed/interrupted) accurately", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, {
      type: "multiagent_node_stop",
      node_id: "ok",
      node_result: { __type__: "NodeResult", status: "completed", result: null },
    })
    foldGraphEvent(snap, {
      type: "multiagent_node_stop",
      node_id: "bad",
      node_result: {
        __type__: "NodeResult",
        status: "failed",
        result: { __type__: "Exception", error: "model exploded" },
      },
    })
    foldGraphEvent(snap, {
      type: "multiagent_node_stop",
      node_id: "stopped",
      node_result: { __type__: "NodeResult", status: "interrupted", result: null },
    })
    expect(snap.nodes["ok"].status).toBe("done")
    expect(snap.nodes["bad"].status).toBe("failed")
    expect(snap.nodes["stopped"].status).toBe("cancelled")
  })

  it("uses the NodeResult final message as text when nothing streamed", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, {
      type: "multiagent_node_stop",
      node_id: "quiet",
      node_result: {
        __type__: "NodeResult",
        status: "completed",
        result: {
          __type__: "AgentResult",
          message: {
            role: "assistant",
            content: [{ text: "Final answer." }],
          },
        },
      },
    })
    expect(snap.nodes["quiet"].text).toBe("Final answer.")
  })

  it("maps cancelled terminal frames and cancels still-open nodes", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, { type: "multiagent_node_start", node_id: "a", node_type: "agent" })
    foldGraphEvent(snap, {
      type: "multiagent_node_stop",
      node_id: "a",
      node_result: { __type__: "NodeResult", status: "completed", result: null },
    })
    foldGraphEvent(snap, { type: "multiagent_node_start", node_id: "b", node_type: "agent" })
    foldGraphEvent(snap, {
      status: "cancelled",
      content: [{ text: "Graph run cancelled." }],
      toolUseId: "graph-tool-1",
    })
    expect(snap.status).toBe("cancelled")
    expect(snap.nodes["a"].status).toBe("done")
    expect(snap.nodes["b"].status).toBe("cancelled")
  })

  it("deduplicates identical handoffs", () => {
    const snap = createGraphRunSnapshot("graph-tool-1")
    foldGraphEvent(snap, {
      type: "multiagent_handoff",
      from_node_ids: ["team/coder"],
      to_node_ids: ["team/reviewer"],
    })
    foldGraphEvent(snap, {
      type: "multiagent_handoff",
      from_node_ids: ["team/coder"],
      to_node_ids: ["team/reviewer"],
    })
    expect(snap.handoffs).toHaveLength(1)
  })
})

describe("buildGraphRuns", () => {
  it("derives node list and run status from a snapshot", () => {
    const runs = buildGraphRuns([
      part(
        snapshot({
          nodes: {
            research: node({ status: "streaming", label: "research", text: "first second" }),
            expert: node({ status: "running", label: "expert" }),
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

  it("keeps structural edges and live handoffs distinct, edge state from real lifecycle", () => {
    const runs = buildGraphRuns([
      part(
        snapshot({
          nodes: {
            research: node({ status: "done", label: "research", text: "out" }),
            team: node({ status: "running", label: "team", kind: "swarm" }),
            "team/coder": node({ status: "done", label: "coder", parentId: "team" }),
            "team/reviewer": node({
              status: "streaming",
              label: "reviewer",
              parentId: "team",
            }),
          },
          edges: [{ from: "research", to: "team" }],
          handoffs: [{ from: ["team/coder"], to: ["team/reviewer"] }],
        })
      ),
    ])
    expect(runs[0].edges).toEqual([
      { from: "research", to: "team", status: "done", kind: "structural" },
      {
        from: "team/coder",
        to: "team/reviewer",
        status: "active",
        kind: "handoff",
      },
    ])
  })

  it("carries completion status, graph id, and result text", () => {
    const runs = buildGraphRuns([
      part(
        snapshot({
          status: "done",
          graphId: "demo",
          nodes: { research: node({ status: "done", text: "out" }) },
          resultText: "Graph g executed in 3724ms (completed).",
        })
      ),
    ])
    expect(runs[0].status).toBe("done")
    expect(runs[0].graphId).toBe("demo")
    expect(runs[0].resultText).toContain("3724ms")
  })

  it("surfaces cancelled runs and cancelled/failed node states", () => {
    const runs = buildGraphRuns([
      part(
        snapshot({
          status: "cancelled",
          nodes: {
            a: node({ status: "failed" }),
            b: node({ status: "cancelled" }),
          },
        })
      ),
    ])
    expect(runs[0].status).toBe("cancelled")
    expect(runs[0].nodes.map((n) => n.status)).toEqual(["failed", "cancelled"])
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

describe("toFlowElements nested layout", () => {
  const nestedRun = () =>
    buildGraphRuns([
      part(
        snapshot({
          nodes: {
            research: node({ status: "done", label: "research", text: "out" }),
            team: node({ status: "running", label: "team", kind: "swarm" }),
            "team/coder": node({ status: "done", label: "coder", parentId: "team" }),
            "team/reviewer": node({
              status: "streaming",
              label: "reviewer",
              parentId: "team",
            }),
            pipeline: node({ status: "pending", label: "pipeline", kind: "graph" }),
            "pipeline/coder": node({
              status: "pending",
              label: "coder",
              parentId: "pipeline",
            }),
          },
          edges: [
            { from: "research", to: "team" },
            { from: "team", to: "pipeline" },
            { from: "pipeline/coder", to: "pipeline/coder" },
          ],
          handoffs: [{ from: ["team/coder"], to: ["team/reviewer"] }],
        })
      ),
    ])[0]

  it("renders containers as sized group nodes and members as children with extent parent", () => {
    const { nodes } = toFlowElements(nestedRun())
    const byId = new Map(nodes.map((n) => [n.id, n]))

    const team = byId.get("team")!
    expect(team.data.isGroup).toBe(true)
    expect(team.style!.width).toBeGreaterThan(0)
    expect(team.style!.height).toBeGreaterThan(0)

    const coder = byId.get("team/coder")!
    expect(coder.parentId).toBe("team")
    expect(coder.extent).toBe("parent")
    expect(coder.data.isGroup).toBe(false)

    // Parents precede children (React Flow subflow ordering rule).
    const order = nodes.map((n) => n.id)
    expect(order.indexOf("team")).toBeLessThan(order.indexOf("team/coder"))
    expect(order.indexOf("pipeline")).toBeLessThan(
      order.indexOf("pipeline/coder")
    )
  })

  it("keeps duplicate leaf labels distinct through path-qualified ids", () => {
    const { nodes } = toFlowElements(nestedRun())
    const coders = nodes.filter((n) => n.data.label === "coder")
    expect(coders.map((n) => n.id).sort()).toEqual([
      "pipeline/coder",
      "team/coder",
    ])
    expect(new Set(coders.map((n) => n.parentId))).toEqual(
      new Set(["team", "pipeline"])
    )
  })

  it("maps structural pending edges to temporary and live edges to animated", () => {
    const run = nestedRun()
    const { edges } = toFlowElements(run)
    const byId = new Map(edges.map((e) => [e.id, e]))
    // research is done → its structural edge is animated.
    expect(byId.get("structural:research->team")!.type).toBe("animated")
    // team → pipeline: team is running → active → animated.
    expect(byId.get("structural:team->pipeline")!.type).toBe("animated")
    // Handoff between live members renders animated.
    expect(byId.get("handoff:team/coder->team/reviewer")!.type).toBe("animated")
  })

  // Regression (React Flow error 008): containers are structural-edge
  // endpoints (research->team, team->pipeline), so their flow-node data must
  // request the target/source handles FormationNode renders. If a group's
  // handles flags were false, no Handle would mount and every touching edge
  // would fail with "source/target handle id null".
  it("flags handle sides on container nodes that structural edges touch", () => {
    const { nodes } = toFlowElements(nestedRun())
    const byId = new Map(nodes.map((n) => [n.id, n]))

    // team: incoming from research, outgoing to pipeline.
    expect(byId.get("team")!.data.isGroup).toBe(true)
    expect(byId.get("team")!.data.handles).toEqual({ target: true, source: true })
    // pipeline: sink container — target only.
    expect(byId.get("pipeline")!.data.isGroup).toBe(true)
    expect(byId.get("pipeline")!.data.handles).toEqual({
      target: true,
      source: false,
    })
  })

  it("preserves node status/kind/model/skill/tools on flow node data", () => {
    const run = buildGraphRuns([
      part(
        snapshot({
          nodes: {
            writer: node({
              status: "running",
              label: "writer",
              kind: "skill_agent",
              skill: "wf-skill",
              model: "fake/text",
              tools: [
                {
                  id: "t1",
                  name: "file_write",
                  status: "output-available",
                  input: "{}",
                  output: "ok",
                },
              ],
            }),
          },
        })
      ),
    ])[0]
    const { nodes } = toFlowElements(run)
    expect(nodes[0].data).toMatchObject({
      kind: "skill_agent",
      skill: "wf-skill",
      model: "fake/text",
      status: "running",
    })
    expect(nodes[0].data.tools).toHaveLength(1)
  })
})

describe("layerMembers", () => {
  it("layers scope members over scoped edges only", () => {
    const members = [
      { id: "a" },
      { id: "b" },
      { id: "c" },
    ] as Parameters<typeof layerMembers>[0]
    const edges = [
      { from: "a", to: "b", status: "done", kind: "structural" },
      { from: "outside", to: "c", status: "done", kind: "structural" },
    ] as Parameters<typeof layerMembers>[1]
    const layers = layerMembers(members, edges)
    expect(layers[0].map((m) => m.id)).toEqual(["a", "c"])
    expect(layers[1].map((m) => m.id)).toEqual(["b"])
  })

  it("stacks handoff-only formations in one layer", () => {
    const members = [{ id: "x" }, { id: "y" }] as Parameters<
      typeof layerMembers
    >[0]
    const layers = layerMembers(members, [])
    expect(layers).toHaveLength(1)
    expect(layers[0].map((m) => m.id)).toEqual(["x", "y"])
  })
})

describe("nodeToolState", () => {
  it("maps node lifecycle onto ToolPart states for getStatusBadge", () => {
    expect(nodeToolState("pending")).toBe("input-streaming")
    expect(nodeToolState("running")).toBe("input-available")
    expect(nodeToolState("streaming")).toBe("input-available")
    expect(nodeToolState("done")).toBe("output-available")
    expect(nodeToolState("failed")).toBe("output-error")
    expect(nodeToolState("cancelled")).toBe("output-error")
  })
})
