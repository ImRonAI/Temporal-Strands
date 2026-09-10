// Session default: the Perplexity Agent API "high" dynamic preset, registered
// by the orchestrator worker as model id "preset:high". The /api/models route
// proxies this helper so the picker never hardcodes a list of its own.
export const DEFAULT_MODEL = "preset:high"

export type Model = {
  id: string
  object: "model"
  created: number
  owned_by: string
  provider_label?: string
}

export type PerplexityModel = Model

const HEALTH_TIMEOUT_MS = 15_000

function orchestratorUrl(): string {
  return process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"
}

type HealthPayload = {
  models?: unknown
  providers?: Record<string, string>
  default_model?: unknown
}

async function fetchHealth(): Promise<HealthPayload | null> {
  try {
    const res = await fetch(`${orchestratorUrl()}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    })
    if (!res.ok) return null
    return (await res.json()) as HealthPayload
  } catch {
    return null
  }
}

// Preserve the live worker catalog and declared provider, never guess ownership
// from model IDs or silently replace unavailable models with a hardcoded list.
export async function listModels(): Promise<Model[]> {
  const payload = await fetchHealth()
  if (!payload || !Array.isArray(payload.models) || !payload.models.length) {
    throw new Error("Live model catalog unavailable; check orchestrator worker readiness")
  }
  return payload.models.map((entry: unknown) => {
    if (!entry || typeof entry !== "object" || !("id" in entry) || !("provider" in entry)
      || typeof entry.id !== "string" || !entry.id || typeof entry.provider !== "string" || !entry.provider) {
      throw new Error("Invalid worker model catalog: expected declared model IDs and providers")
    }
    return { id: entry.id, object: "model", created: 0, owned_by: entry.provider,
      provider_label: payload.providers?.[entry.provider] }
  })
}

// The worker-selected default model from the readiness record, when
// available; otherwise the static DEFAULT_MODEL.
export async function defaultModel(): Promise<string> {
  const payload = await fetchHealth()
  const value = payload?.default_model
  if (typeof value === "string" && value) return value
  return DEFAULT_MODEL
}

export const listPerplexityModels = listModels
