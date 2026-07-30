"use client"

import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport } from "ai"
import { useEffect, useState } from "react"

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
import { AgentActivity } from "@/components/v0/agent-activity"
import { BlurpleBackground } from "@/components/v0/blurple-background"
import { Composer } from "@/components/v0/composer"
import { SiteHeader } from "@/components/v0/site-header"

const SUGGESTIONS = [
  "A pricing page with a yearly toggle",
  "An analytics dashboard with charts",
  "A waitlist landing page",
  "A Kanban board with drag and drop",
  "A settings page with tabs",
]

export default function Page() {
  const [text, setText] = useState("")
  const [model, setModel] = useState(DEFAULT_MODEL)
  // Reused across turns so they land as Updates on the same durable
  // orchestrator session (see orchestrator/workflow.py) instead of starting
  // a fresh one every message.

  const { messages, setMessages, status, sendMessage, stop } = useChat({
    transport: new DefaultChatTransport({ api: "/api/orchestrator" }),
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
      <BlurpleBackground />
      <SiteHeader />

      {hasConversation ? (
        <section className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 pb-6">
          <Conversation className="flex-1">
            <ConversationContent className="gap-8 py-8">
              {messages.map((message, messageIndex) => (
                <Message from={message.role} key={message.id}>
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
                      if (part.type === "text") {
                        return (
                          <MessageResponse key={`${message.id}-${i}`}>
                            {part.text}
                          </MessageResponse>
                        )
                      }
                      // Attachments the user sent, shown on their own message
                      // so the conversation reflects what was actually sent to
                      // the agent. useChat stores them as data: URLs, which is
                      // exactly what Image renders from.
                      if (part.type === "file") {
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
              ))}
            </ConversationContent>
            <ConversationScrollButton />
          </Conversation>

          {pendingApproval ? (
            <Alert className="mb-2">
              <AlertTitle>Approval needed</AlertTitle>
              <AlertDescription>
                <div className="flex w-full flex-col gap-3">
                  <p data-testid="approval-reason">{pendingApproval}</p>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => answerApproval("approve")}>
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
          ) : null}

          <div className="sticky bottom-4 mt-2">
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
          </div>
        </section>
      ) : (
        <section className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-4 pb-24 pt-8 sm:pt-16">
          <span className="mb-8 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.22em] text-muted-foreground backdrop-blur-sm">
            <span className="size-1.5 rounded-full bg-blurple-bright shadow-[0_0_8px_2px_oklch(0.7_0.2_285/0.7)]" />
            Now in beta
          </span>

          <h1 className="text-balance text-center font-editorial text-5xl leading-[0.95] tracking-tight text-foreground sm:text-7xl">
            What should we{" "}
            <span className="italic text-blurple-bright [text-shadow:0_0_40px_oklch(0.7_0.2_285/0.35)]">
              ship
            </span>{" "}
            today?
          </h1>

          <p className="mt-6 max-w-md text-pretty text-center text-base leading-relaxed text-muted-foreground">
            Describe an interface in plain language. v0 turns it into clean,
            production-ready React — components, styling, and all.
          </p>

          <div className="mt-10 w-full">
            <Composer
              text={text}
              onTextChange={setText}
              onSubmit={submit}
              model={model}
              onModelChange={changeModel}
              status={status}
              onStop={stop}
            />

            <div className="mt-5">
              <Suggestions className="justify-center">
                {SUGGESTIONS.map((suggestion) => (
                  <Suggestion
                    key={suggestion}
                    suggestion={suggestion}
                    onClick={runSuggestion}
                    className="border-white/10 bg-white/[0.03] text-muted-foreground backdrop-blur-sm hover:bg-white/[0.07] hover:text-foreground"
                  />
                ))}
              </Suggestions>
            </div>
          </div>

          <p className="mt-16 max-w-sm text-center font-mono text-[11px] leading-relaxed tracking-wide text-muted-foreground/60">
            Trusted by design engineers shipping at the edge of the web.
          </p>
        </section>
      )}
    </main>
  )
}
