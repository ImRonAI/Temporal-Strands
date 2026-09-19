import { renderToStaticMarkup } from "react-dom/server"
import type { UIMessage } from "ai"
import { describe, expect, it } from "vitest"
import { AgentActivity } from "./agent-activity"

// Native Agent API items (data-native-tool parts) render as Chain of Thought
// steps composed per elements.ai-sdk.dev/components/chain-of-thought, with
// each result kind nested in its own documented primitive.
function render(data: Record<string, unknown>, isThinking = false) {
  const parts: UIMessage["parts"] = [{ type: "data-native-tool", id: "native-1", data }]
  return renderToStaticMarkup(<AgentActivity parts={parts} isThinking={isThinking} />)
}

describe("native tool steps", () => {
  it("renders search results as ChainOfThoughtSearchResult badges that are links", () => {
    const html = render({
      type: "search_results",
      results: [{ id: 1, url: "https://example.com/a", title: "Example A", snippet: "" }],
    })
    expect(html).toContain("Web results")
    expect(html).toContain('data-slot="badge"')
    expect(html).toContain('href="https://example.com/a"')
    expect(html).toContain("Example A")
  })

  it("renders an in-flight search as an active step listing its queries", () => {
    const html = render({ type: "response.reasoning.search_queries", queries: ["alpha", "beta"] })
    expect(html).toContain("Searching the web · 2 queries")
    expect(html).toContain("alpha")
    expect(html).toContain("beta")
  })

  it("renders bash sandbox output in a Terminal with the command prompt and failure marker", () => {
    const html = render({
      type: "sandbox_results",
      call_id: "c1",
      language: "bash",
      code: "ls /missing",
      status: "completed",
      results: [{ stdout: "", stderr: "No such file", exit_code: 2, duration_ms: 5 }],
    })
    expect(html).toContain("Running commands")
    expect(html).toContain("bg-zinc-950")
    expect(html).toContain("ls /missing")
    expect(html).toContain("No such file")
    expect(html).toContain("exit 2")
  })

  it("renders a pplx CLI search from bash as a search step, not a Terminal", () => {
    const html = render({
      type: "sandbox_results",
      call_id: "c2",
      language: "bash",
      code: 'pplx search web "temporal workflows"',
      status: "completed",
      results: [{ stdout: '{"url": "https://temporal.io", "title": "Temporal"}', stderr: "", exit_code: 0, duration_ms: 5 }],
    })
    expect(html).toContain("Searching the web · temporal workflows")
    expect(html).toContain('href="https://temporal.io"')
    expect(html).not.toContain("bg-zinc-950")
  })

  it("renders python sandbox execution as a Sandbox with code and output tabs", () => {
    const html = render({
      type: "sandbox_results",
      call_id: "c3",
      language: "python",
      code: "print(1)",
      status: "completed",
      results: [{ stdout: "1", stderr: "", exit_code: 0, duration_ms: 5 }],
    })
    expect(html).toContain("Ran code")
    expect(html).toContain("sandbox.py")
    expect(html).toContain("Completed")
    expect(html).toContain(">Code<")
    expect(html).toContain(">Output<")
    expect(html).toContain('data-language="python"')
  })

  it("marks a python run that exited nonzero as an error even when the container completed", () => {
    const html = render({
      type: "sandbox_results",
      call_id: "c4",
      language: "python",
      code: "raise SystemExit(1)",
      status: "completed",
      results: [{ stdout: "", stderr: "Traceback", exit_code: 1, duration_ms: 5 }],
    })
    expect(html).toContain("Error")
    expect(html).not.toContain("Completed")
  })

  it("renders an MCP call as a Tool with parameters and result", () => {
    const html = render({
      type: "mcp_call",
      server_label: "github",
      name: "list_issues",
      arguments: JSON.stringify({ repo: "vercel/ai" }),
      output: JSON.stringify({ issues: 3 }),
    })
    expect(html).toContain("github · list_issues")
    expect(html).toContain("Parameters")
    expect(html).toContain("Result")
    expect(html).toContain("vercel/ai")
    expect(html).toContain("issues")
  })

  it("renders a native item error through the Tool output-error state", () => {
    const html = render({
      type: "mcp_call",
      server_label: "github",
      name: "list_issues",
      arguments: "{}",
      error: "TOOL_ERROR",
    })
    expect(html).toContain("Error")
    expect(html).toContain("TOOL_ERROR")
    expect(html).not.toContain("Completed")
  })

  it("renders a connector catalog as a tool-count step with badges", () => {
    const html = render({
      type: "mcp_list_tools",
      connector_id: "connector_github",
      server_label: "github",
      tools: [{ name: "get_file_contents" }],
    })
    expect(html).toContain("github · 1 tool")
    expect(html).toContain("get_file_contents")
  })

  it("renders a shared image through ChainOfThoughtImage with a caption", () => {
    const html = render({ type: "share_file", filename: "chart.png", url: "/api/orchestrator/file?id=1" })
    expect(html).toContain('src="/api/orchestrator/file?id=1"')
    expect(html).toContain('alt="chart.png"')
    expect(html).toContain("chart.png")
  })

  it("renders a shared document as an Artifact with a download action", () => {
    const html = render({ type: "share_file", filename: "report.csv", url: "/api/orchestrator/file?id=2" })
    expect(html).toContain("report.csv")
    expect(html).toContain("Produced in the sandbox")
    expect(html).toContain("Download")
  })

  it("renders touched files from a patch as a Task of TaskItemFile chips", () => {
    const html = render({
      type: "sandbox_apply_patch",
      call_id: "p1",
      added: ["src/new.ts"],
      modified: ["src/old.ts"],
    })
    expect(html).toContain("Applied patch · 2 files")
    expect(html).toContain("Added")
    expect(html).toContain("src/new.ts")
    expect(html).toContain("Modified")
    expect(html).toContain("src/old.ts")
  })

  it("renders a loaded skill as a bare step", () => {
    expect(render({ type: "skill_loaded", name: "research" })).toContain("Loaded research")
  })

  it("renders reasoning thoughts as MessageResponse inside a Thinking step", () => {
    const html = render({ type: "response.reasoning.stopped", thought: "Plan the **query**." })
    expect(html).toContain("Thinking")
    expect(html).toContain('data-streamdown="strong">query<')
  })
})
