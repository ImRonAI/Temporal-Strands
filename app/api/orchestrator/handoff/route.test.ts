import { afterEach, describe, expect, it, vi } from "vitest"
import { POST } from "./route"

afterEach(() => vi.unstubAllGlobals())

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
