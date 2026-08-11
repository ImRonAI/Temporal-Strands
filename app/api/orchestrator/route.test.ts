import { afterEach, describe, expect, it, vi } from "vitest"

import { POST } from "./route"

describe("POST graph activity streaming", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("preserves graph envelopes from the thinking topic as UI data parts", async () => {
    const envelope = {
      protocol_version: "1.0",
      run_id: "graph-run-1",
      node_path: [{ id: "research", kind: "agent" }],
      formation_kind: "graph",
      node_kind: "agent",
      event_type: "node.started",
      provisional: false,
      payload: {
        event: { type: "multiagent_node_start", node_id: "research" },
        native_nesting: ["multiagent_node_start"],
      },
    }
    const upstream = [
      `data: ${JSON.stringify({
        topic: "thinking",
        tool_use: { name: "graph", toolUseId: "graph-tool-1" },
        data: envelope,
      })}`,
      `data: ${JSON.stringify({ done: true, reply: "Complete" })}`,
      "",
    ].join("\n")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(upstream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        })
      )
    )

    const response = await POST(
      new Request("http://localhost/api/orchestrator", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId: "session-1",
          messages: [
            {
              id: "message-1",
              role: "user",
              parts: [{ type: "text", text: "Run the research graph" }],
            },
          ],
        }),
      })
    )

    const body = await response.text()

    expect(body).toContain('"type":"data-graph-event"')
    expect(body).toContain('"event_type":"node.started"')
    expect(body).toContain('"id":"research"')
  })

  it("reconciles repeated graph progress for the same node into one UI part", async () => {
    const graphEvent = (sequence: number) => ({
      topic: "thinking",
      tool_use: { name: "graph", toolUseId: "graph-tool-1" },
      data: {
        protocol_version: "1.0",
        event_id: `graph-run-1:${sequence}`,
        sequence,
        run_id: "graph-run-1",
        node_path: [{ id: "research", kind: "agent" }],
        formation_kind: "graph",
        node_kind: "agent",
        event_type: "model.delta",
        provisional: true,
        payload: { event: { data: sequence === 1 ? "first" : "second" } },
      },
    })
    const upstream = [
      `data: ${JSON.stringify(graphEvent(1))}`,
      `data: ${JSON.stringify(graphEvent(2))}`,
      `data: ${JSON.stringify({ done: true, reply: "Complete" })}`,
      "",
    ].join("\n")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(upstream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        })
      )
    )

    const response = await POST(
      new Request("http://localhost/api/orchestrator", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId: "session-1",
          messages: [
            {
              id: "message-1",
              role: "user",
              parts: [{ type: "text", text: "Run the research graph" }],
            },
          ],
        }),
      })
    )

    const ids = [...(await response.text()).matchAll(/"id":"(graph-[^"]+)"/g)].map(
      (match) => match[1]
    )

    expect(ids).toEqual([
      "graph-graph-run-1-research-model.delta",
      "graph-graph-run-1-research-model.delta",
    ])
  })
})

// GWEN-6: a mid-stream model-activity failure makes Temporal retry
// invoke_model_streaming, and the retry republishes every frame from the
// start while the failed attempt's partial frames are already in the stream
// log. messageStop is yielded only on terminal success
// (orchestrator/perplexity_model.py:499), so a messageStart arriving while a
// message is already open on the same topic is unambiguously a retry. The
// route must collapse the duplicates and surface a reconciled data-retry
// part instead of rendering both attempts.
describe("POST model-activity retry handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const frame = (payload: unknown) => `data: ${JSON.stringify(payload)}`

  const stubTurnStream = (lines: string[]) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        // SSE events are terminated by a blank line, so frames are joined
        // with \n\n — a single \n leaves one unterminated event that the
        // parser discards at EOF.
        new Response(lines.join("\n\n") + "\n\n", {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        })
      )
    )
  }

  const postTurn = () =>
    POST(
      new Request("http://localhost/api/orchestrator", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId: "session-1",
          messages: [
            {
              id: "message-1",
              role: "user",
              parts: [{ type: "text", text: "Largest moons of Saturn?" }],
            },
          ],
        }),
      })
    )

  const parseChunks = (body: string): Array<Record<string, unknown>> =>
    body
      .split("\n")
      .filter((line) => line.startsWith("data: ") && !line.includes("[DONE]"))
      .map((line) => JSON.parse(line.slice("data: ".length)))

  it("collapses a retried events-topic message and emits a reconciled data-retry part", async () => {
    const searchResults = {
      type: "search_results",
      results: [
        { id: 1, url: "https://example.com", title: "Moons", snippet: "Titan" },
      ],
    }
    stubTurnStream([
      frame({ topic: "events", messageStart: { role: "assistant" } }),
      frame({ topic: "events", perplexity: searchResults }),
      frame({ topic: "events", contentBlockDelta: { delta: { text: "Titan, Rhea" } } }),
      // Mid-stream failure: no messageStop. Temporal retries the activity and
      // the retry republishes the whole message from the start.
      frame({ topic: "events", messageStart: { role: "assistant" } }),
      frame({ topic: "events", perplexity: searchResults }),
      frame({
        topic: "events",
        contentBlockDelta: { delta: { text: "Titan, Rhea, Iapetus" } },
      }),
      frame({ topic: "events", messageStop: { stopReason: "end_turn" } }),
      frame({ done: true, reply: "Titan, Rhea, Iapetus" }),
    ])

    const chunks = parseChunks(await (await postTurn()).text())

    // A single reconciled retry part with a stable id, so the UI can show a
    // retrying state that updates in place (same pattern as data-session /
    // data-approval).
    const retryParts = chunks.filter((c) => c.type === "data-retry")
    expect(retryParts).toEqual([
      { type: "data-retry", id: "retry", data: { attempt: 2 } },
    ])

    // The retry's native frames must reuse the failed attempt's part ids so
    // the client reconciles them in place instead of appending duplicates.
    const nativeIds = chunks
      .filter((c) => c.type === "data-native-tool")
      .map((c) => c.id)
    expect(nativeIds).toEqual([
      "native-search_results-0",
      "native-search_results-0",
    ])

    // No text id mixes the two attempts' deltas: the aborted attempt's text
    // is closed and the retry streams into a fresh id, exactly once.
    const textById = new Map<string, string>()
    for (const c of chunks) {
      if (c.type === "text-delta") {
        const id = c.id as string
        textById.set(id, (textById.get(id) ?? "") + (c.delta as string))
      }
    }
    expect([...textById.values()]).toEqual(["Titan, Rhea", "Titan, Rhea, Iapetus"])

    // Every text block that was opened is also ended exactly once.
    const startIds = chunks.filter((c) => c.type === "text-start").map((c) => c.id)
    const endIds = chunks.filter((c) => c.type === "text-end").map((c) => c.id)
    expect([...endIds].sort()).toEqual([...startIds].sort())
  })

  it("collapses a retried thinking-topic message into separate reasoning blocks with a data-retry part", async () => {
    stubTurnStream([
      frame({ topic: "thinking", messageStart: { role: "assistant" } }),
      frame({
        topic: "thinking",
        contentBlockDelta: { delta: { text: "Searching Saturn's moons" } },
      }),
      // Retry: messageStart while the thinking message is still open.
      frame({ topic: "thinking", messageStart: { role: "assistant" } }),
      frame({
        topic: "thinking",
        contentBlockDelta: { delta: { text: "Searching again after retry" } },
      }),
      frame({ topic: "thinking", messageStop: { stopReason: "end_turn" } }),
      frame({ done: true, reply: "Done" }),
    ])

    const chunks = parseChunks(await (await postTurn()).text())

    const retryParts = chunks.filter((c) => c.type === "data-retry")
    expect(retryParts).toEqual([
      { type: "data-retry", id: "retry", data: { attempt: 2 } },
    ])

    // The retry's reasoning opens a fresh block: no reasoning id accumulates
    // deltas from both attempts.
    const reasoningById = new Map<string, string>()
    for (const c of chunks) {
      if (c.type === "reasoning-delta") {
        const id = c.id as string
        reasoningById.set(id, (reasoningById.get(id) ?? "") + (c.delta as string))
      }
    }
    expect([...reasoningById.values()]).toEqual([
      "Searching Saturn's moons",
      "Searching again after retry",
    ])

    // Every reasoning block that was opened is also ended exactly once.
    const startIds = chunks
      .filter((c) => c.type === "reasoning-start")
      .map((c) => c.id)
    const endIds = chunks.filter((c) => c.type === "reasoning-end").map((c) => c.id)
    expect([...endIds].sort()).toEqual([...startIds].sort())
  })
})
