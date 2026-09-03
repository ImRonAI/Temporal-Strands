"use client"

import { useMemo } from "react"

import { buildGraphRuns, type GraphRunState } from "@/components/v0/graph-run"

export type UseGraphStreamResult = {
  runs: GraphRunState[]
  hasGraph: boolean
}

/**
 * Normalized formation state from a message's UI parts.
 *
 * Pure data layer (convention: components/v0/use-models.ts): consumes the
 * reconciled `data-graph-run` snapshot parts the orchestrator route emits
 * (contract: app/api/orchestrator/route.test.ts) and derives per-run
 * nodes/edges/status via buildGraphRuns. No rendering here — the canvas
 * (graph-canvas.tsx) and node cards (graph-node.tsx) consume it.
 */
export function useGraphStream(
  parts: ReadonlyArray<{ type: string; id?: string; data?: unknown }>
): UseGraphStreamResult {
  return useMemo(() => {
    const runs = buildGraphRuns(parts)
    return { runs, hasGraph: runs.length > 0 }
  }, [parts])
}
