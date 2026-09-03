// Pinned Google AI Studio model served by the orchestrator worker. The
// /api/models route still proxies this helper so the picker never hardcodes
// a list of its own.
export const DEFAULT_MODEL = "gemini-3.8-flash"

export type Model = {
  id: string
  object: "model"
  created: number
  owned_by: string
}

export type PerplexityModel = Model

// Model catalog served by the orchestrator worker. The orchestrator registers
// GeminiModel factories for gemini-3.8-flash and dynamic alias gemini-flash-latest.
export async function listModels(): Promise<Model[]> {
  return [
    {
      id: DEFAULT_MODEL,
      object: "model",
      created: 0,
      owned_by: "google",
    },
    {
      id: "gemini-flash-latest",
      object: "model",
      created: 0,
      owned_by: "google",
    },
  ]
}

export const listPerplexityModels = listModels
