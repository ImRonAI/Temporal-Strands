import { isDynamicToolUIPart, type UIMessage } from "ai"

/** Gemini 3 Computer Use actions plus 2.5 legacy names.
 *  https://ai.google.dev/gemini-api/docs/computer-use
 */
export const COMPUTER_USE_TOOL_NAMES = new Set([
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

export type ComputerUsePreview = {
  open: boolean
  sessionId: string
  url: string
  livePreviewUrl: string
  devtoolsFrontendUrl: string
  action: string
  intent: string
  screenshot: { base64: string; mediaType: string } | null
}

const EMPTY: ComputerUsePreview = {
  open: false,
  sessionId: "",
  url: "",
  livePreviewUrl: "",
  devtoolsFrontendUrl: "",
  action: "",
  intent: "",
  screenshot: null,
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

/** Pull url / screenshot / intent out of a Computer Use tool part. */
export function computerUseFields(part: {
  toolName: string
  input?: unknown
  output?: unknown
}): {
  url: string
  livePreviewUrl: string
  devtoolsFrontendUrl: string
  action: string
  intent: string
  screenshot: { base64: string; mediaType: string } | null
} {
  const input = asRecord(part.input)
  const output = unwrapToolOutput(part.output)
  const url = stringField(output, "url") || stringField(input, "url")
  const livePreviewUrl = stringField(output, "livePreviewUrl")
  const devtoolsFrontendUrl = stringField(output, "devtoolsFrontendUrl")
  const intent = stringField(output, "intent") || stringField(input, "intent")
  const action = stringField(output, "action") || part.toolName
  const raw = output?.screenshot
  const mediaType = stringField(output, "mediaType") || "image/jpeg"
  const screenshot =
    typeof raw === "string" && raw.length > 0
      ? { base64: raw, mediaType }
      : null
  return { url, livePreviewUrl, devtoolsFrontendUrl, action, intent, screenshot }
}

/** Unwrap Computer Use payloads from tool-output-available shapes. */
export function unwrapToolOutput(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value)
  if (!record) return null

  // TemporalActivityTool JSON-stringifies the whole activity return value:
  // {"status":"success","content":[{"text":"{\"action\":...}"}]}
  const blocks = record.content
  if (Array.isArray(blocks)) {
    for (const block of blocks) {
      const nested = asRecord(block)
      if (!nested) continue
      const fromText = asRecord(nested.text)
      if (fromText) return fromText
    }
  }

  if (typeof record.text === "string") {
    const nested = asRecord(record.text)
    if (nested) return nested
  }
  return record
}

/** Latest Computer Use preview for the conversation. Opens for the rest of
 *  the streaming turn once any Computer Use action is elicited. */
export function computerUsePreview(
  parts: UIMessage["parts"] | undefined,
  isStreaming: boolean
): ComputerUsePreview {
  if (!parts?.length) return EMPTY

  let latest: ComputerUsePreview | null = null
  let panelId = ""

  for (const part of parts) {
    if (!isDynamicToolUIPart(part)) continue
    if (!COMPUTER_USE_TOOL_NAMES.has(part.toolName)) continue
    const fields = computerUseFields(part)
    if (!panelId) panelId = part.toolCallId
    latest = {
      open: false,
      sessionId: panelId,
      url: fields.url || latest?.url || "",
      livePreviewUrl: fields.livePreviewUrl || latest?.livePreviewUrl || "",
      devtoolsFrontendUrl:
        fields.devtoolsFrontendUrl || latest?.devtoolsFrontendUrl || "",
      action: fields.action,
      intent: fields.intent || latest?.intent || "",
      screenshot: fields.screenshot ?? latest?.screenshot ?? null,
    }
  }

  if (!latest) return EMPTY
  latest.open =
    isStreaming ||
    Boolean(latest.url) ||
    Boolean(latest.livePreviewUrl) ||
    Boolean(latest.devtoolsFrontendUrl)
  return latest
}

export function stripComputerUseScreenshot(output: unknown): unknown {
  const record = asRecord(output)
  if (!record || !("screenshot" in record)) return output
  const { screenshot: _screenshot, ...rest } = record
  return rest
}
