import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  DEFAULT_MODEL,
  defaultModel,
  listModels,
  listPerplexityModels,
} from "./perplexity"

// Worker readiness order: presets first, then the sorted live catalog, then
// the Gemini factory ids. listModels() must preserve it verbatim.
const READINESS_IDS = [
  "preset:fast",
  "preset:low",
  "preset:medium",
  "preset:high",
  "preset:xhigh",
  "preset:wide-research",
  "anthropic/claude-fable-5",
  "google/gemini-3.6-flash",
  "openai/gpt-5.6-sol",
  "perplexity/kimi-k3",
  "xai/grok-fresh",
  "gemini-3.8-flash",
  "gemini-3.7-flash",
  "gemini-flash-latest",
]

const READINESS_MODELS = READINESS_IDS.map(id => ({
  id, provider: id.includes("/") || id.startsWith("preset:") ? "perplexity-agent-api" : "google-ai-studio", label: id,
}))

function healthResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  })
}

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe("listModels", () => {
  it("preserves every worker-declared model object and provider without inference", async () => {
    const catalog = READINESS_IDS.map((id) => ({
      id, provider: id.includes("/") || id.startsWith("preset:") ? "perplexity-agent-api" : "google-ai-studio",
      label: id,
    }))
    fetchMock.mockResolvedValue(healthResponse({ models: catalog, providers: {
      "perplexity-agent-api": "Perplexity Agent API", "google-ai-studio": "Google AI Studio",
    } }))
    const result = await listModels()
    expect(result.map(model => model.id)).toEqual(READINESS_IDS)
    expect(result.find(model => model.id === "openai/gpt-5.6-sol")?.owned_by).toBe("perplexity-agent-api")
    expect(result.find(model => model.id === "gemini-3.8-flash")?.owned_by).toBe("google-ai-studio")
  })
  it("returns the readiness catalog in worker order with declared owned_by", async () => {
    fetchMock.mockResolvedValue(
      healthResponse({
        status: "ok",
        models: READINESS_MODELS,
        default_model: "preset:high",
      })
    )

    const models = await listModels()

    expect(models.map((m) => m.id)).toEqual(READINESS_IDS)
    const owners = Object.fromEntries(models.map((m) => [m.id, m.owned_by]))
    for (const entry of READINESS_MODELS) expect(owners[entry.id]).toBe(entry.provider)
    for (const model of models) {
      expect(model.object).toBe("model")
      expect(model.created).toBe(0)
    }
  })

  it("hits ${ORCHESTRATOR_URL}/health with cache: no-store", async () => {
    vi.stubEnv("ORCHESTRATOR_URL", "http://example.test:9999")
    fetchMock.mockResolvedValue(healthResponse({ models: READINESS_MODELS }))

    await listModels()

    expect(fetchMock).toHaveBeenCalledWith(
      "http://example.test:9999/health",
      expect.objectContaining({ cache: "no-store" })
    )
  })

  it("reports unavailable catalog instead of inventing models when health is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"))

    await expect(listModels()).rejects.toThrow("Live model catalog unavailable")
  })

  it("reports an empty worker catalog", async () => {
    fetchMock.mockResolvedValue(
      healthResponse({ status: "degraded", models: [] })
    )

    await expect(listModels()).rejects.toThrow("Live model catalog unavailable")
  })

  it("reports a non-OK health response", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 502 }))

    await expect(listModels()).rejects.toThrow("Live model catalog unavailable")
  })

  it("does not infer missing provider declarations from an ID prefix", async () => {
    fetchMock.mockResolvedValue(healthResponse({ models: [{ id: "openai/new-model" }] }))
    await expect(listModels()).rejects.toThrow("Invalid worker model catalog")
  })

  it("does not filter models to the agent-delegation policy or known vendors", async () => {
    const entries = Array.from({ length: 120 }, (_, index) => ({ id: `new-provider/model-${index}`, provider: "perplexity-agent-api" }))
    fetchMock.mockResolvedValue(healthResponse({ models: entries }))
    expect((await listModels()).map(model => model.id)).toEqual(entries.map(model => model.id))
  })

  it("keeps the listPerplexityModels alias", () => {
    expect(listPerplexityModels).toBe(listModels)
  })
})

describe("defaultModel", () => {
  it("returns readiness default_model when present", async () => {
    fetchMock.mockResolvedValue(
      healthResponse({ model_ids: READINESS_IDS, default_model: "preset:fast" })
    )

    await expect(defaultModel()).resolves.toBe("preset:fast")
  })

  it("returns DEFAULT_MODEL when default_model is absent", async () => {
    fetchMock.mockResolvedValue(healthResponse({ model_ids: READINESS_IDS }))

    await expect(defaultModel()).resolves.toBe(DEFAULT_MODEL)
  })

  it("returns DEFAULT_MODEL when /health is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"))

    await expect(defaultModel()).resolves.toBe(DEFAULT_MODEL)
  })
})

describe("DEFAULT_MODEL", () => {
  it("stays preset:high", () => {
    expect(DEFAULT_MODEL).toBe("preset:high")
  })
})
