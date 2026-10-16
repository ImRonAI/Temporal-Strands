import { z } from "zod"

const requestSchema = z.object({
  sessionId: z.string().regex(/^[a-zA-Z0-9_-]+$/),
  action: z.enum(["take", "release", "give"]),
  message: z.string().default(""),
}).refine((body) => body.action !== "give" || body.message.trim().length > 0, {
  message: "Resume instructions are required",
})

export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store" }
  const sessionId = new URL(request.url).searchParams.get("sessionId") ?? ""
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(sessionId)) {
    return Response.json({ error: "Invalid desktop session" }, { status: 400, headers })
  }
  try {
    const origin = process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"
    const response = await fetch(`${origin}/sessions/${sessionId}/desktop-control`, {
      cache: "no-store",
      redirect: "error",
      signal: request.signal,
    })
    if (!response.ok) {
      await response.body?.cancel()
      return Response.json({ error: "Desktop control status unavailable" }, {
        status: response.status >= 400 ? response.status : 502, headers,
      })
    }
    return Response.json(await response.json(), { headers })
  } catch {
    return Response.json({ error: "Desktop control status unavailable" }, { status: 502, headers })
  }
}

export async function POST(request: Request) {
  const body = requestSchema.safeParse(await request.json().catch(() => null))
  if (!body.success) return Response.json({ error: "Invalid handoff request" }, { status: 400 })
  const { sessionId, action, message } = body.data
  try {
    const origin = process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"
    const response = await fetch(`${origin}/sessions/${sessionId}/handoff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, message }),
      signal: request.signal,
    })
    if (!response.ok) {
      return Response.json({ error: await response.text() }, { status: response.status })
    }
    return new Response(null, { status: 204 })
  } catch {
    return Response.json({ error: "Could not reach the browser session" }, { status: 502 })
  }
}
