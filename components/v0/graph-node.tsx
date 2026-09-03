"use client"

import {
  Node,
  NodeAction,
  NodeContent,
  NodeDescription,
  NodeHeader,
  NodeTitle,
} from "@/components/ai-elements/node"
import { MessageResponse } from "@/components/ai-elements/message"
import { Shimmer } from "@/components/ai-elements/shimmer"
import { getStatusBadge } from "@/components/ai-elements/tool"
import { Toolbar } from "@/components/ai-elements/toolbar"
import { nodeToolState, type GraphNodeStatus } from "@/components/v0/graph-run"

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

export type FormationNodeData = {
  label: string
  kind: string
  status: GraphNodeStatus
  text: string
  handles: { target: boolean; source: boolean }
}

/**
 * Custom Canvas node type (GWEN-32). Composition is the official workflow
 * Node stack (ai-sdk.dev/elements/components/node): Node > NodeHeader /
 * Title / Description / Action + NodeContent, with Toolbar for hover
 * inspection. Status uses vendored getStatusBadge.
 */
export function FormationNode({ data }: { data: FormationNodeData }) {
  const streaming = data.status === "streaming"
  const state = nodeToolState(data.status)
  const excerpt = data.text.length > 480 ? `…${data.text.slice(-480)}` : data.text

  return (
    <Node
      className="border-white/10 bg-white/[0.02] backdrop-blur-sm"
      handles={data.handles}
    >
      <NodeHeader>
        <NodeTitle>{data.label}</NodeTitle>
        <NodeDescription>{kindLabel(data.kind)}</NodeDescription>
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
      <Toolbar>
        <p className="max-w-64 truncate px-1 text-muted-foreground text-xs">
          {kindLabel(data.kind)} · {data.status}
        </p>
      </Toolbar>
    </Node>
  )
}
