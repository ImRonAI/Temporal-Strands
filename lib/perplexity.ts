// Session default: the Perplexity Agent API "high" dynamic preset, registered
// by the orchestrator worker as model id "preset:high". The /api/models route
// proxies this helper so the picker never hardcodes a list of its own.
export const DEFAULT_MODEL = "preset:high"

export type Model = {
  id: string
  object: "model"
  created: number
  owned_by: string
}

export type PerplexityModel = Model

// The six Perplexity Agent API dynamic presets the worker always registers
// (as "preset:<name>") when PERPLEXITY_API_KEY is present. The live catalog
// (provider/model ids from GET /v1/models) is discovered by the worker at
// startup and is deliberately NOT mirrored here -- it changes continuously
// and must never be hardcoded. This list exists only as the offline fallback
// when the orchestrator /health endpoint is unreachable.
const PERPLEXITY_PRESETS = [
  "fast",
  "low",
  "medium",
  "high",
  "xhigh",
  "wide-research",
] as const

// Gemini ids the worker registers GeminiModel factories for (config.py
// GEMINI_MODEL_IDS): pinned 3.8, the 3.7 fallback, and Google's dynamic alias.
const GEMINI_MODEL_IDS = [
  "gemini-3.8-flash",
  "gemini-3.7-flash",
  "gemini-flash-latest",
] as const

const HEALTH_TIMEOUT_MS = 3_000

function orchestratorUrl(): string {
  return process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"
}

// Provider attribution for a worker model id:
// - "preset:*"          -> perplexity (Agent API dynamic presets)
// - "<provider>/<name>" -> the provider prefix (anthropic, openai, google, ...)
// - "gemini*"           -> google (bare GeminiModel factory ids)
function ownerOf(id: string): string {
  if (id.startsWith("preset:")) return "perplexity"
  const slash = id.indexOf("/")
  if (slash > 0) return id.slice(0, slash)
  if (id.startsWith("gemini")) return "google"
  return "unknown"
}

function toModel(id: string): Model {
  return { id, object: "model", created: 0, owned_by: ownerOf(id) }
}

// Static fallback catalog: the six presets plus the Gemini ids, used only
// when the orchestrator is down. Order matches the worker's readiness order
// (presets first, then Gemini).
function fallbackModels(): Model[] {
  return [
    ...PERPLEXITY_PRESETS.map((preset) => toModel(`preset:${preset}`)),
    ...GEMINI_MODEL_IDS.map(toModel),
  ]
}

type HealthPayload = {
  // Readiness catalog in worker registration order (presets, sorted live
  // catalog, gemini). "models" is a count kept for compatibility; older
  // shapes may have carried the array there, so accept both.
  model_ids?: unknown
  models?: unknown
  default_model?: unknown
}

function modelIdsOf(payload: HealthPayload): string[] {
  for (const value of [payload.model_ids, payload.models]) {
    if (
      Array.isArray(value) &&
      value.length > 0 &&
      value.every((id) => typeof id === "string")
    ) {
      return value
    }
  }
  return []
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
    // Unreachable orchestrator (down stack, timeout, DNS): callers fall back
    // to the static catalog rather than surfacing an error to the picker.
    return null
  }
}

// Live model catalog from the orchestrator's /health readiness record, in the
// exact order the worker wrote it. Falls back to the static preset+Gemini
// list when the orchestrator is unreachable or reports no models.
export async function listModels(): Promise<Model[]> {
  const payload = await fetchHealth()
  const ids = payload ? modelIdsOf(payload) : []
  if (ids.length === 0) return fallbackModels()
  return ids.map(toModel)
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
