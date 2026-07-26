// The compare view runs the ORCHESTRATOR once per model, not raw model
// completions. It used to call Perplexity's HTTP API directly from here,
// which meant the view was comparing bare models — no tools, no `think`, no
// durability, no retries — and so was never showing the agent at all. It now
// forwards to the orchestrator's /compare/stream, which runs one TemporalAgent
// per model with the same tools and the same AgentConfig identity as a chat
// session (see orchestrator/compare_workflow.py).

export const maxDuration = 1800

const ORCHESTRATOR_URL =
  process.env.ORCHESTRATOR_URL ?? "http://localhost:8787"

const MAX_MODELS = 4

// What the client already consumes. The orchestrator speaks a richer protocol
// (per-model tool calls, thinking cycles, tool results); this maps the parts
// the compare panes render today and drops the rest, rather than changing the
// client contract in the same step as the backend.
type CompareEvent =
  | { model: string; type: "start" }
  | { model: string; type: "delta"; text: string }
  // Same UI message parts the main chat builds, so each pane can render the
  // identical <AgentActivity> — chain of thought, tool cards and all.
  | { model: string; type: "part"; part: Record<string, unknown> }
  | { model: string; type: "done" }
  | { model: string; type: "error"; error: string }

export async function POST(req: Request) {
  const { prompt, models }: { prompt: string; models: string[] } = await req.json()

  const selected = [...new Set(models)].slice(0, MAX_MODELS)

  if (!prompt?.trim() || selected.length === 0) {
    return Response.json(
      { error: "A prompt and at least one model are required." },
      { status: 400 }
    )
  }

  const encoder = new TextEncoder()

  const stream = new ReadableStream({
    async start(controller) {
      const send = (event: CompareEvent) => {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
      }

      const started = new Set<string>()
      const toolNames: Record<string, string> = {}
      const toolInputs: Record<string, unknown> = {}
      const thinkCount: Record<string, number> = {}
      const ensureStarted = (model: string) => {
        if (!started.has(model)) {
          started.add(model)
          send({ model, type: "start" })
        }
      }

      try {
        const res = await fetch(`${ORCHESTRATOR_URL}/compare/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt, model_ids: selected }),
        })
        if (!res.ok || !res.body) {
          const detail = await res.text().catch(() => "")
          throw new Error(
            `Orchestrator unreachable (${res.status}). Is the worker + Temporal dev server running? ${detail}`
          )
        }

        const reader = res.body.getReader()
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
            const event = JSON.parse(line.slice("data: ".length))

            // Terminal frame: carries every model's final text, so any model
            // whose deltas were missed still renders a complete answer.
            if (event.done) {
              for (const model of selected) {
                const err = event.errors?.[model]
                if (err) {
                  ensureStarted(model)
                  send({ model, type: "error", error: err })
                  continue
                }
                if (!started.has(model) && event.replies?.[model]) {
                  ensureStarted(model)
                  send({ model, type: "delta", text: event.replies[model] })
                }
                send({ model, type: "done" })
              }
              continue
            }

            if (event.error && !event.model) throw new Error(event.error)

            const model: string | undefined = event.model
            if (!model) continue

            if (event.error) {
              ensureStarted(model)
              send({ model, type: "error", error: event.error })
              continue
            }

            ensureStarted(model)

            // Each cycle of the agent's think tool -> a ChainOfThoughtStep.
            if (typeof event.text === "string" && event.topic === undefined) {
              send({
                model,
                type: "part",
                part: { type: "data-thinking-cycle", id: `t-${model}-${thinkCount[model] = (thinkCount[model] ?? 0) + 1}`, data: { text: event.text } },
              })
              continue
            }

            // Tool output.
            if (typeof event.tool_use_id === "string") {
              const content = (event.content ?? []) as Array<{ text?: string; json?: unknown }>
              const vals = content.map((c) => (c.json !== undefined ? c.json : c.text ?? "")).filter((v) => v !== "")
              send({
                model,
                type: "part",
                part: {
                  type: "dynamic-tool",
                  toolCallId: event.tool_use_id,
                  toolName: toolNames[`${model}:${event.tool_use_id}`] ?? "tool",
                  state: event.status === "error" ? "output-error" : "output-available",
                  input: toolInputs[`${model}:${event.tool_use_id}`],
                  output: vals.length === 1 ? vals[0] : vals,
                },
              })
              continue
            }

            // Raw Strands StreamEvents on this model's topic.
            const toolUse = event?.contentBlockStart?.start?.toolUse
            if (toolUse) {
              toolNames[`${model}:${toolUse.toolUseId}`] = toolUse.name
              continue
            }
            const delta = event?.contentBlockDelta?.delta
            if (delta?.text) {
              send({ model, type: "delta", text: delta.text })
            } else if (delta?.reasoningContent?.text) {
              send({ model, type: "part", part: { type: "reasoning", text: delta.reasoningContent.text } })
            } else if (delta?.toolUse?.input !== undefined) {
              const ids = Object.keys(toolNames).filter((k) => k.startsWith(`${model}:`))
              const last = ids[ids.length - 1]
              if (last) {
                let input: unknown = delta.toolUse.input
                try { input = JSON.parse(delta.toolUse.input) } catch {}
                toolInputs[last] = input
                send({
                  model,
                  type: "part",
                  part: {
                    type: "dynamic-tool",
                    toolCallId: last.slice(model.length + 1),
                    toolName: toolNames[last],
                    state: "input-available",
                    input,
                  },
                })
              }
            }
          }
        }
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Comparison failed."
        for (const model of selected) {
          ensureStarted(model)
          send({ model, type: "error", error: message })
        }
      } finally {
        controller.close()
      }
    },
  })

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  })
}
