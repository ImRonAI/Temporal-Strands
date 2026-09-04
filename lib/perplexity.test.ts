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

const FALLBACK_IDS = [
  "preset:fast",
  "preset:low",
  "preset:medium",
  "preset:high",
  "preset:xhigh",
  "preset:wide-research",
  "gemini-3.8-flash",
  "gemini-3.7-flash",
  "gemini-flash-latest",
]

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
  it("returns the readiness catalog in worker order with derived owned_by", async () => {
    fetchMock.mockResolvedValue(
      healthResponse({
        status: "ok",
        models: READINESS_IDS.length,
        model_ids: READINESS_IDS,
        default_model: "preset:high",
      })
    )

    const models = await listModels()

    expect(models.map((m) => m.id)).toEqual(READINESS_IDS)
    const owners = Object.fromEntries(models.map((m) => [m.id, m.owned_by]))
    expect(owners["preset:high"]).toBe("perplexity")
    expect(owners["anthropic/claude-fable-5"]).toBe("anthropic")
    expect(owners["openai/gpt-5.6-sol"]).toBe("openai")
    expect(owners["google/gemini-3.6-flash"]).toBe("google")
    expect(owners["perplexity/kimi-k3"]).toBe("perplexity")
    expect(owners["xai/grok-fresh"]).toBe("xai")
    expect(owners["gemini-3.8-flash"]).toBe("google")
    expect(owners["gemini-flash-latest"]).toBe("google")
    for (const model of models) {
      expect(model.object).toBe("model")
      expect(model.created).toBe(0)
    }
  })

  it("hits ${ORCHESTRATOR_URL}/health with cache: no-store", async () => {
    vi.stubEnv("ORCHESTRATOR_URL", "http://example.test:9999")
    fetchMock.mockResolvedValue(healthResponse({ model_ids: READINESS_IDS }))

    await listModels()

    expect(fetchMock).toHaveBeenCalledWith(
      "http://example.test:9999/health",
      expect.objectContaining({ cache: "no-store" })
    )
  })

  it("falls back to the static catalog when /health is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"))

    const models = await listModels()

    expect(models.map((m) => m.id)).toEqual(FALLBACK_IDS)
  })

  it("falls back to the static catalog when /health returns no models", async () => {
    fetchMock.mockResolvedValue(
      healthResponse({ status: "degraded", models: 0, model_ids: [] })
    )

    const models = await listModels()

    expect(models.map((m) => m.id)).toEqual(FALLBACK_IDS)
  })

  it("falls back on a non-OK /health response", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 502 }))

    const models = await listModels()

    expect(models.map((m) => m.id)).toEqual(FALLBACK_IDS)
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
