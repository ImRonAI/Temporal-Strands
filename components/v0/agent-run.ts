// Pure helpers for the Agent API preset sub-agent runs: tool grouping,
// run-to-chain binding, and run-event projection. No React, no DOM — this is
// what the scoped Vitest suite covers (see agent-run.test.ts), while
// agent-activity.tsx composes the results into AI Elements.

import type { DynamicToolUIPart } from "ai"

// The six preset create tools registered by the orchestrator
// (orchestrator plan: one activity per documented dynamic preset). The
// frontend keys grouping and run binding off these exact names — they must
// stay in lockstep with the activity names in orchestrator/run_worker.py.
export const AGENT_CREATE_TOOL_NAMES = [
  "create_fast_agent_response",
  "create_low_agent_response",
  "create_medium_agent_response",
  "create_high_agent_response",
  "create_xhigh_agent_response",
  "create_wide_research_agent_response",
] as const

export const AGENT_CREATE_TOOL_SET: ReadonlySet<string> = new Set(
  AGENT_CREATE_TOOL_NAMES
)

// One reconciled `data-agent-run` part per preset run, accumulated by
// app/api/orchestrator/route.ts from the backend's `agent_runs` SSE topic.
// Events are the verbatim Agent API stream events, ordered by the API's own
// sequence_number and deduplicated by (response_id, sequence_number).
export type AgentRunEventEntry = {
  sequence: number
  event: Record<string, unknown>
}

export type AgentRunSnapshot = {
  activity: string
  activityId: string
  preset: string
  responseId: string | null
  attempt: number
  status: string
  model: string | null
  text: string
  error: string | null
  events: AgentRunEventEntry[]
}

export function parseJson(value: unknown): Record<string, unknown> | null {
  // Tool output is already an object whenever the Strands content block
  // carried `json` — app/api/orchestrator/route.ts writes `c.json` straight
  // through. Only string-testing it returned null for exactly those cases.
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  if (typeof value !== "string" || !value) return null
  try {
    const parsed = JSON.parse(value)
    return typeof parsed === "object" && parsed !== null ? parsed : null
  } catch {
    return null
  }
}

// "input" is present on every DynamicToolUIPart state except
// "input-streaming" (including "output-error" — failed calls must still show
// what they tried).
export function partInput(
  part: DynamicToolUIPart
): Record<string, unknown> | undefined {
  return "input" in part ? (part.input as Record<string, unknown>) : undefined
}

export function partOutputJson(
  part: DynamicToolUIPart
): Record<string, unknown> | null {
  if (part.state !== "output-available") return null
  return parseJson(part.output)
}

// One preset create call plus every retrieve/list/download call that shares
// its response id — grouped so the UI shows ONE live-updating sub-agent card.
export interface AgentChain {
  key: string
  toolName: string
  create: DynamicToolUIPart
  polls: DynamicToolUIPart[]
  downloads: DynamicToolUIPart[]
}

// The authoritative lifecycle key: the response id in the preset tool's
// terminal result (plan: activity name is only provisional correlation).
export function chainResponseId(chain: AgentChain): string | null {
  const json = partOutputJson(chain.create)
  const id = json?.id ?? json?.response_id
  return typeof id === "string" ? id : null
}

export function groupToolParts(toolParts: DynamicToolUIPart[]) {
  const chains: AgentChain[] = []
  const chainByResponseId = new Map<string, AgentChain>()
  const standalone: DynamicToolUIPart[] = []

  for (const part of toolParts) {
    if (AGENT_CREATE_TOOL_SET.has(part.toolName)) {
      const chain: AgentChain = {
        key: part.toolCallId,
        toolName: part.toolName,
        create: part,
        polls: [],
        downloads: [],
      }
      chains.push(chain)
      const responseId = chainResponseId(chain)
      if (responseId) chainByResponseId.set(responseId, chain)
      continue
    }

    // retrieve_agent_response / list_agent_response_files /
    // download_agent_response_file attach by their response_id argument.
    const responseId = partInput(part)?.response_id
    const chain =
      typeof responseId === "string"
        ? chainByResponseId.get(responseId)
        : undefined
    if (!chain) {
      standalone.push(part)
      continue
    }
    if (part.toolName === "download_agent_response_file") {
      chain.downloads.push(part)
    } else {
      chain.polls.push(part)
    }
  }

  const chainByCreateId = new Map(chains.map((c) => [c.create.toolCallId, c]))
  const absorbedIds = new Set(
    chains.flatMap((c) => [...c.polls, ...c.downloads].map((p) => p.toolCallId))
  )

  return { chains, standalone, chainByCreateId, absorbedIds }
}

// Associate each live run snapshot with its create tool call. Authoritative
// binding is by response id (the create tool's terminal result carries it);
// while the call is still streaming, provisional binding matches the preset
// activity name against unclaimed runs in arrival order — interleaved runs of
// DIFFERENT presets can never cross-bind, and same-preset runs bind in order
// until their terminal results arrive to disambiguate.
export function bindRunsToChains(
  chains: AgentChain[],
  runs: AgentRunSnapshot[]
): Map<string, AgentRunSnapshot> {
  const byChainKey = new Map<string, AgentRunSnapshot>()
  const claimed = new Set<AgentRunSnapshot>()

  for (const chain of chains) {
    const responseId = chainResponseId(chain)
    if (!responseId) continue
    const run = runs.find((r) => r.responseId === responseId)
    if (run) {
      byChainKey.set(chain.key, run)
      claimed.add(run)
    }
  }

  for (const chain of chains) {
    if (byChainKey.has(chain.key)) continue
    const run = runs.find(
      (r) => !claimed.has(r) && r.activity === chain.toolName
    )
    if (run) {
      byChainKey.set(chain.key, run)
      claimed.add(run)
    }
  }

  return byChainKey
}

// The Agent API output-item / reasoning-event types the existing
// NativeToolStep renderer already understands. Run events project onto that
// same union so the nested timeline reuses the exact renderers the outer
// chain uses.
const RUN_NATIVE_ITEM_TYPES: ReadonlySet<string> = new Set([
  "search_results",
  "people_search_results",
  "finance_results",
  "fetch_url_results",
  "sandbox_results",
  "sandbox_glob",
  "sandbox_grep",
  "sandbox_read_file",
  "sandbox_write_file",
  "sandbox_edit_file",
  "sandbox_apply_patch",
  "mcp_list_tools",
  "mcp_call",
  "share_file",
  "skill_loaded",
])

export function nativeFromRunEvent(
  event: Record<string, unknown>
): ({ type: string } & Record<string, unknown>) | null {
  const type = typeof event.type === "string" ? event.type : ""
  if (
    type.startsWith("response.reasoning.") ||
    type === "response.skill.loaded"
  ) {
    return event as { type: string } & Record<string, unknown>
  }
  // Output items render once, on .done — .added would duplicate them.
  if (type === "response.output_item.done") {
    const item = event.item
    if (
      typeof item === "object" &&
      item !== null &&
      typeof (item as { type?: unknown }).type === "string" &&
      RUN_NATIVE_ITEM_TYPES.has((item as { type: string }).type)
    ) {
      return item as { type: string } & Record<string, unknown>
    }
  }
  return null
}

function isReasoningDelta(event: Record<string, unknown>): string | null {
  const type = typeof event.type === "string" ? event.type : ""
  if (/reasoning.*\.delta$/.test(type) && typeof event.delta === "string") {
    return event.delta as string
  }
  return null
}

// share_file items inside run events carry the Agent API's own relative file
// path, which the browser cannot call (it needs the API key). Point it at the
// orchestrator's proxy instead — same rewrite route.ts applies to the outer
// turn's data-native-tool share_file parts.
export function proxyFileUrl(url: string): string {
  if (url.startsWith("/api/orchestrator/file")) return url
  return `/api/orchestrator/file?path=${encodeURIComponent(url)}`
}

// The nested run timeline, in arrival (sequence) order: contiguous reasoning
// deltas coalesce into one text block; recognized native output items and
// reasoning/skill events map onto the same native union the outer chain's
// renderers already understand. Keys are stable across snapshot updates so
// React reconciles in place while the run streams.
export type RunTimelineEntry =
  | { kind: "reasoning"; key: string; text: string }
  | {
      kind: "native"
      key: string
      native: { type: string } & Record<string, unknown>
    }

export function projectRunTimeline(
  events: AgentRunEventEntry[]
): RunTimelineEntry[] {
  const timeline: RunTimelineEntry[] = []
  let reasoningBlock: { kind: "reasoning"; key: string; text: string } | null =
    null

  for (const { sequence, event } of events) {
    const delta = isReasoningDelta(event)
    if (delta !== null) {
      if (!reasoningBlock) {
        reasoningBlock = {
          kind: "reasoning",
          key: `reasoning-${sequence}`,
          text: "",
        }
        timeline.push(reasoningBlock)
      }
      reasoningBlock.text += delta
      continue
    }

    const native = nativeFromRunEvent(event)
    if (!native) continue
    reasoningBlock = null
    let key = `native-${sequence}`
    if (native.type === "sandbox_results" || native.type === "share_file") {
      if (typeof native.call_id === "string") key = `${native.type}-${native.call_id}`
    } else if (native.type === "mcp_call" || native.type === "mcp_list_tools") {
      if (typeof native.id === "string") key = `${native.type}-${native.id}`
    }
    const rewritten =
      native.type === "share_file" && typeof native.url === "string"
        ? { ...native, url: proxyFileUrl(native.url) }
        : native
    timeline.push({ kind: "native", key, native: rewritten })
  }

  return timeline
}

// Download tool results are metadata plus a browser-safe proxy URL — never
// base64 bytes (plan: binary content stays outside Temporal payloads).
export type DownloadFileMeta = {
  filename: string
  url: string | null
  contentType: string | null
  sizeBytes: number | null
}

export function downloadFileMeta(
  json: Record<string, unknown> | null
): DownloadFileMeta | null {
  if (!json) return null
  const filename =
    typeof json.filename === "string" ? json.filename : "file"
  const url = typeof json.url === "string" ? json.url : null
  const contentType =
    typeof json.content_type === "string" ? json.content_type : null
  const sizeBytes =
    typeof json.size_bytes === "number"
      ? json.size_bytes
      : typeof json.bytes === "number"
        ? json.bytes
        : null
  if (!url && !contentType) return null
  return { filename, url, contentType, sizeBytes }
}

const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "cancelled",
  "incomplete",
])

export function isTerminalRunStatus(status: string): boolean {
  return TERMINAL_RUN_STATUSES.has(status)
}
