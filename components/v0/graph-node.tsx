"use client"

import { useReducer } from "react"

import { Handle, Position } from "@xyflow/react"

import {
  Node,
  NodeAction,
  NodeContent,
  NodeDescription,
  NodeHeader,
  NodeTitle,
} from "@/components/ai-elements/node"
import {
  Agent,
  AgentContent,
  AgentHeader,
} from "@/components/ai-elements/agent"
import { MessageResponse } from "@/components/ai-elements/message"
import { Shimmer } from "@/components/ai-elements/shimmer"
import {
  Task,
  TaskContent,
  TaskItem,
  TaskTrigger,
} from "@/components/ai-elements/task"
import { getStatusBadge } from "@/components/ai-elements/tool"
import { Toolbar } from "@/components/ai-elements/toolbar"
import {
  nodeToolState,
  type FlowNodeData,
  type GraphNodeStatus,
  type GraphToolAction,
} from "@/components/v0/graph-run"

const KIND_LABELS: Record<string, string> = {
  agent: "Agent",
  skill_agent: "Skill agent",
  swarm: "Swarm",
  graph: "Nested graph",
  workflow: "Workflow",
  parallel: "Parallel",
}

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

/** Inspector visibility state. Kept as a pure reducer so dismissal rules are
 *  unit-testable in the repo's node-environment vitest (no DOM renderer). */
export type InspectionState = {
  hoveredNode: boolean
  hoveredInspector: boolean
  focused: boolean
  pinned: boolean
}

export type InspectionAction =
  | { type: "node-enter" }
  | { type: "node-leave" }
  | { type: "inspector-enter" }
  | { type: "inspector-leave" }
  | { type: "focus" }
  | { type: "blur" }
  | { type: "toggle-pin" }
  | { type: "dismiss" }

export const inspectionInitial: InspectionState = {
  hoveredNode: false,
  hoveredInspector: false,
  focused: false,
  pinned: false,
}

export function inspectionReducer(
  state: InspectionState,
  action: InspectionAction
): InspectionState {
  switch (action.type) {
    case "node-enter":
      return { ...state, hoveredNode: true }
    case "node-leave":
      return { ...state, hoveredNode: false }
    case "inspector-enter":
      return { ...state, hoveredInspector: true }
    case "inspector-leave":
      return { ...state, hoveredInspector: false }
    case "focus":
      return { ...state, focused: true }
    case "blur":
      return { ...state, focused: false }
    case "toggle-pin":
      return { ...state, pinned: !state.pinned }
    case "dismiss":
      // Escape closes fully even while the pointer rests on the node: hover
      // state resets too, and only a genuine mouse re-enter (which fires
      // node-enter again) or re-focus reopens the inspector.
      return inspectionInitial
  }
}

export function isInspecting(state: InspectionState): boolean {
  return (
    state.hoveredNode || state.hoveredInspector || state.focused || state.pinned
  )
}

/** True when the event originates from an interactive descendant (links in
 *  rendered markdown, buttons, form fields) rather than the node surface
 *  itself — those keep their own semantics and must not toggle the
 *  inspector. The NodeToolbar renders through a portal, so its DOM subtree
 *  is outside currentTarget; React still bubbles those events here, and
 *  `!currentTarget.contains(target)` filters them out. */
export function fromInteractiveDescendant(event: {
  target: EventTarget
  currentTarget: EventTarget
}): boolean {
  const target = event.target
  const current = event.currentTarget
  if (!(target instanceof Element) || !(current instanceof Element)) return false
  if (target === current) return false
  if (!current.contains(target)) return true
  return Boolean(
    target.closest("a, button, input, textarea, select, [role='button']")
  )
}

export type FormationNodeData = FlowNodeData

const TOOL_STATE_LABELS: Record<string, string> = {
  "input-streaming": "pending",
  "input-available": "running",
  "output-available": "done",
  "output-error": "error",
}

/** The inspector's per-node action list: the vendored Task composition
 *  (Task > TaskTrigger > TaskContent > TaskItem) as a variant via
 *  composition — the vendored task.tsx is untouched. One Task per
 *  reconciled tool call carrying its status badge, streamed input, and
 *  output. Exported so the node-environment tests can render it directly. */
export function NodeToolActions({ tools }: { tools: GraphToolAction[] }) {
  if (tools.length === 0) return null
  return (
    <div className="space-y-1" data-testid="node-tool-actions">
      {tools.map((tool) => (
        <Task
          className="app-glass-edge rounded-md border bg-white/[0.02] px-2 py-1.5"
          defaultOpen={false}
          key={tool.id}
        >
          <TaskTrigger title={tool.name}>
            <div className="flex w-full cursor-pointer items-center gap-2 text-muted-foreground text-xs transition-colors hover:text-foreground">
              {getStatusBadge(tool.status)}
              <span className="truncate font-medium">{tool.name}</span>
              <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide">
                {TOOL_STATE_LABELS[tool.status] ?? tool.status}
              </span>
            </div>
          </TaskTrigger>
          <TaskContent>
            {tool.input ? (
              <TaskItem>
                <span className="block text-[10px] text-muted-foreground uppercase tracking-wide">
                  Input
                </span>
                <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-white/[0.03] p-1.5 font-mono text-[11px]">
                  {tool.input}
                </pre>
              </TaskItem>
            ) : null}
            {tool.output ? (
              <TaskItem>
                <span className="block text-[10px] text-muted-foreground uppercase tracking-wide">
                  Output
                </span>
                <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-white/[0.03] p-1.5 font-mono text-[11px]">
                  {tool.output}
                </pre>
              </TaskItem>
            ) : null}
            {!tool.input && !tool.output ? (
              <TaskItem>No input streamed yet.</TaskItem>
            ) : null}
          </TaskContent>
        </Task>
      ))}
    </div>
  )
}

function nodeAriaLabel(data: FormationNodeData): string {
  const skill = data.skill ? `, skill ${data.skill}` : ""
  return `${data.label} — ${kindLabel(data.kind)}${skill}, ${data.status}. Activate to inspect output.`
}

/**
 * Custom Canvas node type (GWEN-32, nested topology 2026-09-07). Two
 * renderings from one type:
 *
 * - Container nodes (swarm / nested graph / workflow / parallel — any node
 *   with declared children) render as a React Flow subflow group: a sized
 *   translucent frame whose header carries label, kind, and live status.
 *   Children are separate Canvas nodes with parentId/extent, so containment
 *   is real layout, never a fake edge.
 * - Leaf nodes keep the official workflow Node stack (Node > NodeHeader /
 *   Title / Description / Action + NodeContent) with hover/focus/pin/Escape
 *   inspection: the portalled inspector is the native Agent component whose
 *   AgentContent holds the live streamed markdown (MessageResponse) plus a
 *   per-node action list built from the vendored Task composition —
 *   status/input/output per reconciled tool call.
 *
 * Click/Enter/Space pins the inspector; Escape dismisses it outright —
 * including under a hovering pointer — until a genuine re-enter or
 * re-focus. Events bubbling from interactive descendants never toggle the
 * node. Status uses vendored getStatusBadge everywhere.
 */
export function FormationNode({ data }: { data: FormationNodeData }) {
  const streaming = data.status === "streaming"
  const state = nodeToolState(data.status)
  const excerpt = data.text.length > 480 ? `…${data.text.slice(-480)}` : data.text

  const [inspection, dispatch] = useReducer(inspectionReducer, inspectionInitial)
  const inspecting = isInspecting(inspection)

  // No manual useCallback: the React Compiler (enforced by
  // react-hooks/preserve-manual-memoization) memoizes these automatically.
  const onClick = (event: React.MouseEvent) => {
    if (fromInteractiveDescendant(event)) return
    dispatch({ type: "toggle-pin" })
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      dispatch({ type: "dismiss" })
      return
    }
    // Enter/Space toggle only from the node surface itself; descendant
    // controls (links in markdown, etc.) keep their native key handling.
    if (event.target !== event.currentTarget) return
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      dispatch({ type: "toggle-pin" })
    }
  }

  if (data.isGroup) {
    // Formation container: a subflow frame, not a card. The frame is
    // intentionally transparent so member nodes inside stay interactive;
    // only the compact header row carries the container's own identity.
    // Containers are still edge endpoints (structural edges connect whole
    // formations), so they must render the same left/target and
    // right/source React Flow Handles the leaf Node renders — otherwise
    // every edge touching the container fails with React Flow error 008
    // ("couldn't create edge… handle id null").
    return (
      <div
        aria-label={`${data.label} — ${kindLabel(data.kind)}, ${data.status}. Contains member nodes.`}
        className="app-glass-edge size-full rounded-lg border border-dashed border-blurple-bright/30 bg-white/[0.015]"
        data-node-kind={data.kind}
        data-node-status={data.status}
        role="group"
      >
        {data.handles.target && <Handle position={Position.Left} type="target" />}
        {data.handles.source && <Handle position={Position.Right} type="source" />}
        <div className="flex items-center gap-2 px-3 py-2">
          <span className="font-medium text-sm">{data.label}</span>
          <span className="text-muted-foreground text-xs">
            {kindLabel(data.kind)}
          </span>
          <span className="ml-auto">{getStatusBadge(state)}</span>
        </div>
      </div>
    )
  }

  return (
    <Node
      aria-expanded={inspecting}
      aria-label={nodeAriaLabel(data)}
      className="app-glass cursor-pointer border transition-shadow duration-300 focus-visible:ring-2 focus-visible:ring-ring/60 hover:shadow-[0_0_28px_-6px_oklch(0.62_0.17_250/0.55)]"
      data-inspecting={inspecting || undefined}
      handles={data.handles}
      onBlur={(event) => {
        if (event.target === event.currentTarget) dispatch({ type: "blur" })
      }}
      onClick={onClick}
      onFocus={(event) => {
        if (event.target === event.currentTarget) dispatch({ type: "focus" })
      }}
      onKeyDown={onKeyDown}
      onMouseEnter={() => dispatch({ type: "node-enter" })}
      onMouseLeave={() => dispatch({ type: "node-leave" })}
      role="button"
      tabIndex={0}
    >
      <NodeHeader className="app-glass-edge bg-white/[0.03]">
        <NodeTitle>{data.label}</NodeTitle>
        <NodeDescription>
          {kindLabel(data.kind)}
          {data.skill ? ` · ${data.skill}` : ""}
        </NodeDescription>
        <NodeAction>{getStatusBadge(state)}</NodeAction>
      </NodeHeader>
      <NodeContent>
        {streaming ? (
          <Shimmer as="p" className="text-xs">
            Streaming…
          </Shimmer>
        ) : excerpt ? (
          <div className="max-h-24 overflow-hidden text-xs">
            <MessageResponse>{excerpt}</MessageResponse>
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">No output yet</p>
        )}
      </NodeContent>
      {/* Inspection surface: the node's formation member as the native Agent
          component, opened by hover/focus and pinned by click. The toolbar is
          portalled, so it tracks its own hover to stay open while the pointer
          crosses from node to inspector. Streamed output renders as markdown
          via MessageResponse (model output, not agent instructions), with
          isAnimating following the node's streaming state; the member's real
          tool calls render as the vendored Task composition below it. The
          model badge is omitted when the stream reports no model — the kind
          is not a model. */}
      <Toolbar
        className="app-glass w-80 border p-0"
        isVisible={inspecting}
        onMouseEnter={() => dispatch({ type: "inspector-enter" })}
        onMouseLeave={() => dispatch({ type: "inspector-leave" })}
      >
        <Agent className="rounded-sm border-0 bg-transparent">
          <AgentHeader
            model={data.model}
            name={`${data.label} · ${data.status}`}
          />
          <AgentContent className="max-h-64 overflow-y-auto text-sm">
            {data.text ? (
              <MessageResponse isAnimating={streaming}>
                {data.text}
              </MessageResponse>
            ) : (
              <p className="text-muted-foreground text-xs">
                No output streamed yet.
              </p>
            )}
            <NodeToolActions tools={data.tools} />
          </AgentContent>
        </Agent>
      </Toolbar>
    </Node>
  )
}

// Keep the status union import in the public surface for tests/consumers.
export type { GraphNodeStatus }
