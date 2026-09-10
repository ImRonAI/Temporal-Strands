import { afterEach, describe, expect, it, vi } from "vitest"
import { GET, POST } from "./route"

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe("desktop control status", () => {
  it("proxies authoritative status without caching and forwards cancellation", async () => {
    vi.stubEnv("ORCHESTRATOR_URL", "http://backend:8787")
    const status = { ready: true, mode: "human", epoch: "1789069438950188500", run_id: "run-2" }
    const fetcher = vi.fn().mockResolvedValue(Response.json(status))
    vi.stubGlobal("fetch", fetcher)
    const request = new Request("http://localhost/api/orchestrator/handoff?sessionId=chat-1")
    const response = await GET(request)
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("http://backend:8787/sessions/chat-1/desktop-control", {
      cache: "no-store", redirect: "error", signal: request.signal,
    })
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(await response.json()).toEqual(status)
  })

  it.each(["", "../other", "chat/other", "chat?x=1", "chat\n", "a".repeat(129)])("rejects invalid session ID %j", async sessionId => {
    const fetcher = vi.fn()
    vi.stubGlobal("fetch", fetcher)
    const response = await GET(new Request(`http://localhost/api/orchestrator/handoff?${new URLSearchParams({ sessionId })}`))
    expect(response.status).toBe(400)
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(fetcher).not.toHaveBeenCalled()
  })

  it.each([403, 404, 409, 503])("preserves unavailable status %s without leaking details", async status => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private backend details", { status })))
    const response = await GET(new Request("http://localhost/api/orchestrator/handoff?sessionId=chat-1"))
    expect(response.status).toBe(status)
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(await response.text()).not.toContain("private backend details")
  })

  it("fails closed on transport failures and malformed upstream JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(new Response("not JSON")))
    for (let i = 0; i < 2; i++) {
      const response = await GET(new Request("http://localhost/api/orchestrator/handoff?sessionId=chat-1"))
      expect(response.status).toBe(502)
      expect(response.headers.get("cache-control")).toBe("no-store")
    }
  })
})

describe("browser handoff", () => {
  it("waits for backend acknowledgement before granting control", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetcher)
    const response = await POST(new Request("http://localhost/api/orchestrator/handoff", {
      method: "POST", body: JSON.stringify({ sessionId: "chat-1", action: "take" }),
    }))
    expect(response.status).toBe(204)
    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/sessions/chat-1/handoff"),
      expect.objectContaining({ body: JSON.stringify({ action: "take", message: "" }) }))
  })

  it("rejects relinquishing without instructions", async () => {
    const fetcher = vi.fn()
    vi.stubGlobal("fetch", fetcher)
    const response = await POST(new Request("http://localhost/api/orchestrator/handoff", {
      method: "POST", body: JSON.stringify({ sessionId: "chat-1", action: "give", message: " " }),
    }))
    expect(response.status).toBe(400)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it("preserves a rejected takeover rather than reporting success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not stopped", { status: 409 })))
    const response = await POST(new Request("http://localhost/api/orchestrator/handoff", {
      method: "POST", body: JSON.stringify({ sessionId: "chat-1", action: "take" }),
    }))
    expect(response.status).toBe(409)
  })
})
