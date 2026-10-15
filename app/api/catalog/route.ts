export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  try {
    const sessionId = new URL(req.url).searchParams.get("session_id")
    const origin = process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"
    const upstream = await fetch(`${origin}/catalog${sessionId ? `?session_id=${sessionId}` : ""}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    })
    if (!upstream.ok) {
      return Response.json({ error: `Catalog request failed (${upstream.status})` }, { status: 502 })
    }
    return Response.json(await upstream.json())
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Failed to load catalog" },
      { status: 502 },
    )
  }
}
