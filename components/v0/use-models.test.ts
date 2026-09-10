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
const catalog = [{ id: "any-provider/new-model", owned_by: "perplexity-agent-api" }]
const response = () => new Response(JSON.stringify({ data: catalog }))

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

describe("live model catalog refresh", () => {
  it("deduplicates concurrent picker loads but refreshes after completion", async () => {
    const { useModels } = await import("./use-models")
    let resolve!: (response: Response) => void
    fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done }))
    useModels()
    useModels()
    h.effects[0]()
    h.effects[1]()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    resolve(response())
    await vi.waitFor(() => expect(h.setters[0]).toHaveBeenCalledWith(catalog))
    expect(h.setters[3]).toHaveBeenCalledWith(catalog)
    fetchMock.mockResolvedValueOnce(response())
    useModels(true)
    h.effects[2]()
    await vi.waitFor(() => expect(h.setters[6]).toHaveBeenCalledWith(catalog))
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenLastCalledWith("/api/models", { cache: "no-store" })
  })

  it("does not cache errors or empty catalogs for the life of the tab", async () => {
    const { useModels } = await import("./use-models")
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: [] })))
    useModels()
    h.effects[0]()
    await vi.waitFor(() => expect(h.setters[1]).toHaveBeenCalledWith("error"))
    fetchMock.mockResolvedValueOnce(response())
    useModels(true)
    h.effects[1]()
    await vi.waitFor(() => expect(h.setters[4]).toHaveBeenCalledWith("ready"))
    expect(h.setters[3]).toHaveBeenCalledWith(catalog)
    expect(h.setters[5]).toHaveBeenCalledWith(null)
  })

  it("does not update a picker that unmounted before the response", async () => {
    const { useModels } = await import("./use-models")
    fetchMock.mockResolvedValueOnce(response())
    useModels()
    h.effects[0]()()
    // Observe completion through another subscriber, not a timing-dependent delay.
    useModels()
    h.effects[1]()
    await vi.waitFor(() => expect(h.setters[3]).toHaveBeenCalledWith(catalog))
    expect(h.setters[0]).not.toHaveBeenCalled()
  })
})
