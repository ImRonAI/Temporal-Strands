import { describe, expect, it } from "vitest"

import type { DynamicToolUIPart } from "ai"

import {
  COMPUTER_USE_TOOL_NAMES,
  computerUseFields,
  computerUsePreview,
  stripComputerUseScreenshot,
} from "./computer-use"

const tool = (
  toolName: string,
  overrides: Record<string, unknown> = {}
): DynamicToolUIPart =>
  ({
    type: "dynamic-tool",
    toolName,
    toolCallId: "cu-1",
    state: "input-available",
    input: {},
    ...overrides,
  }) as unknown as DynamicToolUIPart

describe("computerUsePreview", () => {
  it("lists Gemini Computer Use action names", () => {
    expect(COMPUTER_USE_TOOL_NAMES.has("navigate")).toBe(true)
    expect(COMPUTER_USE_TOOL_NAMES.has("click")).toBe(true)
    expect(COMPUTER_USE_TOOL_NAMES.has("click_at")).toBe(true)
  })

  it("opens while a Computer Use action is in flight", () => {
    const preview = computerUsePreview(
      [
        tool("navigate", {
          state: "input-available",
          input: { url: "https://example.com" },
        }),
      ],
      true
    )
    expect(preview.open).toBe(true)
    expect(preview.url).toBe("https://example.com")
    expect(preview.action).toBe("navigate")
  })

  it("uses the url and intent from the tool output", () => {
    const preview = computerUsePreview(
      [
        tool("click", {
          state: "output-available",
          input: { x: 10, y: 20, intent: "Open the result" },
          output: {
            action: "click",
            url: "https://example.com/search",
            intent: "Open the result",
            livePreviewUrl:
              "http://localhost:3000/computer-use-live.html?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC",
            devtoolsFrontendUrl:
              "http://localhost:9222/devtools/inspector.html?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC",
          },
        }),
      ],
      true
    )
    expect(preview.open).toBe(true)
    expect(preview.url).toBe("https://example.com/search")
    expect(preview.livePreviewUrl).toBe(
      "http://localhost:3000/computer-use-live.html?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC"
    )
    expect(preview.devtoolsFrontendUrl).toBe(
      "http://localhost:9222/devtools/inspector.html?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC"
    )
    expect(preview.screenshot).toBeNull()
    expect(preview.intent).toBe("Open the result")
  })

  it("stays open after the turn if a page URL is known", () => {
    const preview = computerUsePreview(
      [
        tool("navigate", {
          state: "output-available",
          output: { action: "navigate", url: "https://example.com" },
        }),
      ],
      false
    )
    expect(preview.open).toBe(true)
    expect(preview.url).toBe("https://example.com")
  })

  it("stays open between Computer Use actions while the turn is streaming", () => {
    const preview = computerUsePreview(
      [
        tool("navigate", {
          toolCallId: "cu-1",
          state: "output-available",
          output: {
            action: "navigate",
            url: "https://example.com",
          },
        }),
        tool("wait", {
          toolCallId: "cu-2",
          state: "output-available",
          output: { action: "wait" },
        }),
      ],
      true
    )
    expect(preview.open).toBe(true)
    expect(preview.url).toBe("https://example.com")
    expect(preview.sessionId).toBe("cu-1")
  })

  it("unwraps Temporal activity tool envelopes from tool_results", () => {
    const envelope = {
      status: "success",
      content: [
        {
          text: JSON.stringify({
            action: "click",
            url: "https://example.com/search",
            livePreviewUrl:
              "http://localhost:3000/computer-use-live.html?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC",
            devtoolsFrontendUrl:
              "http://localhost:9222/devtools/inspector.html?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC",
            intent: "Open the result",
          }),
        },
      ],
    }
    const preview = computerUsePreview(
      [
        tool("click", {
          state: "output-available",
          output: envelope,
        }),
      ],
      true
    )
    expect(preview.livePreviewUrl).toBe(
      "http://localhost:3000/computer-use-live.html?ws=ws%3A%2F%2Flocalhost%3A9222%2Fdevtools%2Fpage%2FABC"
    )
    expect(preview.devtoolsFrontendUrl).toBe(
      "http://localhost:9222/devtools/inspector.html?ws=localhost%3A9222%2Fdevtools%2Fpage%2FABC"
    )
    expect(preview.url).toBe("https://example.com/search")
    expect(preview.intent).toBe("Open the result")
  })

  it("unwraps {text: json} tool output from the SSE bridge", () => {
    expect(
      computerUseFields({
        toolName: "navigate",
        output: { text: JSON.stringify({ url: "https://example.com", screenshot: "abc" }) },
      }).url
    ).toBe("https://example.com")
    expect(
      computerUseFields({
        toolName: "navigate",
        output: JSON.stringify({
          action: "navigate",
          url: "https://example.com",
          screenshot: "abc",
        }),
      }).screenshot
    ).toEqual({ base64: "abc", mediaType: "image/jpeg" })
    expect(
      stripComputerUseScreenshot({
        action: "click",
        url: "https://example.com",
        screenshot: "abc",
      })
    ).toEqual({ action: "click", url: "https://example.com" })
  })
})
