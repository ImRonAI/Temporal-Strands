import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const h = vi.hoisted(() => ({
  effects: [] as Array<() => (() => void)>,
  setters: [] as Array<ReturnType<typeof vi.fn>>,
}))

vi.mock("react", () => ({
  useEffect: (effect: () => (() => void)) => { h.effects.push(effect) },
  useState: (initial: unknown) => {
    const set = vi.fn()
    h.setters.push(set)
    return [initial, set]
  },
}))

const fetchMock = vi.fn()

const sessionTools = ["shell", "think", "use_agent"]
const discoverableTools = ["graph", "python_repl"]
const skills = [
  { name: "research-brief", description: "Compile a sourced brief" },
  { name: "code-review", description: "Review a diff" },
]
const catalogBody = {
  tools: { session: sessionTools, discoverable: discoverableTools },
  skills,
}
const response = () => new Response(JSON.stringify(catalogBody))

beforeEach(() => {
  vi.resetModules()
  h.effects = []
  h.setters = []
  vi.stubGlobal("fetch", fetchMock)
})
afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
})

describe("useCatalog", () => {
  it("loads and exposes session tools, discoverable tools, and skills", async () => {
    const { useCatalog } = await import("./use-catalog")
    fetchMock.mockResolvedValueOnce(response())
    useCatalog()
    h.effects[0]()
    await vi.waitFor(() => expect(h.setters[1]).toHaveBeenCalledWith("ready"))
    expect(h.setters[0]).toHaveBeenCalledWith({
      tools: { session: sessionTools, discoverable: discoverableTools },
      skills,
    })
    expect(h.setters[2]).toHaveBeenCalledWith(null)
    expect(fetchMock).toHaveBeenCalledWith("/api/catalog", {
      cache: "no-store",
      signal: expect.any(AbortSignal),
    })
  })

  it("reports status error with the upstream message on a non-2xx response", async () => {
    const { useCatalog } = await import("./use-catalog")
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "worker offline" }), { status: 502 })
    )
    useCatalog()
    h.effects[0]()
    await vi.waitFor(() => expect(h.setters[1]).toHaveBeenCalledWith("error"))
    expect(h.setters[0]).not.toHaveBeenCalled()
    expect(h.setters[2]).toHaveBeenCalledWith(expect.stringContaining("502"))
  })

  it("includes the session_id query parameter when provided", async () => {
    const { useCatalog } = await import("./use-catalog")
    fetchMock.mockResolvedValueOnce(response())
    useCatalog("session-abc123")
    h.effects[0]()
    await vi.waitFor(() => expect(h.setters[1]).toHaveBeenCalledWith("ready"))
    expect(fetchMock).toHaveBeenCalledWith("/api/catalog?session_id=session-abc123", {
      cache: "no-store",
      signal: expect.any(AbortSignal),
    })
  })
})
