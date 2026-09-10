"use client"

import type { ChatStatus } from "ai"
import {
  GlobeIcon,
  HandIcon,
  Maximize2Icon,
  Minimize2Icon,
  RotateCwIcon,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"

import {
  Artifact,
  ArtifactActions,
  ArtifactClose,
  ArtifactContent,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact"
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input"
import {
  WebPreview,
  WebPreviewBody,
  WebPreviewNavigation,
  WebPreviewNavigationButton,
  WebPreviewUrl,
} from "@/components/ai-elements/web-preview"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import type { ComputerUsePreview as ComputerUsePreviewState } from "./computer-use"

/** Server acknowledgments gate human input, steering, and agent resumption. */
export type BrowserControlMode =
  | "agent"
  | "stopping"
  | "human"
  | "relinquishing"
  | "instructions"
  | "resuming"

const MODES_LOCKING_CLOSE: ReadonlySet<BrowserControlMode> = new Set([
  "stopping",
  "human",
  "relinquishing",
  "instructions",
  "resuming",
])

const BLURPLE_BADGE = "border-blurple/25 bg-blurple/15 text-blurple-bright"
const AMBER_BADGE = "border-amber-500/30 bg-amber-500/10 text-amber-300"

const BADGE: Record<BrowserControlMode, { label: string; className: string }> = {
  agent: { label: "Agent working", className: BLURPLE_BADGE },
  stopping: { label: "Stopping", className: AMBER_BADGE },
  human: {
    label: "You have control",
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  },
  relinquishing: { label: "Relinquishing", className: AMBER_BADGE },
  instructions: { label: "Paused", className: AMBER_BADGE },
  resuming: { label: "Resuming", className: BLURPLE_BADGE },
}

const HEADER_BUTTON =
  "h-7 shrink-0 gap-1.5 px-2.5 text-[11px] font-medium"

export type ComputerUsePreviewPanelProps = {
  preview: ComputerUsePreviewState
  /** Parent keys the panel by this durable chat ID, not a tool call ID. */
  sessionId?: string
  /** Seed from server-confirmed ownership when restoring a mounted panel. */
  initialControlMode?: BrowserControlMode
  isStreaming: boolean
  status: ChatStatus
  pendingApproval: string
  onApprove: () => void
  onDeny: () => void
  onClose: () => void
  onTakeControl: () => Promise<void>
  onReleaseControl?: () => Promise<void>
  onGiveControl: (message: PromptInputMessage) => Promise<void>
  className?: string
}

/** Native noVNC iframe composed with Artifact, WebPreview and PromptInput.
 * view_only is a viewer preference; the backend must fence actual VNC input. */
export function ComputerUsePreviewPanel({
  preview,
  sessionId,
  initialControlMode = "agent",
  isStreaming,
  status,
  pendingApproval,
  onApprove,
  onDeny,
  onClose,
  onTakeControl,
  onReleaseControl,
  onGiveControl,
  className,
}: ComputerUsePreviewPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const handoffPending = useRef(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [mode, setMode] = useState<BrowserControlMode>(initialControlMode)
  const [handoffText, setHandoffText] = useState("")
  const [handoffError, setHandoffError] = useState("")

  let iframeSrc = ""
  if (preview.viewerType === "novnc" && preview.livePreviewUrl) {
    try {
      const url = new URL(preview.livePreviewUrl)
      if (url.protocol === "http:" || url.protocol === "https:") {
        url.searchParams.set("view_only", mode === "human" ? "false" : "true")
        iframeSrc = url.toString()
      }
    } catch {
      // A missing/malformed viewer must never fall back to the visited page.
    }
  }
  const closeDisabled = MODES_LOCKING_CLOSE.has(mode)
  const takeoverAvailable = Boolean(sessionId && iframeSrc && onReleaseControl && preview.controlAvailable !== false)

  const reconnectViewer = useCallback(() => {
    setReloadKey((key) => key + 1)
  }, [])

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

  const handoffFailure = (error: unknown, verb: string) =>
    error instanceof Error && error.message
      ? `Could not ${verb}: ${error.message}`
      : `Could not ${verb}.`

  // A chat-stream stop alone is not a native input ownership acknowledgment.
  const takeControl = useCallback(async () => {
    if (mode !== "agent" || !takeoverAvailable || handoffPending.current) return
    handoffPending.current = true
    setHandoffError("")
    setMode("stopping")
    try {
      await onTakeControl()
      setMode("human")
    } catch (error) {
      setMode("agent")
      setHandoffError(handoffFailure(error, "take control"))
    } finally {
      handoffPending.current = false
    }
  }, [mode, onTakeControl, takeoverAvailable])

  // instructions→resuming→agent. The handoff awaits the backend (continue-as-
  // new + successor ready); failure returns to instructions with the user's
  // text preserved and the iframe still paused.
  const giveControl = useCallback(
    async (message: PromptInputMessage) => {
      if (mode !== "instructions" || !message.text?.trim() || handoffPending.current) return
      handoffPending.current = true
      setHandoffText(message.text)
      setHandoffError("")
      setMode("resuming")
      try {
        await onGiveControl(message)
        setHandoffText("")
        setMode("agent")
      } catch (error) {
        setMode("instructions")
        setHandoffError(handoffFailure(error, "resume the agent"))
        // Native PromptInput retains its input/attachments on rejected submit.
        throw error
      } finally {
        handoffPending.current = false
      }
    },
    [mode, onGiveControl]
  )

  const releaseControl = async () => {
    if (mode !== "human" || handoffPending.current) return
    handoffPending.current = true
    setHandoffError("")
    setMode("relinquishing")
    try {
      if (!onReleaseControl) throw new Error("Native desktop release unavailable")
      await onReleaseControl()
      setMode("instructions")
    } catch (error) {
      setHandoffError(handoffFailure(error, "release control"))
      setMode("human")
    } finally {
      handoffPending.current = false
    }
  }

  const badge = mode === "agent" && !isStreaming
    ? { label: status === "error" ? "Stream interrupted" : "Chat idle", className: status === "error" ? AMBER_BADGE : BLURPLE_BADGE }
    : BADGE[mode]

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col" ref={rootRef}>
      <Artifact
        className={cn(
          "@container/browser ide-glass flex h-full min-h-0 flex-col overflow-hidden rounded-xl",
          className
        )}
        data-testid="computer-use-preview"
      >
        <ArtifactHeader className="ide-glass-edge flex-wrap gap-2 border-b bg-white/[0.03] px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex size-7 items-center justify-center rounded-lg bg-blurple/30 shadow-[0_0_16px_-2px_oklch(0.62_0.17_250/0.6),inset_0_1px_0_0_oklch(0.86_0.08_235/0.3)]">
              <GlobeIcon className="size-4 text-blurple-bright" />
            </span>
            <ArtifactTitle className="truncate font-medium tracking-tight">
              Linux desktop
            </ArtifactTitle>
            <span
              className={cn(
                "rounded-full border px-2.5 py-0.5 font-mono text-[10px] font-medium",
                badge.className
              )}
              data-testid="browser-control-badge"
            >
              {badge.label}
            </span>
          </div>
          <ArtifactActions>
            {mode === "agent" ? (
              <Button
                className={cn(HEADER_BUTTON, "border-blurple/30 bg-blurple/10 text-blurple-bright hover:bg-blurple/20")}
                data-testid="take-control"
                disabled={!takeoverAvailable}
                title={!takeoverAvailable ? "A chat session, noVNC viewer and native release acknowledgment are required" : undefined}
                onClick={takeControl}
                size="sm"
                type="button"
                variant="outline"
              >
                <HandIcon className="size-3.5" />
                Take control
              </Button>
            ) : null}
            {mode === "human" ? (
              <Button
                className={cn(HEADER_BUTTON, "border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20")}
                data-testid="relinquish"
                onClick={releaseControl}
                size="sm"
                type="button"
                variant="outline"
              >
                <HandIcon className="size-3.5" />
                Relinquish
              </Button>
            ) : null}
            <ArtifactClose
              aria-label="Close browser preview"
              className="w-auto gap-1 px-2.5 text-[11px] font-medium"
              data-testid="browser-close"
              disabled={closeDisabled}
              onClick={onClose}
              title={closeDisabled ? "Finish the control handoff before closing" : "Close"}
            >
              <span aria-hidden>Close</span>
            </ArtifactClose>
          </ArtifactActions>
        </ArtifactHeader>

        <ArtifactContent className="flex min-h-0 flex-1 flex-col overflow-hidden p-0">
          <WebPreview className="min-h-0 flex-1 rounded-none border-0 bg-transparent">
            <WebPreviewNavigation className="ide-glass-edge h-11 border-b bg-black/20 px-2">
              <WebPreviewNavigationButton
                aria-label="Reconnect viewer"
                data-testid="viewer-reconnect"
                disabled={!iframeSrc}
                onClick={reconnectViewer}
                tooltip="Reconnect viewer"
              >
                <RotateCwIcon className="size-4" />
              </WebPreviewNavigationButton>
              <WebPreviewUrl
                className="pointer-events-none opacity-90"
                readOnly
                value={preview.url}
              />
              <WebPreviewNavigationButton
                aria-label={fullscreen ? "Exit fullscreen" : "Fullscreen"}
                data-testid="browser-fullscreen"
                onClick={toggleFullscreen}
                tooltip={fullscreen ? "Exit fullscreen" : "Fullscreen"}
              >
                {fullscreen ? (
                  <Minimize2Icon className="size-4" />
                ) : (
                  <Maximize2Icon className="size-4" />
                )}
              </WebPreviewNavigationButton>
            </WebPreviewNavigation>

            {pendingApproval && mode === "agent" ? (
              <Alert
                className="mx-2 mt-2 shrink-0 border-amber-500/30 bg-amber-500/5"
                data-testid="computer-use-approval"
              >
                <AlertTitle>Approval required</AlertTitle>
                <AlertDescription>
                  <div className="flex w-full flex-col gap-3">
                    <p data-testid="approval-reason">{pendingApproval}</p>
                    <div className="flex gap-2">
                      <Button onClick={onApprove} size="sm">
                        Approve
                      </Button>
                      <Button onClick={onDeny} size="sm" variant="outline">
                        Deny
                      </Button>
                    </div>
                  </div>
                </AlertDescription>
              </Alert>
            ) : null}

            {handoffError ? (
              <Alert
                className="mx-2 mt-2 shrink-0 border-destructive/40 bg-destructive/10"
                data-testid="browser-handoff-error"
                role="alert"
                variant="destructive"
              >
                <AlertTitle>Handoff failed</AlertTitle>
                <AlertDescription>{handoffError}</AlertDescription>
              </Alert>
            ) : null}

            {preview.intent && mode === "agent" ? (
              <p className="ide-glass-edge shrink-0 border-b px-3 py-1.5 text-muted-foreground text-xs">
                <span className="font-medium text-foreground">{preview.action}</span>
                {" · "}
                {preview.intent}
              </p>
            ) : null}

            {iframeSrc ? <WebPreviewBody
              title="Linux desktop via noVNC"
              className={cn(mode === "human" ? "" : "pointer-events-none")}
              data-control-mode={mode}
              key={`${sessionId ?? ""}-${reloadKey}`}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-presentation"
              src={iframeSrc}
            /> : <p role="status" className="p-3 text-sm text-muted-foreground">Desktop viewer unavailable. A native noVNC URL is required.</p>}

            {iframeSrc ? (
              <p className="shrink-0 border-t px-3 py-1.5 text-xs text-muted-foreground">
                Desktop viewer via noVNC. {mode === "human" ? "Your input is enabled." : "Take control to interact; agent screenshots appear in the task timeline."}
              </p>
            ) : null}

            {mode === "human" ? (
              <p className="ide-glass-edge shrink-0 border-t bg-emerald-500/[0.04] px-3 py-1.5 text-emerald-300/90 text-xs">
                You have control of the browser session. Click Relinquish when
                  you are done to hand instructions back to the agent.
              </p>
            ) : null}

            {mode === "instructions" || mode === "resuming" ? (
              <div className="ide-glass-edge shrink-0 border-t bg-white/[0.02] p-3">
                <PromptInput className="w-full" onSubmit={giveControl}>
                  <PromptInputBody>
                    <PromptInputTextarea
                      className="min-h-12"
                      disabled={mode === "resuming"}
                      onChange={(event) => setHandoffText(event.currentTarget.value)}
                      placeholder="What you changed, or instructions for the agent…"
                      value={handoffText}
                    />
                  </PromptInputBody>
                  <PromptInputFooter className="justify-end">
                    <PromptInputSubmit
                      data-testid="give-control-submit"
                      disabled={mode === "resuming" || !handoffText.trim()}
                    />
                  </PromptInputFooter>
                </PromptInput>
              </div>
            ) : null}
          </WebPreview>
        </ArtifactContent>
      </Artifact>
    </div>
  )
}
