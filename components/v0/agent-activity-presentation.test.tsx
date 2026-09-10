import { renderToStaticMarkup } from "react-dom/server"
import type { DynamicToolUIPart } from "ai"
import { describe, expect, it } from "vitest"
import { AgentActivity } from "./agent-activity"

function render(toolName: string, output: unknown, input: unknown = {}) {
  const part: DynamicToolUIPart = { type: "dynamic-tool", toolName, toolCallId: "presentation-test", state: "output-available", input, output }
  return renderToStaticMarkup(<AgentActivity parts={[part]} isThinking={false} />)
}

describe("native tool result presentations", () => {
  it("uses Terminal for execution output including stderr and nonzero exit status", () => {
    const html = render("shell", { status: "success", content: [{ json: { stdout: "checking project", stderr: "validation failed", exit_code: 2 } }] }, { command: "check" })
    expect(html).toContain('data-tool-presentation="terminal"')
    expect(html).toContain('data-tool-state="output-error"')
    expect(html).toContain("checking project")
    expect(html).toContain("validation failed")
    expect(html).toContain("Copy terminal output")
    expect(html).not.toContain("Completed")
  })
  it("uses Artifact and CodeBlock for native file content", () => {
    const html = render("file_read", { status: "success", content: [{ text: 'Content of src/a.ts:\nexport const a = "native";' }] }, { path: "src/a.ts", mode: "view" })
    expect(html).toContain('data-tool-presentation="file"')
    expect(html).toContain("src/a.ts")
    expect(html).toContain("Copy src/a.ts")
    expect(html).toContain("export const a")
  })
  it("uses FileTree for a verified native find result", () => {
    const html = render("file_read", { status: "success", content: [{ text: "Found 2 files:\nsrc/a.ts\nsrc/b.ts" }] }, { mode: "find" })
    expect(html).toContain('data-tool-presentation="files"')
    expect(html).toContain('role="tree"')
    expect(html).toContain("src/a.ts")
    expect(html).toContain("src/b.ts")
  })
  it("uses native JSXPreview fallback without executing JSX-shaped tool output", () => {
    const html = render("unknown_tool", '<button id="untrusted-executable">Do not execute this</button>')
    expect(html).toContain('data-tool-presentation="jsx"')
    expect(html).toContain("Do not execute this")
    expect(html).not.toContain('<button id="untrusted-executable"')
    expect(html).toContain("Input and raw result")
  })
  it("shows native JSXPreview structured result instead of dropping unknown fields", () => {
    const html = render("catalog_lookup", { id: "verified-id", unexpected_metadata: { amount: 17 } })
    expect(html).toContain('data-tool-presentation="jsx"')
    expect(html).toContain("verified-id")
    expect(html).toContain("unexpected_metadata")
    expect(html).toContain("Copy result")
  })
  it("keeps returned error visible in the fallback", () => {
    const html = render("catalog_lookup", { status: "success", content: [{ text: JSON.stringify({ status: "error", content: [{ text: "Catalog unavailable" }] }) }] })
    expect(html).toContain('data-tool-state="output-error"')
    expect(html).toContain("Catalog unavailable")
    expect(html).toContain('role="alert"')
    expect(html).not.toContain("Completed")
  })
})
