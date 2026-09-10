import { renderToStaticMarkup } from "react-dom/server"
import type { DynamicToolUIPart, UIMessage } from "ai"
import { describe, expect, it } from "vitest"

import { AgentActivity } from "./agent-activity"
import { buildActivitySegments, ComputerUseActivity, computerUseTextIndices } from "./computer-use-activity"

describe("native browser computer-use timeline", () => {
  const action = (name: string, id: string): Extract<DynamicToolUIPart, { state: "output-available" }> => ({
    type: "dynamic-tool", toolName: name, toolCallId: id, state: "output-available",
    input: { x: 120, y: 80, key: "Enter", direction: "down" }, output: { status: "success" },
  })

  it("keeps updates and reasoning between clicks, scrolls and keys in one ordered loop", () => {
    const parts: UIMessage["parts"] = [
      action("browser", "nav"),
      { type: "text", text: "The button is visible.", state: "done" },
      action("click", "click"),
      { type: "reasoning", text: "Checking the resulting page.", state: "done" },
      { type: "step-start" },
      action("scroll", "scroll"), action("press_key", "key"),
      { type: "text", text: "Final answer stays outside.", state: "done" },
    ]
    const segments = buildActivitySegments(parts)
    expect(segments.map(segment => segment.kind)).toEqual(["computer-use", "single"])
    expect([...computerUseTextIndices(parts)]).toEqual([1])
    if (segments[0].kind !== "computer-use") throw new Error("Missing loop")
    const html = renderToStaticMarkup(<ComputerUseActivity parts={segments[0].parts} isThinking={false} />)
    expect(html).toContain("Browser activity")
    expect(html).toContain("The button is visible.")
    expect(html).toContain("Checking the resulting page.")
    expect(html).not.toContain("Final answer stays outside.")
    expect(html.indexOf('data-computer-action="click"')).toBeLessThan(html.indexOf("Checking the resulting page."))
    expect(html).toContain("Parameters")
    expect(html).toContain("Result")
  })

  it("shows wrapped browser failures and denied actions without success badges", () => {
    const failed: DynamicToolUIPart = { type: "dynamic-tool", toolName: "browser", toolCallId: "failed",
      state: "output-available", input: {}, output: { status: "success", content: [
      { text: JSON.stringify({ status: "error", content: [{ text: "Invalid coordinates" }] }) },
    ] } }
    const denied: DynamicToolUIPart = { type: "dynamic-tool", toolName: "click", toolCallId: "denied",
      state: "output-denied", input: {}, approval: { id: "approval", approved: false } }
    const html = renderToStaticMarkup(<ComputerUseActivity parts={[failed, denied]} isThinking={false} />)
    expect(html).toContain('data-action-state="output-error"')
    expect(html).toContain('data-action-state="output-denied"')
    expect(html).not.toContain("Completed")
    expect(html).toContain("Invalid coordinates")
  })

  it("keeps unrelated tools outside the browser component", () => {
    expect(buildActivitySegments([action("browser", "a"), action("graph", "b"), action("browser", "c")])
      .map(segment => segment.kind)).toEqual(["computer-use", "single", "computer-use"])
  })

  it("retains Think calls and emitted reasoning inside an active browser loop", () => {
    const parts: UIMessage["parts"] = [action("browser", "nav"), action("think", "think"),
      { type: "reasoning", text: "Compare the visible targets.", state: "streaming" },
      action("click", "click"), { type: "text", text: "Checking the screenshot", state: "streaming" }]
    const segments = buildActivitySegments(parts, true)
    expect(segments).toHaveLength(1)
    expect([...computerUseTextIndices(parts, true)]).toEqual([4])
    const html = renderToStaticMarkup(<ComputerUseActivity parts={parts} isThinking />)
    expect(html).toContain("Compare the visible targets.")
    expect(html).toContain("Checking the screenshot")
    expect(html).toContain("Think")
    expect(html).toContain("2/2 actions finished")
  })
  const observation = {
    artifact_id: "879a4f76-2f47-4039-b5ca-30c8bba286bf", generation: "1", sha256: "a".repeat(64),
    width: 1280, height: 720, mime_type: "image/png",
  }
  it("renders every action with native TaskItem and session-scoped screenshot evidence", () => {
    const parts: DynamicToolUIPart[] = ["navigate", "click", "screenshot"].map((action, index) => ({
      type: "dynamic-tool",
      toolName: "browser",
      toolCallId: `browser-${index}`,
      state: "output-available",
      input: { browser_input: { action: { type: action } } },
      output: { status: "success", content: [{ text: JSON.stringify({
        status: "success", action, url: "https://example.com", observation,
        content: [{ text: "Action finished" }],
      }) }] },
    }))
    const html = renderToStaticMarkup(<ComputerUseActivity parts={parts} isThinking sessionId="actual/chat&session" />)
    expect(html).toContain("Computer use")
    for (let index = 0; index < parts.length; index++) {
      expect(html).toContain(`data-computer-action="browser-${index}"`)
    }
    expect(html.match(/<img /g)).toHaveLength(3)
    expect(html).toContain(`/api/orchestrator/desktop-image?session_id=actual%2Fchat%26session&amp;artifact_id=${observation.artifact_id}`)
    expect(html).not.toContain("data:image/")
    expect(html.match(/Completed/g)).toHaveLength(3)
    expect(html).toContain("navigate: https://example.com")
  })

  it("does not use tool-call IDs or browser session names as screenshot authorization", () => {
    const parts: DynamicToolUIPart[] = [{ ...action("browser", "not-a-chat-id"),
      input: { browser_input: { action: { type: "screenshot", session_name: "not-chat-either" } } },
      output: { status: "success", action: "screenshot", observation },
    }]
    const html = renderToStaticMarkup(<ComputerUseActivity parts={parts} isThinking={false} />)
    expect(html).not.toContain("<img ")
    expect(html).not.toContain("/api/orchestrator/desktop-image")
    expect(html).toContain(observation.artifact_id)
    expect(renderToStaticMarkup(<AgentActivity parts={parts} isThinking={false} />)).toContain("Computer use")
  })

  it("preserves unknown completion and actual Think output without fabricated success", () => {
    const pending: DynamicToolUIPart = { type: "dynamic-tool", toolName: "click", toolCallId: "pending",
      input: { x: 25, y: 40 }, state: "input-available" }
    const html = renderToStaticMarkup(<ComputerUseActivity parts={[pending, {
      ...action("think", "think"), output: { notes: "Need a fresh screenshot before clicking again." },
    }]} isThinking={false} />)
    expect(html).toContain("No final result received")
    expect(html).toContain("0/1 actions finished")
    expect(html).toContain("Need a fresh screenshot before clicking again.")
    expect(html).toContain('data-action-state="input-available"')
  })

  it("does not render inline pixels from older nested tool results", () => {
    const html = renderToStaticMarkup(<ComputerUseActivity parts={[{...action("browser", "old"), output: { status: "success",
      content: [{ text: JSON.stringify({ action: "screenshot", screenshot: "old-inline-pixels", observation }) }],
    }}]} isThinking={false} sessionId="actual-chat" />)
    expect(html).not.toContain("old-inline-pixels")
    expect(html).toContain(observation.artifact_id)
  })
})
