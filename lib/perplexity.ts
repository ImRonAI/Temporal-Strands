const PERPLEXITY_BASE_URL = "https://api.perplexity.ai/v1"

// The pre-fetch placeholder shown before a model picker's live GET /v1/models
// call resolves. A real, currently-listed model id — if Perplexity's catalog
// changes, update this constant.
export const DEFAULT_MODEL = "openai/gpt-5.6-sol"

export type PerplexityModel = {
  id: string
  object: "model"
  created: number
  owned_by: string
}

// GET /v1/models requires a bearer token as of 2026-08-02 — it returns 401
// without one. This runs server-side only (from app/api/models/route.ts), so
// the key never reaches the browser.
//
// This is the only source of model ids anywhere in the app — never hardcode a
// model list.
//
// Nothing else lives in this file any more. `perplexityClient()` (the `openai`
// npm package pointed at Perplexity's baseURL) and DEFAULT_MAX_OUTPUT_TOKENS
// existed only for the deleted /api/chat route. All model calls now go through
// the orchestrator, which uses Perplexity's own SDK directly.
export async function listPerplexityModels(): Promise<PerplexityModel[]> {
  const apiKey = process.env.PERPLEXITY_API_KEY
  if (!apiKey) {
    throw new Error("PERPLEXITY_API_KEY is not set")
  }
  const res = await fetch(`${PERPLEXITY_BASE_URL}/models`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    next: { revalidate: 300 },
  })
  if (!res.ok) {
    throw new Error(`Failed to list models: ${res.status} ${res.statusText}`)
  }
  const body = (await res.json()) as { object: "list"; data: PerplexityModel[] }
  return body.data
}
