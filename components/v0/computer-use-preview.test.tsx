// SSR contracts plus handler tests using explicit hook-state rerenders.
// These do not prove DOM events, React effect lifecycles or native VNC fencing.

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

const h = vi.hoisted(() => ({
  promptInputProps: [] as Array<Record<string, unknown>>,
  iframeProps: [] as Array<Record<string, unknown>>,
  buttons: new Map<string, React.ComponentProps<"button">>(),
  inputProps: {} as React.ComponentProps<"textarea">,
  hooks: [] as unknown[],
  hookIndex: 0,
  renderingPanel: false,
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
    useCallback: (callback: (...args: unknown[]) => unknown, dependencies: unknown[]) =>
      h.renderingPanel ? callback : actual.useCallback(callback, dependencies),
    useEffect: (effect: () => void, dependencies: unknown[]) => {
      if (!h.renderingPanel) actual.useEffect(effect, dependencies)
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

function renderPanel(
  props: Partial<React.ComponentProps<typeof ComputerUsePreviewPanel>> = {}
) {
  return renderToStaticMarkup(
    <ComputerUsePreviewPanel
      isStreaming={true}
      onApprove={() => {}}
      onClose={() => {}}
      onDeny={() => {}}
      onGiveControl={async () => {}}
      onTakeControl={async () => {}}
      onReleaseControl={async () => {}}
      sessionId="actual-chat-id"
      pendingApproval=""
      preview={preview()}
      status="streaming"
      {...props}
    />
  )
}

describe("ComputerUsePreviewPanel", () => {
  beforeEach(() => {
    h.promptInputProps = []
    h.iframeProps = []
    h.hooks = []
    h.hookIndex = 0
    h.renderingPanel = false
    h.buttons.clear()
    h.inputProps = {}
  })
  it("enables takeover for native desktop while keeping agent-mode viewer read-only", () => {
    const html = renderPanel({ preview: preview({
      viewerType: "novnc", controlAvailable: true,
      livePreviewUrl: "http://localhost:6080/vnc.html?autoconnect=true&view_only=false",
    }) })
    const button = html.match(/<button[^>]*data-testid="take-control"[^>]*>/)?.[0]
    expect(button).toBeDefined()
    expect(button).not.toMatch(/\sdisabled(?:=|\s|>)/)
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })
  it("embeds native noVNC and disables takeover when server declares view-only", () => {
    const html = renderPanel({ preview: preview({
        viewerType: "novnc", controlAvailable: false,
        livePreviewUrl: "http://localhost:6080/vnc.html?autoconnect=true&view_only=true",
      }) })
    expect(h.iframeProps.at(-1)?.src).toContain(":6080/vnc.html")
    expect(h.iframeProps.at(-1)?.title).toBe("Linux desktop via noVNC")
    expect(html).toMatch(/data-testid="take-control"[^>]*disabled/)
  })
  it("renders the Agent working badge and Take control in agent mode", () => {
    const html = renderPanel()
    expect(html).toContain("Agent working")
    expect(html).toContain('data-testid="take-control"')
    expect(html).not.toContain('data-testid="relinquish"')
    expect(html).not.toContain('data-testid="handoff-form"')
  })

  it("renders noVNC view-only with no obsolete postMessage hooks", () => {
    renderPanel()
    const iframe = h.iframeProps.at(-1)
    expect(iframe?.src).toBe("https://viewer.test/vnc.html?autoconnect=true&view_only=true")
    expect(iframe?.["data-control-mode"]).toBe("agent")
    expect(iframe?.className ?? "").toContain("pointer-events-none")
    expect(iframe).not.toHaveProperty("onLoad")
    expect(iframe).not.toHaveProperty("ref")
  })

  it("shows the pending approval alert only in agent mode", () => {
    const html = renderPanel({ pendingApproval: "navigate to bank.example" })
    expect(html).toContain('data-testid="computer-use-approval"')
    expect(html).toContain("navigate to bank.example")
  })

  it("renders the intent strip under the agent only", () => {
    const html = renderPanel()
    expect(html).toContain("navigate")
    expect(html).toContain("open the page")
  })

  it("enables Close and viewer reconnect, without legacy DevTools actions", () => {
    const html = renderPanel()
    // Close is a real button and not disabled in agent mode.
    expect(html).toMatch(/data-testid="browser-close"(?![^>]*disabled)/)
    expect(html).toContain('data-testid="viewer-reconnect"')
    expect(html).not.toContain('data-testid="open-devtools"')
    expect(html).not.toContain(">Back<")
    expect(html).not.toContain(">Forward<")
  })

  it.each(["", "not-a-url", "javascript:alert(1)"])("never falls back to the visited URL when noVNC is unavailable: %s", (livePreviewUrl) => {
    const html = renderPanel({ preview: preview({ livePreviewUrl }) })
    expect(h.iframeProps).toHaveLength(0)
    expect(html).toContain("Desktop viewer unavailable")
    expect(html).toMatch(/data-testid="take-control"[^>]*disabled/)
  })

  it("requires a declared noVNC viewer and a true session plus release callback", () => {
    expect(renderPanel({ preview: preview({ viewerType: undefined }) })).toContain("Desktop viewer unavailable")
    expect(renderPanel({ sessionId: undefined })).toMatch(/data-testid="take-control"[^>]*disabled/)
    expect(renderPanel({ onReleaseControl: undefined })).toMatch(/data-testid="take-control"[^>]*disabled/)
  })

  it("does not claim viewer connectivity from an idle or interrupted chat stream", () => {
    expect(renderPanel({ isStreaming: false, status: "ready" })).toContain("Chat idle")
    const interrupted = renderPanel({ isStreaming: false, status: "error" })
    expect(interrupted).toContain("Stream interrupted")
    expect(interrupted).not.toContain("Desktop ready")
  })

  function panelHandlers(overrides: Partial<React.ComponentProps<typeof ComputerUsePreviewPanel>> = {}) {
    let props: React.ComponentProps<typeof ComputerUsePreviewPanel> = {
      preview: preview(), sessionId: "actual-chat-id", isStreaming: true, status: "streaming", pendingApproval: "",
      onApprove: vi.fn(), onDeny: vi.fn(), onClose: vi.fn(), onTakeControl: vi.fn(async () => {}),
      onReleaseControl: vi.fn(async () => {}), onGiveControl: vi.fn(async () => {}), ...overrides,
    }
    const render = (update: Partial<typeof props> = {}) => {
      props = { ...props, ...update }
      h.hookIndex = 0
      h.renderingPanel = true
      let node: React.ReactNode
      try { node = ComputerUsePreviewPanel(props) } finally { h.renderingPanel = false }
      h.buttons.clear()
      h.iframeProps = []
      h.promptInputProps = []
      return renderToStaticMarkup(node)
    }
    render()
    return { render }
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

  it("waits for takeover and release acknowledgments before input and steering", async () => {
    const take = deferred()
    const release = deferred()
    const onTakeControl = vi.fn(() => take.promise)
    const onReleaseControl = vi.fn(() => release.promise)
    const panel = panelHandlers({ onTakeControl, onReleaseControl })
    const taking = click("take-control")
    click("take-control")
    expect(onTakeControl).toHaveBeenCalledTimes(1)
    expect(panel.render()).toContain("Stopping")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
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
    release.resolve()
    await releasing
    expect(panel.render()).toContain('data-testid="handoff-form"')
  })

  it("preserves ownership on a new tool-call panel ID and viewer reconnect", () => {
    const panel = panelHandlers({ initialControlMode: "human" })
    expect(panel.render({ preview: preview({ sessionId: "new-tool-call" }) })).toContain("You have control")
    click("viewer-reconnect")
    expect(panel.render()).toContain("You have control")
    expect(h.iframeProps.at(-1)?.["data-control-mode"]).toBe("human")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=false")
  })

  it("keeps steering text and rejects native submit on resume failure, then clears only on success", async () => {
    const resume = deferred()
    const onGiveControl = vi.fn(() => resume.promise)
    const panel = panelHandlers({ initialControlMode: "instructions", onGiveControl })
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
    panel.render({ onGiveControl: async () => {} })
    const retry = h.promptInputProps.at(-1)?.onSubmit
    if (typeof retry !== "function") throw new Error("Missing retry callback")
    await retry(message)
    expect(panel.render()).toContain("Agent working")
    expect(panel.render()).not.toContain('data-testid="handoff-form"')
  })

  it("reports failed release without claiming steering is safe", async () => {
    const panel = panelHandlers({ initialControlMode: "human", onReleaseControl: async () => {
      throw new Error("Native input was not revoked")
    } })
    await click("relinquish")
    const html = panel.render()
    expect(html).toContain("Could not release control: Native input was not revoked")
    expect(html).toContain("You have control")
    expect(html).not.toContain('data-testid="handoff-form"')
  })

  it("does not enable human input when takeover fails", async () => {
    const panel = panelHandlers({ onTakeControl: async () => { throw new Error("Action still settling") } })
    await click("take-control")
    expect(panel.render()).toContain("Could not take control: Action still settling")
    expect(h.iframeProps.at(-1)?.src).toContain("view_only=true")
  })

})
