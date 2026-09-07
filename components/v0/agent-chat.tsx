"use client"

import { useChat } from "@ai-sdk/react"
import { isFileUIPart, isTextUIPart, DefaultChatTransport } from "ai"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useEffect, useImperativeHandle, useRef, useState } from "react"
import type { ReactNode, Ref } from "react"
import type { ChatStatus } from "ai"

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input"

import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import {
  Message,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message"
import { Image } from "@/components/ai-elements/image"
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { DEFAULT_MODEL } from "@/lib/perplexity"
import { cn } from "@/lib/utils"
import { AgentActivity } from "@/components/v0/agent-activity"
import { BlurpleBackground } from "@/components/v0/blurple-background"
import { GraphActivity } from "@/components/v0/graph-activity"
import { computerUsePreview } from "@/components/v0/computer-use"
import { ComputerUsePreviewPanel } from "@/components/v0/computer-use-preview"
import { Composer } from "@/components/v0/composer"
import { reasoningLevels } from "@/components/v0/model-picker"
import { projectIdePreview } from "@/components/v0/project-ide"
import { ProjectIdePanel } from "@/components/v0/project-ide-panel"

const LINK_SAFETY = { enabled: false } as const

// One curve for the whole surface: fast attack, long silk tail. Every
// entrance below uses it, which is what makes the page feel single-authored.
const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1]

const SUGGESTIONS = [
  "A pricing page with a yearly toggle",
  "An analytics dashboard with charts",
  "A waitlist landing page",
  "A Kanban board with drag and drop",
  "A settings page with tabs",
]

const HEADLINE: { word: string; accent?: boolean }[] = [
  { word: "What" },
  { word: "should" },
  { word: "we" },
  { word: "ship", accent: true },
  { word: "today?" },
]

/** Fade-rise-blur block used for every hero element; delay is the only
 *  choreography input, so the build order reads top-to-bottom. */
function Reveal({
  children,
  delay = 0,
  reduce,
  className,
}: {
  children: ReactNode
  delay?: number
  reduce: boolean
  className?: string
}) {
  return (
    <motion.div
      className={className}
      initial={reduce ? false : { opacity: 0, y: 18, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.7, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  )
}

/** Masked per-word headline: each word rises out of an overflow-hidden slot
 *  with a whisper of rotation. One-time, mount-only. */
function Headline({ reduce }: { reduce: boolean }) {
  return (
    <h1 className="text-balance text-center font-editorial text-5xl leading-[0.95] tracking-tight text-foreground sm:text-7xl">
      {HEADLINE.map(({ word, accent }, i) => (
        <span
          key={word}
          className="-mb-[0.1em] inline-block overflow-hidden pb-[0.1em] align-bottom"
        >
          <motion.span
            className={cn(
              "inline-block",
              accent &&
                "italic text-blurple-bright [text-shadow:0_0_40px_oklch(0.7_0.2_285/0.35)]"
            )}
            initial={reduce ? false : { y: "115%", rotate: 3 }}
            animate={{ y: "0%", rotate: 0 }}
            transition={{ duration: 0.9, delay: 0.2 + i * 0.07, ease: EASE }}
          >
            {word}
          </motion.span>
          {i < HEADLINE.length - 1 ? "\u00A0" : null}
        </span>
      ))}
    </h1>
  )
}

/** Mount-only message entrance. initial/animate fire once per message id, so
 *  streaming re-renders never retrigger it — tokens flow, the frame is still. */
function MessageShell({
  children,
  reduce,
}: {
  children: ReactNode
  reduce: boolean
}) {
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 16, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.55, ease: EASE }}
    >
      {children}
    </motion.div>
  )
}

export type AgentChatHandle = {
  submit: (message: PromptInputMessage) => void
  stop: () => void
}

type AgentChatProps = {
  ref?: Ref<AgentChatHandle>
  paneId?: string
  model?: string
  onModelChange?: (model: string) => void
  onStatusChange?: (paneId: string, status: ChatStatus) => void
}

export function AgentChat({
  ref,
  paneId,
  model: selectedModel,
  onModelChange,
  onStatusChange,
}: AgentChatProps) {
  const [text, setText] = useState("")
  const [localModel, setLocalModel] = useState(DEFAULT_MODEL)
  const model = selectedModel ?? localModel
  const [efforts, setEfforts] = useState<Record<string, string>>({})
  const selectedEffort = efforts[model] ?? "default"
  const reasoningEffort = reasoningLevels(model).includes(selectedEffort) ? selectedEffort : undefined
  const turnBody = { model, reasoningEffort }
  const reduce = useReducedMotion() ?? false
  // Reused across turns so they land as Updates on the same durable
  // orchestrator session (see orchestrator/workflow.py) instead of starting
  // a fresh one every message.

  const { messages, status, sendMessage, stop, error } = useChat({
    transport: new DefaultChatTransport({ api: "/api/orchestrator" }),
    // Without this every token re-renders the whole conversation: the two
    // full messages x parts scans below, plus AgentActivity's filter passes
    // for every assistant message. The default is undefined, which the SDK
    // documents as "disables throttling". Streamdown's fade-in is CSS driven
    // by isAnimating, so coalescing updates does not affect smoothness.
    throttle: 50,
  })

  // Models switch per turn, mid-session: every sendMessage below carries the
  // currently selected model in its body, the route forwards it as model_id
  // on the turn POST, and the orchestrator rebuilds the durable session's
  // agent on the new registered factory (conversation intact). Picking a new
  // model here simply means the NEXT turn uses it.
  function changeModel(next: string) {
    if (onModelChange) onModelChange(next)
    else setLocalModel(next)
  }

  useEffect(() => {
    if (paneId) onStatusChange?.(paneId, status)
  }, [paneId, status, onStatusChange])

  // The orchestrator route reports the durable session as a `data-session`
  // part. It is message data, so derive it during render instead of mirroring
  // it into component state with an Effect.
  const sessionId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      for (const part of messages[i].parts) {
        if (part.type === "data-session") {
          return (part as { data: { sessionId?: string } }).data.sessionId
        }
      }
    }
    return undefined
  })()

  // A ChatWorkflow execution runs until it is signalled to end, so a session
  // the browser walks away from would otherwise stay live in Temporal for
  // good. `pagehide` is the reliable teardown event (unlike `beforeunload` it
  // also fires on mobile/bfcache navigations), and sendBeacon survives the
  // page going away where a plain fetch does not. /sessions/{id}/end is
  // idempotent, so firing it for an already-finished session is harmless.
  useEffect(() => {
    if (!sessionId) return
    const end = () => {
      navigator.sendBeacon(
        "/api/orchestrator/end",
        new Blob([JSON.stringify({ sessionId })], { type: "application/json" }),
      )
    }
    window.addEventListener("pagehide", end)
    return () => {
      window.removeEventListener("pagehide", end)
      end()
    }
  }, [sessionId])

  // A turn can park on a gated tool waiting for a human. The workflow PUSHES
  // that prompt down the same stream as everything else, so it arrives as a
  // `data-approval` part — no polling. This replaced a setInterval that hit
  // the API once a second for the entire duration of a streaming turn, which
  // meant thousands of requests behind any long-running tool call.
  const approval = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      for (const part of messages[i].parts) {
        if (part.type === "data-approval") {
          return (part as { data: { reason: string | null } }).data.reason ?? ""
        }
      }
    }
    return ""
  })()

  const [answered, setAnswered] = useState<string | null>(null)
  const pendingApproval = approval && approval !== answered ? approval : ""

  async function answerApproval(response: string) {
    const result = await fetch("/api/orchestrator/approval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, response }),
    })
    if (result.ok) setAnswered(approval)
  }

  const hasConversation = messages.length > 0
  const lastMessage = messages.at(-1)
  const awaitingAssistant =
    status === "submitted" && lastMessage?.role === "user"
  const browserPreview = computerUsePreview(
    messages.filter((message) => message.role === "assistant").flatMap((message) => message.parts),
    status === "streaming"
  )
  const projectIde = projectIdePreview(
    messages.filter((message) => message.role === "assistant").flatMap((message) => message.parts),
    status === "streaming"
  )
  const [dismissedPreview, setDismissedPreview] = useState("")
  // Closing the IDE is scoped to the activity it was closed against: the next
  // REAL activity (a new activityId) reopens it, and the composer's Preview
  // button reopens it any time — reopening is never a dead end. Closing is
  // presentation only: the turn keeps streaming and nothing is stopped.
  const [dismissedIde, setDismissedIde] = useState<string | null>(null)
  const [previewRequested, setPreviewRequested] = useState(false)
  // Fullscreen keeps the SAME mounted panel (all live IDE state survives) and
  // only swaps layout classes: the chat column hides, the IDE takes the whole
  // chat area. Restore returns to the normal split.
  const [ideFullscreen, setIdeFullscreen] = useState(false)
  // Auto-close: 10s after the last IDE tool activity the panel closes on its
  // own — unless the user has the Preview tab selected (an explicit choice to
  // keep the preview up) or an operation is still running. Project state is
  // derived from the message stream, so closing loses nothing: any new
  // activity (or the composer's Preview button) reopens it.
  const ideLastActiveAtRef = useRef<number | null>(null)
  const [idePreviewTab, setIdePreviewTab] = useState(false)
  // Forcing the preview tab is the user's explicit request only; a live dev
  // server engages the preview through the stream's own activeView instead,
  // so tool activity (edits, commands) can still pull focus while it runs.
  const previewForced = previewRequested
  const showBrowserPreview =
    browserPreview.open && dismissedPreview !== browserPreview.sessionId
  const showProjectIde =
    !showBrowserPreview &&
    dismissedIde !== (projectIde.activityId ?? "closed") &&
    (previewRequested || projectIde.open)
  const splitPreview = showBrowserPreview || showProjectIde

  // The timestamp advances on every new project operation (activityId), so
  // the idle window is measured from the agent's most recent IDE work. A ref
  // (not state) — it never drives rendering, only the auto-close timer below,
  // which already re-runs on the same open/activityId transitions.
  useEffect(() => {
    if (projectIde.open) ideLastActiveAtRef.current = Date.now()
  }, [projectIde.open, projectIde.activityId])
  useEffect(() => {
    if (!showProjectIde || idePreviewTab || projectIde.isStreaming) return
    const ideLastActiveAt = ideLastActiveAtRef.current
    if (ideLastActiveAt == null) return
    const remaining = 10_000 - (Date.now() - ideLastActiveAt)
    const timer = setTimeout(() => {
      setPreviewRequested(false)
      setIdeFullscreen(false)
      setDismissedIde(projectIde.activityId ?? "closed")
    }, Math.max(0, remaining))
    return () => clearTimeout(timer)
  }, [
    showProjectIde,
    idePreviewTab,
    projectIde.isStreaming,
    projectIde.activityId,
  ])

  async function handoff(action: "take" | "give", message = "") {
    if (!sessionId) throw new Error("No active browser session")
    const response = await fetch("/api/orchestrator/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, action, message }),
    })
    if (!response.ok) throw new Error(await response.text())
  }

  async function takeControl() {
    await handoff("take")
    await stop()
  }

  async function giveControl(message: PromptInputMessage) {
    const text = message.text?.trim()
    if (!text) return
    await handoff("give", text)
    void sendMessage({ text }, { body: { ...turnBody, sessionId } })
  }

  function submit(message: PromptInputMessage) {
    if (status === "submitted" || status === "streaming") return
    const hasText = Boolean(message.text?.trim())
    const hasAttachments = Boolean(message.files?.length)
    if (!hasText && !hasAttachments) return

    sendMessage(
      { text: message.text ?? "", files: message.files },
      { body: { ...turnBody, sessionId } },
    )
    setText("")
  }

  function runSuggestion(suggestion: string) {
    sendMessage({ text: suggestion }, { body: { ...turnBody, sessionId } })
    setText("")
  }

  useImperativeHandle(ref, () => ({ submit, stop }))

  return (
    <div className="@container/chat flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {!paneId && <BlurpleBackground settled={hasConversation} />}
      {/* mode="wait" makes the hero fully exit before the conversation
          enters — the composer never exists twice on screen, and the swap
          reads as a scene change rather than a layout jump. */}
      <AnimatePresence mode="wait">
        {hasConversation || paneId ? (
          // The chat column is ALWAYS the same centered max-w-3xl shell. A
          // split (IDE or browser preview) never restyles it — it only pads
          // the row, so the chat is shoved over by the panel's width as the
          // panel slides in, instead of snapping to a different track. The
          // panel animates width 0 → its share, so the shove and the slide
          // are the same motion.
          <motion.section
            key="chat"
            className={cn(
              "flex min-h-0 w-full flex-1 overflow-hidden",
              splitPreview && "flex-row"
            )}
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.4, ease: EASE }}
          >
            <div
              className={cn(
                "flex min-h-0 flex-col",
                // Fullscreen IDE: the chat column hides (CSS only — the chat
                // stays mounted, streaming, and fully stateful) and the IDE
                // takes the entire chat area until Restore.
                showProjectIde && ideFullscreen
                  ? "hidden"
                  : splitPreview
                    ? "order-1 flex h-full min-h-0 w-1/3 min-w-0 shrink-0 flex-col border-r border-white/10"
                    : "mx-auto min-h-0 w-full max-w-3xl flex-1 px-4 pb-6"
              )}
            >
            <div className={cn("flex min-h-0 flex-1 flex-col", splitPreview && "px-4 pb-4 pt-3")}>
            <Conversation className="h-0 min-h-0 flex-1">
              <ConversationContent className="gap-8 py-8">
                {!hasConversation && (
                  <ConversationEmptyState
                    title="No response yet"
                    description="Send a shared prompt or start this model's conversation below."
                  />
                )}
                {messages.map((message, messageIndex) => (
                  <MessageShell key={message.id} reduce={reduce}>
                    <Message from={message.role}>
                      {/* Vendored MessageContent is w-fit, which collapses
                          width-only children: the GraphActivity canvas
                          (w-full inside a fit-content parent) shrank to the
                          React Flow intrinsic minimum (~142px). Assistant
                          messages stretch instead; user bubbles keep w-fit. */}
                      <MessageContent className="group-[.is-assistant]:w-full">
                        {message.role === "assistant" && (
                          <AgentActivity
                            parts={message.parts}
                            isThinking={
                              (status === "streaming" || status === "submitted") &&
                              messageIndex === messages.length - 1
                            }
                          />
                        )}
                        {/* Formation graph canvas: renders only for messages
                            carrying data-graph-event parts (GWEN-29). */}
                        {message.role === "assistant" && (
                          <GraphActivity parts={message.parts} />
                        )}
                        {message.parts.map((part, i) => {
                          if (isTextUIPart(part)) {
                            return (
                              // isAnimating drives Streamdown's own streaming
                              // affordances — token fade-in and the caret. It
                              // defaults to false, so without passing it text just
                              // appears in silent chunks. MessageResponse is memo'd
                              // on children + isAnimating, so this is the one prop
                              // that must be live.
                              <MessageResponse
                                key={`${message.id}-${i}`}
                                isAnimating={
                                  status === "streaming" &&
                                  messageIndex === messages.length - 1
                                }
                                // Streamdown's link-safety interstitial renders a
                                // modal <div> inside the <p> that holds the link,
                                // which React reports as a hydration error. These
                                // are model-authored citations in our own UI, so
                                // the interstitial buys nothing.
                                linkSafety={LINK_SAFETY}
                              >
                                {part.text}
                              </MessageResponse>
                            )
                          }
                          // Attachments the user sent, shown on their own message
                          // so the conversation reflects what was actually sent to
                          // the agent. useChat stores them as data: URLs, which is
                          // exactly what Image renders from.
                          if (isFileUIPart(part)) {
                            return (
                              <Image
                                key={`${message.id}-${i}`}
                                base64={part.url.split(",")[1] ?? ""}
                                uint8Array={new Uint8Array()}
                                mediaType={part.mediaType}
                                alt={part.filename ?? "Attachment"}
                                className="max-h-64 w-auto"
                              />
                            )
                          }
                          return null
                        })}
                      </MessageContent>
                    </Message>
                  </MessageShell>
                ))}
                {awaitingAssistant && (
                  <MessageShell key="awaiting-assistant" reduce={reduce}>
                    <Message from="assistant">
                      <MessageContent>
                        <AgentActivity parts={[]} isThinking />
                      </MessageContent>
                    </Message>
                  </MessageShell>
                )}
              </ConversationContent>
              <ConversationScrollButton />
            </Conversation>

            <AnimatePresence>
              {error ? (
                <motion.div
                  key="chat-error"
                  className="mb-2"
                  initial={
                    reduce ? false : { opacity: 0, y: 16, scale: 0.98 }
                  }
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.99 }}
                  transition={{ duration: 0.35, ease: EASE }}
                >
                  <Alert
                    variant="destructive"
                    className="border-destructive/25 bg-card/80 backdrop-blur-xl"
                    data-testid="chat-error"
                  >
                    <AlertTitle>Request failed</AlertTitle>
                    <AlertDescription>{error.message}</AlertDescription>
                  </Alert>
                </motion.div>
              ) : null}
              {pendingApproval && !showBrowserPreview ? (
                <motion.div
                  key="approval"
                  className="mb-2"
                  initial={
                    reduce ? false : { opacity: 0, y: 16, scale: 0.98 }
                  }
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.99 }}
                  transition={{ duration: 0.35, ease: EASE }}
                >
                  <Alert className="border-blurple/25 bg-card/80 shadow-[0_8px_32px_-12px_oklch(0.55_0.22_277/0.5)] backdrop-blur-xl">
                    <AlertTitle>Approval needed</AlertTitle>
                    <AlertDescription>
                      <div className="flex w-full flex-col gap-3">
                        <p data-testid="approval-reason">{pendingApproval}</p>
                        <div className="flex gap-2">
                          <Button
                            size="sm"
                            onClick={() => answerApproval("approve")}
                          >
                            Approve
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => answerApproval("deny")}
                          >
                            Deny
                          </Button>
                        </div>
                      </div>
                    </AlertDescription>
                  </Alert>
                </motion.div>
              ) : null}
            </AnimatePresence>

            <div className="mt-2 shrink-0">
              {projectIde.open && !showProjectIde && (
                <Button type="button" variant="outline" size="sm" className="mb-2" onClick={() => { setDismissedIde(null); setPreviewRequested(true) }}>
                  Open IDE
                </Button>
              )}
              <motion.div
                initial={reduce ? false : { opacity: 0, y: 24 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.6, delay: 0.15, ease: EASE }}
              >
                <Composer
                  text={text}
                  onTextChange={setText}
                  onSubmit={submit}
                  model={model}
                  onModelChange={changeModel}
                  reasoningEffort={reasoningEffort ?? "default"}
                  onReasoningEffortChange={(value) => setEfforts({ ...efforts, [model]: value })}
                  status={status}
                   onStop={stop}
                  globalDrop={!paneId}
                  placeholder="Ask for a change, or start something new…"
                  previewActive={showProjectIde}
                  onPreview={() => {
                    setDismissedIde(null)
                    setPreviewRequested(true)
                  }}
                />
              </motion.div>
            </div>
            </div>
            </div>

            <AnimatePresence>
              {showBrowserPreview ? (
                <motion.div
                  key={browserPreview.sessionId}
                  className="order-1 flex min-h-96 min-w-0 flex-1 shrink-0 flex-col @min-[64rem]/chat:order-2 @min-[64rem]/chat:min-h-0"
                  initial={reduce ? false : { opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 12 }}
                  transition={{ duration: 0.45, ease: EASE }}
                >
                  <ComputerUsePreviewPanel
                    preview={browserPreview}
                    isStreaming={status === "streaming"}
                    status={status}
                    pendingApproval={pendingApproval}
                    onApprove={() => answerApproval("approve")}
                    onDeny={() => answerApproval("deny")}
                    onClose={() => setDismissedPreview(browserPreview.sessionId)}
                    onTakeControl={takeControl}
                    onGiveControl={giveControl}
                  />
                </motion.div>
              ) : showProjectIde ? (
                // Full-height right column. The slot is a width track: it
                // grows from 0 to two-thirds (fullscreen: full width) while the
                // panel inside, pinned to a fixed viewport width and right-
                // aligned, stays put — so the IDE slides in from the right edge
                // while the chat (left third) is shoved left by the same single
                // motion. transform-only would slide through space the chat had
                // already vacated, reading as a jump cut instead of a shove.
                <motion.div
                  key="project-workspace"
                  className="order-2 flex h-full min-h-0 shrink-0 flex-col overflow-hidden"
                  initial={reduce ? false : { width: 0, opacity: 0 }}
                  animate={{
                    width: ideFullscreen ? "100%" : "67%",
                    opacity: 1,
                  }}
                  exit={reduce ? { opacity: 0 } : { width: 0, opacity: 0 }}
                  transition={{ duration: reduce ? 0 : 0.6, ease: [0.22, 0.68, 0, 1] }}
                >
                  <div className="flex h-full min-h-0 w-[67vw] min-w-0 flex-1 flex-col self-end py-3 pr-3">
                    <ProjectIdePanel
                      ide={projectIde}
                      previewForced={previewForced}
                      fullscreen={ideFullscreen}
                      onFullscreenChange={setIdeFullscreen}
                      onViewChange={(view) => setIdePreviewTab(view === "preview")}
                      onClose={() => {
                        // Presentation-only: hides the panel, never stops or
                        // ends the running turn/session. Scoped to the activity
                        // it was closed against — the next real activity
                        // (a new activityId) reopens it.
                        setDismissedIde(projectIde.activityId ?? "closed")
                        setPreviewRequested(false)
                        setIdeFullscreen(false)
                      }}
                    />
                  </div>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </motion.section>
        ) : (
          <motion.section
            key="hero"
            className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-4 pb-24 pt-8 sm:pt-16"
            exit={
              reduce
                ? undefined
                : { opacity: 0, y: -24, transition: { duration: 0.35, ease: EASE } }
            }
          >
            <Reveal reduce={reduce} delay={0.05}>
              <span className="mb-8 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.22em] text-muted-foreground backdrop-blur-sm">
                <span className="size-1.5 animate-pulse-soft rounded-full bg-blurple-bright" />
                Now in beta
              </span>
            </Reveal>

            <Headline reduce={reduce} />

            <Reveal reduce={reduce} delay={0.55}>
              <p className="mt-6 max-w-md text-pretty text-center text-base leading-relaxed text-muted-foreground">
                Describe an interface in plain language. v0 turns it into clean,
                production-ready React — components, styling, and all.
              </p>
            </Reveal>

            <Reveal reduce={reduce} delay={0.7} className="mt-10 w-full">
              <Composer
                text={text}
                onTextChange={setText}
                onSubmit={submit}
                model={model}
                onModelChange={changeModel}
                reasoningEffort={reasoningEffort ?? "default"}
                onReasoningEffortChange={(value) => setEfforts({ ...efforts, [model]: value })}
                status={status}
                onStop={stop}
              />

              {/* Suggestions is a horizontally scrolling ScrollArea whose inner
                  row is `w-max flex-nowrap`. `justify-center` cannot center that
                  row — a w-max element is exactly as wide as its content, so
                  there is no free space to distribute, and the row simply
                  overflowed with the last chip clipped off-screen behind a
                  hidden scrollbar. Wrapping instead keeps every chip reachable
                  and lets them actually center. */}
              <div className="mt-5">
                <Suggestions className="!w-full flex-wrap justify-center">
                  {SUGGESTIONS.map((suggestion, i) => (
                    <motion.span
                      key={suggestion}
                      className="inline-block"
                      initial={
                        reduce ? false : { opacity: 0, y: 10, scale: 0.96 }
                      }
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      transition={{
                        duration: 0.5,
                        delay: 0.85 + i * 0.05,
                        ease: EASE,
                      }}
                    >
                      <Suggestion
                        suggestion={suggestion}
                        onClick={runSuggestion}
                        className="border-white/10 bg-white/[0.03] text-muted-foreground backdrop-blur-sm transition-all duration-300 hover:-translate-y-0.5 hover:border-blurple/40 hover:bg-blurple/10 hover:text-foreground hover:shadow-[0_4px_20px_-6px_oklch(0.62_0.205_277/0.45)]"
                      />
                    </motion.span>
                  ))}
                </Suggestions>
              </div>
            </Reveal>

            <Reveal reduce={reduce} delay={1.05}>
              <p className="mt-16 max-w-sm text-center font-mono text-[11px] leading-relaxed tracking-wide text-muted-foreground/60">
                Trusted by design engineers shipping at the edge of the web.
              </p>
            </Reveal>
          </motion.section>
        )}
      </AnimatePresence>
    </div>
  )
}
