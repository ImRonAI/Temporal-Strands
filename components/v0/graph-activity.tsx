"use client"

import { GraphArtifacts } from "@/components/v0/graph-artifacts"
import { GraphCanvas } from "@/components/v0/graph-canvas"
import { useGraphStream } from "@/components/v0/use-graph-stream"

/**
 * Formation graph surface for one assistant message (GWEN-29): feeds the
 * message's parts through the graph stream hook and renders a live canvas
 * per run plus completed-run artifacts. Renders nothing when the message
 * carries no `data-graph-run` parts, so non-graph turns are untouched.
 */
export function GraphActivity({
  parts,
}: {
  parts: ReadonlyArray<{ type: string; id?: string; data?: unknown }>
}) {
  const { runs, hasGraph } = useGraphStream(parts)
  if (!hasGraph) return null

  return (
    <div className="space-y-3">
      {runs.map((run) => (
        <div className="space-y-2" key={run.runId}>
          <GraphCanvas run={run} />
          <GraphArtifacts run={run} />
        </div>
      ))}
    </div>
  )
}
