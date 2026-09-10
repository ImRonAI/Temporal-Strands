import { isDynamicToolUIPart, isReasoningUIPart, type DynamicToolUIPart, type UIMessage } from "ai"

/** Gemini 3 Computer Use actions plus 2.5 legacy names, and the stock
 *  strands browser tool name (any provider).
 *  https://ai.google.dev/gemini-api/docs/computer-use
 */
export const COMPUTER_USE_TOOL_NAMES = new Set([
  "browser",
  "click",
  "double_click",
  "triple_click",
  "middle_click",
  "right_click",
  "mouse_down",
  "mouse_up",
  "move",
  "type",
  "drag_and_drop",
  "wait",
  "press_key",
  "key_down",
  "key_up",
  "hotkey",
  "take_screenshot",
  "scroll",
  "go_back",
  "navigate",
  "go_forward",
  "click_at",
  "hover_at",
  "type_text_at",
  "key_combination",
  "scroll_at",
  "scroll_document",
  "open_web_browser",
  "wait_5_seconds",
])

/** noVNC viewer for the Linux desktop the stock strands browser runs on.
 *  View-only is x11vnc's default; takeover is server-toggled on handoff. */
export const DESKTOP_NOVNC_URL =
  process.env.NEXT_PUBLIC_DESKTOP_NOVNC_URL ??
  "http://localhost:6080/vnc.html?autoconnect=true&resize=scale&reconnect=true"

export type ComputerUsePreview = {
  open: boolean
  /** Timeline panel identity, not the durable chat session ID. */
  sessionId: string
  url: string
  livePreviewUrl: string
  action: string
  intent: string
  observation: ComputerUseObservation | null
  viewerType?: "novnc"
  controlAvailable?: boolean
}

/** Display metadata only; the server verifies artifact scope and integrity. */
export type ComputerUseObservation = {
  artifact_id: string
  generation: string
  sha256: string
  width: number
  height: number
  mime_type: "image/png" | "image/jpeg"
}

const EMPTY: ComputerUsePreview = {
  open: false,
  sessionId: "",
  url: "",
  livePreviewUrl: "",
  action: "",
  intent: "",
  observation: null,
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed.startsWith("{")) return null
    try {
      const parsed: unknown = JSON.parse(trimmed)
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
    } catch {
      return null
    }
  }
  return null
}

function stringField(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key]
  return typeof value === "string" ? value : ""
}

/** Read the native activity result, including reference-only screenshot metadata. */
export function computerUseFields(part: {
  toolName: string
  input?: unknown
  output?: unknown
}): {
  url: string
  action: string
  intent: string
  status: string
  observation: ComputerUseObservation | null
} {
  const rawInput = asRecord(part.input)
  const input = part.toolName === "browser"
    ? asRecord(asRecord(rawInput?.browser_input)?.action) ?? rawInput
    : rawInput
  const output = unwrapToolOutput(part.output)
  const url = stringField(output, "url") || stringField(input, "url")
  const intent = stringField(output, "intent") || stringField(input, "intent")
  const action = stringField(output, "action") || stringField(input, "type") || part.toolName
  const status = stringField(output, "status")
  const raw = asRecord(output?.observation)
  const artifact_id = stringField(raw, "artifact_id")
  const generation = stringField(raw, "generation")
  const sha256 = stringField(raw, "sha256")
  const width = raw?.width
  const height = raw?.height
  const mime_type = raw?.mime_type
  const observation: ComputerUseObservation | null =
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(artifact_id) &&
    /^[1-9][0-9]*$/.test(generation) && /^[0-9a-f]{64}$/.test(sha256) &&
    typeof width === "number" && Number.isSafeInteger(width) && width > 0 &&
    typeof height === "number" && Number.isSafeInteger(height) && height > 0 &&
    (mime_type === "image/png" || mime_type === "image/jpeg")
      ? { artifact_id, generation, sha256, width, height, mime_type }
      : null
  return { url, action, intent, status, observation }
}

/** Unwrap Computer Use payloads from tool-output-available shapes. */
export function unwrapToolOutput(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value)
  if (!record) return null
  // Metadata on the activity itself wins over JSON-looking page content.
  if ("action" in record || "observation" in record || record.status === "error") return record

  // TemporalActivityTool JSON-stringifies the whole activity return value:
  // {"status":"success","content":[{"text":"{\"action\":...}"}]}
  const blocks = record.content
  if (Array.isArray(blocks)) {
    for (const block of blocks) {
      const nested = asRecord(block)
      if (!nested) continue
      const fromText = asRecord(nested.text)
      if (fromText) return unwrapToolOutput(fromText)
    }
  }

  if (typeof record.text === "string") {
    const nested = asRecord(record.text)
    if (nested) return unwrapToolOutput(nested)
  }
  return record
}

/** Latest Computer Use preview for the conversation. The browser tool runs
 *  only on the Linux desktop, so any browser/computer-use part opens the
 *  noVNC viewer. */
export function computerUsePreview(
  parts: UIMessage["parts"] | undefined,
  _isStreaming: boolean
): ComputerUsePreview {
  if (!parts?.length) return EMPTY

  let latest: ComputerUsePreview | null = null
  let panelId = ""

  for (const part of parts) {
    if (!isDynamicToolUIPart(part)) continue
    if (part.toolName !== "browser" && !COMPUTER_USE_TOOL_NAMES.has(part.toolName)) continue
    const fields = computerUseFields(part)
    if (!panelId) panelId = part.toolCallId
    const previous: ComputerUsePreview = latest ?? EMPTY
    latest = {
      open: false,
      sessionId: panelId,
      url: fields.url || previous.url,
      livePreviewUrl: DESKTOP_NOVNC_URL,
      action: fields.action,
      intent: fields.intent || previous.intent,
      observation: fields.observation ?? previous.observation,
      viewerType: "novnc",
      controlAvailable: true,
    }
  }

  if (!latest) return EMPTY
  latest.open = true
  return latest
}

export function stripComputerUseScreenshot(output: unknown): unknown {
  if (Array.isArray(output)) return output.map(stripComputerUseScreenshot)
  const record = asRecord(output)
  if (!record) return output
  // Older persisted tool results may contain inline pixels. Keep their other
  // arguments/results, but never render those bytes in the activity history.
  const rest = Object.fromEntries(Object.entries(record)
    .filter(([key]) => key !== "screenshot" && key !== "image" && key !== "base64")
    .map(([key, value]) => [key, stripComputerUseScreenshot(value)]))
  return typeof output === "string" ? JSON.stringify(rest) : rest
}

export function computerUseFailed(output: unknown): boolean {
  const record = asRecord(output)
  if (!record) return false
  if (record.status === "error") return true
  return unwrapToolOutput(record)?.status === "error"
}

/** Inspect native result envelopes, including Temporal's serialized result.
 * Keep all content blocks; arbitrary embedded page strings are not envelopes.
 */
export function toolPresentation(part: DynamicToolUIPart): {
  output: unknown; error: string | undefined; state: DynamicToolUIPart["state"]; text: string; terminal: boolean
} {
  let output: unknown = "output" in part
    ? COMPUTER_USE_TOOL_NAMES.has(part.toolName) ? stripComputerUseScreenshot(part.output) : part.output
    : undefined
  let failed = false
  for (let depth = 0; depth < 8; depth++) {
    const record = asRecord(output)
    if (!record) break
    failed ||= record.status === "error" || record.isError === true
    const content = record.content
    if (!Array.isArray(content) || content.length !== 1) break
    const block = asRecord(content[0])
    const inner = asRecord(block?.text) ?? asRecord(block?.json)
    if (!inner || !("content" in inner || "status" in inner || "isError" in inner)) break
    output = inner
  }
  const record = asRecord(output)
  failed ||= record?.status === "error" || record?.isError === true
  const blocks = Array.isArray(record?.content) ? record.content : []
  const text = blocks.map(block => {
    const item = asRecord(block)
    return typeof item?.text === "string" ? item.text : ""
  }).filter(Boolean).join("\n\n") || (typeof output === "string" ? output
    : Array.isArray(output) ? output.filter(value => typeof value === "string").join("\n\n")
    : typeof record?.notes === "string" ? record.notes : "")
  let error = part.state === "output-error" ? part.errorText : undefined
  if (part.state === "output-denied") error = "Tool execution was denied."
  if (failed && !error) {
    const detail = record?.error
    error = text || (typeof detail === "string" ? detail : undefined)
      || (typeof asRecord(detail)?.message === "string" ? String(asRecord(detail)?.message) : undefined)
      || (typeof record?.message === "string" ? record.message : undefined)
      || "Tool reported an error without a message."
  }
  return { output, text, error,
    state: part.state === "output-available" && failed ? "output-error" : part.state,
    terminal: ["output-available", "output-error", "output-denied"].includes(part.state) }
}

/** Hide only conclusions already shown in the live reasoning stream. */
export function thinkSummaryWasStreamed(text: string, parts: UIMessage["parts"]): boolean {
  const normalize = (value: string) => value.replace(/Cycle \d+\/\d+:\s*/g, "").replace(/\s+/g, " ").trim()
  const summary = normalize(text)
  const reasoning = normalize(parts.filter(isReasoningUIPart).map(part => part.text).join(" "))
  return !!summary && reasoning.includes(summary)
}
