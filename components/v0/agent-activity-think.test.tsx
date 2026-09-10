import { renderToStaticMarkup } from "react-dom/server"
import type { DynamicToolUIPart, UIMessage } from "ai"
import { describe, expect, it } from "vitest"
import { AgentActivity } from "./agent-activity"
import { ComputerUseActivity } from "./computer-use-activity"
import { toolPresentation } from "./computer-use"

function think(output: unknown): DynamicToolUIPart {
  return { type: "dynamic-tool", toolName: "think", toolCallId: "think-1", state: "output-available", input: { cycle_count: 1 }, output }
}
const displays = {
  general: (parts: UIMessage["parts"]) => renderToStaticMarkup(<AgentActivity parts={parts} isThinking={false} />),
  desktop: (parts: UIMessage["parts"]) => renderToStaticMarkup(<ComputerUseActivity parts={parts} isThinking={false} />),
}

for (const [name, render] of Object.entries(displays)) {
  describe(`${name} Think output`, () => {
    it.each([
      { status: "error", content: [{ text: "Astra request failed" }] },
      JSON.stringify({ status: "error", content: [{ text: "Astra request failed" }] }),
      { status: "success", content: [{ text: JSON.stringify({ status: "error", content: [{ text: "Astra request failed" }] }) }] },
      { isError: true, content: [{ text: "Astra request failed" }] },
    ])("renders returned errors in native reasoning, not hidden as duplicate summaries", output => {
      const html = render([think(output)])
      expect(html).toContain("Thinking failed")
      expect(html).toContain("Astra request failed")
      expect(html).toContain('role="alert"')
      expect(html).not.toContain("Completed")
    })

    it("renders thrown errors and denied results", () => {
      const error: DynamicToolUIPart = { type: "dynamic-tool", toolName: "think", toolCallId: "err", input: {}, state: "output-error", errorText: "Connection lost" }
      expect(render([error])).toContain("Connection lost")
      const denied: DynamicToolUIPart = { type: "dynamic-tool", toolName: "think", toolCallId: "no", input: {}, state: "output-denied", approval: { id: "a", approved: false } }
      expect(render([denied])).toContain("Thinking denied")
    })

    it("suppresses only actual duplicates, not conclusions missing from stream", () => {
      const summary = think({ status: "success", content: [{ text: "Cycle 1/1:\nThe page is ready." }] })
      const streamed: UIMessage["parts"] = [{ type: "reasoning", text: "The page is ready.", state: "done" }, summary]
      expect(render(streamed).match(/The page is ready\./g)).toHaveLength(1)
      expect(render([summary])).toContain("The page is ready.")
      expect(render([{ type: "reasoning", text: "An earlier thought.", state: "done" }, summary])).toContain("The page is ready.")
    })

    it("does not invent output for zero-cycle Think", () => {
      expect(render([think({ status: "success", content: [{ text: "" }] })])).not.toContain("Thinking result")
    })

    it("preserves a summary alongside native tool evidence arrays", () => {
      expect(render([think(["Unstreamed conclusion", { messages: [] }])])).toContain("Unstreamed conclusion")
    })
  })
}

describe("native result presentation", () => {
  it("retains multi-block errors and structured evidence", () => {
    const output = { status: "error", content: [{ text: "First failure" }, { text: "Second failure" }, { json: { code: 42 } }] }
    const result = toolPresentation(think(output))
    expect(result.error).toBe("First failure\n\nSecond failure")
    expect(result.output).toEqual(output)
  })
  it("does not reinterpret an arbitrary embedded page object as a failed transport", () => {
    const result = toolPresentation(think({ status: "success", content: [{ text: JSON.stringify({ article: { status: "error" } }) }] }))
    expect(result.error).toBeUndefined()
  })
  it("preserves known failed state without a diagnostic message", () => {
    expect(toolPresentation(think({ status: "error" })).error).toBe("Tool reported an error without a message.")
  })
})
