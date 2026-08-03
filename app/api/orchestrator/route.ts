import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  isFileUIPart,
  isTextUIPart,
  jsonSchema,
  parseJsonEventStream,
  type UIMessage,
} from "ai"

import { DEFAULT_MODEL } from "@/lib/perplexity"

// The orchestrator's frames are a discriminated union validated structurally
// by the handlers below, so this only satisfies parseJsonEventStream's schema
// requirement and carries the TypeScript type through.
const ORCHESTRATOR_EVENT_SCHEMA = jsonSchema<OrchestratorEvent>({
  type: "object",
})

// The orchestrator's model-call activities can legitimately run for up to
// MODEL_SCHEDULE_TO_CLOSE_TIMEOUT (30 min, see orchestrator/workflow.py) —
// give this route room to actually wait that out instead of timing out first.
export const maxDuration = 1800

const ORCHESTRATOR_URL =
  process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"

// Perplexity's Agent API carries images as `input_image` content parts (see
// the perplexity SDK's input_item_param.py), which the orchestrator's
// PerplexityModel builds from Strands ImageContent blocks — those take a
// bare format plus raw bytes, so the data: URL useChat produces for an
// attachment is split into exactly that here.
const IMAGE_FORMATS: Record<string, "png" | "jpeg" | "gif" | "webp"> = {
  "image/png": "png",
  "image/jpeg": "jpeg",
  "image/jpg": "jpeg",
  "image/gif": "gif",
  "image/webp": "webp",
}

type TurnImage = { format: "png" | "jpeg" | "gif" | "webp"; data: string }

function lastUserMessage(messages: UIMessage[]): UIMessage | undefined {
  return messages.findLast((m) => m.role === "user")
}

function messageText(message: UIMessage | undefined): string {
  if (!message) return ""
  return message.parts
    .filter(isTextUIPart)
    .map((p) => p.text)
    .join("")
}

function messageImages(message: UIMessage | undefined): TurnImage[] {
  if (!message) return []
  const images: TurnImage[] = []
  for (const part of message.parts) {
    if (!isFileUIPart(part)) continue
    const format = IMAGE_FORMATS[part.mediaType]
    // Only image formats the Agent API accepts are forwarded; anything else
    // would be silently dropped downstream, so the composer restricts the
    // file picker to these types rather than accepting files it cannot send.
    if (!format) continue
    const base64 = part.url.split(",")[1]
    if (base64) images.push({ format, data: base64 })
  }
  return images
}

// Shapes the orchestrator's /turns/stream endpoint forwards (see
// orchestrator/server.py's send_turn_stream docstring). Three topics:
// "events" carries raw Strands StreamEvent dicts (see
// orchestrator/perplexity_model.py's _format_chunk for the authoritative
// source of those shapes); "thinking" carries one raw text blob per cycle of
// the agent's `think` tool. This route is the client-side mirror of that
// protocol, not a
// separate one — don't invent shapes here that the backend doesn't send.
// "tool_results" carries each tool's output, sourced from the ToolResultEvents
// Agent.stream_async yields (see ChatWorkflow._invoke).
// Native server-side tool payloads, forwarded verbatim from the Agent API by
// PerplexityModel (`{"perplexity": <SDK payload>}` on the events topic). Two
// families, both discriminated by `type`:
//
//   response.reasoning.*  streaming activity: search_queries, search_results,
//                         fetch_url_queries, fetch_url_results, started,
//                         stopped, plus response.skill.loaded
//   output items          terminal results: search_results, fetch_url_results,
//                         people_search_results, finance_results,
//                         sandbox_results, mcp_list_tools, mcp_call,
//                         skill_loaded, share_file
//
// Field names are the Agent API's own (docs.perplexity.ai, POST /v1/agent).
type PerplexitySearchResult = {
  id: number
  url: string
  title: string
  snippet: string
  date?: string | null
  last_updated?: string | null
}

type PerplexityUrlContent = { url: string; title: string; snippet: string }

type PerplexityNative =
  | { type: "response.reasoning.search_queries"; queries: string[] }
  | { type: "response.reasoning.search_results"; results: PerplexitySearchResult[] }
  | { type: "response.reasoning.fetch_url_queries"; urls: string[] }
  | { type: "response.reasoning.fetch_url_results"; contents: PerplexityUrlContent[] }
  | {
      type: "response.reasoning.finance_search_queries"
      tickers?: string[]
      categories?: string[]
    }
  | {
      type: "response.reasoning.finance_search_results"
      results: Array<{ category: string; content: string; sources?: string[] }>
    }
  | { type: "response.reasoning.started" | "response.reasoning.stopped"; thought?: string | null }
  | { type: "response.skill.loaded" | "skill_loaded"; name: string }
  | { type: "search_results"; queries?: string[]; results: PerplexitySearchResult[] }
  | { type: "people_search_results"; queries?: string[]; results: PerplexitySearchResult[] }
  | {
      type: "finance_results"
      tickers?: string[]
      categories?: string[]
      results: Array<{ category: string; content: string; sources?: string[]; tickers?: string[] }>
    }
  | { type: "fetch_url_results"; contents: PerplexityUrlContent[] }
  | {
      type: "sandbox_results"
      call_id: string
      container_id?: string | null
      language: "python" | "bash"
      code: string
      status: "completed" | "timed_out" | "failed" | "in_progress"
      results: Array<{
        stdout: string
        stderr: string
        exit_code: number
        duration_ms: number
        status: string
      }>
    }
  | {
      type: "mcp_list_tools"
      id: string
      server_label: string
      tools: Array<{ name: string; description?: string | null }>
      error?: string | null
    }
  | {
      type: "mcp_call"
      id: string
      server_label: string
      name: string
      arguments: string
      output?: string | null
      error?: string | null
    }
  | {
      type: "share_file"
      call_id: string
      file_id?: string | null
      filename?: string | null
      size_bytes?: number | null
      url?: string | null
      error?: string | null
    }

type OrchestratorEvent =
  // Both agents publish identical StreamEvent shapes; the topic is what
  // distinguishes the reasoning stage from the orchestrator.
  | ({ topic: "events" | "thinking" } & (
      | { contentBlockStart: { start: { toolUse?: { name: string; toolUseId: string } } } }
      | {
          contentBlockDelta: {
            delta:
              | { text: string }
              | { reasoningContent: { text: string } }
              | { toolUse: { input: string } }
          }
        }
      | { contentBlockStop: Record<string, never> }
      | { messageStart: { role: string } }
      | { messageStop: { stopReason: string } }
      | { metadata: unknown }
      | { perplexity: PerplexityNative }
    ))
  // Text deltas from thinking_activity.py: `data` is a fragment, `cycle`/`total`
  // locate it. Non-string `data` is the tool's final ToolResult.
  | { topic: "thinking"; tool_use?: { name?: string }; data: unknown; cycle?: number; total?: number }
  | { topic: "approval"; reason: string | null }
  | {
      topic: "tool_results"
      tool_use_id: string
      status: "success" | "error"
      // Strands ToolResult content blocks, passed through untouched.
      content: Array<{ text?: string; json?: unknown }>
    }
  | { done: true; reply: string }
  | { error: string }

export async function POST(req: Request) {
  const {
    messages,
    model = DEFAULT_MODEL,
    sessionId,
  }: {
    messages: UIMessage[]
    model?: string
    sessionId?: string
  } = await req.json()

  const lastUser = lastUserMessage(messages)
  const prompt = messageText(lastUser)
  const images = messageImages(lastUser)

  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      let activeSessionId = sessionId

      // Declared outside the try so the error path below can close whatever
      // the happy path had already opened. Re-emitting text-start for an id
      // that is already open, or leaving reasoning-start unmatched, is a UI
      // Message Stream protocol violation the client renders as a duplicated
      // or permanently-pending block.
      const textId = "response"
      let textStarted = false

      // A NEW reasoning part per contiguous run of reasoning, not one part for
      // the whole turn. Reusing a single id made every delta reconcile into
      // one block pinned at its first position, so the reasoning stage's
      // thinking rendered as a single lump after the tools it interleaved
      // with. Each run gets its own id, and any tool activity closes the open
      // run, so Chain of Thought shows thinking and tools in the true order
      // they happened.
      let reasoningSeq = 0
      let openReasoningId: string | null = null

      const ensureText = () => {
        if (!textStarted) {
          writer.write({ type: "text-start", id: textId })
          textStarted = true
        }
      }
      const ensureReasoning = () => {
        if (openReasoningId === null) {
          openReasoningId = `reasoning-${reasoningSeq++}`
          writer.write({ type: "reasoning-start", id: openReasoningId })
        }
        return openReasoningId
      }
      // Called before any non-reasoning part is written, so the next reasoning
      // delta opens a fresh block after it rather than appending to the one
      // that came before.
      const closeReasoning = () => {
        if (openReasoningId !== null) {
          writer.write({ type: "reasoning-end", id: openReasoningId })
          openReasoningId = null
        }
      }

      try {
        if (!activeSessionId) {
          const startRes = await fetch(`${ORCHESTRATOR_URL}/sessions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model_id: model }),
          })
          if (!startRes.ok) {
            throw new Error(
              `Orchestrator unreachable (${startRes.status}). Is the worker + Temporal dev server running? See orchestrator/README.md.`
            )
          }
          const started = (await startRes.json()) as { session_id: string }
          activeSessionId = started.session_id
          // Tell the client which session to reuse on the next turn.
          writer.write({
            type: "data-session",
            id: "session",
            data: { sessionId: activeSessionId },
          })
        }

        const turnRes = await fetch(
          `${ORCHESTRATOR_URL}/sessions/${activeSessionId}/turns/stream`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt, images }),
          }
        )
        if (!turnRes.ok || !turnRes.body) {
          const detail = await turnRes.text().catch(() => "")
          throw new Error(`Orchestrator turn failed (${turnRes.status}): ${detail}`)
        }

        // The tool block currently open. PerplexityModel emits one
        // contentBlockStart / contentBlockDelta / contentBlockStop triple per
        // tool call, so the delta always belongs to the block the most recent
        // start opened — tracked explicitly rather than inferred from the
        // insertion order of a map.
        let openTool: { id: string; name: string } | undefined
        let gotAnyEvent = false
        let finalReply = ""
        // Distinguishes successive native frames that carry no id of their own
        // (the reasoning stream), so each renders as its own step.
        let nativeSeq = 0
        // parseJsonEventStream is the AI SDK's own SSE reader: it decodes,
        // frames on the event-source protocol, drops [DONE], and safely
        // parses each payload. Hand-rolling this missed \r\n framing.
        const events = parseJsonEventStream({
          stream: turnRes.body,
          schema: ORCHESTRATOR_EVENT_SCHEMA,
        })

        for await (const parsed of events) {
          if (!parsed.success) throw parsed.error
          {
            const event = parsed.value
            gotAnyEvent = true

            // Native server-side tool activity, forwarded exactly as the Agent
            // API sent it. Each payload becomes one reconciled data part keyed
            // so repeats of the same call update in place rather than append —
            // the sandbox emits several results against one call_id, and the
            // reasoning stream repeats queries as it refines them.
            if ("perplexity" in event) {
              const native = event.perplexity
              // mcp_list_tools is the MCP transport handshake -- the server
              // announcing its catalog once per connection, identical every
              // turn. It is not something either agent did.
              if (native.type === "mcp_list_tools") continue
              let id: string
              switch (native.type) {
                case "sandbox_results":
                  id = `sandbox-${native.call_id}`
                  break
                case "mcp_call":
                  id = `mcp-${native.id}`
                  break
                case "share_file":
                  id = `file-${native.call_id}`
                  break
                default:
                  id = `native-${native.type}-${nativeSeq++}`
              }
              // share_file carries a relative Agent API path the browser
              // cannot call (it needs the API key). Point it at the
              // orchestrator's proxy instead, which adds auth server-side.
              const data =
                native.type === "share_file" && native.url
                  ? { ...native, url: `/api/orchestrator/file?path=${encodeURIComponent(native.url)}` }
                  : native
              closeReasoning()
              writer.write({ type: "data-native-tool", id, data })
              continue
            }

            // Tool output — what completes a tool card that tool-input-start
            // / tool-input-available opened. Checked before the bare
            // {error: string} terminal payload below, which it would
            // otherwise be confused with.
            if ("topic" in event && event.topic === "tool_results") {
              const text = event.content
                .map((c) => (c.json !== undefined ? c.json : (c.text ?? "")))
                .filter((c) => c !== "")
              const output = text.length === 1 ? text[0] : text
              if (event.status === "error") {
                writer.write({
                  type: "tool-output-error",
                  toolCallId: event.tool_use_id,
                  errorText:
                    typeof output === "string" ? output : JSON.stringify(output),
                  dynamic: true,
                })
              } else {
                closeReasoning()
                writer.write({
                  type: "tool-output-available",
                  toolCallId: event.tool_use_id,
                  output,
                  dynamic: true,
                })
              }
              continue
            }

            // A separate channel from the model's own native reasoningContent
            // (which stays on reasoning-delta below) — each thinking-tool
            // cycle becomes its own step inside the ChainOfThought component
            // itself (see components/v0/agent-activity.tsx), not merged into
            // the same reasoning stream.
            // Live sub-agent output. Accumulated per response id and
            // re-emitted as one data part per sub-agent, so the client

            // Human-in-the-loop prompt, pushed by the workflow. A single
            // reconciled part: the id is stable, so the reason appearing and
            // then clearing updates the same part rather than appending.
            if ("topic" in event && event.topic === "approval") {
              writer.write({
                type: "data-approval",
                id: "approval",
                data: { reason: event.reason },
              })
              continue
            }

            // The "thinking" topic carries the reasoning stage's own
            // StreamEvents — native tool frames and reasoningContent deltas,
            // identical in shape to the orchestrator's. They fall through to
            // the same handlers below rather than needing a parallel path.

            if ("error" in event) {
              throw new Error(event.error)
            }

            if ("done" in event) {
              finalReply = event.reply
              continue
            }

            if ("contentBlockStart" in event) {
              const toolUse = event.contentBlockStart.start.toolUse
              if (toolUse) {
                openTool = { id: toolUse.toolUseId, name: toolUse.name }
                closeReasoning()
                writer.write({
                  type: "tool-input-start",
                  toolCallId: toolUse.toolUseId,
                  toolName: toolUse.name,
                  dynamic: true,
                })
              }
              continue
            }

            if ("contentBlockDelta" in event) {
              const delta = event.contentBlockDelta.delta
              const isThinking = "topic" in event && event.topic === "thinking"
              if ("text" in delta) {
                // Same StreamEvent shape from both agents, so the topic is
                // what separates them: the reasoning stage's text belongs in
                // Chain of Thought as a thought block, the orchestrator's is
                // the answer. Routing both to text-delta put the reasoning
                // stage's private notes in the reply body.
                if (isThinking) {
                  writer.write({
                    type: "reasoning-delta",
                    id: ensureReasoning(),
                    delta: delta.text,
                  })
                } else {
                  closeReasoning()
                  ensureText()
                  writer.write({ type: "text-delta", id: textId, delta: delta.text })
                }
              } else if ("reasoningContent" in delta) {
                writer.write({
                  type: "reasoning-delta",
                  id: ensureReasoning(),
                  delta: delta.reasoningContent.text,
                })
              } else if ("toolUse" in delta) {
                // Perplexity's function-call arguments arrive complete in
                // one chunk (confirmed live), not streamed piecemeal, so
                // this is the full JSON input every time — no accumulation
                // needed across multiple deltas for the same call.
                if (openTool) {
                  let input: unknown = delta.toolUse.input
                  try {
                    input = JSON.parse(delta.toolUse.input)
                  } catch {
                    // Leave as raw string if it isn't valid JSON.
                  }
                  writer.write({
                    type: "tool-input-available",
                    toolCallId: openTool.id,
                    toolName: openTool.name,
                    input,
                    dynamic: true,
                  })
                }
              }
              continue
            }
          }
        }

        // Fallback: stream ended without ever producing a text delta (e.g.
        // a turn that was only tool calls) — still show the final reply.
        if (!textStarted && finalReply) {
          ensureText()
          writer.write({ type: "text-delta", id: textId, delta: finalReply })
        }

        if (!gotAnyEvent) {
          throw new Error("Orchestrator stream closed with no events.")
        }
      } catch (error) {
        // An `error` chunk, not assistant prose: this is what puts useChat
        // into its error state so status stops being "ready" and
        // PromptInputSubmit can surface it. Writing the message as text-delta
        // made a transport failure look like something the model said.
        writer.write({
          type: "error",
          errorText:
            error instanceof Error
              ? error.message
              : "The orchestrator request failed.",
        })
      } finally {
        // Close in one place, on both paths, so every block that was opened
        // is also ended exactly once.
        // closeReasoning is a no-op when nothing is open.
        closeReasoning()
        if (textStarted) {
          writer.write({ type: "text-end", id: textId })
        }
      }
    },
  })

  return createUIMessageStreamResponse({ stream })
}
