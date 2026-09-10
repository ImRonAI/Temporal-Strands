"use client"

import { useEffect, useState } from "react"

export type PerplexityModel = {
  id: string
  owned_by: string
  provider_label?: string
}

export type UseModelsResult = {
  models: PerplexityModel[]
  status: "idle" | "loading" | "ready" | "error"
  error: string | null
}

// One in-flight request for the whole page, shared by every caller.
//
// Share only concurrent loads, not the resolved catalog for the entire tab.
// Reopening a picker can recover after worker startup or catalog changes.
let catalog: Promise<PerplexityModel[]> | null = null

function loadModels(): Promise<PerplexityModel[]> {
  catalog ??= fetch("/api/models", { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error(`Failed to load models (${res.status})`)
      return res.json() as Promise<{ data: PerplexityModel[] }>
    })
    .then((body) => {
      if (!Array.isArray(body.data) || !body.data.length) throw new Error("Live model catalog is empty")
      return body.data
    })
    .finally(() => {
      catalog = null
    })
  return catalog
}

export function useModels(refreshKey = false): UseModelsResult {
  const [models, setModels] = useState<PerplexityModel[]>([])
  const [status, setStatus] = useState<UseModelsResult["status"]>("loading")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    loadModels()
      .then((data) => {
        if (cancelled) return
        setModels(data)
        setError(null)
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
  }, [refreshKey])

  return { models, status, error }
}
