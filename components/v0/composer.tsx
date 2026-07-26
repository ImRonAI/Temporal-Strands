"use client"

import type { ChatStatus } from "ai"
import { PaperclipIcon } from "lucide-react"

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
  model: string
  onModelChange: (value: string) => void
  status?: ChatStatus
  onStop?: () => void
  placeholder?: string
}

// The Agent API carries attachments as `input_image` content parts and
// accepts nothing else (perplexity SDK, input_item_param.py), so the picker
// offers exactly the formats app/api/orchestrator/route.ts can forward.
// Accepting PDFs or documents here would mean silently discarding them.
const ACCEPTED_FILE_TYPES = "image/png,image/jpeg,image/gif,image/webp"

export function Composer({
  text,
  onTextChange,
  onSubmit,
  model,
  onModelChange,
  status,
  onStop,
  placeholder,
}: ComposerProps) {
  return (
    <PromptInput
      onSubmit={onSubmit}
      accept={ACCEPTED_FILE_TYPES}
      globalDrop
      multiple
      className="overflow-hidden rounded-3xl border border-white/10 bg-card/70 shadow-[0_8px_40px_-12px_oklch(0.4_0.2_277/0.5)] backdrop-blur-xl"
    >
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
        <PromptInputTools>
          <PromptInputActionMenu>
            <PromptInputActionMenuTrigger>
              <PaperclipIcon size={16} />
            </PromptInputActionMenuTrigger>
            <PromptInputActionMenuContent>
              <PromptInputActionAddAttachments />
            </PromptInputActionMenuContent>
          </PromptInputActionMenu>
          <ModelPicker value={model} onValueChange={onModelChange} />
        </PromptInputTools>
        {/* PromptInputSubmit swaps its own icon off `status` — send arrow
            when idle, spinner on submitted, stop square while streaming (which
            calls onStop), X on error. onStop is what makes the stop state real
            rather than decorative. */}
        <PromptInputSubmit
          disabled={!text.trim() && status !== "streaming"}
          status={status}
          onStop={onStop}
          className="rounded-full"
        />
      </PromptInputFooter>
    </PromptInput>
  )
}
