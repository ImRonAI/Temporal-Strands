// Regression tests for components/v0/agent-chat.tsx (GWEN chat extraction).
//
// Environment notes: the repo's vitest runs in the default node environment
// (no jsdom/happy-dom installed), so these tests render with
// react-dom/server.renderToStaticMarkup and mock every heavy subcomponent.
// Behavior (submit, approval answers, suggestion clicks) is exercised through
// the props the component hands to those mocks during render — the same
// functions the real UI would invoke — rather than through DOM events.
// Effects (useEffect / useImperativeHandle) intentionally do not run in SSR;
// the imperative handle path is covered indirectly via compare-view.test.tsx.

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { DEFAULT_MODEL } from "@/lib/perplexity"

// ---------------------------------------------------------------------------
// Hoisted capture state shared with the vi.mock factories below.
// ---------------------------------------------------------------------------

const h = vi.hoisted(() => {
  const state = {
    // Controls what the mocked useChat returns for the next render.
    chat: {
      messages: [] as Array<{
        id: string
        role: "user" | "assistant"
        parts: Array<Record<string, unknown>>
      }>,
      status: "ready" as string,
      sendMessage: (() => {}) as (...args: unknown[]) => void,
      stop: () => {},
      error: undefined as Error | undefined,
    },
    useChatCalls: [] as Array<Record<string, unknown>>,
    transportOptions: [] as Array<Record<string, unknown>>,
    composerProps: [] as Array<Record<string, unknown>>,
    buttonProps: [] as Array<Record<string, unknown>>,
    suggestionProps: [] as Array<Record<string, unknown>>,
    previewPanelProps: [] as Array<Record<string, unknown>>,
    computerUsePreviewResult: {
      open: false,
      sessionId: "",
    } as Record<string, unknown>,
    projectIdePreviewResult: {
      open: false,
      sessionId: "",
      isDevServer: false,
      previewUrl: "",
    } as Record<string, unknown>,
  }
  return state
})

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@ai-sdk/react", () => ({
  useChat: (init: Record<string, unknown>) => {
    h.useChatCalls.push(init)
    return h.chat
  },
}))

vi.mock("ai", () => ({
  DefaultChatTransport: class {
    options: Record<string, unknown>
    constructor(options: Record<string, unknown>) {
      this.options = options
      h.transportOptions.push(options)
    }
  },
  isTextUIPart: (part: { type?: string }) => part.type === "text",
  isFileUIPart: (part: { type?: string }) => part.type === "file",
}))

vi.mock("motion/react", () => {
  function passthrough(Tag: string) {
    function MotionPassthrough({
      children,
      className,
    }: {
      children?: React.ReactNode
      className?: string
    }) {
      return React.createElement(Tag, { className }, children)
    }
    MotionPassthrough.displayName = `MotionPassthrough(${Tag})`
    return MotionPassthrough
  }
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => (
      <>{children}</>
    ),
    useReducedMotion: () => true,
    motion: new Proxy(
      {},
      { get: (_target, tag: string) => passthrough(tag === "span" ? "span" : "div") },
    ),
  }
})

vi.mock("@/components/ai-elements/conversation", () => ({
  Conversation: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="conversation">{children}</div>
  ),
  ConversationContent: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  ConversationEmptyState: ({ title }: { title?: string }) => (
    <div data-testid="conversation-empty">{title}</div>
  ),
  ConversationScrollButton: () => null,
}))

vi.mock("@/components/ai-elements/message", () => ({
  Message: ({ from, children }: { from?: string; children?: React.ReactNode }) => (
    <div data-testid="message" data-from={from}>
      {children}
    </div>
  ),
  MessageContent: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  MessageResponse: ({
    children,
    isAnimating,
    linkSafety,
  }: {
    children?: React.ReactNode
    isAnimating?: boolean
    linkSafety?: { enabled: boolean }
  }) => (
    <div
      data-testid="message-response"
      data-animating={String(Boolean(isAnimating))}
      data-link-safety={JSON.stringify(linkSafety)}
    >
      {children}
    </div>
  ),
}))

vi.mock("@/components/ai-elements/image", () => ({
  Image: ({
    alt,
    mediaType,
    base64,
  }: {
    alt?: string
    mediaType?: string
    base64?: string
  }) => (
    <div
      data-testid="attachment-image"
      data-alt={alt}
      data-media-type={mediaType}
      data-base64={base64}
    />
  ),
}))

vi.mock("@/components/ai-elements/suggestion", () => ({
  Suggestions: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="suggestions">{children}</div>
  ),
  Suggestion: (props: { suggestion: string; onClick?: (s: string) => void }) => {
    h.suggestionProps.push(props)
    return <span data-testid="suggestion">{props.suggestion}</span>
  },
}))

vi.mock("@/components/ui/alert", () => ({
  Alert: ({
    children,
    ...rest
  }: {
    children?: React.ReactNode
    [key: string]: unknown
  }) => (
    <div data-testid={(rest["data-testid"] as string) ?? "alert"}>{children}</div>
  ),
  AlertTitle: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="alert-title">{children}</div>
  ),
  AlertDescription: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))

vi.mock("@/components/ui/button", () => ({
  Button: (props: { children?: React.ReactNode; onClick?: () => void }) => {
    h.buttonProps.push(props)
    return <button>{props.children}</button>
  },
}))

vi.mock("@/components/v0/agent-activity", () => ({
  AgentActivity: ({
    parts,
    isThinking,
  }: {
    parts: unknown[]
    isThinking?: boolean
  }) => (
    <div
      data-testid="agent-activity"
      data-thinking={String(Boolean(isThinking))}
      data-part-count={parts.length}
    />
  ),
}))

vi.mock("@/components/v0/graph-activity", () => ({
  GraphActivity: ({ parts }: { parts: unknown[] }) => (
    <div data-testid="graph-activity" data-part-count={parts.length} />
  ),
}))

vi.mock("@/components/v0/blurple-background", () => ({
  BlurpleBackground: () => <div data-testid="blurple-background" />,
}))

vi.mock("@/components/v0/composer", () => ({
  Composer: (props: Record<string, unknown>) => {
    h.composerProps.push(props)
    return (
      <div
        data-testid="composer"
        data-model={props.model as string}
        data-status={props.status as string}
        data-placeholder={props.placeholder as string}
      />
    )
  },
}))

vi.mock("@/components/v0/computer-use", () => ({
  computerUsePreview: () => h.computerUsePreviewResult,
}))

vi.mock("@/components/v0/computer-use-preview", () => ({
  ComputerUsePreviewPanel: (props: Record<string, unknown>) => {
    h.previewPanelProps.push(props)
    return (
      <div
        data-testid="computer-use-preview-panel"
        data-pending-approval={props.pendingApproval as string}
      />
    )
  },
}))

vi.mock("@/components/v0/project-ide", () => ({
  projectIdePreview: () => h.projectIdePreviewResult,
}))

vi.mock("@/components/v0/project-ide-panel", () => ({
  ProjectIdePanel: () => <div data-testid="project-ide-panel" />,
}))

vi.mock("@/components/v0/site-header", () => ({
  SiteHeader: () => <header data-testid="site-header" />,
}))

// ---------------------------------------------------------------------------
// Subject under test (imported after mocks)
// ---------------------------------------------------------------------------

import { AgentChat } from "@/components/v0/agent-chat"
import Page from "@/app/page"

type Part = Record<string, unknown>

function setChat(overrides: Partial<typeof h.chat>) {
  Object.assign(h.chat, overrides)
}

function render(props: React.ComponentProps<typeof AgentChat> = {}) {
  return renderToStaticMarkup(<AgentChat {...props} />)
}

const textPart = (text: string): Part => ({ type: "text", text })
const sessionPart = (sessionId: string): Part => ({
  type: "data-session",
  data: { sessionId },
})
const approvalPart = (reason: string): Part => ({
  type: "data-approval",
  data: { reason },
})

beforeEach(() => {
  h.chat.messages = []
  h.chat.status = "ready"
  h.chat.sendMessage = vi.fn()
  h.chat.stop = vi.fn()
  h.chat.error = undefined
  h.useChatCalls.length = 0
  h.transportOptions.length = 0
  h.composerProps.length = 0
  h.buttonProps.length = 0
  h.suggestionProps.length = 0
  h.previewPanelProps.length = 0
  h.computerUsePreviewResult = { open: false, sessionId: "" }
  h.projectIdePreviewResult = {
    open: false,
    sessionId: "",
    isDevServer: false,
    previewUrl: "",
  }
})

describe("AgentChat transport wiring", () => {
  it("creates a DefaultChatTransport against /api/orchestrator and passes it to useChat with throttling", () => {
    render()
    expect(h.transportOptions).toEqual([{ api: "/api/orchestrator" }])
    expect(h.useChatCalls).toHaveLength(1)
    const init = h.useChatCalls[0]
    expect(init.throttle).toBe(50)
    expect((init.transport as { options: { api: string } }).options.api).toBe(
      "/api/orchestrator",
    )
  })

  it("uses the same /api/orchestrator transport when rendered as a compare pane", () => {
    render({ paneId: "pane-0", model: "gemini-3.8-flash" })
    expect(h.transportOptions).toEqual([{ api: "/api/orchestrator" }])
  })
})

describe("AgentChat hero vs conversation vs pane", () => {
  it("renders the hero with suggestions and a composer when there are no messages", () => {
    const html = render()
    expect(html).toContain("data-testid=\"suggestions\"")
    expect(html).toContain("data-testid=\"composer\"")
    expect(html).toContain("data-testid=\"blurple-background\"")
    expect(html).not.toContain("data-testid=\"conversation\"")
  })

  it("renders the conversation surface once messages exist", () => {
    setChat({
      messages: [{ id: "u1", role: "user", parts: [textPart("hi")] }],
    })
    const html = render()
    expect(html).toContain("data-testid=\"conversation\"")
    expect(html).not.toContain("data-testid=\"suggestions\"")
  })

  it("skips the hero and background in pane mode, showing the empty state instead", () => {
    const html = render({ paneId: "pane-0", model: "preset:fast" })
    expect(html).toContain("data-testid=\"conversation\"")
    expect(html).toContain("data-testid=\"conversation-empty\"")
    expect(html).toContain("No response yet")
    expect(html).not.toContain("data-testid=\"blurple-background\"")
    expect(html).not.toContain("data-testid=\"suggestions\"")
  })
})

describe("AgentChat message rendering", () => {
  it("renders AgentActivity and GraphActivity for assistant messages only", () => {
    setChat({
      messages: [
        { id: "u1", role: "user", parts: [textPart("prompt")] },
        { id: "a1", role: "assistant", parts: [textPart("answer")] },
      ],
    })
    const html = render()
    expect(html.match(/data-testid="agent-activity"/g)).toHaveLength(1)
    expect(html.match(/data-testid="graph-activity"/g)).toHaveLength(1)
    expect(html.match(/data-testid="message-response"/g)).toHaveLength(2)
  })

  it("animates only the last message while streaming and always disables link safety", () => {
    setChat({
      status: "streaming",
      messages: [
        { id: "a1", role: "assistant", parts: [textPart("first")] },
        { id: "a2", role: "assistant", parts: [textPart("second")] },
      ],
    })
    const html = render()
    const responses = [...html.matchAll(/data-animating="(\w+)"/g)].map(
      (m) => m[1],
    )
    expect(responses).toEqual(["false", "true"])
    expect(html).toContain(
      `data-link-safety="${JSON.stringify({ enabled: false }).replace(/"/g, "&quot;")}"`,
    )
  })

  it("stops animating once the stream finishes", () => {
    setChat({
      status: "ready",
      messages: [{ id: "a1", role: "assistant", parts: [textPart("done")] }],
    })
    const html = render()
    expect(html).toContain('data-animating="false"')
    expect(html).not.toContain('data-animating="true"')
  })

  it("renders user file attachments through the Image primitive with the data-URL payload", () => {
    setChat({
      messages: [
        {
          id: "u1",
          role: "user",
          parts: [
            {
              type: "file",
              url: "data:image/png;base64,QUJD",
              mediaType: "image/png",
              filename: "shot.png",
            },
          ],
        },
      ],
    })
    const html = render()
    expect(html).toContain('data-testid="attachment-image"')
    expect(html).toContain('data-alt="shot.png"')
    expect(html).toContain('data-media-type="image/png"')
    expect(html).toContain('data-base64="QUJD"')
  })
})

describe("AgentChat pending state and errors", () => {
  it("shows a thinking assistant shell while a just-submitted turn awaits its first token", () => {
    setChat({
      status: "submitted",
      messages: [{ id: "u1", role: "user", parts: [textPart("go")] }],
    })
    const html = render()
    const thinking = [...html.matchAll(/data-thinking="(\w+)"/g)].map((m) => m[1])
    expect(thinking).toContain("true")
    expect(html).toContain('data-part-count="0"')
  })

  it("does not render the pending shell when the last message is already an assistant reply", () => {
    setChat({
      status: "ready",
      messages: [
        { id: "u1", role: "user", parts: [textPart("go")] },
        { id: "a1", role: "assistant", parts: [textPart("done")] },
      ],
    })
    const html = render()
    expect(html).not.toContain('data-part-count="0"')
  })

  it("surfaces useChat errors in the destructive alert", () => {
    setChat({
      messages: [{ id: "u1", role: "user", parts: [textPart("go")] }],
      error: new Error("orchestrator unreachable"),
    })
    const html = render()
    expect(html).toContain('data-testid="chat-error"')
    expect(html).toContain("Request failed")
    expect(html).toContain("orchestrator unreachable")
  })
})

describe("AgentChat submit behavior", () => {
  function capturedComposerSubmit() {
    const composer = h.composerProps.at(-1)
    expect(composer).toBeDefined()
    return composer!.onSubmit as (message: {
      text?: string
      files?: unknown[]
    }) => void
  }

  it("sends text with the selected model and durable session id in the turn body", () => {
    setChat({
      messages: [
        {
          id: "a1",
          role: "assistant",
          parts: [sessionPart("sess-42"), textPart("hello")],
        },
      ],
    })
    render()
    capturedComposerSubmit()({ text: "next step", files: [] })
    expect(h.chat.sendMessage).toHaveBeenCalledTimes(1)
    expect(h.chat.sendMessage).toHaveBeenCalledWith(
      { text: "next step", files: [] },
      { body: { model: DEFAULT_MODEL, sessionId: "sess-42" } },
    )
  })

  it("uses the controlled pane model instead of the local default", () => {
    render({ paneId: "pane-1", model: "gemini-3.8-flash" })
    capturedComposerSubmit()({ text: "compare me" })
    expect(h.chat.sendMessage).toHaveBeenCalledWith(
      { text: "compare me", files: undefined },
      { body: { model: "gemini-3.8-flash", sessionId: undefined } },
    )
  })

  it("allows attachment-only submissions", () => {
    render()
    capturedComposerSubmit()({ text: "", files: [{ url: "data:x" }] })
    expect(h.chat.sendMessage).toHaveBeenCalledTimes(1)
  })

  it("ignores empty submissions", () => {
    render()
    capturedComposerSubmit()({ text: "   ", files: [] })
    expect(h.chat.sendMessage).not.toHaveBeenCalled()
  })

  it("refuses to double-send while a turn is submitted or streaming", () => {
    for (const status of ["submitted", "streaming"]) {
      h.composerProps.length = 0
      setChat({
        status,
        messages: [{ id: "u1", role: "user", parts: [textPart("go")] }],
      })
      render()
      capturedComposerSubmit()({ text: "again", files: [] })
    }
    expect(h.chat.sendMessage).not.toHaveBeenCalled()
  })

  it("sends suggestion clicks as regular turns with the model body", () => {
    render()
    expect(h.suggestionProps.length).toBeGreaterThan(0)
    const { suggestion, onClick } = h.suggestionProps[0] as {
      suggestion: string
      onClick: (s: string) => void
    }
    onClick(suggestion)
    expect(h.chat.sendMessage).toHaveBeenCalledWith(
      { text: suggestion },
      { body: { model: DEFAULT_MODEL, sessionId: undefined } },
    )
  })

  it("hands the composer live status, model, and the useChat stop handle", () => {
    setChat({
      status: "streaming",
      messages: [{ id: "u1", role: "user", parts: [textPart("go")] }],
    })
    render()
    const composer = h.composerProps.at(-1)!
    expect(composer.status).toBe("streaming")
    expect(composer.model).toBe(DEFAULT_MODEL)
    expect(composer.onStop).toBe(h.chat.stop)
  })
})

describe("AgentChat approval flow", () => {
  it("renders the pushed data-approval prompt and posts the answer with the session id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal("fetch", fetchMock)
    try {
      setChat({
        status: "streaming",
        messages: [
          {
            id: "a1",
            role: "assistant",
            parts: [sessionPart("sess-7"), approvalPart("Run the deploy tool?")],
          },
        ],
      })
      const html = render()
      expect(html).toContain("Approval needed")
      expect(html).toContain("Run the deploy tool?")

      const approve = h.buttonProps.find(
        (b) => b.children === "Approve",
      ) as { onClick: () => Promise<void> }
      const deny = h.buttonProps.find((b) => b.children === "Deny")
      expect(approve).toBeDefined()
      expect(deny).toBeDefined()

      await approve.onClick()
      expect(fetchMock).toHaveBeenCalledWith("/api/orchestrator/approval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId: "sess-7", response: "approve" }),
      })
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it("routes a pending approval into the browser preview panel instead of the alert when the preview is open", () => {
    h.computerUsePreviewResult = { open: true, sessionId: "cu-1" }
    setChat({
      status: "streaming",
      messages: [
        {
          id: "a1",
          role: "assistant",
          parts: [sessionPart("sess-7"), approvalPart("Take over the browser?")],
        },
      ],
    })
    const html = render()
    expect(html).toContain('data-testid="computer-use-preview-panel"')
    expect(html).toContain('data-pending-approval="Take over the browser?"')
    expect(html).not.toContain("Approval needed")
  })
})

describe("main page composition", () => {
  it("app/page.tsx renders the extracted AgentChat as the full-page chat", () => {
    const html = renderToStaticMarkup(<Page />)
    expect(html).toContain('data-testid="site-header"')
    // The hero composer and suggestions prove the real AgentChat rendered.
    expect(html).toContain('data-testid="composer"')
    expect(html).toContain('data-testid="suggestions"')
    expect(h.transportOptions).toEqual([{ api: "/api/orchestrator" }])
  })
})
