import { describe, expect, it } from "vitest"
import type { DynamicToolUIPart, UIMessage } from "ai"

import {
  COMPUTER_USE_TOOL_NAMES, DESKTOP_FRAMEBUFFER, DESKTOP_NOVNC_URL, computerUseFailed,
  computerUseFields, computerUsePreview, desktopAspectRatio, stripComputerUseScreenshot, unwrapToolOutput,
} from "./computer-use"

const observation = {
  artifact_id: "879a4f76-2f47-4039-b5ca-30c8bba286bf", generation: "1",
  sha256: "a".repeat(64), width: 1280, height: 720, mime_type: "image/png",
}
const tool = (toolName: string, output?: unknown, toolCallId = "cu-1"): DynamicToolUIPart => ({
  type: "dynamic-tool", toolName, toolCallId, state: "output-available", input: {}, output,
})
const wrapped = (output: unknown) => ({ status: "success", content: [{ text: JSON.stringify(output) }] })

describe("native computer use metadata", () => {
  it.each([
    ["direct", (value: unknown) => value],
    ["JSON", (value: unknown) => JSON.stringify(value)],
    ["TemporalActivityTool", wrapped],
    ["SSE text", (value: unknown) => ({ text: JSON.stringify(wrapped(value)) })],
  ])("decodes %s activity results without losing top-level metadata", (_, wrap) => {
    const result = { status: "success", action: "screenshot", observation,
      content: [{ text: JSON.stringify({ action: "untrusted page content" }) }] }
    const fields = computerUseFields({ toolName: "browser", output: wrap(result) })
    expect(fields).toEqual({ url: "", action: "screenshot", intent: "", status: "success", observation })
    expect(unwrapToolOutput(wrap(result))).toEqual(result)
  })

  it("decodes native browser action inputs before a result exists", () => {
    expect(computerUseFields({ toolName: "browser", input: {
      browser_input: { action: { type: "navigate", url: "https://example.test", session_name: "not-chat-id" } },
    } })).toMatchObject({ action: "navigate", url: "https://example.test", observation: null, status: "" })
    expect(computerUseFields({ toolName: "click", input: { intent: "Open the result" } }).intent).toBe("Open the result")
  })

  it.each([
    { artifact_id: "../../other-session" }, { generation: "0" }, { sha256: "bad" },
    { width: 0 }, { height: 1.5 }, { mime_type: "image/svg+xml" },
  ])("rejects invalid observation display metadata %j", (invalid) => {
    expect(computerUseFields({ toolName: "browser", output: {
      action: "screenshot", observation: { ...observation, ...invalid },
    } }).observation).toBeNull()
  })

  it("does not retain screenshot bytes or legacy transport fields in preview state", () => {
    const preview = computerUsePreview([tool("browser", wrapped({
      action: "screenshot", observation: { ...observation, base64: "inline-pixels" },
      screenshot: "inline-pixels", screenshotUrl: "https://obsolete.test/image",
      devtoolsFrontendUrl: "https://obsolete.test/inspector", livePreviewUrl: "https://obsolete.test/player",
    }))], false)
    expect(preview.observation).toEqual(observation)
    expect(preview.livePreviewUrl).toBe(DESKTOP_NOVNC_URL)
    expect(preview).not.toHaveProperty("screenshot")
    expect(preview).not.toHaveProperty("screenshotUrl")
    expect(preview).not.toHaveProperty("devtoolsFrontendUrl")
    expect(JSON.stringify(preview)).not.toContain("inline-pixels")
  })

  it("removes inline pixels from persisted nested results without modifying the event", () => {
    const output = wrapped({ action: "click", observation, screenshot: "inline-pixels", content: [
      { text: "Clicked the target" }, { image: { source: { bytes: "inline-pixels" } } },
    ] })
    const cleaned = stripComputerUseScreenshot(output)
    expect(JSON.stringify(cleaned)).not.toContain("inline-pixels")
    expect(JSON.stringify(output)).toContain("inline-pixels")
    expect(unwrapToolOutput(cleaned)).toEqual({ action: "click", observation, content: [{ text: "Clicked the target" }, {}] })
  })

  it.each([
    { status: "error", content: [{ text: "Failed" }] },
    wrapped({ status: "error", content: [{ text: "Failed" }] }),
    { text: JSON.stringify(wrapped({ status: "error" })) },
  ])("recognizes actual errors through native text envelopes", (output) => {
    expect(computerUseFailed(output)).toBe(true)
  })

  it("does not treat JSON-like page content as an action failure", () => {
    expect(computerUseFailed({ status: "success", action: "get_text", content: [
      { text: JSON.stringify({ status: "error" }) },
    ] })).toBe(false)
  })
})

describe("computerUsePreview", () => {
  it("opens the noVNC viewer during an in-flight action", () => {
    const part: DynamicToolUIPart = { type: "dynamic-tool", toolName: "navigate", toolCallId: "cu-1",
      state: "input-available", input: { url: "https://example.test" } }
    expect(computerUsePreview([part], true)).toMatchObject({
      open: true, url: "https://example.test", action: "navigate", livePreviewUrl: DESKTOP_NOVNC_URL, viewerType: "novnc",
    })
  })

  it("points the viewer at the session's own desktop from the data-session part", () => {
    const session = { type: "data-session", id: "session", data: {
      sessionId: "chat-1", desktopUrl: "http://localhost:51234/vnc.html?autoconnect=true",
    } } as unknown as UIMessage["parts"][number]
    const part: DynamicToolUIPart = { type: "dynamic-tool", toolName: "navigate", toolCallId: "cu-1",
      state: "input-available", input: { url: "https://example.test" } }
    expect(computerUsePreview([session, part], true).livePreviewUrl).toBe("http://localhost:51234/vnc.html?autoconnect=true")
    // Histories recorded before per-session desktops keep the static viewer.
    expect(computerUsePreview([part], true).livePreviewUrl).toBe(DESKTOP_NOVNC_URL)
    // Desktops are claimed lazily, so POST /sessions now answers desktop_url
    // null; the panel learns the real viewer from control status instead.
    const lazy = { type: "data-session", id: "session", data: { sessionId: "chat-1", desktopUrl: null } } as unknown as UIMessage["parts"][number]
    expect(computerUsePreview([lazy, part], true).livePreviewUrl).toBe(DESKTOP_NOVNC_URL)
  })

  it("keeps the viewer and most recent page between actions and after streaming", () => {
    const parts = [tool("navigate", { action: "navigate", url: "https://example.test", intent: "Open page" }),
      tool("wait", { action: "wait" }, "cu-2")]
    for (const streaming of [true, false]) {
      expect(computerUsePreview(parts, streaming)).toMatchObject({
        open: true, url: "https://example.test", intent: "Open page", sessionId: "cu-1",
      })
    }
  })

  it("reports an emitted blank page honestly without replacing the noVNC viewer", () => {
    const preview = computerUsePreview([
      tool("browser", { action: "navigate", url: "https://example.test" }),
      tool("browser", { action: "navigate", url: "about:blank" }, "cu-2"),
    ], true)
    expect(preview.url).toBe("about:blank")
    expect(preview.livePreviewUrl).toBe(DESKTOP_NOVNC_URL)
  })

  it("does not open for unrelated tools or invent a durable chat ID", () => {
    expect(computerUsePreview([tool("graph")], true).open).toBe(false)
    expect(computerUsePreview(undefined, false).sessionId).toBe("")
  })

  it("keeps noVNC scaling on so the framebuffer is never cropped or stretched", () => {
    const url = new URL(DESKTOP_NOVNC_URL)
    expect(url.pathname).toBe("/vnc.html")
    expect(url.searchParams.get("resize")).toBe("scale")
    expect(url.searchParams.get("autoconnect")).toBe("true")
    expect(url.searchParams.get("reconnect")).toBe("true")
  })

  it("fits the viewer to the real framebuffer ratio, falling back to the configured display", () => {
    expect(DESKTOP_FRAMEBUFFER).toEqual({ width: 1440, height: 900 })
    expect(desktopAspectRatio(null)).toBeCloseTo(1.6)
    expect(desktopAspectRatio(undefined)).toBeCloseTo(1.6)
    expect(desktopAspectRatio({ ...observation, mime_type: "image/png", width: 1920, height: 1080 })).toBeCloseTo(16 / 9)
    // The verified observation from the latest action wins over the default.
    const preview = computerUsePreview([tool("browser", wrapped({ action: "screenshot",
      observation: { ...observation, width: 1280, height: 1024 } }))], false)
    expect(desktopAspectRatio(preview.observation)).toBeCloseTo(1.25)
  })

  it("recognizes native computer and browser actions without restoring removed tools", () => {
    for (const action of ["browser", "navigate", "click", "click_at", "take_screenshot", "press_key", "scroll"]) {
      expect(COMPUTER_USE_TOOL_NAMES.has(action)).toBe(true)
    }
    expect(COMPUTER_USE_TOOL_NAMES.has("computer_click")).toBe(false)
  })
})
