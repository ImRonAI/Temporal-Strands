import { renderToStaticMarkup } from "react-dom/server"
import type { DynamicToolUIPart, UIMessage } from "ai"
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
      code: 'pplx search web "temporal workflows" -n 2',
      status: "completed",
      // Documented `pplx search web` stdout: title after url, plus metadata.
      results: [{
        stdout: JSON.stringify({
          total: 1,
          hits: [{ url: "https://temporal.io", title: "Temporal", domain: "temporal.io", snippet: "Durable execution" }],
        }),
        stderr: "",
        exit_code: 0,
        duration_ms: 5,
      }],
    })
    expect(html).toContain("Searching the web · temporal workflows")
    expect(html).toContain('href="https://temporal.io"')
    expect(html).toContain("Temporal")
    expect(html).not.toContain("bg-zinc-950")
  })

  it("renders a pplx content snippets fetch from bash as a fetched-pages step, not a Terminal", () => {
    const html = render({
      type: "sandbox_results",
      call_id: "c3",
      language: "bash",
      code: 'pplx content snippets "bloom filter" https://en.wikipedia.org/wiki/Bloom_filter',
      status: "completed",
      results: [{
        stdout: JSON.stringify({
          results: [{ url: "https://en.wikipedia.org/wiki/Bloom_filter", text: "A Bloom filter…", tokens_count: 12 }],
        }),
        stderr: "",
        exit_code: 0,
        duration_ms: 5,
      }],
    })
    expect(html).toContain("Fetching pages · bloom filter")
    expect(html).toContain('href="https://en.wikipedia.org/wiki/Bloom_filter"')
    expect(html).not.toContain("bg-zinc-950")
  })

  it("renders a pplx_sdk people search from python as a people step, not a Sandbox", () => {
    // Shape from a live Agent API sandbox run: the preinstalled pplx_sdk
    // (search.people) prints a bare list of hits.
    const html = render({
      type: "sandbox_results",
      call_id: "c5",
      language: "python",
      code: 'import pplx_sdk, json\nhits = pplx_sdk.search.people("head of engineering Temporal", limit=3)\nprint(json.dumps([dict(h) for h in hits]))',
      status: "completed",
      results: [{
        stdout: JSON.stringify([{ url: "https://temporal.io/careers/1", title: "Head of Engineering, Compute - Temporal" }]),
        stderr: "",
        exit_code: 0,
        duration_ms: 5,
      }],
    })
    expect(html).toContain("Searching people · head of engineering Temporal")
    expect(html).toContain('href="https://temporal.io/careers/1"')
    expect(html).not.toContain("sandbox.py")
  })

  it("renders people search results as search-result badges", () => {
    const html = render({
      type: "people_search_results",
      queries: ["head of platform engineering Notion"],
      results: [{ id: 1, url: "https://example.com/profile", title: "Example profile", snippet: "", source: "web" }],
    })
    expect(html).toContain("People")
    expect(html).toContain('href="https://example.com/profile"')
    expect(html).toContain("Example profile")
  })

  it("renders finance results as a market-data step with source badges", () => {
    const html = render({
      // docs.perplexity.ai/docs/agent-api/tools/finance-search "Response Shape".
      type: "finance_results",
      categories: ["quote"],
      tickers: ["NVDA"],
      results: [{ category: "quote", tickers: ["NVDA"], content: "NVDA last price", sources: ["https://example.com/nvda"] }],
    })
    expect(html).toContain("Market data · NVDA")
    expect(html).toContain('href="https://example.com/nvda"')
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

  it("renders an in-flight image search as an active step listing its queries", () => {
    const html = render({
      type: "response.reasoning.image_search_queries",
      call_id: "img_1",
      queries: ["saturn rings"],
    })
    expect(html).toContain("Searching images · 1 query")
    expect(html).toContain("saturn rings")
  })

  it("renders image search results as ChainOfThoughtImages linking to their origin pages", () => {
    // docs.perplexity.ai/docs/agent-api/tools/image-search ImageResult shape.
    const html = render({
      type: "image_search_results",
      call_id: "img_1",
      queries: ["saturn rings"],
      results: [
        {
          image_url: "https://images.example/saturn.jpg",
          origin_url: "https://nasa.gov/saturn",
          width: 1200,
          height: 800,
          title: "Saturn's rings",
        },
        {
          image_url: "https://images.example/untitled.png",
          origin_url: "https://wikimedia.org/w/untitled",
          width: 640,
          height: 480,
        },
      ],
    })
    expect(html).toContain("Images · saturn rings")
    expect(html).toContain('src="https://images.example/saturn.jpg"')
    expect(html).toContain('href="https://nasa.gov/saturn"')
    expect(html).toContain("Saturn&#x27;s rings · 1200×800")
    // Untitled results fall back to the origin hostname badge and dimensions.
    expect(html).toContain("wikimedia.org")
    expect(html).toContain("640×480")
    expect(html).toContain('alt="Image result"')
  })

  it("renders a failed image search as a native tool error, not an empty gallery", () => {
    const html = render({
      type: "image_search_results",
      call_id: "img_2",
      queries: ["nothing"],
      results: [],
      error: "image_search_failed",
    })
    expect(html).toContain("image_search_failed")
    expect(html).not.toContain("<img")
  })

  it("renders reasoning thoughts as MessageResponse inside a Thinking step", () => {
    const html = render({ type: "response.reasoning.stopped", thought: "Plan the **query**." })
    expect(html).toContain("Thinking")
    expect(html).toContain('data-streamdown="strong">query<')
  })
})

describe("Think chain continuity", () => {
  const think: DynamicToolUIPart = {
    type: "dynamic-tool",
    toolName: "think",
    toolCallId: "think-1",
    state: "input-available",
    input: { thought: "Check the evidence", cycle_count: 1 },
  }
  const tool: DynamicToolUIPart = {
    type: "dynamic-tool",
    toolName: "http_request",
    toolCallId: "request-1",
    state: "input-available",
    input: { method: "GET", url: "https://example.com" },
  }
  const reasoning: UIMessage["parts"][number] = {
    type: "reasoning", text: "Check the published evidence.", state: "done",
  }

  it("keeps Think and its streamed text visible in one open chain across nested events", () => {
    const parts: UIMessage["parts"] = [think, reasoning]
    const stages: UIMessage["parts"][] = [
      [...parts],
      [...parts, tool],
      [...parts, { ...tool, state: "output-available", output: "Page retrieved." }],
      [...parts, { ...tool, state: "output-available", output: "Page retrieved." }, {
        type: "data-native-tool", id: "search-1",
        data: { type: "response.reasoning.search_queries", queries: ["published evidence"] },
      }],
    ]
    for (const stage of stages) {
      const html = renderToStaticMarkup(<AgentActivity parts={stage} isThinking />)
      expect(html.match(/not-prose w-full/g)).toHaveLength(1)
      expect(html).toContain('data-think-call-id="think-1"')
      expect(html).toContain('data-activity-status="active" data-think-call-id="think-1"')
      expect(html.match(/Check the published evidence\./g)).toHaveLength(1)
      expect(html).not.toContain('data-state="closed"')
    }

    const completed: UIMessage["parts"] = [
      { ...think, state: "output-available", output: {
        status: "success", content: [{ text: "Cycle 1/1:\nCheck the published evidence." }],
      } },
      reasoning,
      { ...tool, state: "output-available", output: "Page retrieved." },
      { type: "data-native-tool", id: "search-1", data: {
        type: "search_results",
        results: [{ id: 1, url: "https://example.com", title: "Published evidence", snippet: "" }],
      } },
      { type: "reasoning", text: "The result confirms the evidence.", state: "done" },
    ]
    const html = renderToStaticMarkup(<AgentActivity parts={completed} isThinking={false} />)
    expect(html.match(/not-prose w-full/g)).toHaveLength(1)
    expect(html).toContain('data-activity-status="complete" data-think-call-id="think-1"')
    expect(html.match(/Check the published evidence\./g)).toHaveLength(1)
    expect(html).toContain("The result confirms the evidence.")
    expect(html).toContain("Page retrieved.")
    expect(html).toContain("Published evidence")
    expect(html).not.toContain("Cycle 1/1")
    expect(html).not.toContain('data-state="closed"')
  })
})
