"use client"

import type { ChatStatus } from "ai"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ExternalLinkIcon,
  HandIcon,
  Maximize2Icon,
  Minimize2Icon,
  PlayIcon,
  RotateCwIcon,
  XIcon,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"

import {
  WebPreview,
  WebPreviewBody,
  WebPreviewNavigation,
  WebPreviewNavigationButton,
  WebPreviewUrl,
} from "@/components/ai-elements/web-preview"
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputTextarea,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import type { ComputerUsePreview as ComputerUsePreviewState } from "./computer-use"

type ControlMode = "agent" | "human"

export type ComputerUsePreviewPanelProps = {
  preview: ComputerUsePreviewState
  isStreaming: boolean
  status: ChatStatus
  pendingApproval: string
  onApprove: () => void
  onDeny: () => void
  onClose: () => void
  onStop: () => void
  onGiveControl: (message: PromptInputMessage) => void
  className?: string
}

/** Documented WebPreview composition for live Computer Use CDP sessions.
 *  https://ai-sdk.dev/elements/components/web-preview */
export function ComputerUsePreviewPanel({
  preview,
  isStreaming,
  status,
  pendingApproval,
  onApprove,
  onDeny,
  onClose,
  onStop,
  onGiveControl,
  className,
}: ComputerUsePreviewPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [controlMode, setControlMode] = useState<ControlMode>("agent")
  const [handoffText, setHandoffText] = useState("")

  useEffect(() => {
    setControlMode("agent")
    setHandoffText("")
  }, [preview.sessionId])

  const iframeSrc = preview.livePreviewUrl || preview.url

  const reload = useCallback(() => setReloadKey((key) => key + 1), [])

  const historyBack = useCallback(() => {
    try {
      iframeRef.current?.contentWindow?.history.back()
    } catch {
      // CDP inspector may block parent-driven history.
    }
  }, [])

  const historyForward = useCallback(() => {
    try {
      iframeRef.current?.contentWindow?.history.forward()
    } catch {
      // CDP inspector may block parent-driven history.
    }
  }, [])

  const openExternal = useCallback(() => {
    const target = preview.devtoolsFrontendUrl || preview.livePreviewUrl || preview.url
    if (target) window.open(target, "_blank", "noopener,noreferrer")
  }, [preview.devtoolsFrontendUrl, preview.livePreviewUrl, preview.url])

  const toggleFullscreen = useCallback(async () => {
    const el = rootRef.current
    if (!el) return
    if (!document.fullscreenElement) {
      await el.requestFullscreen()
      setFullscreen(true)
      return
    }
    await document.exitFullscreen()
    setFullscreen(false)
  }, [])

  useEffect(() => {
    const sync = () => setFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener("fullscreenchange", sync)
    return () => document.removeEventListener("fullscreenchange", sync)
  }, [])

  const takeControl = useCallback(() => {
    onStop()
    if (pendingApproval) onDeny()
    setControlMode("human")
  }, [onDeny, onStop, pendingApproval])

  const giveControl = useCallback(
    (message: PromptInputMessage) => {
      const text = message.text?.trim()
      if (!text) return
      onGiveControl(message)
      setHandoffText("")
      setControlMode("agent")
    },
    [onGiveControl]
  )

  return (
    <div
      ref={rootRef}
      className={cn("flex min-h-0 min-w-0 flex-1 flex-col bg-background", className)}
    >
      <WebPreview
        className="min-h-0 flex-1 rounded-none border-0 bg-card"
        defaultUrl={preview.url}
      >
        <WebPreviewNavigation className="border-white/10 px-2">
          <WebPreviewNavigationButton tooltip="Back" onClick={historyBack}>
            <ArrowLeftIcon className="size-4" />
          </WebPreviewNavigationButton>
          <WebPreviewNavigationButton tooltip="Forward" onClick={historyForward}>
            <ArrowRightIcon className="size-4" />
          </WebPreviewNavigationButton>
          <WebPreviewNavigationButton tooltip="Reload" onClick={reload}>
            <RotateCwIcon className="size-4" />
          </WebPreviewNavigationButton>
          <WebPreviewUrl
            value={preview.url}
            readOnly
            className="pointer-events-none opacity-90"
          />
          <WebPreviewNavigationButton tooltip="Open session" onClick={openExternal}>
            <ExternalLinkIcon className="size-4" />
          </WebPreviewNavigationButton>
          <WebPreviewNavigationButton
            tooltip={fullscreen ? "Exit fullscreen" : "Fullscreen"}
            onClick={toggleFullscreen}
          >
            {fullscreen ? (
              <Minimize2Icon className="size-4" />
            ) : (
              <Maximize2Icon className="size-4" />
            )}
          </WebPreviewNavigationButton>
          {controlMode === "agent" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 shrink-0 gap-1.5 border-amber-500/40 text-xs"
              onClick={takeControl}
            >
              <HandIcon className="size-3.5" />
              Take control
            </Button>
          ) : null}
          <WebPreviewNavigationButton tooltip="Close preview" onClick={onClose}>
            <XIcon className="size-4" />
          </WebPreviewNavigationButton>
        </WebPreviewNavigation>

        {pendingApproval ? (
          <Alert
            className="mx-2 mt-2 shrink-0 border-amber-500/30 bg-amber-500/5"
            data-testid="computer-use-approval"
          >
            <AlertTitle>Approval required</AlertTitle>
            <AlertDescription>
              <div className="flex w-full flex-col gap-3">
                <p data-testid="approval-reason">{pendingApproval}</p>
                <div className="flex gap-2">
                  <Button size="sm" onClick={onApprove}>
                    Approve
                  </Button>
                  <Button size="sm" variant="outline" onClick={onDeny}>
                    Deny
                  </Button>
                </div>
              </div>
            </AlertDescription>
          </Alert>
        ) : null}

        {preview.intent && controlMode === "agent" ? (
          <p className="shrink-0 border-b border-white/10 px-3 py-1.5 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{preview.action}</span>
            {" · "}
            {preview.intent}
          </p>
        ) : null}

        <WebPreviewBody
          key={`${iframeSrc}-${reloadKey}`}
          ref={iframeRef}
          src={iframeSrc}
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-presentation"
        />

        {controlMode === "human" ? (
          <div className="shrink-0 border-t border-white/10 p-3">
            <p className="mb-2 text-xs text-muted-foreground">
              You have control of the browser session. Use the preview, then tell
              the agent how to continue.
            </p>
            <PromptInput onSubmit={giveControl} className="w-full">
              <PromptInputBody>
                <PromptInputTextarea
                  value={handoffText}
                  onChange={(event) => setHandoffText(event.currentTarget.value)}
                  placeholder="What you changed, or instructions for the agent…"
                  className="min-h-[72px]"
                />
              </PromptInputBody>
              <PromptInputFooter className="justify-end gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setControlMode("agent")}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  size="sm"
                  disabled={!handoffText.trim()}
                  className="gap-1.5"
                >
                  <PlayIcon className="size-3.5" />
                  Give control
                </Button>
              </PromptInputFooter>
            </PromptInput>
          </div>
        ) : null}
      </WebPreview>
    </div>
  )
}
