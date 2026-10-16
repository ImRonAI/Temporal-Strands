import { afterEach, describe, expect, it, vi } from "vitest"
import { GET } from "./route"

const catalogBody = {
  tools: { session: ["shell", "think"], discoverable: ["graph"] },
  skills: [{ name: "research-brief", description: "Compile a sourced brief" }],
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe("catalog proxy", () => {
  it("proxies to ${ORCHESTRATOR_URL}/catalog and passes the JSON through untouched", async () => {
    vi.stubEnv("ORCHESTRATOR_URL", "http://backend:8787")
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(catalogBody)))
    vi.stubGlobal("fetch", fetcher)
    const response = await GET(new Request("http://localhost/api/catalog"))
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("http://backend:8787/catalog", {
      cache: "no-store",
      signal: expect.any(AbortSignal),
    })
    expect(response.status).toBe(200)
    expect(await response.json()).toEqual(catalogBody)
  })

  it("uses the default orchestrator URL when ORCHESTRATOR_URL is unset", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(catalogBody)))
    vi.stubGlobal("fetch", fetcher)
    await GET(new Request("http://localhost/api/catalog"))
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("http://localhost:8787/catalog", expect.anything())
  })

  it("forwards a session_id search param to the orchestrator", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(catalogBody)))
    vi.stubGlobal("fetch", fetcher)
    await GET(new Request("http://localhost/api/catalog?session_id=session-abc123"))
    expect(fetcher).toHaveBeenCalledExactlyOnceWith(
      "http://localhost:8787/catalog?session_id=session-abc123",
      expect.anything(),
    )
  })

  it("returns 502 with an error body when the orchestrator fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")))
    const response = await GET(new Request("http://localhost/api/catalog"))
    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({ error: "connection refused" })
  })

  it("returns 502 when the orchestrator responds non-ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("down", { status: 503 })))
    const response = await GET(new Request("http://localhost/api/catalog"))
    expect(response.status).toBe(502)
    expect((await response.json()).error).toMatch(/503/)
  })
})
