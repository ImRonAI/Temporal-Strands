import type { ToolPart } from "@/components/ai-elements/tool"

// Formation graph stream contract (shared by the route and the canvas).
//
// orchestrator/graph_activity.py publishes, per graph tool call, on the
// thinking topic with `tool_use: {name: "graph", toolUseId}`:
//   1. one initial {type:"graph_topology", graph_id, nodes, edges} frame —
//      path-qualified node_id, label, parent_id (containment, never an
//      edge), node_type (agent|skill_agent|swarm|graph|workflow|parallel),
//      optional skill/model, and the declared structural edges;
//   2. flattened native multiagent_* events with full-path node ids —
//      start/stop/cancel/interrupt lifecycle (stop carries a NodeResult),
//      leaf multiagent_node_stream frames retaining the WHOLE sanitized
//      agent event (text deltas AND tool use / tool results), and
//      path-prefixed multiagent_handoff frames;
//   3. one terminal {status: success|error|cancelled, content} frame.
//
// app/api/orchestrator/route.ts folds those frames through foldGraphEvent
// into ONE reconciled `data-graph-run` snapshot part per tool call (the
// same accumulation pattern as data-agent-run). This module owns the
// snapshot definition and the fold so the route and the frontend can never
// drift apart. Contract tests: app/api/orchestrator/route.test.ts ("POST
// graph nested topology") and components/v0/graph-run.test.ts.

export type GraphNodeStatus =
  | "pending"
  | "running"
  | "streaming"
  | "done"
  | "failed"
  | "cancelled"

/** One reconciled tool action inside a formation node, keyed by toolUseId.
 *  `status` is a ToolPart state so the vendored getStatusBadge renders it. */
export type GraphToolAction = {
  id: string
  name: string
  status: ToolPart["state"]
  input: string
  output: string
}

export type GraphNodeSnapshot = {
  status: GraphNodeStatus
  label: string
  parentId: string | null
  kind: string
  skill?: string
  model?: string
  text: string
  tools: GraphToolAction[]
}

export type GraphRunSnapshot = {
  toolUseId: string
  graphId: string | null
  status: "running" | "done" | "failed" | "cancelled"
  nodes: Record<string, GraphNodeSnapshot>
  /** Declared structural edges (path-qualified), from graph_topology. */
  edges: Array<{ from: string; to: string }>
  /** Observed runtime handoffs (path-qualified), deduplicated. */
  handoffs: Array<{ from: string[]; to: string[] }>
  resultText: string | null
}

export function createGraphRunSnapshot(toolUseId: string): GraphRunSnapshot {
  return {
    toolUseId,
    graphId: null,
    status: "running",
    nodes: {},
    edges: [],
    handoffs: [],
    resultText: null,
  }
}

type Frame = Record<string, unknown>

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined
}

function nodeEntry(
  snap: GraphRunSnapshot,
  id: string,
  frame?: Frame
): GraphNodeSnapshot {
  const slash = id.lastIndexOf("/")
  const node = (snap.nodes[id] ??= {
    status: "pending",
    label: slash >= 0 ? id.slice(slash + 1) : id,
    parentId: slash >= 0 ? id.slice(0, slash) : null,
    kind: "agent",
    text: "",
    tools: [],
  })
  if (frame) {
    const label = asString(frame.label)
    if (label) node.label = label
    if (typeof frame.parent_id === "string") node.parentId = frame.parent_id
    const kind = asString(frame.node_type)
    if (kind) node.kind = kind
    const skill = asString(frame.skill)
    if (skill) node.skill = skill
    const model = asString(frame.model)
    if (model) node.model = model
  }
  return node
}

function toolEntry(
  node: GraphNodeSnapshot,
  id: string,
  name?: string
): GraphToolAction {
  let tool = node.tools.find((t) => t.id === id)
  if (!tool) {
    tool = { id, name: name ?? "tool", status: "input-streaming", input: "", output: "" }
    node.tools.push(tool)
  }
  if (name) tool.name = name
  return tool
}

function contentText(content: unknown): string {
  if (!Array.isArray(content)) return ""
  return content
    .map((block) => {
      if (!block || typeof block !== "object") return ""
      const b = block as { text?: unknown; json?: unknown }
      if (typeof b.text === "string") return b.text
      if (b.json !== undefined) return JSON.stringify(b.json)
      return ""
    })
    .filter(Boolean)
    .join("\n")
}

function foldToolResult(node: GraphNodeSnapshot, result: Frame) {
  const id = asString(result.toolUseId)
  if (!id) return
  const tool = toolEntry(node, id)
  tool.output = contentText(result.content)
  tool.status = result.status === "error" ? "output-error" : "output-available"
}

// Fold one sanitized leaf agent event (the whole inner event of a
// multiagent_node_stream frame) into the node. Wire shapes, per the Strands
// SDK (strands/types/_events.py) and the live capture in
// docs/evidence/GWEN-30/event-log.json:
//   {data, delta}                      TextStreamEvent — THE text carrier
//   {event: {contentBlock*/message*}}  ModelStreamChunkEvent raw chunks —
//                                      toolUse start + input fragments only
//                                      (text deltas here would double-count)
//   {type:"tool_use_stream", current_tool_use}  authoritative accumulated
//                                      tool input; replaces fragments
//   {message: {content:[{toolUse}|{toolResult}...]}}  complete tool call /
//                                      result blocks
//   {type:"tool_result", tool_result}  ToolResultEvent
//   {type:"tool_stream", tool_stream_event}  nested tool sub-events
export function foldLeafEvent(node: GraphNodeSnapshot, inner: unknown) {
  if (!inner || typeof inner !== "object") return
  const ev = inner as Frame

  // Text delta (TextStreamEvent).
  if (typeof ev.data === "string") {
    node.text += ev.data
    if (node.status === "pending" || node.status === "running") {
      node.status = "streaming"
    }
    return
  }

  // Authoritative streamed tool input: current_tool_use carries the full
  // accumulated input each time, so it replaces (never appends).
  if (ev.type === "tool_use_stream") {
    const current = ev.current_tool_use as
      | { toolUseId?: unknown; name?: unknown; input?: unknown }
      | undefined
    const id = asString(current?.toolUseId)
    if (id) {
      const tool = toolEntry(node, id, asString(current?.name))
      if (current?.input !== undefined) {
        tool.input =
          typeof current.input === "string"
            ? current.input
            : JSON.stringify(current.input)
      }
      if (tool.status === "input-streaming" || tool.status === "input-available") {
        tool.status = "input-streaming"
      }
    }
    return
  }

  // Nested tool sub-events: the call is at least running with known input.
  if (ev.type === "tool_stream") {
    const streamEvent = ev.tool_stream_event as
      | { tool_use?: { toolUseId?: unknown; name?: unknown } }
      | undefined
    const id = asString(streamEvent?.tool_use?.toolUseId)
    if (id) {
      const tool = toolEntry(node, id, asString(streamEvent?.tool_use?.name))
      if (tool.status === "input-streaming") tool.status = "input-available"
    }
    return
  }

  // Terminal tool result (ToolResultEvent).
  if (ev.type === "tool_result" && ev.tool_result && typeof ev.tool_result === "object") {
    foldToolResult(node, ev.tool_result as Frame)
    return
  }

  // Complete message: toolUse blocks finalize inputs, toolResult blocks
  // finalize outputs. Assistant text blocks are skipped — the streamed text
  // already accumulated through TextStreamEvent deltas.
  if (ev.message && typeof ev.message === "object") {
    const content = (ev.message as { content?: unknown }).content
    if (!Array.isArray(content)) return
    for (const block of content) {
      if (!block || typeof block !== "object") continue
      const b = block as { toolUse?: Frame; toolResult?: Frame }
      if (b.toolUse && typeof b.toolUse === "object") {
        const id = asString(b.toolUse.toolUseId)
        if (id) {
          const tool = toolEntry(node, id, asString(b.toolUse.name))
          if (b.toolUse.input !== undefined) {
            tool.input =
              typeof b.toolUse.input === "string"
                ? b.toolUse.input
                : JSON.stringify(b.toolUse.input)
          }
          if (tool.status === "input-streaming") tool.status = "input-available"
        }
      }
      if (b.toolResult && typeof b.toolResult === "object") {
        foldToolResult(node, b.toolResult)
      }
    }
    return
  }

  // Raw model chunks (ModelStreamChunkEvent): tool call start + raw input
  // fragments. Fragments append to the currently-open (input-streaming)
  // call; a later tool_use_stream / message toolUse replaces them with the
  // authoritative value. Text deltas in raw chunks are intentionally NOT
  // accumulated (TextStreamEvent already carried them).
  if (ev.event && typeof ev.event === "object") {
    const chunk = ev.event as Frame
    const start = (chunk.contentBlockStart as { start?: { toolUse?: Frame } } | undefined)
      ?.start?.toolUse
    if (start && typeof start === "object") {
      const id = asString(start.toolUseId)
      if (id) toolEntry(node, id, asString(start.name))
      return
    }
    const delta = (chunk.contentBlockDelta as { delta?: Frame } | undefined)?.delta
    const toolUseDelta = delta?.toolUse as { input?: unknown } | undefined
    if (toolUseDelta && typeof toolUseDelta.input === "string") {
      const open = [...node.tools].reverse().find((t) => t.status === "input-streaming")
      if (open) open.input += toolUseDelta.input
    }
  }
}

function nodeResultStatus(status: unknown): GraphNodeStatus {
  switch (status) {
    case "failed":
      return "failed"
    case "interrupted":
      return "cancelled"
    default:
      return "done"
  }
}

function nodeResultText(nodeResult: Frame): string {
  const result = nodeResult.result as Frame | undefined
  if (!result || typeof result !== "object") return ""
  const message = result.message as { content?: unknown } | undefined
  if (!message || typeof message !== "object") return ""
  return contentText(message.content)
}

/** Fold one backend graph frame into the cumulative snapshot (mutates). */
export function foldGraphEvent(snap: GraphRunSnapshot, data: Frame) {
  const type = asString(data.type) ?? ""
  const nodeId = asString(data.node_id)

  if (type === "graph_topology") {
    snap.graphId = asString(data.graph_id) ?? snap.graphId
    const declared = Array.isArray(data.nodes) ? data.nodes : []
    for (const raw of declared) {
      if (!raw || typeof raw !== "object") continue
      const frame = raw as Frame
      const id = asString(frame.node_id)
      if (id) nodeEntry(snap, id, frame)
    }
    const declaredEdges = Array.isArray(data.edges) ? data.edges : []
    for (const raw of declaredEdges) {
      if (!raw || typeof raw !== "object") continue
      const edge = raw as { from?: unknown; to?: unknown }
      const from = asString(edge.from)
      const to = asString(edge.to)
      if (!from || !to) continue
      if (!snap.edges.some((e) => e.from === from && e.to === to)) {
        snap.edges.push({ from, to })
      }
    }
    return
  }

  if (type === "multiagent_node_start" && nodeId) {
    nodeEntry(snap, nodeId, data).status = "running"
    return
  }

  if (type === "multiagent_node_stream" && nodeId) {
    foldLeafEvent(nodeEntry(snap, nodeId, data), data.event)
    return
  }

  if (type === "multiagent_node_stop" && nodeId) {
    const node = nodeEntry(snap, nodeId, data)
    const nodeResult = data.node_result as Frame | undefined
    if (nodeResult && typeof nodeResult === "object") {
      node.status = nodeResultStatus(nodeResult.status)
      if (!node.text) node.text = nodeResultText(nodeResult)
    } else {
      node.status = "done"
    }
    return
  }

  if ((type === "multiagent_node_cancel" || type === "multiagent_node_interrupt") && nodeId) {
    nodeEntry(snap, nodeId, data).status = "cancelled"
    return
  }

  if (type === "multiagent_handoff") {
    const from = Array.isArray(data.from_node_ids)
      ? (data.from_node_ids as unknown[]).filter((n): n is string => typeof n === "string")
      : []
    const to = Array.isArray(data.to_node_ids)
      ? (data.to_node_ids as unknown[]).filter((n): n is string => typeof n === "string")
      : []
    const duplicate = snap.handoffs.some(
      (h) =>
        h.from.length === from.length &&
        h.to.length === to.length &&
        h.from.every((f, i) => f === from[i]) &&
        h.to.every((t, i) => t === to[i])
    )
    if (!duplicate) snap.handoffs.push({ from, to })
    return
  }

  if (type === "multiagent_result") return

  // The tool's terminal yield: {status: success|error|cancelled, content}.
  if (typeof data.status === "string" && Array.isArray(data.content)) {
    snap.status =
      data.status === "success"
        ? "done"
        : data.status === "cancelled"
          ? "cancelled"
          : "failed"
    snap.resultText = contentText(data.content) || null
    if (snap.status === "cancelled") {
      for (const node of Object.values(snap.nodes)) {
        if (node.status !== "done" && node.status !== "failed") {
          node.status = "cancelled"
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Frontend render-state derivation.

export type GraphNodeState = {
  id: string
  label: string
  parentId: string | null
  kind: string
  status: GraphNodeStatus
  text: string
  skill?: string
  model?: string
  tools: GraphToolAction[]
}

export type GraphEdgeState = {
  from: string
  to: string
  status: "pending" | "active" | "done"
  kind: "structural" | "handoff"
}

export type GraphRunState = {
  runId: string
  graphId: string | null
  status: "running" | "done" | "failed" | "cancelled"
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
// "cancelled" is terminal-not-success, so it shares the error badge; the
// exact word still shows in the node label and inspector header.
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
    case "cancelled":
      return "output-error"
  }
}

function edgeStatus(source: GraphNodeStatus | undefined): GraphEdgeState["status"] {
  if (source === "done" || source === "failed" || source === "cancelled") return "done"
  if (source === "running" || source === "streaming") return "active"
  return "pending"
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

    const nodes: GraphNodeState[] = Object.entries(snap.nodes ?? {}).map(
      ([id, n]) => ({
        id,
        label: n.label ?? id,
        parentId: n.parentId ?? null,
        kind: n.kind,
        status: n.status,
        text: n.text,
        skill: n.skill,
        model: n.model,
        tools: n.tools ?? [],
      })
    )
    const nodeStatus = new Map(nodes.map((n) => [n.id, n.status]))

    // Structural edges first (declared topology), then live handoffs that
    // the topology did not already declare. Handoffs are real observed
    // flow, never source-state inferences.
    const edges: GraphEdgeState[] = []
    const hasEdge = (from: string, to: string) =>
      edges.some((e) => e.from === from && e.to === to)
    for (const edge of snap.edges ?? []) {
      if (hasEdge(edge.from, edge.to)) continue
      edges.push({
        from: edge.from,
        to: edge.to,
        status: edgeStatus(nodeStatus.get(edge.from)),
        kind: "structural",
      })
    }
    for (const handoff of snap.handoffs ?? []) {
      for (const from of handoff.from) {
        for (const to of handoff.to) {
          if (hasEdge(from, to)) continue
          const target = nodeStatus.get(to)
          edges.push({
            from,
            to,
            status:
              target === "done" || target === "failed" || target === "cancelled"
                ? "done"
                : "active",
            kind: "handoff",
          })
        }
      }
    }

    runs.push({
      runId: snap.toolUseId,
      graphId: snap.graphId ?? null,
      status: snap.status,
      nodes,
      edges,
      resultText: snap.resultText,
    })
  }
  return runs
}

// ---------------------------------------------------------------------------
// Nested layout: containers (swarm/graph/workflow/parallel) become React
// Flow subflow parents; children position relative to the parent with
// extent "parent". Leaf columns are topological layers over the edges whose
// endpoints share the same scope.

const LEAF_W = 384 // vendored Node card width (w-sm)
const LEAF_H = 176
const GAP_X = 96
const GAP_Y = 48
const PAD = 24
const HEADER_H = 72

/** Topological layers among the members of one scope. Only edges whose two
 *  endpoints are both members count; handoff-only formations (swarm /
 *  parallel) have none and stack in a single layer. */
export function layerMembers(
  members: GraphNodeState[],
  edges: GraphEdgeState[]
): GraphNodeState[][] {
  const ids = new Set(members.map((m) => m.id))
  const scoped = edges.filter((e) => ids.has(e.from) && ids.has(e.to))
  const layerOf = new Map<string, number>()

  const resolve = (id: string, seen: Set<string>): number => {
    const cached = layerOf.get(id)
    if (cached !== undefined) return cached
    if (seen.has(id)) return 0
    seen.add(id)
    const preds = scoped.filter((e) => e.to === id)
    const layer = preds.length
      ? Math.max(...preds.map((e) => resolve(e.from, seen))) + 1
      : 0
    layerOf.set(id, layer)
    return layer
  }

  const layers: GraphNodeState[][] = []
  for (const member of members) {
    const layer = resolve(member.id, new Set())
    ;(layers[layer] ??= []).push(member)
  }
  return layers.filter(Boolean)
}

export type FlowNodeData = {
  pathId: string
  label: string
  kind: string
  status: GraphNodeStatus
  text: string
  model?: string
  skill?: string
  tools: GraphToolAction[]
  isGroup: boolean
  handles: { target: boolean; source: boolean }
}

export type FlowNode = {
  id: string
  type: "formation"
  position: { x: number; y: number }
  parentId?: string
  extent?: "parent"
  style?: { width: number; height: number }
  data: FlowNodeData
}

export type FlowEdge = {
  id: string
  source: string
  target: string
  type: "animated" | "temporary"
}

/** Map a run onto AI Elements Canvas nodes/edges with nested containment. */
export function toFlowElements(run: GraphRunState): {
  nodes: FlowNode[]
  edges: FlowEdge[]
} {
  const byParent = new Map<string | null, GraphNodeState[]>()
  for (const node of run.nodes) {
    const list = byParent.get(node.parentId) ?? []
    list.push(node)
    byParent.set(node.parentId, list)
  }

  const size = new Map<string, { w: number; h: number }>()
  const rel = new Map<string, { x: number; y: number }>()

  // Bottom-up: place a scope's members in layered columns (relative to the
  // scope origin) and report the scope's bounding box.
  const placeScope = (members: GraphNodeState[]): { w: number; h: number } => {
    for (const member of members) measure(member)
    const layers = layerMembers(members, run.edges)
    let x = 0
    let maxH = 0
    for (const layer of layers) {
      const colW = Math.max(...layer.map((m) => size.get(m.id)!.w))
      let y = 0
      for (const member of layer) {
        rel.set(member.id, { x, y })
        y += size.get(member.id)!.h + GAP_Y
      }
      maxH = Math.max(maxH, y - GAP_Y)
      x += colW + GAP_X
    }
    return { w: Math.max(x - GAP_X, 0), h: Math.max(maxH, 0) }
  }

  const measure = (node: GraphNodeState): void => {
    if (size.has(node.id)) return
    const children = byParent.get(node.id) ?? []
    if (children.length === 0) {
      size.set(node.id, { w: LEAF_W, h: LEAF_H })
      return
    }
    const box = placeScope(children)
    // Shift children inside the container's padding + header.
    for (const child of children) {
      const p = rel.get(child.id)!
      rel.set(child.id, { x: p.x + PAD, y: p.y + HEADER_H })
    }
    size.set(node.id, { w: box.w + PAD * 2, h: box.h + HEADER_H + PAD })
  }

  const roots = byParent.get(null) ?? []
  placeScope(roots)

  const incoming = new Set(run.edges.map((e) => e.to))
  const outgoing = new Set(run.edges.map((e) => e.from))
  const isolated = run.edges.length === 0

  // Parents must precede children in the array (React Flow subflow rule).
  const nodes: FlowNode[] = []
  const emit = (node: GraphNodeState, parentId: string | null) => {
    const children = byParent.get(node.id) ?? []
    const isGroup = children.length > 0
    const dims = size.get(node.id)!
    nodes.push({
      id: node.id,
      type: "formation",
      position: rel.get(node.id) ?? { x: 0, y: 0 },
      ...(parentId ? { parentId, extent: "parent" as const } : {}),
      ...(isGroup ? { style: { width: dims.w, height: dims.h } } : {}),
      data: {
        pathId: node.id,
        label: node.label,
        kind: node.kind,
        status: node.status,
        text: node.text,
        model: node.model,
        skill: node.skill,
        tools: node.tools,
        isGroup,
        handles: {
          target: incoming.has(node.id),
          source: isolated || outgoing.has(node.id),
        },
      },
    })
    for (const child of children) emit(child, node.id)
  }
  for (const root of roots) emit(root, null)

  const edges: FlowEdge[] = run.edges.map((edge) => ({
    id: `${edge.kind}:${edge.from}->${edge.to}`,
    source: edge.from,
    target: edge.to,
    type: edge.status === "pending" ? "temporary" : "animated",
  }))
  return { nodes, edges }
}
