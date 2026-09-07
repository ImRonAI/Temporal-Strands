import { z } from "zod"

const requestSchema = z.object({
  sessionId: z.string().regex(/^[a-zA-Z0-9_-]+$/),
  action: z.enum(["take", "give"]),
  message: z.string().default(""),
}).refine((body) => body.action !== "give" || body.message.trim().length > 0, {
  message: "Resume instructions are required",
})

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
