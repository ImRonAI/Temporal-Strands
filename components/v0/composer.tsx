"use client"

import type { ChatStatus } from "ai"
import { useEffect, useRef, useState, type ClipboardEvent, type ReactNode } from "react"
import { AppWindowIcon, PaperclipIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

import {
  Attachment,
  AttachmentPreview,
  AttachmentRemove,
  Attachments,
} from "@/components/ai-elements/attachments"
import {
  PromptInput,
  PromptInputActionAddAttachments,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuTrigger,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  type PromptInputMessage,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputAttachments,
} from "@/components/ai-elements/prompt-input"
import { ModelPicker } from "@/components/v0/model-picker"
import { PastedPromptAttachment } from "@/components/v0/pasted-prompt-attachment"
import {
  PASTED_PROMPT_INSTRUCTION,
  applyPastedEdits,
  isLongPaste,
  messageForPastedPrompt,
  pastedPromptFilename,
  type PastedPrompt,
} from "@/components/v0/pasted-prompt"

function AttachmentsDisplay({
  pasted,
  setPasted,
}: {
  pasted: readonly PastedPrompt[]
  setPasted: (update: (current: PastedPrompt[]) => PastedPrompt[]) => void
}) {
  const attachments = usePromptInputAttachments()
  const seen = useRef(new Set<string>())

  useEffect(() => {
    const live = new Set(
      attachments.files.map((file) => file.filename).filter((name): name is string => Boolean(name)),
    )
    for (const name of live) seen.current.add(name)
    setPasted((current) => {
      const next = current.filter((item) => !seen.current.has(item.filename) || live.has(item.filename))
      return next.length === current.length ? current : next
    })
  }, [attachments.files, setPasted])

  if (attachments.files.length === 0) {
    return null
  }

  const pastedByName = new Map(pasted.map((item) => [item.filename, item]))

  return (
    <Attachments variant="inline" className="px-1 pt-1">
      {attachments.files.map((attachment) => {
        const prompt = attachment.filename ? pastedByName.get(attachment.filename) : undefined
        if (prompt) {
          return (
            <PastedPromptAttachment
              attachment={attachment}
              key={attachment.id}
              onRemove={() => {
                attachments.remove(attachment.id)
                setPasted((current) => current.filter((item) => item.filename !== prompt.filename))
              }}
              onTextChange={(text) => {
                setPasted((current) =>
                  current.map((item) => (item.filename === prompt.filename ? { ...item, text } : item)),
                )
              }}
              prompt={prompt}
            />
          )
        }
        return (
          <Attachment
            data={attachment}
            key={attachment.id}
            onRemove={() => attachments.remove(attachment.id)}
          >
            <AttachmentPreview />
            <AttachmentRemove />
          </Attachment>
        )
      })}
    </Attachments>
  )
}

function ComposerTextarea({
  text,
  onTextChange,
  placeholder,
  pasted,
  setPasted,
}: {
  text: string
  onTextChange: (value: string) => void
  placeholder?: string
  pasted: readonly PastedPrompt[]
  setPasted: (update: (current: PastedPrompt[]) => PastedPrompt[]) => void
}) {
  const attachments = usePromptInputAttachments()

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const clipboard = event.clipboardData
    if (!clipboard) return

    const files: File[] = []
    for (const item of clipboard.items) {
      if (item.kind !== "file") continue
      const file = item.getAsFile()
      if (file) files.push(file)
    }
    if (files.length > 0) {
      event.preventDefault()
      attachments.add(files)
      return
    }

    const pastedText = clipboard.getData("text/plain")
    if (!isLongPaste(pastedText)) return

    event.preventDefault()
    const filename = pastedPromptFilename(pastedText, [
      ...pasted.map((item) => item.filename),
      ...attachments.files.map((file) => file.filename),
    ])
    setPasted((current) => [...current, { filename, text: pastedText }])
    attachments.add([new File([pastedText], filename, { type: "text/plain" })])
    if (!text.trim()) onTextChange(PASTED_PROMPT_INSTRUCTION)
  }

  return (
    <PromptInputTextarea
      value={text}
      onChange={(event) => onTextChange(event.target.value)}
      onPaste={handlePaste}
      placeholder={placeholder ?? "Describe what you want to ship…"}
      className="min-h-[64px] px-4 pt-3.5 text-base leading-relaxed text-foreground placeholder:text-muted-foreground md:text-base"
    />
  )
}

type ComposerProps = {
  text: string
  onTextChange: (value: string) => void
  onSubmit: (message: PromptInputMessage) => void
  model?: string
  onModelChange?: (value: string) => void
  modelControls?: ReactNode
  reasoningEffort?: string
  onReasoningEffortChange?: (value: string) => void
  status?: ChatStatus
  onStop?: () => void
  placeholder?: string
  onPreview?: () => void
  previewActive?: boolean
  globalDrop?: boolean
}

function ComposerSubmit({ text, status, onStop }: Pick<ComposerProps, "text" | "status" | "onStop">) {
  const attachments = usePromptInputAttachments()
  return (
    <PromptInputSubmit
      disabled={status === "submitted" || (!text.trim() && attachments.files.length === 0 && status !== "streaming")}
      status={status}
      onStop={onStop}
      className="rounded-full shadow-[inset_0_1px_0_0_oklch(0.95_0.05_285/0.2)] transition-all duration-300 enabled:hover:shadow-[0_0_20px_-2px_oklch(0.499_0.214_278/0.8)] active:scale-90"
    />
  )
}

// Gemini multimodal inputs: image, document, and video formats that
// app/api/orchestrator/route.ts forwards as Strands content blocks.
const ACCEPTED_FILE_TYPES = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "application/pdf",
  "text/plain",
  "text/html",
  "text/csv",
  "text/markdown",
  "application/json",
  "video/mp4",
  "video/mpeg",
  "video/quicktime",
  "video/webm",
  "video/x-msvideo",
  "video/x-ms-wmv",
  "video/x-flv",
  "video/3gpp",
  ".md",
  ".mov",
  ".avi",
].join(",")

export function Composer({
  text,
  onTextChange,
  onSubmit,
  model,
  onModelChange,
  status,
  onStop,
  placeholder,
  onPreview,
  previewActive,
  modelControls,
  reasoningEffort = "default",
  onReasoningEffortChange,
  globalDrop = true,
}: ComposerProps) {
  const [pasted, setPasted] = useState<PastedPrompt[]>([])

  return (
    <PromptInput
      onSubmit={(message) => {
        const pastedNames = new Set(pasted.map((item) => item.filename))
        const includesPasted = (message.files ?? []).some(
          (file) => file.filename && pastedNames.has(file.filename),
        )
        onSubmit({
          ...message,
          files: applyPastedEdits(message.files, pasted),
          text: messageForPastedPrompt(message.text, includesPasted),
        })
      }}
      accept={ACCEPTED_FILE_TYPES}
      globalDrop={globalDrop}
      multiple
      className="group/composer app-glass-edge overflow-hidden rounded-3xl border bg-card/85 shadow-[inset_0_1px_0_0_oklch(0.9_0.04_285/0.07),0_16px_48px_-16px_oklch(0.05_0.01_285/0.9)] backdrop-blur-xl transition-[border-color,box-shadow] duration-500 focus-within:border-blurple-bright/40 focus-within:shadow-[inset_0_1px_0_0_oklch(0.9_0.04_285/0.07),0_16px_56px_-14px_oklch(0.499_0.214_278/0.45)]"
    >
      {/* Focus hairline: a single thread of indigo light along the top edge.
          Lives inside InputGroup (which is `relative`); the named group on the
          form above lets it respond to focus anywhere in the composer. */}
      <div
        aria-hidden="true"
        className="hairline-indigo pointer-events-none absolute inset-x-10 top-0 z-10 h-px opacity-0 transition-opacity duration-700 group-focus-within/composer:opacity-100"
      />
      <PromptInputHeader className="border-0">
        <AttachmentsDisplay pasted={pasted} setPasted={setPasted} />
      </PromptInputHeader>
      <PromptInputBody>
        <ComposerTextarea
          onTextChange={onTextChange}
          pasted={pasted}
          placeholder={placeholder}
          setPasted={setPasted}
          text={text}
        />
      </PromptInputBody>
      <PromptInputFooter className="border-0 px-2.5 pb-2.5">
        <PromptInputTools className="min-w-0 flex-wrap">
          <PromptInputActionMenu>
            <PromptInputActionMenuTrigger>
              <PaperclipIcon size={16} />
            </PromptInputActionMenuTrigger>
            <PromptInputActionMenuContent>
              <PromptInputActionAddAttachments />
            </PromptInputActionMenuContent>
          </PromptInputActionMenu>
          {modelControls ?? (model && onModelChange ? (
            <ModelPicker
              value={model}
              onValueChange={onModelChange}
              reasoningEffort={reasoningEffort}
              onReasoningEffortChange={onReasoningEffortChange}
            />
          ) : null)}
          {onPreview ? (
            <Button
              aria-pressed={previewActive}
              data-testid="composer-web-preview"
              onClick={onPreview}
              size="icon-xs"
              title="Web preview"
              type="button"
              variant={previewActive ? "secondary" : "ghost"}
            >
              <AppWindowIcon />
              <span className="sr-only">Web preview</span>
            </Button>
          ) : null}
        </PromptInputTools>
        {/* PromptInputSubmit swaps its own icon off `status` — send arrow
            when idle, spinner on submitted, stop square while streaming (which
            calls onStop), X on error. onStop is what makes the stop state real
            rather than decorative. */}
        <ComposerSubmit
          text={text}
          status={status}
          onStop={onStop}
        />
      </PromptInputFooter>
    </PromptInput>
  )
}
