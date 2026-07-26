import { listPerplexityModels } from "@/lib/perplexity"

export const revalidate = 300

export async function GET() {
  try {
    const data = await listPerplexityModels()
    return Response.json({ object: "list", data })
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Failed to list models" },
      { status: 502 }
    )
  }
}
