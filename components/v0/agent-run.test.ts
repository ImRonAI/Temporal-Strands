import { describe, expect, it } from "vitest"

import type { DynamicToolUIPart } from "ai"

import {
  AGENT_CREATE_TOOL_NAMES,
  bindRunsToChains,
  downloadFileMeta,
  groupToolParts,
  projectRunTimeline,
  type AgentRunSnapshot,
} from "./agent-run"

const tool = (
  toolName: string,
  toolCallId: string,
  overrides: Record<string, unknown> = {}
): DynamicToolUIPart =>
  ({
    type: "dynamic-tool",
    toolName,
    toolCallId,
    state: "input-available",
    input: {},
    ...overrides,
  }) as unknown as DynamicToolUIPart

const run = (overrides: Partial<AgentRunSnapshot>): AgentRunSnapshot => ({
  activity: "create_medium_agent_response",
  activityId: "act-1",
  preset: "medium",
  responseId: null,
  attempt: 1,
  status: "in_progress",
  model: null,
  text: "",
  error: null,
  events: [],
  ...overrides,
})

describe("groupToolParts", () => {
  it("groups every one of the six preset create tool names into a chain", () => {
    const parts = AGENT_CREATE_TOOL_NAMES.map((name, i) =>
      tool(name, `call-${i}`)
    )
    const { chains, standalone } = groupToolParts(parts)
    expect(chains.map((c) => c.toolName)).toEqual([...AGENT_CREATE_TOOL_NAMES])
    expect(standalone).toEqual([])
  })

  it("attaches retrieve/list/download to their chain by response_id", () => {
    const create = tool("create_fast_agent_response", "call-create", {
      state: "output-available",
      output: { id: "resp-1", status: "completed" },
    })
    const retrieve = tool("retrieve_agent_response", "call-retrieve", {
      input: { response_id: "resp-1" },
    })
    const list = tool("list_agent_response_files", "call-list", {
      input: { response_id: "resp-1" },
    })
    const download = tool("download_agent_response_file", "call-dl", {
      input: { response_id: "resp-1", file_id: "file-1" },
    })
    const unrelated = tool("retrieve_agent_response", "call-other", {
      input: { response_id: "resp-other" },
    })

    const { chains, standalone, absorbedIds } = groupToolParts([
      create,
      retrieve,
      list,
      download,
      unrelated,
    ])

    expect(chains).toHaveLength(1)
    expect(chains[0].polls.map((p) => p.toolCallId)).toEqual([
      "call-retrieve",
      "call-list",
    ])
    expect(chains[0].downloads.map((p) => p.toolCallId)).toEqual(["call-dl"])
    expect(standalone.map((p) => p.toolCallId)).toEqual(["call-other"])
    expect(absorbedIds).toEqual(
      new Set(["call-retrieve", "call-list", "call-dl"])
    )
  })
})

describe("bindRunsToChains", () => {
  it("binds authoritatively by response id and provisionally by activity name", () => {
    const boundCreate = tool("create_fast_agent_response", "call-a", {
      state: "output-available",
      output: { id: "resp-a" },
    })
    const liveCreate = tool("create_high_agent_response", "call-b")
    const { chains } = groupToolParts([boundCreate, liveCreate])

    const runA = run({
      activityId: "act-a",
      activity: "create_fast_agent_response",
      responseId: "resp-a",
    })
    const runB = run({
      activityId: "act-b",
      activity: "create_high_agent_response",
      preset: "high",
    })

    const bound = bindRunsToChains(chains, [runB, runA])
    expect(bound.get("call-a")).toBe(runA)
    expect(bound.get("call-b")).toBe(runB)
  })

  it("never cross-binds interleaved runs of different presets", () => {
    const fast = tool("create_fast_agent_response", "call-fast")
    const wide = tool("create_wide_research_agent_response", "call-wide")
    const { chains } = groupToolParts([fast, wide])

    const wideRun = run({
      activityId: "act-wide",
      activity: "create_wide_research_agent_response",
    })
    const bound = bindRunsToChains(chains, [wideRun])
    expect(bound.get("call-fast")).toBeUndefined()
    expect(bound.get("call-wide")).toBe(wideRun)
  })
})

describe("projectRunTimeline", () => {
  it("preserves order, coalesces reasoning deltas, and proxies share_file urls", () => {
    const timeline = projectRunTimeline([
      {
        sequence: 1,
        event: { type: "response.reasoning_text.delta", delta: "Think " },
      },
      {
        sequence: 2,
        event: { type: "response.reasoning_text.delta", delta: "hard" },
      },
      {
        sequence: 3,
        event: {
          type: "response.output_item.done",
          item: {
            type: "share_file",
            call_id: "c1",
            filename: "chart.png",
            url: "/v1/agent/resp-1/files/f1/content",
          },
        },
      },
      { sequence: 4, event: { type: "response.skill.loaded", name: "office" } },
    ])

    expect(timeline.map((t) => t.kind)).toEqual([
      "reasoning",
      "native",
      "native",
    ])
    expect(timeline[0]).toMatchObject({ text: "Think hard" })
    expect(timeline[1]).toMatchObject({
      key: "share_file-c1",
      native: {
        type: "share_file",
        url: "/api/orchestrator/file?path=%2Fv1%2Fagent%2Fresp-1%2Ffiles%2Ff1%2Fcontent",
      },
    })
    expect(timeline[2]).toMatchObject({
      native: { type: "response.skill.loaded", name: "office" },
    })
  })
})

describe("downloadFileMeta", () => {
  it("selects proxy url metadata and never base64 content", () => {
    const meta = downloadFileMeta({
      filename: "report.pdf",
      url: "/api/orchestrator/file?path=%2Fv1%2Fagent%2Fr%2Ffiles%2Ff%2Fcontent",
      content_type: "application/pdf",
      size_bytes: 1234,
    })
    expect(meta).toEqual({
      filename: "report.pdf",
      url: "/api/orchestrator/file?path=%2Fv1%2Fagent%2Fr%2Ffiles%2Ff%2Fcontent",
      contentType: "application/pdf",
      sizeBytes: 1234,
    })
    expect(downloadFileMeta({ filename: "x" })).toBeNull()
  })
})
