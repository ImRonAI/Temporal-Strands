"use client"

import { useEffect, useState } from "react"

export type CatalogSkill = { name: string; description: string }
export type Catalog = { tools: { session: string[]; discoverable: string[] }; skills: CatalogSkill[] }
export type UseCatalogResult = {
  catalog: Catalog | null
  status: "idle" | "loading" | "ready" | "error"
  error: string | null
}

// One in-flight request per session scope, shared by every caller. Cleared in
// `finally` so a reopened selector re-fetches after worker startup or changes.
const inflight = new Map<string, Promise<Catalog>>()

function loadCatalog(sessionId?: string): Promise<Catalog> {
  const key = sessionId ?? ""
  let pending = inflight.get(key)
  if (!pending) {
    pending = fetch(`/api/catalog${sessionId ? `?session_id=${sessionId}` : ""}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load catalog (${res.status})`)
        return res.json() as Promise<unknown>
      })
      .then((body) => {
        const catalog = body as Catalog
        if (
          !Array.isArray(catalog?.tools?.session) ||
          !Array.isArray(catalog?.tools?.discoverable) ||
          !Array.isArray(catalog?.skills)
        ) {
          throw new Error("Catalog response is malformed")
        }
        return catalog
      })
      .finally(() => {
        inflight.delete(key)
      })
    inflight.set(key, pending)
  }
  return pending
}

export function useCatalog(sessionId?: string, refreshKey = false): UseCatalogResult {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [status, setStatus] = useState<UseCatalogResult["status"]>("loading")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    loadCatalog(sessionId)
      .then((data) => {
        if (cancelled) return
        setCatalog(data)
        setError(null)
        setStatus("ready")
      })
      .catch((err) => {
        if (cancelled) return
        setError(
          err instanceof Error && err.name === "TimeoutError"
            ? "Catalog request timed out. Reopen the selector to retry."
            : err instanceof Error
              ? err.message
              : "Failed to load catalog",
        )
        setStatus("error")
      })

    return () => {
      cancelled = true
    }
  }, [sessionId, refreshKey])

  return { catalog, status, error }
}
