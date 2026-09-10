import { afterEach, describe, expect, it, vi } from "vitest"
import { GET } from "./route"

const sessionId = "chat-c8a5830d3a78e208"
const artifactId = "879a4f76-2f47-4039-b5ca-30c8bba286bf"
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64")
const maxBytes = 10 * 1024 * 1024

function request(params: Record<string, string> = { session_id: sessionId, artifact_id: artifactId }) {
  return new Request(`http://localhost/api/orchestrator/desktop-image?${new URLSearchParams(params)}`)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe("desktop image proxy", () => {
  it("proxies PNG bytes under the durable session with no caching and the request abort signal", async () => {
    vi.stubEnv("ORCHESTRATOR_URL", "http://backend:8787")
    const fetcher = vi.fn().mockResolvedValue(new Response(png, { headers: { "Content-Type": "image/png" } }))
    vi.stubGlobal("fetch", fetcher)
    const req = request()
    const response = await GET(req)

    expect(fetcher).toHaveBeenCalledExactlyOnceWith(
      `http://backend:8787/sessions/${sessionId}/desktop-images/${artifactId}`,
      { method: "GET", headers: { Accept: "image/png" }, cache: "no-store", redirect: "error", signal: req.signal },
    )
    expect(response.status).toBe(200)
    expect(response.headers.get("content-type")).toBe("image/png")
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(response.headers.get("x-content-type-options")).toBe("nosniff")
    expect(response.headers.get("content-length")).toBe(String(png.length))
    expect(Buffer.from(await response.arrayBuffer())).toEqual(png)
  })

  it.each<Record<string, string>>([
    {},
    { session_id: sessionId },
    { artifact_id: artifactId },
    { session_id: "", artifact_id: artifactId },
    { session_id: "../other-session", artifact_id: artifactId },
    { session_id: "chat?other=1", artifact_id: artifactId },
    { session_id: "chat%2fother", artifact_id: artifactId },
    { session_id: "chat\n", artifact_id: artifactId },
    { session_id: "a".repeat(129), artifact_id: artifactId },
    { session_id: sessionId, artifact_id: "" },
    { session_id: sessionId, artifact_id: "../image.png" },
    { session_id: sessionId, artifact_id: `${artifactId}/extra` },
    { session_id: sessionId, artifact_id: "879a4f76-2f47-1039-b5ca-30c8bba286bf" },
    { session_id: sessionId, artifact_id: `${artifactId}\n` },
  ])("rejects invalid session/artifact query values before fetching: %j", async params => {
    const fetcher = vi.fn()
    vi.stubGlobal("fetch", fetcher)
    const response = await GET(request(params))
    expect(response.status).toBe(400)
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(fetcher).not.toHaveBeenCalled()
  })

  it.each([403, 404, 410, 500, 302])("handles backend status %s without forwarding error bodies or headers", async status => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private backend details", {
      status, headers: { "Set-Cookie": "secret=value", Location: "https://other.example" },
    })))
    const response = await GET(request())
    expect(response.status).toBe(status >= 400 && status < 500 ? status : 502)
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(response.headers.has("set-cookie")).toBe(false)
    expect(response.headers.has("location")).toBe(false)
    expect(await response.text()).not.toContain("private backend details")
  })

  it.each(["image/jpeg", "image/svg+xml", "text/html", "application/octet-stream", ""])(
    "rejects non-PNG content type %s and cancels the upstream body", async contentType => {
      const cancel = vi.fn()
      const body = new ReadableStream({ cancel })
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, {
        headers: { "Content-Type": contentType },
      })))
      const response = await GET(request())
      expect(response.status).toBe(502)
      expect(cancel).toHaveBeenCalledOnce()
    },
  )

  it("rejects an oversized Content-Length before reading the body", async () => {
    const cancel = vi.fn()
    const body = new ReadableStream({ cancel })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, {
      headers: { "Content-Type": "image/png", "Content-Length": String(maxBytes + 1) },
    })))
    expect((await GET(request())).status).toBe(502)
    expect(cancel).toHaveBeenCalledOnce()
  })

  it.each([undefined, "1"])("bounds streamed bytes even with Content-Length %s", async contentLength => {
    const cancel = vi.fn()
    let chunks = 0
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        chunks++
        controller.enqueue(new Uint8Array(1024 * 1024))
      },
      cancel,
    })
    const headers = new Headers({ "Content-Type": "image/png" })
    if (contentLength) headers.set("Content-Length", contentLength)
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { headers })))
    const response = await GET(request())
    expect(response.status).toBe(502)
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(cancel).toHaveBeenCalledOnce()
    expect(chunks).toBeLessThanOrEqual(12)
  })

  it("accepts an image at the byte limit and sets the actual response length", async () => {
    const image = Buffer.alloc(maxBytes)
    png.copy(image)
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(image, {
      headers: { "Content-Type": "image/png", "Content-Length": "1" },
    })))
    const response = await GET(request())
    expect(response.status).toBe(200)
    expect(response.headers.get("content-length")).toBe(String(maxBytes))
    expect((await response.arrayBuffer()).byteLength).toBe(maxBytes)
  })

  it.each([null, "", "<html>not a PNG</html>"])("rejects missing or mislabeled image bytes: %s", async body => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { headers: { "Content-Type": "image/png" } })))
    expect((await GET(request())).status).toBe(502)
  })

  it("returns an uncached gateway error on network or stream failure", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("internal connection details"))
      .mockResolvedValueOnce(new Response(new ReadableStream({
        start(controller) { controller.error(new Error("upstream read failed")) },
      }), { headers: { "Content-Type": "image/png" } }))
    vi.stubGlobal("fetch", fetcher)
    for (let i = 0; i < 2; i++) {
      const response = await GET(request())
      expect(response.status).toBe(502)
      expect(response.headers.get("cache-control")).toBe("no-store")
      expect(await response.json()).toEqual({ error: "Desktop image unavailable" })
    }
  })

  it("propagates client cancellation to an in-flight backend fetch", async () => {
    const controller = new AbortController()
    const req = new Request(request(), { signal: controller.signal })
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true })
    }))
    vi.stubGlobal("fetch", fetcher)
    const pending = GET(req)
    controller.abort()
    expect((await pending).status).toBe(502)
    expect(fetcher.mock.calls[0][1].signal?.aborted).toBe(true)
  })

  it("cancels an in-flight image body when the client disconnects after headers", async () => {
    const controller = new AbortController()
    const req = new Request(request(), { signal: controller.signal })
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({
      pull(stream) {
        stream.enqueue(png)
        controller.abort()
      },
      cancel,
    }, { highWaterMark: 0 })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { headers: { "Content-Type": "image/png" } })))
    expect((await GET(req)).status).toBe(502)
    expect(cancel).toHaveBeenCalledOnce()
  })
})
