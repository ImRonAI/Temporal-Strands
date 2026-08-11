"use client"

import { useChat } from "@ai-sdk/react"
import { isFileUIPart, isTextUIPart, DefaultChatTransport } from "ai"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useEffect, useState } from "react"
import type { ReactNode } from "react"

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input"

import {
  Conversation,
  ConversationContent,
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
import { Composer } from "@/components/v0/composer"
import { SiteHeader } from "@/components/v0/site-header"

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

export default function Page() {
  const [text, setText] = useState("")
  const [model, setModel] = useState(DEFAULT_MODEL)
  const reduce = useReducedMotion() ?? false
  // Reused across turns so they land as Updates on the same durable
  // orchestrator session (see orchestrator/workflow.py) instead of starting
  // a fresh one every message.

  const { messages, setMessages, status, sendMessage, stop } = useChat({
    transport: new DefaultChatTransport({ api: "/api/orchestrator" }),
    // Without this every token re-renders the whole conversation: the two
    // full messages x parts scans below, plus AgentActivity's filter passes
    // for every assistant message. The default is undefined, which the SDK
    // documents as "disables throttling". Streamdown's fade-in is CSS driven
    // by isAnimating, so coalescing updates does not affect smoothness.
    throttle: 50,
  })

  // A session's model is fixed when the session starts: a TemporalAgent's
  // model provider cannot be reconfigured afterwards (TemporalModel's
  // update_config is a documented no-op). So switching models ends the
  // current session and starts a fresh conversation, rather than silently
  // leaving the picker pointing at a model the running session isn't using.
  function changeModel(next: string) {
    if (next === model) return
    setModel(next)
    if (sessionId) {
      void fetch("/api/orchestrator/end", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId }),
      })
      setMessages([])
    }
  }

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
    return () => window.removeEventListener("pagehide", end)
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
    setAnswered(approval)
    await fetch("/api/orchestrator/approval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, response }),
    })
  }

  const hasConversation = messages.length > 0

  function submit(message: PromptInputMessage) {
    const hasText = Boolean(message.text?.trim())
    const hasAttachments = Boolean(message.files?.length)
    if (!hasText && !hasAttachments) return

    sendMessage(
      { text: message.text ?? "", files: message.files },
      { body: { model, sessionId } },
    )
    setText("")
  }

  function runSuggestion(suggestion: string) {
    sendMessage({ text: suggestion }, { body: { model, sessionId } })
    setText("")
  }

  return (
    <main className="relative flex min-h-screen flex-col">
      <BlurpleBackground settled={hasConversation} />
      <SiteHeader />

      {/* mode="wait" makes the hero fully exit before the conversation
          enters — the composer never exists twice on screen, and the swap
          reads as a scene change rather than a layout jump. */}
      <AnimatePresence mode="wait">
        {hasConversation ? (
          <motion.section
            key="chat"
            className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 pb-6"
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.4, ease: EASE }}
          >
            <Conversation className="flex-1">
              <ConversationContent className="gap-8 py-8">
                {messages.map((message, messageIndex) => (
                  <MessageShell key={message.id} reduce={reduce}>
                    <Message from={message.role}>
                      <MessageContent>
                        {message.role === "assistant" && (
                          <AgentActivity
                            parts={message.parts}
                            isThinking={
                              status === "streaming" &&
                              messageIndex === messages.length - 1
                            }
                          />
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
              </ConversationContent>
              <ConversationScrollButton />
            </Conversation>

            <AnimatePresence>
              {pendingApproval ? (
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

            <div className="sticky bottom-4 mt-2">
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
                  status={status}
                  onStop={stop}
                  placeholder="Ask for a change, or start something new…"
                />
              </motion.div>
            </div>
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
    </main>
  )
}
