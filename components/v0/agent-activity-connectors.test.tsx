import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { AgentActivity } from "./agent-activity"

function render(data: Record<string, unknown>) {
  return renderToStaticMarkup(<AgentActivity
    parts={[{ type: "data-native-tool", id: "mcp-connector-test", data }]}
    isThinking={false}
  />)
}

const catalog = {
  type: "mcp_list_tools",
  id: "catalog-1",
  connector_id: "connector_github",
  server_label: "github",
  tools: [],
}
const setupUrl = "https://console.perplexity.ai/group/connectors"

describe("connector availability presentation", () => {
  it("marks an empty connector catalog unavailable without inventing authorization failure or an action", () => {
    const html = render(catalog)
    expect(html).toContain("github · Connector unavailable")
    expect(html).toContain("text-destructive hover:text-destructive")
    expect(html).toContain("Connector availability notice, not a performed tool action.")
    expect(html).toContain(`href="${setupUrl}"`)
    expect(html).not.toMatch(/authorization required|unauthorized|AUTH_REQUIRED|Completed|Running/)
  })

  it("shows the direct harness notice verbatim as availability, not a performed call", () => {
    const error = "Connector unavailable for this request. Continue independent work; do not claim access to it."
    const html = render({ ...catalog, id: "availability-connector_github", error })
    expect(html).toContain(error)
    expect(html).toContain("github · Connector unavailable")
    expect(html).toContain("not a performed tool action")
    expect(html).toContain(`href="${setupUrl}"`)
    expect(html).not.toMatch(/authorization required|Completed|Running/)
  })

  it.each(["AUTH_REQUIRED", "CONNECTOR_UNAVAILABLE", "Unknown catalog error"])("marks a catalog with tools and error %s failed", (error) => {
    const html = render({ ...catalog, tools: [{ name: "search" }], error })
    expect(html).toContain(error)
    expect(html).toContain("text-destructive hover:text-destructive")
    expect(html).toContain(`href="${setupUrl}"`)
    expect(html).toContain(error === "AUTH_REQUIRED" ? "Connector authorization required" : "Connector unavailable")
    if (error !== "AUTH_REQUIRED") expect(html).not.toContain("authorization required")
  })

  it.each([undefined, "AUTH_REQUIRED", "Unknown remote error"])("does not infer managed connector availability for a remote MCP catalog: %s", (error) => {
    const html = render({ ...catalog, connector_id: undefined, error })
    expect(html).not.toContain(setupUrl)
    expect(html).not.toContain("Connector unavailable")
    expect(html).not.toContain("Connector authorization required")
    if (error) {
      expect(html).toContain(error)
      expect(html).toContain("text-destructive hover:text-destructive")
    } else {
      expect(html).not.toContain("text-destructive hover:text-destructive")
    }
  })

  it.each([
    "AUTH_REQUIRED", "INVALID_ARGUMENTS", "POLICY_DENIED", "CONNECTOR_UNAVAILABLE",
    "CONNECTOR_INTERNAL_ERROR", "TOOL_ERROR", "Unknown call error", null,
  ])("adds setup guidance only for managed MCP call AUTH_REQUIRED, not %s", (error) => {
    const html = render({
      type: "mcp_call", connector_id: "connector_github", server_label: "github",
      name: "search", arguments: "{}", output: "Matched repository", error,
    })
    expect(html).toContain("github · search")
    if (error) {
      expect(html).toContain(error)
      expect(html).toContain("text-destructive hover:text-destructive")
      expect(html).not.toContain("Matched repository")
    } else {
      expect(html).toContain("Matched repository")
      expect(html).not.toContain("text-destructive hover:text-destructive")
    }
    if (error === "AUTH_REQUIRED") {
      expect(html).toContain("Connector authorization required")
      expect(html).toContain(`href="${setupUrl}"`)
      expect(html).toContain("reconnect this connector")
    } else {
      expect(html).not.toContain(setupUrl)
      expect(html).not.toContain("Connector authorization required")
    }
  })

  it("keeps remote MCP call errors unchanged without a managed connector setup link", () => {
    const html = render({ type: "mcp_call", server_label: "custom-server", name: "search", arguments: "{}", error: "AUTH_REQUIRED" })
    expect(html).toContain("AUTH_REQUIRED")
    expect(html).toContain("custom-server · search")
    expect(html).not.toContain(setupUrl)
  })
})
