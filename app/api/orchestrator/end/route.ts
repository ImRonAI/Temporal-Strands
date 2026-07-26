const ORCHESTRATOR_URL =
  process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"

// Ends a durable chat session — the `end_chat` signal that lets ChatWorkflow
// return instead of running forever (orchestrator/server.py's /sessions/{id}/end,
// which is idempotent and 204s for a session that is already gone).
//
// Called from `pagehide` via navigator.sendBeacon, which can only send a POST
// with a body and cannot read the response, so this route takes the session id
// in the body and always resolves quickly. Without it every page load left a
// ChatWorkflow execution running in Temporal indefinitely.
export async function POST(req: Request) {
  const { sessionId }: { sessionId?: string } = await req
    .json()
    .catch(() => ({}))
  if (!sessionId) return new Response(null, { status: 204 })

  await fetch(`${ORCHESTRATOR_URL}/sessions/${sessionId}/end`, {
    method: "POST",
  }).catch(() => {
    // The orchestrator being down is not a client error: the session is
    // already unreachable, which is the state this endpoint aims for.
  })

  return new Response(null, { status: 204 })
}
