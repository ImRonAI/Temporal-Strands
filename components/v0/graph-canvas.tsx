"use client"

import { useMemo } from "react"

import { Canvas } from "@/components/ai-elements/canvas"
import { Connection } from "@/components/ai-elements/connection"
import { Controls } from "@/components/ai-elements/controls"
import { Edge } from "@/components/ai-elements/edge"
import { Panel } from "@/components/ai-elements/panel"
import { FormationNode } from "@/components/v0/graph-node"
import { toFlowElements, type GraphRunState } from "@/components/v0/graph-run"

const nodeTypes = {
  formation: FormationNode,
}

const edgeTypes = {
  animated: Edge.Animated,
  temporary: Edge.Temporary,
}

/**
 * Formation graph canvas (GWEN-34/GWEN-24): official AI Elements Workflow
 * composition — Canvas + custom Node type + Edge.Animated/Temporary +
 * Connection + Controls + Panel. Documented at
 * https://ai-sdk.dev/elements/examples/workflow
 */
export function GraphCanvas({ run }: { run: GraphRunState }) {
  const { nodes, edges } = useMemo(() => toFlowElements(run), [run])
  if (nodes.length === 0) return null

  return (
    <div className="h-[32rem] w-full overflow-hidden rounded-lg border border-white/10 bg-white/[0.02]">
      <Canvas
        className="h-full"
        connectionLineComponent={Connection}
        edges={edges}
        edgeTypes={edgeTypes}
        elementsSelectable
        fitView
        nodes={nodes}
        nodesConnectable={false}
        nodesDraggable={false}
        nodeTypes={nodeTypes}
      >
        <Controls />
        <Panel position="top-left">
          <p className="px-2 py-1 font-medium text-muted-foreground text-xs uppercase tracking-wide">
            Formation · {run.status}
          </p>
        </Panel>
      </Canvas>
    </div>
  )
}
