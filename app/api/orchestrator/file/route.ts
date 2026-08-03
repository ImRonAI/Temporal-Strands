import { type NextRequest, NextResponse } from "next/server"

const ORCHESTRATOR_URL =
  process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"

/**
 * Streams a sandbox-produced file through the orchestrator.
 *
 * `share_file` output items carry a relative Agent API path
 * (`/v1/responses/{response_id}/files/{file_id}/content`) whose real endpoint
 * needs the Perplexity API key. The browser must never see that key, so the
 * orchestrator holds it and this route forwards the request. Only the two ids
 * embedded in that documented path are honoured.
 */
export async function GET(req: NextRequest) {
  const path = req.nextUrl.searchParams.get("path")
  if (!path) {
    return NextResponse.json({ error: "path is required" }, { status: 400 })
  }

  const match = path.match(
    /^\/v1\/(?:responses|agent)\/([^/]+)\/files\/([^/]+)\/content$/
  )
  if (!match) {
    return NextResponse.json({ error: "unsupported path" }, { status: 400 })
  }
  const [, responseId, fileId] = match

  const upstream = await fetch(
    `${ORCHESTRATOR_URL}/responses/${encodeURIComponent(responseId)}/files/${encodeURIComponent(fileId)}/content`
  )
  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { error: "file unavailable" },
      { status: upstream.status }
    )
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/octet-stream",
      "Cache-Control": "private, max-age=3600",
    },
  })
}
