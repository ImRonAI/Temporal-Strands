import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessage,
} from "ai"

import { DEFAULT_MODEL } from "@/lib/perplexity"

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
  return [...messages].reverse().find((m) => m.role === "user")
}

function messageText(message: UIMessage | undefined): string {
  if (!message) return ""
  return message.parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("")
}

function messageImages(message: UIMessage | undefined): TurnImage[] {
  if (!message) return []
  const images: TurnImage[] = []
  for (const part of message.parts) {
    if (part.type !== "file") continue
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
type OrchestratorEvent =
  | ({ topic: "events" } & (
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
    ))
  | { topic: "thinking"; text: string }
  | { topic: "approval"; reason: string | null }
  // A create_agent_response sub-agent, streamed live from inside that
  // activity (orchestrator/perplexity_tools.py). Perplexity only exposes a
  // background run's stream on the create call itself — retrieve has no
  // stream option — so this is the only way to watch a sub-agent work.
  | {
      topic: "subagent"
      subagent_id: string | null
      status?: "created" | "completed"
      delta?: string
      reasoning?: string
      output_item?: Record<string, unknown>
      error?: string
    }
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
      const reasoningId = "reasoning"
      let textStarted = false
      let reasoningStarted = false

      const ensureText = () => {
        if (!textStarted) {
          writer.write({ type: "text-start", id: textId })
          textStarted = true
        }
      }
      const ensureReasoning = () => {
        if (!reasoningStarted) {
          writer.write({ type: "reasoning-start", id: reasoningId })
          reasoningStarted = true
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
        // Discrete, labeled steps (per AI Elements' own guidance: Chain of
        // Thought is for exactly this, not the single-block Reasoning
        // component) — one data-thinking-cycle part per cycle, each its
        // own ChainOfThoughtStep on the client (components/v0/agent-activity.tsx).
        let thinkingCycleIndex = 0
        // Accumulated sub-agent state, keyed by Perplexity response id.
        type SubagentState = {
          id: string
          text: string
          reasoning: string[]
          outputItems: Record<string, unknown>[]
          status: string
          error?: string
        }
        const subagentState = new Map<string, SubagentState>()

        const reader = turnRes.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ""

        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          const lines = buffer.split("\n")
          buffer = lines.pop() ?? ""

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue
            const event = JSON.parse(line.slice("data: ".length)) as OrchestratorEvent
            gotAnyEvent = true

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
            // reconciles by id and the card grows in place instead of
            // stacking a new one per delta.
            if ("topic" in event && event.topic === "subagent") {
              const id = event.subagent_id ?? "pending"
              const state = subagentState.get(id) ?? {
                id,
                text: "",
                reasoning: [] as string[],
                outputItems: [] as Record<string, unknown>[],
                status: "running" as string,
              }
              if (event.delta) state.text += event.delta
              if (event.reasoning) state.reasoning.push(event.reasoning)
              if (event.output_item) state.outputItems.push(event.output_item)
              if (event.error) {
                state.status = "failed"
                state.error = event.error
              } else if (event.status) {
                state.status = event.status === "completed" ? "completed" : "running"
              }
              subagentState.set(id, state)
              writer.write({
                type: "data-subagent",
                id: `subagent-${id}`,
                data: state,
              })
              continue
            }

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

            if ("topic" in event && event.topic === "thinking") {
              writer.write({
                type: "data-thinking-cycle",
                id: `thinking-${thinkingCycleIndex++}`,
                data: { text: event.text },
              })
              continue
            }

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
              if ("text" in delta) {
                ensureText()
                writer.write({ type: "text-delta", id: textId, delta: delta.text })
              } else if ("reasoningContent" in delta) {
                ensureReasoning()
                writer.write({
                  type: "reasoning-delta",
                  id: reasoningId,
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
        ensureText()
        writer.write({
          type: "text-delta",
          id: textId,
          delta:
            error instanceof Error
              ? error.message
              : "The orchestrator request failed.",
        })
      } finally {
        // Close in one place, on both paths, so every block that was opened
        // is also ended exactly once.
        if (reasoningStarted) {
          writer.write({ type: "reasoning-end", id: reasoningId })
        }
        if (textStarted) {
          writer.write({ type: "text-end", id: textId })
        }
      }
    },
  })

  return createUIMessageStreamResponse({ stream })
}
