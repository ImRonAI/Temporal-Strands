"use client"

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
  AgentInstructions,
} from "@/components/ai-elements/agent"
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
  model?: string
  handles: { target: boolean; source: boolean }
}

/**
 * Custom Canvas node type (GWEN-32). Composition is the official workflow
 * Node stack (ai-sdk.dev/elements/components/node): Node > NodeHeader /
 * Title / Description / Action + NodeContent, with the hover Toolbar wired
 * to the AI Elements Agent component so the formation member behind the node
 * is inspectable in place. Status uses vendored getStatusBadge.
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
      {/* Hover inspection: the node's formation member as the native Agent
          component (Agent > AgentHeader + AgentContent > AgentInstructions),
          rendered by NodeToolbar on hover. */}
      <Toolbar className="w-80 border-white/10 bg-background/95 p-0 backdrop-blur-md">
        <Agent className="rounded-sm border-0">
          <AgentHeader
            model={data.model ?? kindLabel(data.kind)}
            name={`${data.label} · ${data.status}`}
          />
          <AgentContent className="max-h-64 overflow-y-auto">
            <AgentInstructions>
              {data.text || "No output streamed yet."}
            </AgentInstructions>
          </AgentContent>
        </Agent>
      </Toolbar>
    </Node>
  )
}
