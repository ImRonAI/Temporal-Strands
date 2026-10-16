import { renderToStaticMarkup } from "react-dom/server"
import type { DynamicToolUIPart } from "ai"
import { describe, expect, it } from "vitest"
import { AgentActivity } from "./agent-activity"

function render(toolName: string, output: unknown, input: unknown = {}) {
  const part: DynamicToolUIPart = { type: "dynamic-tool", toolName, toolCallId: "presentation-test", state: "output-available", input, output }
  return renderToStaticMarkup(<AgentActivity parts={[part]} isThinking={false} />)
}

// Every dynamic tool call renders the documented Tool composition
// (elements.ai-sdk.dev/components/tool): ToolHeader carries the tool name and
// the native status badge, ToolContent holds ToolInput ("Parameters") and
// ToolOutput ("Result" / "Error"). Result shapes with their own documented
// primitive nest it as the ToolOutput body.
describe("dynamic tool presentations", () => {
  it("renders shell output in a native Terminal inside the Tool, with the error state on a nonzero exit", () => {
    const html = render("shell", { status: "success", content: [{ json: { stdout: "checking project", stderr: "validation failed", exit_code: 2 } }] }, { command: "check" })
    expect(html).toContain("Parameters")
    expect(html).toContain("Error")
    expect(html).toContain("Command exited with code 2.")
    expect(html).toContain("checking project")
    expect(html).toContain("validation failed")
    expect(html).toContain("bg-zinc-950") // Terminal root
    expect(html).not.toContain("Completed")
  })
  it("renders native file content as an Artifact with a CodeBlock per file", () => {
    const html = render("file_read", { status: "success", content: [{ text: 'Content of src/a.ts:\nexport const a = "native";' }] }, { path: "src/a.ts", mode: "view" })
    expect(html).toContain("File content")
    expect(html).toContain("src/a.ts")
    expect(html).toContain('data-language="typescript"')
    expect(html).toContain("export const a")
    expect(html).toContain("Completed")
  })
  it("renders a verified native find result as an Artifact with a FileTree", () => {
    const html = render("file_read", { status: "success", content: [{ text: "Found 2 files:\nsrc/a.ts\nsrc/b.ts" }] }, { mode: "find" })
    expect(html).toContain("Files found")
    expect(html).toContain('role="tree"')
    expect(html).toContain("src/a.ts")
    expect(html).toContain("src/b.ts")
  })
  it("shows JSX-shaped text output as text through ToolOutput, never executed", () => {
    const html = render("unknown_tool", '<button id="untrusted-executable">Do not execute this</button>')
    expect(html).toContain("Result")
    expect(html).toContain("Do not execute this")
    expect(html).not.toContain('<button id="untrusted-executable"')
  })
  it("shows a structured result as ToolOutput JSON instead of dropping unknown fields", () => {
    const html = render("catalog_lookup", { id: "verified-id", unexpected_metadata: { amount: 17 } })
    expect(html).toContain("Result")
    expect(html).toContain("verified-id")
    expect(html).toContain("unexpected_metadata")
    expect(html).toContain('data-language="json"')
  })
  it("surfaces a returned error through the Tool output-error state", () => {
    const html = render("catalog_lookup", { status: "success", content: [{ text: JSON.stringify({ status: "error", content: [{ text: "Catalog unavailable" }] }) }] })
    expect(html).toContain("Error")
    expect(html).toContain("Catalog unavailable")
    expect(html).not.toContain("Completed")
  })
})
