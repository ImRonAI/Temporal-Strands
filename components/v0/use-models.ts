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

export function useModels(): UseModelsResult {
  const [models, setModels] = useState<PerplexityModel[]>([])
  const [status, setStatus] = useState<UseModelsResult["status"]>("loading")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch("/api/models")
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load models (${res.status})`)
        return res.json() as Promise<{ data: PerplexityModel[] }>
      })
      .then((body) => {
        if (cancelled) return
        setModels(body.data ?? [])
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
