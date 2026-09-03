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
import { COMPUTER_USE_TOOL_NAMES } from "@/components/v0/computer-use"

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

function computerUseIntent(input: unknown): string {
  if (!input || typeof input !== "object" || Array.isArray(input)) return ""
  const intent = (input as { intent?: unknown }).intent
  return typeof intent === "string" ? intent.trim() : ""
}

function reasoningDeltaText(delta: {
  reasoningContent: unknown
}): string {
  const content = delta.reasoningContent
  if (!content || typeof content !== "object") return ""
  const record = content as Record<string, unknown>
  if (typeof record.text === "string") return record.text
  const nested = record.reasoningText
  if (
    nested &&
    typeof nested === "object" &&
    typeof (nested as { text?: unknown }).text === "string"
  ) {
    return (nested as { text: string }).text
  }
  return ""
}

// Strands Gemini content blocks take a bare format plus raw bytes. useChat
// attachments arrive as data: URLs; this route splits them into that shape
// for the orchestrator's TurnInput (image / document / video).
const IMAGE_FORMATS: Record<string, "png" | "jpeg" | "gif" | "webp"> = {
  "image/png": "png",
  "image/jpeg": "jpeg",
  "image/jpg": "jpeg",
  "image/gif": "gif",
  "image/webp": "webp",
}

const DOCUMENT_FORMATS: Record<
  string,
  "pdf" | "txt" | "html" | "csv" | "md" | "json"
> = {
  "application/pdf": "pdf",
  "text/plain": "txt",
  "text/html": "html",
  "text/csv": "csv",
  "text/markdown": "md",
  "text/md": "md",
  "application/json": "json",
}

const VIDEO_FORMATS: Record<
  string,
  | "mp4"
  | "mpeg"
  | "mov"
  | "avi"
  | "webm"
  | "wmv"
  | "flv"
  | "mpg"
  | "mpegps"
  | "3gpp"
> = {
  "video/mp4": "mp4",
  "video/mpeg": "mpeg",
  "video/mpg": "mpg",
  "video/quicktime": "mov",
  "video/webm": "webm",
  "video/x-msvideo": "avi",
  "video/x-ms-wmv": "wmv",
  "video/x-flv": "flv",
  "video/3gpp": "3gpp",
}

type TurnMedia = { format: string; data: string }

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

function filePartBase64(part: { url: string }): string | undefined {
  return part.url.split(",")[1]
}

function messageImages(message: UIMessage | undefined): TurnMedia[] {
  if (!message) return []
  const images: TurnMedia[] = []
  for (const part of message.parts) {
    if (!isFileUIPart(part)) continue
    const format = IMAGE_FORMATS[part.mediaType]
    if (!format) continue
    const base64 = filePartBase64(part)
    if (base64) images.push({ format, data: base64 })
  }
  return images
}

function messageDocuments(message: UIMessage | undefined): TurnMedia[] {
  if (!message) return []
  const documents: TurnMedia[] = []
  for (const part of message.parts) {
    if (!isFileUIPart(part)) continue
    const format = DOCUMENT_FORMATS[part.mediaType]
    if (!format) continue
    const base64 = filePartBase64(part)
    if (base64) documents.push({ format, data: base64 })
  }
  return documents
}

function messageVideos(message: UIMessage | undefined): TurnMedia[] {
  if (!message) return []
  const videos: TurnMedia[] = []
  for (const part of message.parts) {
    if (!isFileUIPart(part)) continue
    const format = VIDEO_FORMATS[part.mediaType]
    if (!format) continue
    const base64 = filePartBase64(part)
    if (base64) videos.push({ format, data: base64 })
  }
  return videos
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

// One nested Agent API preset run's stream, forwarded verbatim from the
// worker's `agent_runs` topic (see the orchestrator plan, step 5). The
// activity streams the sub-agent's SSE events with correlation metadata:
// `activity`/`activity_id` identify the Temporal activity while it is live,
// `response_id` arrives once the Agent API's response.created event does and
// is the authoritative lifecycle key, `sequence_number` is the API's own
// event ordering, and `attempt` distinguishes Temporal retries.
type AgentRunFrame = {
  topic: "agent_runs"
  activity: string
  activity_id: string
  preset: string
  response_id?: string | null
  sequence_number: number
  attempt: number
  event: Record<string, unknown>
}

// Cumulative snapshot re-emitted as ONE reconciled `data-agent-run` part per
// run (stable id), mirroring the AgentRunSnapshot shape consumed by
// components/v0/agent-run.ts.
type AgentRunState = {
  snapshot: {
    activity: string
    activityId: string
    preset: string
    responseId: string | null
    attempt: number
    status: string
    model: string | null
    text: string
    error: string | null
    events: Array<{ sequence: number; event: Record<string, unknown> }>
  }
  // Dedupe key set: `${response_id ?? ""}:${sequence_number}` — a Temporal
  // retry or SSE reconnect republishes events with the same response id and
  // sequence numbers, which must not duplicate in the snapshot.
  seen: Set<string>
}

// Cumulative snapshot re-emitted as ONE reconciled `data-graph-run` part per
// graph tool call, folded from the tool's raw native multiagent_* events
// (shapes live-captured in docs/evidence/GWEN-30/event-log.json). Consumed
// by components/v0/graph-run.ts.
type GraphRunSnapshot = {
  toolUseId: string
  status: "running" | "done" | "failed"
  nodes: Record<
    string,
    { status: "running" | "streaming" | "done"; kind: string; text: string }
  >
  handoffs: Array<{ from: string[]; to: string[] }>
  resultText: string | null
}

// Fold one native graph event into the snapshot. The tool yields flat
// node_id-tagged events (verified live, GWEN-30): lifecycle start/stop,
// per-node streams whose inner `event.data` is the model delta text, a
// handoff between node sets, and a final {status, content} tool result.
function foldGraphEvent(snap: GraphRunSnapshot, data: Record<string, unknown>) {
  const type = typeof data.type === "string" ? data.type : ""
  const nodeId = typeof data.node_id === "string" ? data.node_id : ""
  const node = (id: string) =>
    (snap.nodes[id] ??= { status: "running", kind: "agent", text: "" })

  if (type === "multiagent_node_start" && nodeId) {
    const n = node(nodeId)
    n.status = "running"
    if (typeof data.node_type === "string") n.kind = data.node_type
  } else if (type === "multiagent_node_stream" && nodeId) {
    const n = node(nodeId)
    const inner = data.event as { data?: unknown } | undefined
    if (typeof inner?.data === "string") {
      n.status = "streaming"
      n.text += inner.data
    }
  } else if (type === "multiagent_node_stop" && nodeId) {
    node(nodeId).status = "done"
  } else if (type === "multiagent_handoff") {
    const from = Array.isArray(data.from_node_ids) ? (data.from_node_ids as string[]) : []
    const to = Array.isArray(data.to_node_ids) ? (data.to_node_ids as string[]) : []
    snap.handoffs.push({ from, to })
  } else if (typeof data.status === "string" && Array.isArray(data.content)) {
    // The tool's final yield: {status: "success"|"error", content: [{text}]}.
    snap.status = data.status === "success" ? "done" : "failed"
    snap.resultText = (data.content as Array<{ text?: string }>)
      .map((block) => block.text ?? "")
      .filter(Boolean)
      .join("\n")
  }
}

type OrchestratorEvent =
  | AgentRunFrame
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
      | {
          gemini:
            | {
                type: "google_maps"
                google_maps_widget_context_token?: string | null
                places?: Array<{ title?: string; uri?: string; placeId?: string }>
              }
            | {
                type: "google_search"
                queries?: string[]
                results?: Array<{ title?: string; uri?: string }>
                images?: Array<{
                  title?: string
                  image_uri: string
                  source_uri?: string
                }>
              }
        }
    ))
  // Text deltas from thinking_activity.py: `data` is a fragment, `cycle`/`total`
  // locate it. Non-string `data` is the tool's final ToolResult. The graph
  // tool's frames arrive on the same channel: `data` is one raw native
  // multiagent_* event (or the final {status, content} tool result) and
  // `tool_use.toolUseId` correlates the run.
  | {
      topic: "thinking"
      tool_use?: { name?: string; toolUseId?: string }
      data: unknown
      cycle?: number
      total?: number
    }
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
  const documents = messageDocuments(lastUser)
  const videos = messageVideos(lastUser)

  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      let activeSessionId = sessionId

      // Declared outside the try so the error path below can close whatever
      // the happy path had already opened. Re-emitting text-start for an id
      // that is already open, or leaving reasoning-start unmatched, is a UI
      // Message Stream protocol violation the client renders as a duplicated
      // or permanently-pending block.
      // Mutable: an activity retry (see the messageStart handler) ends the
      // aborted attempt's text block and rotates to a fresh id — a text-end'd
      // id must never receive further deltas.
      let textId = "response"
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

      // Strands lifecycle events (messageStart/messageStop) carry no renderable
      // payload — only contentBlockDelta / tool frames become UI parts. On
      // turns that reuse a session the route used to block on the turn fetch
      // and then wait for the first delta, so useChat stayed in `submitted`
      // with zero assistant parts while Temporal was already running.
      // Emit one placeholder reasoning block immediately so the AI SDK flips
      // to `streaming` and AgentActivity mounts before orchestrator I/O.
      let turnPendingId: string | null = "turn-pending"
      writer.write({ type: "reasoning-start", id: turnPendingId })
      const clearTurnPending = () => {
        if (turnPendingId !== null) {
          writer.write({ type: "reasoning-end", id: turnPendingId })
          turnPendingId = null
        }
      }

      const ensureText = () => {
        clearTurnPending()
        if (!textStarted) {
          writer.write({ type: "text-start", id: textId })
          textStarted = true
        }
      }
      const ensureReasoning = () => {
        clearTurnPending()
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
            body: JSON.stringify({ prompt, images, documents, videos }),
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
        let openToolStarted = false
        let gotAnyEvent = false
        let finalReply = ""
        // Distinguishes successive native frames that carry no id of their own
        // (the reasoning stream), so each renders as its own step.
        let nativeSeq = 0
        // GWEN-6: which topics currently have an open model message. A
        // Temporal retry of the model activity republishes the whole message
        // from the start, and messageStop is yielded only on terminal success
        // (orchestrator/perplexity_model.py:499) — so a messageStart arriving
        // while this set already holds its topic is unambiguously a retry of
        // an attempt whose partial frames were already streamed.
        const openMessages = new Set<string>()
        let retryAttempt = 1
        // Nested Agent API preset runs, one cumulative snapshot per Temporal
        // activity execution. Keyed by activity_id — stable across the run
        // and across activity retries, so every frame reconciles into the
        // same data part instead of appending a new card per event.
        const agentRuns = new Map<string, AgentRunState>()
        // Formation graph runs, one cumulative snapshot per graph tool call
        // (same reconciliation pattern as agentRuns). Keyed by toolUseId.
        const graphRuns = new Map<string, GraphRunSnapshot>()
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

            // A formation graph tool call's stream: the workflow publishes the
            // tool's raw native multiagent_* events (and its final
            // {status, content} result) on the thinking topic, tagged with the
            // graph tool_use. Each frame folds into a cumulative per-call
            // snapshot re-emitted as ONE reconciled data-graph-run part —
            // identical reconciliation pattern to data-agent-run below.
            if (
              "topic" in event &&
              event.topic === "thinking" &&
              "tool_use" in event &&
              event.tool_use?.name === "use_skill" &&
              typeof event.tool_use.toolUseId === "string" &&
              "data" in event &&
              typeof event.data === "object" &&
              event.data !== null
            ) {
              const frame = event.data as {
                skill_name?: string
                text?: string
                event?: Record<string, unknown>
              }
              let delta = ""
              if (typeof frame.text === "string" && frame.text) {
                delta = frame.text
              } else if (frame.event && typeof frame.event === "object") {
                const inner = frame.event
                if (typeof inner.data === "string") delta = inner.data
                else if (
                  inner.contentBlockDelta &&
                  typeof inner.contentBlockDelta === "object"
                ) {
                  const d = (inner.contentBlockDelta as { delta?: { text?: string } })
                    .delta
                  if (typeof d?.text === "string") delta = d.text
                }
              }
              if (delta) {
                clearTurnPending()
                const label =
                  typeof frame.skill_name === "string" && frame.skill_name
                    ? `[${frame.skill_name}] `
                    : ""
                writer.write({
                  type: "reasoning-delta",
                  id: ensureReasoning(),
                  delta: `${label}${delta}`,
                })
              }
              continue
            }

            if (
              "topic" in event &&
              event.topic === "thinking" &&
              "tool_use" in event &&
              event.tool_use?.name === "graph" &&
              typeof event.tool_use.toolUseId === "string" &&
              "data" in event &&
              typeof event.data === "object" &&
              event.data !== null
            ) {
              const toolUseId = event.tool_use.toolUseId
              let snap = graphRuns.get(toolUseId)
              if (!snap) {
                snap = { toolUseId, status: "running", nodes: {}, handoffs: [], resultText: null }
                graphRuns.set(toolUseId, snap)
              }
              foldGraphEvent(snap, event.data as Record<string, unknown>)
              clearTurnPending()
              closeReasoning()
              writer.write({
                type: "data-graph-run",
                id: `graph-${toolUseId}`,
                // Snapshot is mutated in place across frames; clone so each
                // written part is an immutable value.
                data: structuredClone(snap),
              })
              continue
            }

            // A nested Agent API preset run's stream. Each frame folds into a
            // cumulative per-run snapshot re-emitted as one reconciled
            // data-agent-run part: stable id, events ordered by the API's
            // sequence_number, replays deduplicated by
            // (response_id, sequence_number). While the response id is still
            // unknown the activity name is the provisional binding key the
            // client uses; once response.created arrives the id becomes the
            // authoritative one.
            if ("topic" in event && event.topic === "agent_runs") {
              const frame = event as AgentRunFrame
              let run = agentRuns.get(frame.activity_id)
              if (!run) {
                run = {
                  snapshot: {
                    activity: frame.activity,
                    activityId: frame.activity_id,
                    preset: frame.preset,
                    responseId: null,
                    attempt: frame.attempt,
                    status: "queued",
                    model: null,
                    text: "",
                    error: null,
                    events: [],
                  },
                  seen: new Set(),
                }
                agentRuns.set(frame.activity_id, run)
              }
              const snap = run.snapshot
              snap.attempt = frame.attempt
              if (typeof frame.response_id === "string" && frame.response_id) {
                snap.responseId = frame.response_id
              }

              const dedupeKey = `${frame.response_id ?? ""}:${frame.sequence_number}`
              if (run.seen.has(dedupeKey)) continue
              run.seen.add(dedupeKey)

              const runEvent = frame.event ?? {}
              const type =
                typeof runEvent.type === "string" ? runEvent.type : ""
              // The terminal/created events carry the full response object:
              // status, actually-selected model, output text, and error.
              const response = runEvent.response as
                | Record<string, unknown>
                | undefined
              if (response) {
                if (typeof response.id === "string") snap.responseId = response.id
                if (typeof response.status === "string") snap.status = response.status
                if (typeof response.model === "string") snap.model = response.model
                const error = response.error as
                  | { message?: string }
                  | null
                  | undefined
                if (error && typeof error.message === "string") {
                  snap.error = error.message
                }
              } else if (type === "response.created") {
                snap.status = "in_progress"
              }
              // Streamed final markdown: output text deltas accumulate; a
              // terminal completed text replaces the accumulation so a
              // reconnect that skipped deltas still shows the whole answer.
              if (
                type === "response.output_text.delta" &&
                typeof runEvent.delta === "string"
              ) {
                snap.text += runEvent.delta
              } else if (
                type === "response.output_text.done" &&
                typeof runEvent.text === "string"
              ) {
                snap.text = runEvent.text
              }

              // Insert in sequence order (frames are near-ordered already, so
              // this is almost always a push).
              const entry = { sequence: frame.sequence_number, event: runEvent }
              const events = snap.events
              let at = events.length
              while (at > 0 && events[at - 1].sequence > entry.sequence) at--
              events.splice(at, 0, entry)

              clearTurnPending()
              closeReasoning()
              writer.write({
                type: "data-agent-run",
                id: `agent-run-${frame.activity_id}`,
                // Snapshot is mutated in place across frames; clone so each
                // written part is an immutable value.
                data: {
                  ...snap,
                  events: [...snap.events],
                },
              })
              continue
            }

            // Native server-side tool activity, forwarded exactly as the Agent
            // API sent it. Each payload becomes one reconciled data part keyed
            // so repeats of the same call update in place rather than append —
            // the sandbox emits several results against one call_id, and the
            // reasoning stream repeats queries as it refines them.
            if ("gemini" in event) {
              const native = event.gemini
              clearTurnPending()
              closeReasoning()
              writer.write({
                type: "data-native-tool",
                id: `gemini-${native.type}-${nativeSeq++}`,
                data: native,
              })
              continue
            }

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
              clearTurnPending()
              closeReasoning()
              writer.write({ type: "data-native-tool", id, data })
              continue
            }

            // Tool output — what completes a tool card that tool-input-start
            // / tool-input-available opened. Checked before the bare
            // {error: string} terminal payload below, which it would
            // otherwise be confused with.
            if ("topic" in event && event.topic === "tool_results") {
              clearTurnPending()
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

            // GWEN-6: messageStart/messageStop bracket one model-activity
            // attempt per topic. A messageStart while that topic's message is
            // still open means the previous attempt failed mid-stream and
            // Temporal is retrying — the retry republishes every frame from
            // the start, so the aborted attempt's partial output must be
            // reconciled away rather than left to duplicate.
            if ("messageStart" in event && "topic" in event) {
              const topic = event.topic
              if (openMessages.has(topic)) {
                retryAttempt++
                // Close the aborted attempt's open blocks so the retry's
                // frames open fresh ones instead of appending to them.
                closeReasoning()
                openTool = undefined
                openToolStarted = false
                if (textStarted) {
                  writer.write({ type: "text-end", id: textId })
                  textStarted = false
                  textId = `response-retry-${retryAttempt}`
                }
                // The retry re-emits the same native frames; resetting the
                // sequence makes their data-part ids collide with the failed
                // attempt's, so the client reconciles them in place instead
                // of appending a second copy of every step.
                nativeSeq = 0
                // A single reconciled part (stable id, like data-session /
                // data-approval) the UI can render as a retrying state
                // during the backoff window.
                clearTurnPending()
                writer.write({
                  type: "data-retry",
                  id: "retry",
                  data: { attempt: retryAttempt },
                })
              }
              openMessages.add(topic)
              continue
            }

            if ("messageStop" in event && "topic" in event) {
              openMessages.delete(event.topic)
              continue
            }

            if ("contentBlockStart" in event) {
              const toolUse = event.contentBlockStart.start.toolUse
              if (toolUse) {
                openTool = { id: toolUse.toolUseId, name: toolUse.name }
                openToolStarted = false
                // tool-input-start is deferred until tool-input-available so
                // native reasoningContent / intent reasoning-delta reconcile
                // into message.parts before the tool card (true CoT order).
              }
              continue
            }

            if ("contentBlockStop" in event) {
              closeReasoning()
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
                const text = reasoningDeltaText(delta)
                if (text) {
                  writer.write({
                    type: "reasoning-delta",
                    id: ensureReasoning(),
                    delta: text,
                  })
                }
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
                  if (COMPUTER_USE_TOOL_NAMES.has(openTool.name)) {
                    const intent = computerUseIntent(input)
                    if (intent) {
                      writer.write({
                        type: "reasoning-delta",
                        id: ensureReasoning(),
                        delta: `${intent}\n`,
                      })
                    }
                  } else {
                    closeReasoning()
                  }
                  if (!openToolStarted) {
                    writer.write({
                      type: "tool-input-start",
                      toolCallId: openTool.id,
                      toolName: openTool.name,
                      dynamic: true,
                    })
                    openToolStarted = true
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
        clearTurnPending()
        closeReasoning()
        if (textStarted) {
          writer.write({ type: "text-end", id: textId })
        }
      }
    },
  })

  return createUIMessageStreamResponse({ stream })
}
