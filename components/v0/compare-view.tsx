"use client"

import { useRef, useState } from "react"
import type { ChatStatus } from "ai"
import { PlusIcon, XIcon } from "lucide-react"

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { AgentChat, type AgentChatHandle } from "@/components/v0/agent-chat"
import { Composer } from "@/components/v0/composer"
import { DEFAULT_MODEL } from "@/lib/perplexity"
import { useModels } from "./use-models"

const MIN_MODELS = 2
const MAX_MODELS = 5

type Pane = { id: string; model: string }

export function CompareView() {
  const { models: availableModels } = useModels()
  const [panes, setPanes] = useState<Pane[]>([
    { id: "pane-0", model: DEFAULT_MODEL },
    { id: "pane-1", model: "" },
  ])
  const nextPaneId = useRef(2)
  const chats = useRef(new Map<string, AgentChatHandle>())
  const [statuses, setStatuses] = useState<Record<string, ChatStatus>>({})
  const [text, setText] = useState("")
  const modelsToShow = panes.map((pane) => ({
    ...pane,
    model: pane.model || availableModels.find((model) => model.id !== DEFAULT_MODEL)?.id || DEFAULT_MODEL,
  }))
  const isComparing = panes.some((pane) =>
    statuses[pane.id] === "submitted" || statuses[pane.id] === "streaming"
  )

  function updateStatus(paneId: string, status: ChatStatus) {
    setStatuses((previous) => previous[paneId] === status
      ? previous
      : { ...previous, [paneId]: status })
  }

  function addModel() {
    if (panes.length >= MAX_MODELS) return
    const unused = availableModels.find((model) => !modelsToShow.some((pane) => pane.model === model.id))
    setPanes([...modelsToShow, { id: `pane-${nextPaneId.current++}`, model: unused?.id ?? DEFAULT_MODEL }])
  }

  function removeModel(paneId: string) {
    if (panes.length <= MIN_MODELS) return
    chats.current.get(paneId)?.stop()
    setPanes(modelsToShow.filter((pane) => pane.id !== paneId))
    setStatuses((previous) => {
      const next = { ...previous }
      delete next[paneId]
      return next
    })
  }

  function submit(message: PromptInputMessage) {
    if (isComparing || (!message.text.trim() && !message.files.length)) return
    // Each pane owns a regular durable chat, including its history and tools.
    // Duplicate model selections intentionally remain independent sessions.
    for (const pane of panes) chats.current.get(pane.id)?.submit(message)
    setText("")
  }

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
      <div className="flex shrink-0 items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {panes.length} models
          {/* Mobile pane discovery: below sm each pane is an 88%-wide snap
              column, so the next pane peeks in from the edge — this hint
              names the gesture for anyone who misses the peek. */}
          <span className="ml-2 text-xs text-muted-foreground/80 sm:hidden" aria-hidden="true">
            · Swipe to compare →
          </span>
        </p>
        <Button disabled={panes.length >= MAX_MODELS} onClick={addModel} size="sm" type="button" variant="outline">
          <PlusIcon className="size-4" />
          Add model
        </Button>
      </div>

      <div
        aria-label="Model conversations"
        className="grid min-h-96 flex-1 snap-x snap-mandatory auto-cols-[88%] grid-flow-col gap-4 overflow-x-auto overscroll-x-contain pb-2 sm:snap-none sm:auto-cols-[minmax(min(100%,22rem),1fr)]"
      >
        {modelsToShow.map((pane, index) => (
          <section
            key={pane.id}
            aria-label={`Model ${index + 1}: ${pane.model}`}
            data-testid="compare-pane"
            className="app-glass flex min-h-0 min-w-0 snap-start snap-always flex-col overflow-hidden rounded-2xl border"
          >
            <div className="app-glass-edge flex shrink-0 items-center justify-between gap-2 border-b bg-white/[0.02] px-4 py-2.5">
              <span className="truncate font-mono text-xs text-muted-foreground">{pane.model}</span>
              {(statuses[pane.id] === "streaming" || statuses[pane.id] === "submitted") && <Spinner className="size-3.5 shrink-0" />}
              {panes.length > MIN_MODELS && (
                <Button aria-label={`Remove model ${index + 1}`} onClick={() => removeModel(pane.id)} size="icon-sm" type="button" variant="ghost">
                  <XIcon className="size-4" />
                </Button>
              )}
            </div>
            <AgentChat
              ref={(chat) => {
                if (chat) chats.current.set(pane.id, chat)
                else chats.current.delete(pane.id)
              }}
              paneId={pane.id}
              model={pane.model}
              onModelChange={(model) => setPanes(modelsToShow.map((entry) => entry.id === pane.id ? { ...entry, model } : entry))}
              onStatusChange={updateStatus}
            />
          </section>
        ))}
      </div>

      <div className="mx-auto w-full max-w-3xl shrink-0">
        <Composer
          text={text}
          onTextChange={setText}
          onSubmit={submit}
          globalDrop={false}
          modelControls={<span className="px-2 text-xs text-muted-foreground">Send to all {panes.length} models</span>}
          placeholder="Ask all selected models the same thing..."
          status={isComparing ? "streaming" : "ready"}
          onStop={() => { for (const chat of chats.current.values()) chat.stop() }}
        />
      </div>
    </div>
  )
}
