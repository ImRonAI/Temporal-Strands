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
