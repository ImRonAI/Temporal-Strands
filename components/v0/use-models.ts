"use client"

import { useEffect, useState } from "react"

export type PerplexityModel = {
  id: string
  owned_by: string
}

export type UseModelsResult = {
  models: PerplexityModel[]
  status: "idle" | "loading" | "ready" | "error"
  error: string | null
}

// One in-flight request for the whole page, shared by every caller.
//
// The catalog is process-global and immutable for the life of the tab, but the
// hook used to fetch per component instance: CompareView calls it once and
// then renders a ModelPicker per pane, each calling it again -- three
// concurrent identical requests with two panes, five with four. It also
// refired whenever Composer remounted, which app/page.tsx does every time
// `hasConversation` flips.
let catalog: Promise<PerplexityModel[]> | null = null

function loadModels(): Promise<PerplexityModel[]> {
  catalog ??= fetch("/api/models")
    .then((res) => {
      if (!res.ok) throw new Error(`Failed to load models (${res.status})`)
      return res.json() as Promise<{ data: PerplexityModel[] }>
    })
    .then((body) => body.data ?? [])
    .catch((error) => {
      // Clear on failure so a later mount can retry rather than replaying the
      // rejection forever.
      catalog = null
      throw error
    })
  return catalog
}

export function useModels(): UseModelsResult {
  const [models, setModels] = useState<PerplexityModel[]>([])
  const [status, setStatus] = useState<UseModelsResult["status"]>("loading")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    loadModels()
      .then((data) => {
        if (cancelled) return
        setModels(data)
        setStatus("ready")
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : "Failed to load models")
        setStatus("error")
      })

    return () => {
      cancelled = true
    }
  }, [])

  return { models, status, error }
}
