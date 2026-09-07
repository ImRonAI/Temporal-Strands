// Render tests for components/v0/computer-use-preview.tsx.
//
// Environment: the repo's vitest runs in the default node environment (no
// jsdom), so the panel renders through react-dom/server.renderToStaticMarkup
// and the heavy AI Elements primitives are mocked (SSR keeps children + props,
// which is what these tests assert on). Effects never run under SSR, so the
// postMessage handshake and the async take/give transitions are verified by
// their rendered contracts (control flag on the iframe, disabled states,
// badge text, submit wiring) rather than by simulating browser events.

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

const h = vi.hoisted(() => ({
  promptInputProps: [] as Array<Record<string, unknown>>,
  iframeProps: [] as Array<Record<string, unknown>>,
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
  }) => (
    <button
      aria-label={tooltip}
      data-testid={(rest["data-testid"] as string) ?? tooltip}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  ),
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
  PromptInputTextarea: (props: Record<string, unknown>) => (
    <textarea data-testid="handoff-input" disabled={props.disabled as boolean} />
  ),
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
    livePreviewUrl: "https://viewer.test/live",
    devtoolsFrontendUrl: "https://devtools.test/inspector",
    action: "navigate",
    intent: "open the page",
    screenshot: null,
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
      pendingApproval=""
      preview={preview()}
      status="streaming"
      {...props}
    />
  )
}

describe("ComputerUsePreviewPanel", () => {
  it("renders the Agent working badge and Take control in agent mode", () => {
    const html = renderPanel()
    expect(html).toContain("Agent working")
    expect(html).toContain('data-testid="take-control"')
    expect(html).not.toContain('data-testid="relinquish"')
    expect(html).not.toContain('data-testid="handoff-form"')
  })

  it("keeps the iframe mounted with pointer events off and control mode agent", () => {
    renderPanel()
    const iframe = h.iframeProps.at(-1)
    expect(iframe?.src).toBe("https://viewer.test/live")
    expect(iframe?.["data-control-mode"]).toBe("agent")
    expect(iframe?.className ?? "").toContain("pointer-events-none")
    // Iframe src must never carry a control query flag — control is postMessage.
    expect(String(iframe?.src)).not.toContain("control")
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

  it("enables Close in agent mode and wires the reconnect + devtools actions", () => {
    const html = renderPanel()
    // Close is a real button and not disabled in agent mode.
    expect(html).toMatch(/data-testid="browser-close"(?![^>]*disabled)/)
    expect(html).toContain('data-testid="viewer-reconnect"')
    expect(html).toContain('data-testid="open-devtools"')
    // Back/forward are gone — the parent viewer owns history via CDP.
    expect(html).not.toContain(">Back<")
    expect(html).not.toContain(">Forward<")
  })

  it("disables DevTools when the session has no inspector URL", () => {
    const html = renderPanel({ preview: preview({ devtoolsFrontendUrl: "" }) })
    expect(html).toMatch(/data-testid="open-devtools"[^>]*disabled/)
  })

})
