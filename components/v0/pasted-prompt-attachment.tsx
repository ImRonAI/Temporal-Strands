"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import type { FileUIPart } from "ai"
import { ChevronDownIcon, ChevronUpIcon, CopyIcon } from "lucide-react"

import {
  Attachment,
  AttachmentHoverCard,
  AttachmentHoverCardContent,
  AttachmentHoverCardTrigger,
  AttachmentPreview,
  AttachmentRemove,
} from "@/components/ai-elements/attachments"
import {
  Artifact,
  ArtifactActions,
  ArtifactClose,
  ArtifactContent,
  ArtifactDescription,
  ArtifactHeader,
  ArtifactTitle,
  ArtifactAction,
} from "@/components/ai-elements/artifact"
import {
  WebPreview,
  WebPreviewBody,
  WebPreviewNavigation,
  WebPreviewNavigationButton,
  WebPreviewUrl,
} from "@/components/ai-elements/web-preview"
import {
  PASTED_PROMPT_FRAME,
  PASTED_PROMPT_HOST,
  buildPastedPromptDocument,
  findTextMatches,
  pastedPromptText,
  titleFromPastedText,
  wrapMatchIndex,
  type PastedPrompt,
} from "@/components/v0/pasted-prompt"

type PastedPromptPreviewProps = {
  prompt: PastedPrompt
  onTextChange: (text: string) => void
  onClose: () => void
}

export function PastedPromptPreview({ prompt, onTextChange, onClose }: PastedPromptPreviewProps) {
  const { filename, text } = prompt
  const [initialText] = useState(text)
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const [seek, setSeek] = useState(0)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const focusMatch = useRef(false)
  const srcDoc = useMemo(
    () => buildPastedPromptDocument(initialText, filename),
    [initialText, filename],
  )
  const matches = useMemo(() => findTextMatches(text, query), [text, query])
  const safeIndex = wrapMatchIndex(activeIndex, matches.length)
  const highlight = useRef({ ranges: matches, activeIndex: safeIndex })

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const data = event.data as { source?: string; filename?: string; type?: string; text?: string } | null
      if (!data || data.source !== PASTED_PROMPT_FRAME || data.filename !== filename) return
      if (data.type === "edit" && typeof data.text === "string") onTextChange(data.text)
      if (data.type === "close") onClose()
    }
    window.addEventListener("message", onMessage)
    return () => window.removeEventListener("message", onMessage)
  }, [filename, onClose, onTextChange])

  function publish(focus: boolean) {
    const current = highlight.current
    iframeRef.current?.contentWindow?.postMessage(
      {
        activeIndex: current.activeIndex,
        filename,
        focus,
        ranges: current.ranges,
        source: PASTED_PROMPT_HOST,
        type: "highlight",
      },
      "*",
    )
  }

  useEffect(() => {
    highlight.current = { ranges: matches, activeIndex: safeIndex }
    const focus = focusMatch.current
    focusMatch.current = false
    iframeRef.current?.contentWindow?.postMessage(
      {
        activeIndex: safeIndex,
        filename,
        focus,
        ranges: matches,
        source: PASTED_PROMPT_HOST,
        type: "highlight",
      },
      "*",
    )
  }, [filename, matches, safeIndex, seek])

  function moveMatch(delta: number) {
    if (matches.length === 0) return
    focusMatch.current = true
    setActiveIndex((index) => wrapMatchIndex(index + delta, matches.length))
    setSeek((value) => value + 1)
  }

  const title = titleFromPastedText(text)
  const status = query.trim()
    ? matches.length > 0
      ? `${safeIndex + 1} / ${matches.length}`
      : "No matches"
    : "Find"

  return (
    <Artifact
      aria-label={`Preview of ${title}`}
      className="app-elevated relative h-[15rem] w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-xl border shadow-[0_24px_80px_-28px_oklch(0.05_0.01_285/0.95),0_0_0_1px_oklch(0.7_0.14_284/0.08)]"
      data-testid="pasted-prompt-preview"
    >
      <div aria-hidden="true" className="hairline-indigo pointer-events-none absolute inset-x-8 top-0 z-10 h-px" />
      <ArtifactHeader className="border-b border-white/8 bg-transparent px-3 py-2">
        <div className="min-w-0">
          <ArtifactTitle className="truncate font-editorial text-sm tracking-normal">{title}</ArtifactTitle>
          <ArtifactDescription className="text-[11px] tabular-nums tracking-wide">
            {text.length.toLocaleString()} characters
          </ArtifactDescription>
        </div>
        <ArtifactActions>
          <ArtifactAction
            icon={CopyIcon}
            label="Copy pasted prompt"
            onClick={() => {
              void navigator.clipboard.writeText(text)
            }}
            tooltip="Copy"
          />
          <ArtifactClose onClick={onClose} />
        </ArtifactActions>
      </ArtifactHeader>
      <ArtifactContent className="flex min-h-0 flex-1 flex-col bg-transparent p-0">
        <WebPreview className="min-h-0 flex-1 rounded-none border-0 bg-transparent" defaultUrl="">
          <WebPreviewNavigation className="h-10 gap-1.5 border-white/8 bg-black/20 px-2.5">
            <WebPreviewUrl
              aria-label="Search pasted text"
              autoComplete="off"
              className="h-8 min-w-0 flex-1 rounded-full border-white/10 bg-white/[0.03] px-3 text-[13px] shadow-[inset_0_1px_0_0_oklch(0.9_0.04_285/0.06)]"
              onChange={(event) => {
                setQuery(event.currentTarget.value)
                setActiveIndex(0)
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault()
                  moveMatch(event.shiftKey ? -1 : 1)
                }
                if (event.key === "Escape") {
                  event.preventDefault()
                  onClose()
                }
              }}
              placeholder="Search this prompt…"
              spellCheck={false}
              value={query}
            />
            <span
              aria-live="polite"
              className="shrink-0 rounded-full border border-white/10 bg-white/[0.03] px-2 py-1 text-[10px] tabular-nums tracking-wide text-muted-foreground"
            >
              {status}
            </span>
            <WebPreviewNavigationButton
              aria-label="Previous match"
              className="rounded-full"
              disabled={matches.length === 0}
              onClick={() => moveMatch(-1)}
              tooltip="Previous match"
              type="button"
            >
              <ChevronUpIcon />
            </WebPreviewNavigationButton>
            <WebPreviewNavigationButton
              aria-label="Next match"
              className="rounded-full"
              disabled={matches.length === 0}
              onClick={() => moveMatch(1)}
              tooltip="Next match"
              type="button"
            >
              <ChevronDownIcon />
            </WebPreviewNavigationButton>
          </WebPreviewNavigation>
          <WebPreviewBody
            className="min-h-0 bg-transparent"
            onLoad={() => publish(false)}
            ref={iframeRef}
            sandbox="allow-scripts"
            src="about:blank"
            srcDoc={srcDoc}
            title={title}
          />
        </WebPreview>
      </ArtifactContent>
    </Artifact>
  )
}

type PastedPromptAttachmentProps = {
  attachment: FileUIPart & { id: string }
  prompt: PastedPrompt
  onRemove?: () => void
  onTextChange: (text: string) => void
}

export function PastedPromptAttachment({
  attachment,
  prompt,
  onRemove,
  onTextChange,
}: PastedPromptAttachmentProps) {
  const [open, setOpen] = useState(false)
  const pinned = useRef(false)
  const pointerInside = useRef(false)
  const focusInside = useRef(false)
  const title = titleFromPastedText(prompt.text)

  function dismiss() {
    pinned.current = false
    pointerInside.current = false
    focusInside.current = false
    setOpen(false)
  }

  return (
    <AttachmentHoverCard
      onOpenChange={(next) => {
        if (next) {
          setOpen(true)
          return
        }
        if (pinned.current || pointerInside.current || focusInside.current) return
        setOpen(false)
      }}
      open={open}
    >
      <AttachmentHoverCardTrigger closeDelay={300} delay={150}>
        <Attachment
          aria-label={`${title}, ${prompt.text.length} characters`}
          className="h-auto w-max max-w-[22rem] items-center gap-2 rounded-xl border border-white/10 bg-transparent px-2 py-1.5 app-glass shadow-[0_16px_40px_-24px_oklch(0.05_0.01_285/0.9)] transition-[border-color,box-shadow] duration-300 hover:border-blurple-bright/35 hover:shadow-[0_18px_44px_-18px_oklch(0.499_0.214_278/0.55)]"
          data-testid="pasted-prompt-attachment"
          data={attachment}
          onClick={() => {
            pinned.current = true
            setOpen(true)
          }}
          onPointerEnter={() => {
            pointerInside.current = true
          }}
          onPointerLeave={() => {
            pointerInside.current = false
          }}
          title={title}
          onRemove={onRemove}
        >
          <AttachmentPreview className="size-7 rounded-lg bg-blurple/15 text-blurple-bright ring-1 ring-blurple-bright/25" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium tracking-tight">{title}</p>
            <p className="text-[10px] tabular-nums tracking-wide text-muted-foreground">
              {prompt.text.length.toLocaleString()} characters
            </p>
          </div>
          <AttachmentRemove />
        </Attachment>
      </AttachmentHoverCardTrigger>
      <AttachmentHoverCardContent
        className="w-auto border-0 bg-transparent p-0 shadow-none ring-0"
        onFocusCapture={() => {
          focusInside.current = true
          pinned.current = true
          setOpen(true)
        }}
        onPointerDown={() => {
          pinned.current = true
        }}
        onPointerEnter={() => {
          pointerInside.current = true
        }}
        onPointerLeave={() => {
          pointerInside.current = false
        }}
        side="top"
        sideOffset={8}
      >
        {open ? (
          <PastedPromptPreview onClose={dismiss} onTextChange={onTextChange} prompt={prompt} />
        ) : null}
      </AttachmentHoverCardContent>
    </AttachmentHoverCard>
  )
}

/** The same card after send. Local edits stay on the card; the sent part is unchanged. */
export function SentPastedPrompt({ id, part }: { id: string; part: FileUIPart }) {
  const initial = pastedPromptText(part)
  const [text, setText] = useState(initial ?? "")
  if (initial == null || !part.filename) return null
  return (
    <PastedPromptAttachment
      attachment={{ ...part, filename: part.filename, id }}
      onTextChange={setText}
      prompt={{ filename: part.filename, text }}
    />
  )
}
