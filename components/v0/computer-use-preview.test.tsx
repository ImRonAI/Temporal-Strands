// SSR contracts plus handler tests using explicit hook-state rerenders.
// These do not prove DOM events, React effect lifecycles or native VNC fencing.

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { WebPreviewBody } from "@/components/ai-elements/web-preview"

const h = vi.hoisted(() => ({
  promptInputProps: [] as Array<Record<string, unknown>>,
  iframeProps: [] as Array<Record<string, unknown>>,
  iframeKeys: [] as Array<string | null>,
  buttons: new Map<string, React.ComponentProps<"button">>(),
  inputProps: {} as React.ComponentProps<"textarea">,
  hooks: [] as unknown[],
  hookIndex: 0,
  renderingPanel: false,
  effects: [] as Array<{ callback: () => void | (() => void); dependencies: unknown[]; cleanup?: void | (() => void) }>,
  pendingEffects: new Set<number>(),
  effectIndex: 0,
  callbacks: [] as Array<{ callback: (...args: unknown[]) => unknown; dependencies: unknown[] }>,
  callbackIndex: 0,
  serverMode: "agent",
  serverReady: true,
}))

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof React>()
  return {
    ...actual,
    useState: (initial: unknown) => {
      if (!h.renderingPanel) return actual.useState(initial)
      const index = h.hookIndex++
      if (!(index in h.hooks)) h.hooks[index] = initial
      return [h.hooks[index], (value: unknown) => {
        h.hooks[index] = typeof value === "function" ? value(h.hooks[index]) : value
      }]
    },
    useRef: (initial: unknown) => {
      if (!h.renderingPanel) return actual.useRef(initial)
      const index = h.hookIndex++
      if (!(index in h.hooks)) h.hooks[index] = { current: initial }
      return h.hooks[index]
    },
    useCallback: (callback: (...args: unknown[]) => unknown, dependencies: unknown[]) => {
      if (!h.renderingPanel) return actual.useCallback(callback, dependencies)
      const index = h.callbackIndex++
      const previous = h.callbacks[index]
      if (!previous || dependencies.some((dep, i) => !Object.is(dep, previous.dependencies[i]))) {
        h.callbacks[index] = { callback, dependencies }
      }
      return h.callbacks[index].callback
    },
    useEffect: (callback: () => void | (() => void), dependencies: unknown[]) => {
      if (!h.renderingPanel) return actual.useEffect(callback, dependencies)
      const index = h.effectIndex++
      const previous = h.effects[index]
      if (!previous || dependencies.some((dep, i) => !Object.is(dep, previous.dependencies[i]))) {
        h.effects[index] = { callback, dependencies, cleanup: previous?.cleanup }
        h.pendingEffects.add(index)
      }
    },
  }
})

vi.mock("@/components/ui/button", () => ({
  Button: (props: React.ComponentProps<"button"> & { "data-testid"?: string }) => {
    if (props["data-testid"]) h.buttons.set(props["data-testid"], props)
    return <button data-testid={props["data-testid"]} disabled={props.disabled} type="button">{props.children}</button>
  },
}))

vi.mock("@/components/ai-elements/artifact", () => ({
  Artifact: ({ children, ...rest }: { children?: React.ReactNode; [key: string]: unknown }) => (
    <div data-testid={(rest["data-testid"] as string) ?? "artifact"}>{children}</div>
  ),
  ArtifactHeader: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  ArtifactTitle: ({ children }: { children?: React.ReactNode }) => <span>{children}</span>,
  ArtifactActions: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  ArtifactClose: ({
    children,
    disabled,
    ...rest
  }: { children?: React.ReactNode; disabled?: boolean; [key: string]: unknown }) => (
    <button
      data-testid={(rest["data-testid"] as string) ?? "artifact-close"}
      disabled={disabled}
      type="button"
    >
      {children}
    </button>
  ),
  ArtifactContent: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock("@/components/ai-elements/web-preview", () => ({
  WebPreview: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  WebPreviewNavigation: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  WebPreviewNavigationButton: ({
    children,
    disabled,
    onClick,
    tooltip,
    ...rest
  }: {
    children?: React.ReactNode
    disabled?: boolean
    onClick?: () => void
    tooltip?: string
    [key: string]: unknown
  }) => {
    if (typeof rest["data-testid"] === "string") h.buttons.set(rest["data-testid"], { onClick, disabled })
    return (
    <button
      aria-label={tooltip}
      data-testid={(rest["data-testid"] as string) ?? tooltip}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
    )
  },
  WebPreviewUrl: () => <input readOnly />,
  WebPreviewBody: (props: Record<string, unknown> & { src?: string }) => {
    h.iframeProps.push(props)
    return (
      <iframe
        data-control-mode={props["data-control-mode"] as string}
        data-testid="browser-iframe"
        src={props.src}
        title="Preview"
      />
    )
  },
}))

vi.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInput: (props: { children?: React.ReactNode; onSubmit?: unknown }) => {
    h.promptInputProps.push(props)
    return <form data-testid="handoff-form">{props.children}</form>
  },
  PromptInputBody: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  PromptInputFooter: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  PromptInputTextarea: (props: React.ComponentProps<"textarea">) => {
    h.inputProps = props
    return <textarea data-testid="handoff-input" disabled={props.disabled} value={props.value} readOnly />
  },
  PromptInputSubmit: (props: Record<string, unknown>) => (
    <button data-testid="give-control-submit" disabled={props.disabled as boolean} type="submit" />
  ),
}))

import { ComputerUsePreviewPanel } from "./computer-use-preview"
import type { ComputerUsePreview } from "./computer-use"

function preview(overrides: Partial<ComputerUsePreview> = {}): ComputerUsePreview {
  return {
    open: true,
    sessionId: "cu-1",
    url: "https://example.test/page",
    livePreviewUrl: "https://viewer.test/vnc.html?autoconnect=true",
    viewerType: "novnc",
    action: "navigate",
    intent: "open the page",
    observation: null,
    ...overrides,
  }
}

const observation: NonNullable<ComputerUsePreview["observation"]> = {
  artifact_id: "879a4f76-2f47-4039-b5ca-30c8bba286bf", generation: "1", sha256: "a".repeat(64),
  width: 1280, height: 720, mime_type: "image/png",
}

const desktopEpoch = "1789069438950188500"

async function renderPanel(
  props: Partial<React.ComponentProps<typeof ComputerUsePreviewPanel>> = {}
) {
  const panel = panelHandlers(props)
  await panel.flushEffects()
  return panel.render()
}

beforeEach(() => {
    h.promptInputProps = []
    h.iframeProps = []
    h.hooks = []
    h.hookIndex = 0
    h.renderingPanel = false
    h.buttons.clear()
    h.inputProps = {}
    h.effects = []
    h.pendingEffects.clear()
    h.callbacks = []
    h.serverMode = "agent"
    h.serverReady = true
    vi.stubGlobal("document", { addEventListener: vi.fn(), removeEventListener: vi.fn() })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ready: h.serverReady, mode: h.serverMode, epoch: desktopEpoch })))
  })
afterEach(() => vi.unstubAllGlobals())

describe("ComputerUsePreviewPanel", () => {
  it("enables takeover for native desktop while keeping agent-mode viewer read-only", async () => {
    const html = await renderPanel({ preview: preview({
      viewerType: "novnc", controlAvailable: true,
      livePreviewUrl: "http://localhost:6080/vnc.html?autoconnect=true&view_only=false",
    }) })
    const button = html.match(/<button[^>]*data-testid="take-control"[^>]*>/)?.[0]
    expect(button).toBeDefined()
    expect(button).not.toMatch(/\sdisabled(?:=|\s|>)/)
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })
  it("embeds native noVNC and disables takeover when server declares view-only", async () => {
    const html = await renderPanel({ preview: preview({
        viewerType: "novnc", controlAvailable: false,
        livePreviewUrl: "http://localhost:6080/vnc.html?autoconnect=true&view_only=true",
      }) })
    expect(h.iframeProps.at(-1)?.src).toContain(":6080/vnc.html")
    expect(h.iframeProps.at(-1)?.title).toBe("Linux desktop via noVNC")
    expect(html).toMatch(/data-testid="take-control"[^>]*disabled/)
  })
  it("renders the Agent working badge and Take control in agent mode", async () => {
    const html = await renderPanel()
    expect(html).toContain("Agent working")
    expect(html).toContain('data-testid="take-control"')
    expect(html).not.toContain('data-testid="relinquish"')
    expect(html).not.toContain('data-testid="handoff-form"')
  })

  it("renders noVNC view-only with no obsolete postMessage hooks", async () => {
    await renderPanel()
    const iframe = h.iframeProps.at(-1)
    expect(iframe?.src).toBe("https://viewer.test/vnc.html?autoconnect=true&view_only=true")
    expect(iframe?.["data-control-mode"]).toBe("agent")
    expect(iframe?.className ?? "").toContain("pointer-events-none")
    expect(iframe).not.toHaveProperty("onLoad")
    expect(iframe).not.toHaveProperty("ref")
  })

  it("shows the pending approval alert only in agent mode", async () => {
    const html = await renderPanel({ pendingApproval: "navigate to bank.example" })
    expect(html).toContain('data-testid="computer-use-approval"')
    expect(html).toContain("navigate to bank.example")
  })

  it("renders the intent strip under the agent only", async () => {
    const html = await renderPanel()
    expect(html).toContain("navigate")
    expect(html).toContain("open the page")
  })

  it("enables Close and viewer reconnect, without legacy DevTools actions", async () => {
    const html = await renderPanel()
    // Close is a real button and not disabled in agent mode.
    expect(html).toMatch(/data-testid="browser-close"(?![^>]*disabled)/)
    expect(html).toContain('data-testid="viewer-reconnect"')
    expect(html).not.toContain('data-testid="open-devtools"')
    expect(html).not.toContain(">Back<")
    expect(html).not.toContain(">Forward<")
  })

  it.each(["", "not-a-url", "javascript:alert(1)"])("never falls back to the visited URL when noVNC is unavailable: %s", async (livePreviewUrl) => {
    const html = await renderPanel({ preview: preview({ livePreviewUrl }) })
    expect(h.iframeProps).toHaveLength(0)
    expect(html).toContain("Desktop viewer unavailable")
    expect(html).toMatch(/data-testid="take-control"[^>]*disabled/)
  })

  it("requires a declared noVNC viewer and a true session plus release callback", async () => {
    expect(await renderPanel({ preview: preview({ viewerType: undefined }) })).toContain("Desktop viewer unavailable")
    expect(await renderPanel({ sessionId: undefined })).toMatch(/data-testid="take-control"[^>]*disabled/)
    expect(await renderPanel({ onReleaseControl: undefined })).toMatch(/data-testid="take-control"[^>]*disabled/)
  })

  it("does not claim viewer connectivity from an idle or interrupted chat stream", async () => {
    expect(await renderPanel({ isStreaming: false, status: "ready" })).toContain("Chat idle")
    const interrupted = await renderPanel({ isStreaming: false, status: "error" })
    expect(interrupted).toContain("Stream interrupted")
    expect(interrupted).not.toContain("Desktop ready")
  })

})

function panelHandlers(overrides: Partial<React.ComponentProps<typeof ComputerUsePreviewPanel>> = {}) {
    let props: React.ComponentProps<typeof ComputerUsePreviewPanel> = {
      preview: preview(), sessionId: "actual-chat-id", isStreaming: true, status: "streaming", pendingApproval: "",
      onApprove: vi.fn(), onDeny: vi.fn(), onClose: vi.fn(), onTakeControl: vi.fn(async () => { h.serverMode = "human" }),
      onReleaseControl: vi.fn(async () => { h.serverMode = "instructions" }),
      onGiveControl: vi.fn(async () => { h.serverMode = "agent" }), ...overrides,
    }
    const render = (update: Partial<typeof props> = {}) => {
      props = { ...props, ...update }
      h.hookIndex = 0
      h.effectIndex = 0
      h.callbackIndex = 0
      h.renderingPanel = true
      let node: React.ReactNode
      try { node = ComputerUsePreviewPanel(props) } finally { h.renderingPanel = false }
      h.iframeKeys = []
      const collectIframeKeys = (children: React.ReactNode) => {
        React.Children.forEach(children, child => {
          if (!React.isValidElement<{ children?: React.ReactNode }>(child)) return
          if (child.type === WebPreviewBody) h.iframeKeys.push(child.key)
          collectIframeKeys(child.props.children)
        })
      }
      collectIframeKeys(node)
      h.buttons.clear()
      h.iframeProps = []
      h.promptInputProps = []
      return renderToStaticMarkup(node)
    }
    render()
    const flushEffects = async () => {
      for (const index of h.pendingEffects) {
        const effect = h.effects[index]
        effect.cleanup?.()
        effect.cleanup = effect.callback()
      }
      h.pendingEffects.clear()
      await new Promise<void>(resolve => setImmediate(resolve))
    }
    return { render, flushEffects, unmount: () => h.effects.forEach(effect => effect.cleanup?.()) }
  }

  const click = (id: string) => {
    const handler = h.buttons.get(id)?.onClick
    if (!handler) throw new Error(`Missing button ${id}`)
    return handler({} as React.MouseEvent<HTMLButtonElement>)
  }
  const deferred = () => {
    let resolve!: () => void
    let reject!: (error: Error) => void
    const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
  }

describe("ComputerUsePreviewPanel ownership", () => {
  it("preserves adjacent nanosecond epoch strings exactly in the iframe identity", async () => {
    h.serverMode = "human"
    const panel = panelHandlers()
    await panel.flushEffects()
    expect(panel.render()).toContain("You have control")
    expect(h.iframeKeys).toEqual([`actual-chat-id-${desktopEpoch}-0`])
    const nextEpoch = "1789069438950188501"
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ready: true, mode: "human", epoch: nextEpoch }))
    await click("viewer-reconnect")
    expect(panel.render()).toContain("You have control")
    expect(h.iframeKeys).toEqual([`actual-chat-id-${nextEpoch}-1`])
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=false")
  })

  it.each([1, 1789069438950188500, "", "0", "01", "-1", "1.5", "1e18", " 123", "123\n"])(
    "rejects numeric or noncanonical epoch %j without coercion", async epoch => {
      vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ready: true, mode: "human", epoch }))
      const panel = panelHandlers()
      await panel.flushEffects()
      expect(panel.render()).toContain("Recovery required")
      expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
      expect(h.buttons.get("take-control")?.disabled).toBe(true)
    },
  )

  it("recovers from initial owner 409 on first observation and does not refetch after confirmation", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 409 }))
    const panel = panelHandlers()
    await panel.flushEffects()
    expect(panel.render()).toContain("Recovery required")
    expect(h.buttons.get("take-control")?.disabled).toBe(true)

    panel.render({ preview: preview({ observation }) })
    await panel.flushEffects()
    expect(panel.render()).toContain("Agent working")
    expect(h.buttons.get("take-control")?.disabled).toBe(false)
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    expect(fetch).toHaveBeenCalledTimes(2)

    panel.render({ preview: preview({ observation: { ...observation, artifact_id: "979a4f76-2f47-4039-b5ca-30c8bba286bf" } }) })
    await panel.flushEffects()
    panel.render()
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it("retries a failed status once per new observation, including one arriving during the initial request", async () => {
    const initial = deferred()
    vi.mocked(fetch).mockImplementationOnce(async () => {
      await initial.promise
      return new Response(null, { status: 409 })
    }).mockResolvedValueOnce(new Response(null, { status: 503 }))
    const panel = panelHandlers()
    await panel.flushEffects()
    panel.render({ preview: preview({ observation }) })
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledTimes(1)
    initial.resolve()
    await panel.flushEffects()
    panel.render()
    await panel.flushEffects()
    expect(panel.render()).toContain("Recovery required")
    await panel.flushEffects()
    panel.render()
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledTimes(2)

    panel.render({ preview: preview({ observation: { ...observation, artifact_id: "979a4f76-2f47-4039-b5ca-30c8bba286bf" } }) })
    await panel.flushEffects()
    panel.render()
    expect(h.buttons.get("take-control")?.disabled).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it("ignores a late observation-triggered status reply after an authoritative reconnect", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 409 }))
    const panel = panelHandlers()
    await panel.flushEffects()
    panel.render()
    const stale = deferred()
    vi.mocked(fetch).mockImplementationOnce(async () => {
      await stale.promise
      return Response.json({ ready: true, mode: "human", epoch: desktopEpoch })
    })
    panel.render({ preview: preview({ observation }) })
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledTimes(2)
    const oldSignal = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.signal
    h.serverMode = "instructions"
    await click("viewer-reconnect")
    expect(oldSignal?.aborted).toBe(true)
    stale.resolve()
    await panel.flushEffects()
    expect(panel.render()).toContain('data-testid="handoff-form"')
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

  it("does not interrupt a handoff when new screenshot evidence arrives", async () => {
    const take = deferred()
    const panel = panelHandlers({ onTakeControl: () => take.promise })
    await panel.flushEffects()
    panel.render()
    const taking = click("take-control")
    panel.render({ preview: preview({ observation }) })
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(false)
    h.serverMode = "human"
    take.resolve()
    await taking
    expect(panel.render()).toContain("You have control")
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it("never trusts an initial human flag before the mount status request succeeds", async () => {
    h.serverMode = "human"
    const panel = panelHandlers({ initialControlMode: "human" })
    expect(panel.render()).toContain("Checking control")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    expect(panel.render()).toMatch(/data-testid="browser-close"[^>]*disabled/)
    expect(fetch).not.toHaveBeenCalled()
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/orchestrator/handoff?sessionId=actual-chat-id", {
      cache: "no-store", signal: expect.any(AbortSignal),
    })
    expect(panel.render()).toContain("You have control")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=false")
    panel.render({ preview: preview({ sessionId: "another-browser-session" }) })
    await panel.flushEffects()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each(["stopping", "human", "relinquishing", "instructions", "resuming", "recovery"])("restores server mode %s instead of defaulting to agent", async mode => {
    h.serverMode = mode
    const panel = panelHandlers()
    await panel.flushEffects()
    const html = panel.render()
    expect(h.iframeProps.at(-1)?.["data-control-mode"]).toBe(mode)
    expect(h.iframeProps.at(-1)?.src).toContain(mode === "human" ? "view_only=false" : "view_only=true")
    expect(html).toMatch(/data-testid="browser-close"[^>]*disabled/)
    if (mode === "instructions") expect(html).toContain('data-testid="handoff-form"')
    if (mode === "recovery") expect(html).toContain("Recovery required")
  })

  it.each([
    { ready: false, mode: "human", epoch: desktopEpoch },
    { ready: true, mode: "recovery", epoch: desktopEpoch },
    { ready: true, mode: "unknown", epoch: desktopEpoch },
    { ready: "true", mode: "human", epoch: desktopEpoch },
    { ready: true, mode: "human" },
    { ready: true, mode: "human", epoch: "-1" },
  ])("fails closed on unavailable or invalid status %j", async body => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(body)))
    const panel = panelHandlers({ initialControlMode: "human" })
    await panel.flushEffects()
    const html = panel.render()
    expect(html).toContain("Recovery required")
    expect(html).toMatch(/data-testid="take-control"[^>]*disabled/)
    expect(html).not.toContain('data-testid="relinquish"')
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

  it.each([403, 409, 503])("fails closed on HTTP %s during mount", async status => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })))
    const panel = panelHandlers()
    await panel.flushEffects()
    expect(panel.render()).toContain("Could not verify desktop control")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    expect(h.buttons.get("viewer-reconnect")?.disabled).toBe(false)
  })

  it("revokes the interactive viewer on reconnect failure and restores only fresh server ownership", async () => {
    h.serverMode = "human"
    const panel = panelHandlers()
    await panel.flushEffects()
    panel.render()
    vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"))
    const pending = click("viewer-reconnect")
    expect(panel.render()).toContain("Checking control")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    await pending
    expect(panel.render()).toContain("Recovery required")
    h.serverMode = "instructions"
    await click("viewer-reconnect")
    expect(panel.render()).toContain('data-testid="handoff-form"')
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

  it("ignores a late old-session response and cancels status fetching on unmount", async () => {
    let resolve!: (response: Response) => void
    vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>(yes => { resolve = yes }))
    const panel = panelHandlers()
    await panel.flushEffects()
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal
    panel.render({ sessionId: "new-durable-session" })
    await panel.flushEffects()
    expect(signal?.aborted).toBe(true)
    expect(panel.render()).toContain("Agent working")
    resolve(Response.json({ ready: true, mode: "human", epoch: desktopEpoch }))
    await new Promise<void>(done => setImmediate(done))
    expect(panel.render()).toContain("Agent working")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    const latestSignal = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.signal
    panel.unmount()
    expect(latestSignal?.aborted).toBe(true)
  })

  it("does not enable human input on takeover acknowledgment without successful human status", async () => {
    const panel = panelHandlers({ onTakeControl: async () => { h.serverMode = "recovery" } })
    await panel.flushEffects()
    panel.render()
    await click("take-control")
    expect(panel.render()).toContain("Recovery required")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

  it("keeps the viewer read-only between takeover acknowledgment and status confirmation", async () => {
    const status = deferred()
    const panel = panelHandlers()
    await panel.flushEffects()
    panel.render()
    vi.mocked(fetch).mockImplementationOnce(async () => {
      await status.promise
      return Response.json({ ready: true, mode: "human", epoch: desktopEpoch })
    })
    const taking = click("take-control")
    await new Promise<void>(done => setImmediate(done))
    expect(panel.render()).toContain("Stopping")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    expect(h.buttons.get("viewer-reconnect")?.disabled).toBe(true)
    status.resolve()
    await taking
    expect(panel.render()).toContain("You have control")
  })

  it("ignores superseded reconnect replies even when transport ignores cancellation", async () => {
    const panel = panelHandlers()
    await panel.flushEffects()
    panel.render()
    const stale = deferred()
    vi.mocked(fetch).mockImplementationOnce(async () => {
      await stale.promise
      return Response.json({ ready: true, mode: "human", epoch: desktopEpoch })
    })
    const oldRequest = click("viewer-reconnect")
    const oldSignal = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.signal
    h.serverMode = "instructions"
    await click("viewer-reconnect")
    expect(oldSignal?.aborted).toBe(true)
    stale.resolve()
    await oldRequest
    expect(panel.render()).toContain('data-testid="handoff-form"')
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

  it("does not restore client-side human ownership after an ambiguous failed release", async () => {
    h.serverMode = "human"
    const panel = panelHandlers({ onReleaseControl: async () => {
      h.serverMode = "instructions"
      throw new Error("Acknowledgment lost")
    } })
    await panel.flushEffects()
    panel.render()
    await click("relinquish")
    expect(panel.render()).toContain('data-testid="handoff-form"')
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

  it("preserves steering text through observation-triggered recovery in server instructions mode", async () => {
    h.serverMode = "instructions"
    const panel = panelHandlers({ onGiveControl: async () => { h.serverReady = false } })
    await panel.flushEffects()
    panel.render()
    const message = { text: "Keep HUMAN-UPDATED", files: [] }
    const submit = h.promptInputProps.at(-1)?.onSubmit
    if (typeof submit !== "function") throw new Error("Missing submit")
    await expect(submit(message)).rejects.toThrow("Resumption is unconfirmed")
    expect(panel.render()).toContain("Recovery required")
    expect(h.inputProps.value).toBe(message.text)
    expect(h.inputProps.disabled).toBe(true)
    h.serverReady = true
    panel.render({ preview: preview({ observation }) })
    await panel.flushEffects()
    panel.render()
    expect(h.inputProps.value).toBe(message.text)
    expect(h.inputProps.disabled).toBe(false)
  })

  it("waits for takeover and release acknowledgments before input and steering", async () => {
    const take = deferred()
    const release = deferred()
    const onTakeControl = vi.fn(() => take.promise)
    const onReleaseControl = vi.fn(() => release.promise)
    const panel = panelHandlers({ onTakeControl, onReleaseControl })
    await panel.flushEffects()
    panel.render()
    const taking = click("take-control")
    click("take-control")
    expect(onTakeControl).toHaveBeenCalledTimes(1)
    expect(panel.render()).toContain("Stopping")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    h.serverMode = "human"
    take.resolve()
    await taking
    expect(panel.render()).toContain("You have control")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=false")
    const releasing = click("relinquish")
    click("relinquish")
    expect(onReleaseControl).toHaveBeenCalledTimes(1)
    const html = panel.render()
    expect(html).toContain("Relinquishing")
    expect(html).not.toContain('data-testid="handoff-form"')
    expect(html).toMatch(/data-testid="browser-close"[^>]*disabled/)
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    h.serverMode = "instructions"
    release.resolve()
    await releasing
    expect(panel.render()).toContain('data-testid="handoff-form"')
  })

  it("preserves authoritative ownership on a new tool-call panel ID and viewer reconnect", async () => {
    h.serverMode = "human"
    const panel = panelHandlers({ initialControlMode: "human" })
    await panel.flushEffects()
    expect(panel.render({ preview: preview({ sessionId: "new-tool-call" }) })).toContain("You have control")
    const reconnecting = click("viewer-reconnect")
    expect(panel.render()).toContain("Checking control")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    await reconnecting
    expect(panel.render()).toContain("You have control")
    expect(h.iframeProps.at(-1)?.["data-control-mode"]).toBe("human")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=false")
  })

  it("keeps steering text and rejects native submit on resume failure, then clears only on success", async () => {
    const resume = deferred()
    const onGiveControl = vi.fn(() => resume.promise)
    h.serverMode = "instructions"
    const panel = panelHandlers({ initialControlMode: "instructions", onGiveControl })
    await panel.flushEffects()
    panel.render()
    const message = { text: "Keep my HUMAN-UPDATED note", files: [] }
    const submit = h.promptInputProps.at(-1)?.onSubmit
    if (typeof submit !== "function") throw new Error("Missing native submit callback")
    const resuming = submit(message)
    const failed = expect(resuming).rejects.toThrow("Rollover unavailable")
    expect(panel.render()).toContain("Resuming")
    expect(h.inputProps.disabled).toBe(true)
    resume.reject(new Error("Rollover unavailable"))
    await failed
    expect(panel.render()).toContain("Could not resume the agent: Rollover unavailable")
    expect(h.inputProps.value).toBe(message.text)
    expect(h.inputProps.disabled).toBe(false)
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
    panel.render({ onGiveControl: async () => { h.serverMode = "agent" } })
    const retry = h.promptInputProps.at(-1)?.onSubmit
    if (typeof retry !== "function") throw new Error("Missing retry callback")
    await retry(message)
    expect(panel.render()).toContain("Agent working")
    expect(panel.render()).not.toContain('data-testid="handoff-form"')
  })

  it("reports failed release without claiming steering is safe", async () => {
    h.serverMode = "human"
    const panel = panelHandlers({ initialControlMode: "human", onReleaseControl: async () => {
      throw new Error("Native input was not revoked")
    } })
    await panel.flushEffects()
    panel.render()
    await click("relinquish")
    const html = panel.render()
    expect(html).toContain("Could not release control: Native input was not revoked")
    expect(html).toContain("You have control")
    expect(html).not.toContain('data-testid="handoff-form"')
  })

  it("does not enable human input when takeover fails", async () => {
    const panel = panelHandlers({ onTakeControl: async () => { throw new Error("Action still settling") } })
    await panel.flushEffects()
    panel.render()
    await click("take-control")
    expect(panel.render()).toContain("Could not take control: Action still settling")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

})
