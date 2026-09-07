"use client"

import type { ChatStatus } from "ai"
import type { ReactNode } from "react"
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

function AttachmentsDisplay() {
  const attachments = usePromptInputAttachments()

  if (attachments.files.length === 0) {
    return null
  }

  return (
    <Attachments variant="inline" className="px-1 pt-1">
      {attachments.files.map((attachment) => (
        <Attachment
          data={attachment}
          key={attachment.id}
          onRemove={() => attachments.remove(attachment.id)}
        >
          <AttachmentPreview />
          <AttachmentRemove />
        </Attachment>
      ))}
    </Attachments>
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
      className="rounded-full transition-all duration-300 enabled:hover:shadow-[0_0_20px_-2px_oklch(0.62_0.17_250/0.7)] active:scale-90"
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
  return (
    <PromptInput
      onSubmit={onSubmit}
      accept={ACCEPTED_FILE_TYPES}
      globalDrop={globalDrop}
      multiple
      className="group/composer app-glass-edge overflow-hidden rounded-3xl border bg-card/70 shadow-[0_8px_40px_-12px_oklch(0.4_0.16_250/0.5)] backdrop-blur-xl transition-[border-color,box-shadow] duration-500 focus-within:border-blurple/50 focus-within:shadow-[0_12px_56px_-12px_oklch(0.55_0.17_250/0.65)]"
    >
      {/* Focus hairline: a single thread of blue light along the top edge.
          Lives inside InputGroup (which is `relative`); the named group on the
          form above lets it respond to focus anywhere in the composer. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-10 top-0 z-10 h-px bg-gradient-to-r from-transparent via-blurple-bright/70 to-transparent opacity-0 transition-opacity duration-700 group-focus-within/composer:opacity-100"
      />
      <PromptInputHeader className="border-0">
        <AttachmentsDisplay />
      </PromptInputHeader>
      <PromptInputBody>
        <PromptInputTextarea
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          placeholder={placeholder ?? "Describe what you want to ship…"}
          className="min-h-[64px] text-base leading-relaxed placeholder:text-muted-foreground/70"
        />
      </PromptInputBody>
      <PromptInputFooter className="border-0 px-2 pb-2">
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
