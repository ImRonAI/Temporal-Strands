// Regression tests for components/v0/compare-view.tsx.
//
// CompareView composes 2-5 independent AgentChat panes (the exact same
// component the main page renders) behind one shared broadcast Composer.
// The repo's vitest runs in the node environment with no DOM library, so
// rendering uses react-dom/server.renderToStaticMarkup with AgentChat and
// the Composer mocked. The AgentChat mock invokes the ref-as-prop callback
// during render, which populates CompareView's imperative-handle map exactly
// the way the real component's useImperativeHandle would — letting the
// shared-Composer broadcast and stop paths run against real CompareView code.
// State transitions that require a second committed render (add/remove pane)
// are out of SSR reach and are covered by prop/guard assertions instead.

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { DEFAULT_MODEL } from "@/lib/perplexity"
import type { AgentChatHandle } from "@/components/v0/agent-chat"

const h = vi.hoisted(() => ({
  models: [] as Array<{ id: string; owned_by: string }>,
  agentChatProps: [] as Array<Record<string, unknown>>,
  paneHandles: new Map<string, { submit: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }>(),
  composerProps: [] as Array<Record<string, unknown>>,
  buttonProps: [] as Array<Record<string, unknown>>,
}))

vi.mock("./use-models", () => ({
  useModels: () => ({ models: h.models, status: "ready", error: null }),
}))

vi.mock("@/components/v0/agent-chat", () => ({
  AgentChat: (props: {
    ref?: (handle: AgentChatHandle | null) => void
    paneId?: string
    model?: string
  }) => {
    h.agentChatProps.push(props as Record<string, unknown>)
    if (props.ref && props.paneId) {
      const handle = { submit: vi.fn(), stop: vi.fn() }
      h.paneHandles.set(props.paneId, handle)
      // React 19 ref-as-prop: attach the imperative handle the same way
      // useImperativeHandle would after mount.
      props.ref(handle)
    }
    return (
      <div
        data-testid="agent-chat"
        data-pane-id={props.paneId}
        data-model={props.model}
      />
    )
  },
}))

vi.mock("@/components/v0/composer", () => ({
  Composer: (props: Record<string, unknown>) => {
    h.composerProps.push(props)
    return (
      <div
        data-testid="composer"
        data-status={props.status as string}
        data-placeholder={props.placeholder as string}
      >
        {props.modelControls as React.ReactNode}
      </div>
    )
  },
}))

vi.mock("@/components/ui/button", () => ({
  Button: (props: {
    children?: React.ReactNode
    disabled?: boolean
    "aria-label"?: string
    onClick?: () => void
  }) => {
    h.buttonProps.push(props as Record<string, unknown>)
    return (
      <button aria-label={props["aria-label"]} disabled={props.disabled}>
        {props.children}
      </button>
    )
  },
}))

vi.mock("@/components/ui/spinner", () => ({
  Spinner: () => <span data-testid="spinner" />,
}))

vi.mock("lucide-react", () => ({
  PlusIcon: () => <span data-testid="plus-icon" />,
  XIcon: () => <span data-testid="x-icon" />,
}))

vi.mock("@/components/v0/blurple-background", () => ({
  BlurpleBackground: () => <div data-testid="blurple-background" />,
}))

vi.mock("@/components/v0/site-header", () => ({
  SiteHeader: () => <header data-testid="site-header" />,
}))

import { CompareView } from "@/components/v0/compare-view"
import ComparePage from "@/app/compare/page"

function render() {
  return renderToStaticMarkup(<CompareView />)
}

beforeEach(() => {
  h.models = [
    { id: DEFAULT_MODEL, owned_by: "perplexity" },
    { id: "preset:fast", owned_by: "perplexity" },
    { id: "gemini-3.8-flash", owned_by: "google" },
  ]
  h.agentChatProps.length = 0
  h.paneHandles.clear()
  h.composerProps.length = 0
  h.buttonProps.length = 0
})

describe("CompareView initial panes", () => {
  it("starts with exactly two panes, each an independent AgentChat instance", () => {
    const html = render()
    expect(html.match(/data-testid="compare-pane"/g)).toHaveLength(2)
    expect(h.agentChatProps).toHaveLength(2)
    const paneIds = h.agentChatProps.map((p) => p.paneId)
    expect(paneIds).toEqual(["pane-0", "pane-1"])
    // Distinct pane identities → independent durable sessions.
    expect(new Set(paneIds).size).toBe(2)
  })

  it("gives the first pane the default model and resolves the second to a different catalog model", () => {
    render()
    const models = h.agentChatProps.map((p) => p.model)
    expect(models[0]).toBe(DEFAULT_MODEL)
    expect(models[1]).toBe("preset:fast")
    expect(models[1]).not.toBe(models[0])
  })

  it("falls back to the default model for the second pane when the catalog has nothing else", () => {
    h.models = [{ id: DEFAULT_MODEL, owned_by: "perplexity" }]
    render()
    expect(h.agentChatProps.map((p) => p.model)).toEqual([
      DEFAULT_MODEL,
      DEFAULT_MODEL,
    ])
  })

  it("renders every pane through the same full AgentChat surface (identical chat UI per model)", () => {
    const html = render()
    // Both panes render the AgentChat component imported from
    // @/components/v0/agent-chat — the same module app/page.tsx uses —
    // with the full pane prop contract.
    expect(html.match(/data-testid="agent-chat"/g)).toHaveLength(2)
    for (const props of h.agentChatProps) {
      expect(typeof props.paneId).toBe("string")
      expect(typeof props.model).toBe("string")
      expect(typeof props.onModelChange).toBe("function")
      expect(typeof props.onStatusChange).toBe("function")
      expect(typeof props.ref).toBe("function")
    }
    // Pane headers label each model for the user.
    expect(html).toContain(`Model 1: ${DEFAULT_MODEL}`)
    expect(html).toContain("Model 2: preset:fast")
  })
})

describe("CompareView pane management guards", () => {
  it("shows no remove buttons at the two-pane minimum", () => {
    const html = render()
    expect(html).not.toContain("Remove model")
    expect(html).not.toContain('data-testid="x-icon"')
  })

  it("offers an enabled Add model button below the five-pane maximum", () => {
    render()
    const addButton = h.buttonProps.find(
      (b) =>
        Array.isArray(b.children) &&
        (b.children as React.ReactNode[]).some((c) => c === "Add model"),
    )
    expect(addButton).toBeDefined()
    expect(addButton!.disabled).toBe(false)
  })

  it("reports the pane count in the header and the shared composer controls", () => {
    const html = render()
    expect(html).toContain("2 models")
    expect(html).toContain("Send to all 2 models")
  })
})

describe("CompareView shared Composer broadcast", () => {
  function composer() {
    const props = h.composerProps.at(-1)
    expect(props).toBeDefined()
    return props! as {
      onSubmit: (message: { text: string; files: unknown[] }) => void
      onStop: () => void
      status: string
      text: string
    }
  }

  it("broadcasts one shared prompt to every pane's chat handle", () => {
    render()
    const message = { text: "build a pricing page", files: [] }
    composer().onSubmit(message)
    expect(h.paneHandles.size).toBe(2)
    for (const handle of h.paneHandles.values()) {
      expect(handle.submit).toHaveBeenCalledTimes(1)
      expect(handle.submit).toHaveBeenCalledWith(message)
    }
  })

  it("broadcasts attachment-only messages", () => {
    render()
    composer().onSubmit({ text: "", files: [{ url: "data:image/png;base64,x" }] })
    for (const handle of h.paneHandles.values()) {
      expect(handle.submit).toHaveBeenCalledTimes(1)
    }
  })

  it("ignores empty shared prompts", () => {
    render()
    composer().onSubmit({ text: "   ", files: [] })
    for (const handle of h.paneHandles.values()) {
      expect(handle.submit).not.toHaveBeenCalled()
    }
  })

  it("stops every pane from the shared composer stop control", () => {
    render()
    composer().onStop()
    expect(h.paneHandles.size).toBe(2)
    for (const handle of h.paneHandles.values()) {
      expect(handle.stop).toHaveBeenCalledTimes(1)
    }
  })

  it("idles the shared composer when no pane is busy", () => {
    render()
    expect(composer().status).toBe("ready")
    expect(h.composerProps.at(-1)!.placeholder).toBe(
      "Ask all selected models the same thing...",
    )
  })
})

describe("compare page composition", () => {
  it("app/compare/page.tsx renders CompareView with two AgentChat panes", () => {
    const html = renderToStaticMarkup(<ComparePage />)
    expect(html).toContain('data-testid="site-header"')
    expect(html).toContain("Compare models")
    expect(html.match(/data-testid="compare-pane"/g)).toHaveLength(2)
    expect(html.match(/data-testid="agent-chat"/g)).toHaveLength(2)
    expect(html).toContain('data-testid="composer"')
  })
})
