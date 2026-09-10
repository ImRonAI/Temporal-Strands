// Match the backend's desktop observation byte limit and canonical UUIDv4 IDs.
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const SESSION_ID = /^[a-zA-Z0-9_-]{1,128}$/
const ARTIFACT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const RESPONSE_HEADERS = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" }

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams
  const sessionId = params.get("session_id") ?? ""
  const artifactId = params.get("artifact_id") ?? ""
  if (!SESSION_ID.test(sessionId) || !ARTIFACT_ID.test(artifactId)) {
    return Response.json({ error: "Invalid desktop image request" }, { status: 400, headers: RESPONSE_HEADERS })
  }

  try {
    const origin = process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"
    const upstream = await fetch(`${origin}/sessions/${sessionId}/desktop-images/${artifactId}`, {
      method: "GET",
      headers: { Accept: "image/png" },
      cache: "no-store",
      redirect: "error",
      signal: request.signal,
    })
    if (!upstream.ok || !upstream.body) {
      await upstream.body?.cancel()
      const status = upstream.status >= 400 && upstream.status < 500 ? upstream.status : 502
      return Response.json({ error: "Desktop image unavailable" }, { status, headers: RESPONSE_HEADERS })
    }
    if (upstream.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "image/png" ||
        Number(upstream.headers.get("content-length")) > MAX_IMAGE_BYTES) {
      await upstream.body.cancel()
      return Response.json({ error: "Invalid desktop image response" }, { status: 502, headers: RESPONSE_HEADERS })
    }

    // Enforce the limit on actual bytes even when Content-Length is absent or false.
    let size = 0
    const bounded = upstream.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        size += chunk.byteLength
        if (size > MAX_IMAGE_BYTES) throw new Error("Desktop image exceeds byte limit")
        controller.enqueue(chunk)
      },
    }), { signal: request.signal })
    const image = Buffer.from(await new Response(bounded).arrayBuffer())
    if (!image.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
      return Response.json({ error: "Invalid desktop image response" }, { status: 502, headers: RESPONSE_HEADERS })
    }
    return new Response(image, {
      headers: { ...RESPONSE_HEADERS, "Content-Type": "image/png", "Content-Length": String(size) },
    })
  } catch {
    return Response.json({ error: "Desktop image unavailable" }, { status: 502, headers: RESPONSE_HEADERS })
  }
}
