"use client"

import { useMemo, useRef, useState } from "react"

import type { UIMessage } from "ai"

import { AgentActivity } from "@/components/v0/agent-activity"
import { PlusIcon, XIcon } from "lucide-react"

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
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  type PromptInputMessage,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { DEFAULT_MODEL } from "@/lib/perplexity"
import { cn } from "@/lib/utils"
import { ModelPicker } from "./model-picker"
import { useModels } from "./use-models"

const MIN_MODELS = 2
const MAX_MODELS = 4

type PaneStatus = "idle" | "streaming" | "done" | "error"

type Pane = {
  status: PaneStatus
  text: string
  // The same UI message parts the main chat accumulates, so each pane can
  // render the identical <AgentActivity>.
  parts: UIMessage["parts"]
  error?: string
}

type CompareEvent =
  | { model: string; type: "start" }
  | { model: string; type: "delta"; text: string }
  | { model: string; type: "part"; part: UIMessage["parts"][number] }
  | { model: string; type: "done" }
  | { model: string; type: "error"; error: string }

function emptyPanes(models: string[]): Record<string, Pane> {
  return Object.fromEntries(models.map((m) => [m, { status: "idle", text: "", parts: [] }]))
}

export function CompareView() {
  const { models: availableModels } = useModels()
  const [selectedModels, setSelectedModels] = useState<string[]>([DEFAULT_MODEL])
  const [text, setText] = useState("")
  const [panes, setPanes] = useState<Record<string, Pane>>({})
  const [isComparing, setIsComparing] = useState(false)
  // Lets PromptInputSubmit's streaming state actually stop the run, instead of
  // rendering a stop square that does nothing.
  const abortRef = useRef<AbortController | null>(null)

  // The catalog is fetched live from GET /v1/models — no model id is ever
  // hardcoded here. Until it resolves the view shows DEFAULT_MODEL twice
  // rather than inventing a second id that may not exist.
  const modelsToShow = useMemo(() => {
    if (selectedModels.length >= MIN_MODELS) return selectedModels
    const filler = availableModels.find((m) => !selectedModels.includes(m.id))
    return [...selectedModels, filler?.id ?? DEFAULT_MODEL].slice(0, MIN_MODELS)
  }, [selectedModels, availableModels])

  const addModel = () => {
    if (modelsToShow.length >= MAX_MODELS) return
    const unused = availableModels.find((m) => !modelsToShow.includes(m.id))
    setSelectedModels([...modelsToShow, unused?.id ?? DEFAULT_MODEL])
  }

  const removeModel = (index: number) => {
    if (modelsToShow.length <= MIN_MODELS) return
    setSelectedModels(modelsToShow.filter((_, i) => i !== index))
  }

  const updateModel = (index: number, value: string) => {
    const next = [...modelsToShow]
    next[index] = value
    setSelectedModels(next)
  }

  async function runCompare(prompt: string) {
    const activeModels = [...new Set(modelsToShow)]
    setPanes(emptyPanes(activeModels))
    setIsComparing(true)
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, models: activeModels }),
        signal: controller.signal,
      })

      if (!res.body) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const chunks = buffer.split("\n\n")
        buffer = chunks.pop() ?? ""

        for (const chunk of chunks) {
          const line = chunk.trim()
          if (!line.startsWith("data:")) continue
          const raw = line.slice(5).trim()
          if (!raw) continue

          const event = JSON.parse(raw) as CompareEvent
          setPanes((prev) => {
            const pane = prev[event.model] ?? { status: "streaming", text: "", parts: [] }
            if (event.type === "delta") {
              return {
                ...prev,
                [event.model]: { ...pane, status: "streaming", text: pane.text + event.text },
              }
            }
            if (event.type === "part") {
              // Tool parts are keyed by toolCallId so a later state
              // (output-available) replaces the earlier input-available card
              // instead of stacking a second one.
              const incoming = event.part as { type: string; toolCallId?: string }
              const parts = [...pane.parts]
              const at = incoming.toolCallId
                ? parts.findIndex(
                    (p) =>
                      (p as { toolCallId?: string }).toolCallId === incoming.toolCallId,
                  )
                : -1
              if (at >= 0) parts[at] = event.part
              else parts.push(event.part)
              return {
                ...prev,
                [event.model]: { ...pane, status: "streaming", parts },
              }
            }
            if (event.type === "done") {
              return { ...prev, [event.model]: { ...pane, status: "done" } }
            }
            if (event.type === "error") {
              return { ...prev, [event.model]: { ...pane, status: "error", error: event.error } }
            }
            return prev
          })
        }
      }
    } catch (error) {
      // An abort is the user pressing stop, not a failure — leave the panes
      // showing whatever streamed in before they stopped it.
      if ((error as Error)?.name !== "AbortError") throw error
    } finally {
      abortRef.current = null
      setIsComparing(false)
    }
  }

  function submit(message: PromptInputMessage) {
    const prompt = message.text?.trim()
    if (!prompt || isComparing) return
    setText("")
    void runCompare(prompt)
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 pb-6">
      <div className="flex flex-wrap items-center gap-2">
        {modelsToShow.map((model, index) => (
          <div key={index} className="flex items-center gap-1">
            <ModelPicker
              value={model}
              onValueChange={(value) => updateModel(index, value)}
              triggerClassName="w-56"
            />
            {modelsToShow.length > MIN_MODELS && (
              <Button
                aria-label={`Remove ${model}`}
                onClick={() => removeModel(index)}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <XIcon className="size-4" />
              </Button>
            )}
          </div>
        ))}
        {modelsToShow.length < MAX_MODELS && (
          <Button onClick={addModel} size="sm" type="button" variant="outline">
            <PlusIcon className="size-4" />
            Add model
          </Button>
        )}
      </div>

      <div
        className={cn(
          "grid flex-1 gap-4",
          modelsToShow.length === 2 && "md:grid-cols-2",
          modelsToShow.length === 3 && "md:grid-cols-3",
          modelsToShow.length >= 4 && "md:grid-cols-2 xl:grid-cols-4"
        )}
      >
        {modelsToShow.map((model) => {
          const pane = panes[model]
          return (
            <div
              key={model}
              className="flex min-h-[280px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-white/[0.02] backdrop-blur-sm"
            >
              <div className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-2.5">
                <span className="truncate font-mono text-xs text-muted-foreground">{model}</span>
                {pane?.status === "streaming" && <Spinner className="size-3.5 shrink-0" />}
              </div>
              {/* Same native auto-scroll behavior as the main chat: sticks
                  to the bottom while this pane streams, stops following if
                  the user scrolls up, scroll button self-hides at bottom. */}
              <Conversation className="flex-1">
                <ConversationContent className="min-h-full p-4">
                  {!pane && (
                    <ConversationEmptyState
                      title="No response yet"
                      description="Send a prompt to compare this model."
                    />
                  )}
                  {pane?.status === "error" ? (
                    <p className="text-sm text-destructive">{pane.error}</p>
                  ) : (
                    (pane?.parts?.length || pane?.text) && (
                      <Message from="assistant" className="max-w-full">
                        <MessageContent>
                          {pane.parts?.length ? (
                            <AgentActivity
                              parts={pane.parts}
                              isThinking={pane.status === "streaming"}
                            />
                          ) : null}
                          {pane.text ? (
                            <MessageResponse>{pane.text}</MessageResponse>
                          ) : null}
                        </MessageContent>
                      </Message>
                    )
                  )}
                </ConversationContent>
                <ConversationScrollButton />
              </Conversation>
            </div>
          )
        })}
      </div>

      <PromptInput
        className="sticky bottom-4 overflow-hidden rounded-3xl border border-white/10 bg-card/70 shadow-[0_8px_40px_-12px_oklch(0.4_0.2_277/0.5)] backdrop-blur-xl"
        onSubmit={submit}
      >
        <PromptInputBody>
          <PromptInputTextarea
            className="min-h-[64px] text-base leading-relaxed placeholder:text-muted-foreground/70"
            onChange={(e) => setText(e.target.value)}
            placeholder="Ask all selected models the same thing…"
            value={text}
          />
        </PromptInputBody>
        <PromptInputFooter className="justify-end border-0 px-2 pb-2">
          <PromptInputSubmit
            className="rounded-full"
            disabled={!isComparing && !text.trim()}
            status={isComparing ? "streaming" : undefined}
            onStop={() => abortRef.current?.abort()}
          />
        </PromptInputFooter>
      </PromptInput>
    </div>
  )
}
