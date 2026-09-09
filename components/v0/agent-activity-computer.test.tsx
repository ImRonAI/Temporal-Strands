import { renderToStaticMarkup } from "react-dom/server"
import type { DynamicToolUIPart } from "ai"
import { describe, expect, it } from "vitest"

import { AgentActivity } from "./agent-activity"

describe("native browser computer-use timeline", () => {
  it("renders every browser action as a TaskItem with screenshot evidence", () => {
    const parts: DynamicToolUIPart[] = ["navigate", "click", "screenshot"].map((action, index) => ({
      type: "dynamic-tool",
      toolName: "browser",
      toolCallId: `browser-${index}`,
      state: "output-available",
      input: { browser_input: { action: { type: action } } },
      output: { browserPreview: {
        action, url: "https://example.com",
        screenshotUrl: `http://localhost:8787/browser-observations/image-${index}/content`,
      } },
    }))
    const html = renderToStaticMarkup(<AgentActivity parts={parts} isThinking />)
    expect(html).toContain("Computer use")
    for (let index = 0; index < parts.length; index++) {
      expect(html).toContain(`data-computer-action="browser-${index}"`)
      expect(html).toContain(`/browser-observations/image-${index}/content`)
    }
    expect(html.match(/Completed/g)).toHaveLength(3)
    expect(html).toContain("navigate: https://example.com")
  })
})
