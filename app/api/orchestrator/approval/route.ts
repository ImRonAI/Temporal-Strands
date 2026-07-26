const ORCHESTRATOR_URL =
  process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"

// Thin passthrough to ChatWorkflow's `approve` signal (orchestrator/server.py).
// Answering releases workflow.wait_condition and lets the original
// /turns/stream request finish normally.
//
// There is no GET here any more. The pending reason is PUSHED down the
// session's stream as a `data-approval` part; the old GET was polled once a
// second for the whole of every streaming turn.

export async function POST(req: Request) {
  const { sessionId, response }: { sessionId: string; response: string } =
    await req.json()

  const res = await fetch(`${ORCHESTRATOR_URL}/sessions/${sessionId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ response }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => "")
    return Response.json(
      { error: `Failed to approve (${res.status}): ${detail}` },
      { status: 502 }
    )
  }
  return new Response(null, { status: 204 })
}
