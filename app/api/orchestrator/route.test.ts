import { afterEach, describe, expect, it, vi } from "vitest"

import { POST } from "./route"

// The graph tool (strands-tools src/strands_graph_tool/graph.py) yields raw
// native multiagent_* events; the workflow publishes each on the thinking
// topic with `tool_use: {name: "graph", toolUseId}` — the same frame shape
// thinking_activity.py already uses. The route folds them into ONE
// reconciled data-graph-run snapshot per tool call, exactly the pattern
// data-agent-run uses for nested preset runs. Native event shapes are the
// live-captured ones in docs/evidence/GWEN-30/event-log.json.
describe("POST graph activity streaming", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const graphFrame = (data: unknown) => ({
    topic: "thinking",
    tool_use: { name: "graph", toolUseId: "graph-tool-1" },
    data,
  })

  async function postUpstream(frames: unknown[]): Promise<string> {
    const upstream =
      [
        ...frames.map((frame) => `data: ${JSON.stringify(frame)}`),
        `data: ${JSON.stringify({ done: true, reply: "Complete" })}`,
      ].join("\n\n") + "\n\n"

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
    return response.text()
  }

  it("folds native graph events into one data-graph-run snapshot", async () => {
    const body = await postUpstream([
      graphFrame({
        type: "multiagent_node_start",
        node_id: "research",
        node_type: "agent",
      }),
      graphFrame({
        type: "multiagent_node_stream",
        node_id: "research",
        event: { data: "first" },
      }),
      graphFrame({
        type: "multiagent_node_stream",
        node_id: "research",
        event: { data: " second" },
      }),
      graphFrame({
        type: "multiagent_node_stop",
        node_id: "research",
      }),
      graphFrame({
        type: "multiagent_handoff",
        from_node_ids: ["research"],
        to_node_ids: ["expert"],
      }),
      graphFrame({
        status: "success",
        content: [{ text: "Graph g executed in 3724ms (completed)." }],
      }),
    ])

    expect(body).toContain('"type":"data-graph-run"')
    // Accumulated per-node stream text, not just the last delta.
    expect(body).toContain('"text":"first second"')
    // Node lifecycle reached done; run completed with the tool result text.
    expect(body).toContain('"status":"done"')
    expect(body).toContain("executed in 3724ms")
    expect(body).toContain('"from":["research"]')
  })

  it("reconciles every graph frame into the same per-run part id", async () => {
    const body = await postUpstream([
      graphFrame({
        type: "multiagent_node_start",
        node_id: "research",
        node_type: "agent",
      }),
      graphFrame({
        type: "multiagent_node_stream",
        node_id: "research",
        event: { data: "first" },
      }),
      graphFrame({
        type: "multiagent_node_stream",
        node_id: "research",
        event: { data: "second" },
      }),
    ])

    const ids = [...body.matchAll(/"id":"(graph-[^"]+)"/g)].map(
      (match) => match[1]
    )
    expect(ids.length).toBeGreaterThan(0)
    expect(new Set(ids)).toEqual(new Set(["graph-graph-tool-1"]))
  })
})

// Nested Agent API preset runs (agent_runs topic): the backend envelope
// carries per-run correlation metadata and verbatim Agent API events that the
// pre-existing events/thinking mapping cannot represent — the route must fold
// each run into one reconciled data-agent-run part with a stable id, ordered
// by the API's sequence_number and deduplicated by
// (response_id, sequence_number).
describe("POST agent_runs streaming", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const frame = (payload: unknown) => `data: ${JSON.stringify(payload)}`

  const stubTurnStream = (lines: string[]) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
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
              parts: [{ type: "text", text: "Research something" }],
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

  const runFrame = (overrides: Partial<Record<string, unknown>>) => ({
    topic: "agent_runs",
    activity: "create_medium_agent_response",
    activity_id: "act-1",
    preset: "medium",
    sequence_number: 0,
    attempt: 1,
    event: {},
    ...overrides,
  })

  it("accumulates a run into one reconciled data-agent-run part with dedupe and binding", async () => {
    stubTurnStream([
      // Provisional: no response id yet.
      frame(
        runFrame({
          sequence_number: 1,
          event: { type: "response.reasoning.search_queries", queries: ["q"] },
        })
      ),
      // response.created binds the response id.
      frame(
        runFrame({
          sequence_number: 2,
          response_id: "resp-1",
          event: {
            type: "response.created",
            response: { id: "resp-1", status: "in_progress", model: "sonar-pro" },
          },
        })
      ),
      frame(
        runFrame({
          sequence_number: 3,
          response_id: "resp-1",
          event: { type: "response.output_text.delta", delta: "Hello " },
        })
      ),
      // Replay of sequence 3 (reconnect) — must be dropped.
      frame(
        runFrame({
          sequence_number: 3,
          response_id: "resp-1",
          event: { type: "response.output_text.delta", delta: "Hello " },
        })
      ),
      frame(
        runFrame({
          sequence_number: 4,
          response_id: "resp-1",
          event: {
            type: "response.completed",
            response: { id: "resp-1", status: "completed", model: "sonar-pro" },
          },
        })
      ),
      frame({ done: true, reply: "Done" }),
    ])

    const chunks = parseChunks(await (await postTurn()).text())
    const runParts = chunks.filter((c) => c.type === "data-agent-run")

    // Every frame reconciles into the same stable part id.
    expect(new Set(runParts.map((c) => c.id))).toEqual(
      new Set(["agent-run-act-1"])
    )
    // The replayed sequence emitted no extra part: 5 unique frames minus the
    // duplicate = 4 snapshots.
    expect(runParts).toHaveLength(4)

    const last = runParts.at(-1)!.data as {
      activity: string
      preset: string
      responseId: string
      status: string
      model: string
      text: string
      events: Array<{ sequence: number }>
    }
    expect(last.activity).toBe("create_medium_agent_response")
    expect(last.preset).toBe("medium")
    expect(last.responseId).toBe("resp-1")
    expect(last.status).toBe("completed")
    expect(last.model).toBe("sonar-pro")
    expect(last.text).toBe("Hello ")
    // Ordered by sequence_number, duplicate dropped.
    expect(last.events.map((e) => e.sequence)).toEqual([1, 2, 3, 4])
  })

  it("keeps interleaved distinct preset runs separate with repeated sequence numbers", async () => {
    stubTurnStream([
      frame(
        runFrame({
          activity: "create_fast_agent_response",
          activity_id: "act-a",
          preset: "fast",
          sequence_number: 1,
          response_id: "resp-a",
          event: { type: "response.output_text.delta", delta: "A" },
        })
      ),
      frame(
        runFrame({
          activity: "create_high_agent_response",
          activity_id: "act-b",
          preset: "high",
          sequence_number: 1,
          response_id: "resp-b",
          event: { type: "response.output_text.delta", delta: "B" },
        })
      ),
      frame(
        runFrame({
          activity: "create_fast_agent_response",
          activity_id: "act-a",
          preset: "fast",
          sequence_number: 2,
          response_id: "resp-a",
          event: { type: "response.output_text.delta", delta: "A2" },
        })
      ),
      frame({ done: true, reply: "Done" }),
    ])

    const chunks = parseChunks(await (await postTurn()).text())
    const runParts = chunks.filter((c) => c.type === "data-agent-run")

    const ids = runParts.map((c) => c.id)
    expect(ids).toEqual(["agent-run-act-a", "agent-run-act-b", "agent-run-act-a"])

    // Same sequence_number on different responses must not cross-dedupe.
    const finalA = runParts
      .filter((c) => c.id === "agent-run-act-a")
      .at(-1)!.data as { text: string; responseId: string }
    const finalB = runParts
      .filter((c) => c.id === "agent-run-act-b")
      .at(-1)!.data as { text: string; responseId: string }
    expect(finalA).toMatchObject({ responseId: "resp-a", text: "AA2" })
    expect(finalB).toMatchObject({ responseId: "resp-b", text: "B" })
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

  it("emits turn-pending reasoning before the first orchestrator delta on reused sessions", async () => {
    stubTurnStream([
      frame({ topic: "events", messageStart: { role: "assistant" } }),
      frame({ topic: "events", contentBlockDelta: { delta: { text: "hello" } } }),
      frame({ done: true, reply: "hello" }),
    ])

    const chunks = parseChunks(await (await postTurn()).text())

    expect(chunks[0]).toEqual({ type: "reasoning-start", id: "turn-pending" })
    const pendingEnd = chunks.findIndex(
      (c) => c.type === "reasoning-end" && c.id === "turn-pending"
    )
    const firstText = chunks.findIndex((c) => c.type === "text-delta")
    expect(pendingEnd).toBeGreaterThan(-1)
    expect(firstText).toBeGreaterThan(pendingEnd)
  })

  it("maps use_skill thinking-topic frames to reasoning-delta", async () => {
    stubTurnStream([
      frame({
        topic: "thinking",
        tool_use: { name: "use_skill", toolUseId: "skill-run-1" },
        data: { skill_name: "demo-skill", text: "Sub-agent chunk" },
      }),
      frame({ done: true, reply: "Done" }),
    ])

    const chunks = parseChunks(await (await postTurn()).text())
    const reasoning = chunks.filter((c) => c.type === "reasoning-delta")
    expect(reasoning.some((c) => String(c.delta).includes("demo-skill"))).toBe(true)
    expect(reasoning.some((c) => String(c.delta).includes("Sub-agent chunk"))).toBe(true)
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

  it("streams Gemini reasoningContent and computer-use intent before tool parts", async () => {
    stubTurnStream([
      frame({ topic: "events", messageStart: { role: "assistant" } }),
      frame({
        topic: "events",
        contentBlockDelta: {
          delta: { reasoningContent: { text: "I'll open the site first." } },
        },
      }),
      frame({ topic: "events", contentBlockStop: {} }),
      frame({
        topic: "events",
        contentBlockStart: {
          start: { toolUse: { name: "navigate", toolUseId: "cu-nav-1" } },
        },
      }),
      frame({
        topic: "events",
        contentBlockDelta: {
          delta: {
            toolUse: {
              input: JSON.stringify({
                url: "https://example.com",
                intent: "Navigate to the homepage",
              }),
            },
          },
        },
      }),
      frame({ topic: "events", contentBlockStop: {} }),
      frame({
        topic: "tool_results",
        tool_use_id: "cu-nav-1",
        status: "success",
        content: [{ text: '{"action":"navigate","url":"https://example.com"}' }],
      }),
      frame({ topic: "events", messageStop: { stopReason: "tool_use" } }),
      frame({ done: true, reply: "" }),
    ])

    const chunks = parseChunks(await (await postTurn()).text())
    const reasoningById = new Map<string, string>()
    for (const chunk of chunks) {
      if (chunk.type === "reasoning-delta") {
        const id = chunk.id as string
        reasoningById.set(id, (reasoningById.get(id) ?? "") + (chunk.delta as string))
      }
    }
    expect([...reasoningById.values()]).toEqual([
      "I'll open the site first.",
      "Navigate to the homepage\n",
    ])

    const toolStartIdx = chunks.findIndex((c) => c.type === "tool-input-start")
    const firstReasoningIdx = chunks.findIndex((c) => c.type === "reasoning-start")
    expect(firstReasoningIdx).toBeGreaterThanOrEqual(0)
    expect(toolStartIdx).toBeGreaterThan(firstReasoningIdx)

    const startIds = chunks
      .filter((c) => c.type === "reasoning-start")
      .map((c) => c.id)
    const endIds = chunks.filter((c) => c.type === "reasoning-end").map((c) => c.id)
    expect([...endIds].sort()).toEqual([...startIds].sort())
  })
})

describe("POST multimodal attachments", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("forwards image, document, and video file parts on the turn body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(`data: ${JSON.stringify({ done: true, reply: "ok" })}\n\n`, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })
    )
    vi.stubGlobal("fetch", fetchMock)

    await POST(
      new Request("http://localhost/api/orchestrator", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId: "session-1",
          messages: [
            {
              id: "message-1",
              role: "user",
              parts: [
                { type: "text", text: "What is this?" },
                {
                  type: "file",
                  mediaType: "image/png",
                  url: "data:image/png;base64,aaa",
                },
                {
                  type: "file",
                  mediaType: "application/pdf",
                  url: "data:application/pdf;base64,bbb",
                },
                {
                  type: "file",
                  mediaType: "video/mp4",
                  url: "data:video/mp4;base64,ccc",
                },
              ],
            },
          ],
        }),
      })
    )

    const turnCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/turns/stream")
    )
    expect(turnCall).toBeDefined()
    const body = JSON.parse((turnCall?.[1] as RequestInit).body as string)
    expect(body.prompt).toBe("What is this?")
    expect(body.images).toEqual([{ format: "png", data: "aaa" }])
    expect(body.documents).toEqual([{ format: "pdf", data: "bbb" }])
    expect(body.videos).toEqual([{ format: "mp4", data: "ccc" }])
  })
})

describe("POST Gemini Google Maps grounding", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("forwards google_maps widget token and places as a data-native-tool part", async () => {
    const maps = {
      type: "google_maps",
      google_maps_widget_context_token: "tok_1",
      places: [
        {
          title: "Caffe Trieste",
          uri: "https://maps.google.com/?cid=1",
          placeId: "places/ChIJ123",
        },
      ],
    }
    const upstream =
      [
        `data: ${JSON.stringify({ topic: "events", gemini: maps })}`,
        `data: ${JSON.stringify({ done: true, reply: "ok" })}`,
      ].join("\n\n") + "\n\n"
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
              parts: [{ type: "text", text: "Coffee nearby?" }],
            },
          ],
        }),
      })
    )
    const chunks = (await response.text())
      .split("\n")
      .filter((line) => line.startsWith("data: ") && !line.includes("[DONE]"))
      .map((line) => JSON.parse(line.slice("data: ".length))) as Array<
      Record<string, unknown>
    >
    const native = chunks.filter((c) => c.type === "data-native-tool")
    expect(native).toEqual([
      { type: "data-native-tool", id: "gemini-google_maps-0", data: maps },
    ])
  })

  it("forwards google_search queries, results, and images as a data-native-tool part", async () => {
    const search = {
      type: "google_search",
      queries: ["best espresso north beach"],
      results: [
        {
          title: "Caffe Trieste",
          uri: "https://www.caffetrieste.com/",
        },
      ],
      images: [
        {
          title: "Espresso",
          image_uri: "https://example.com/espresso.jpg",
          source_uri: "https://example.com/",
        },
      ],
    }
    const upstream =
      [
        `data: ${JSON.stringify({ topic: "events", gemini: search })}`,
        `data: ${JSON.stringify({ done: true, reply: "ok" })}`,
      ].join("\n\n") + "\n\n"
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
              parts: [{ type: "text", text: "Coffee nearby?" }],
            },
          ],
        }),
      })
    )
    const chunks = (await response.text())
      .split("\n")
      .filter((line) => line.startsWith("data: ") && !line.includes("[DONE]"))
      .map((line) => JSON.parse(line.slice("data: ".length))) as Array<
      Record<string, unknown>
    >
    const native = chunks.filter((c) => c.type === "data-native-tool")
    expect(native).toEqual([
      { type: "data-native-tool", id: "gemini-google_search-0", data: search },
    ])
  })
})
