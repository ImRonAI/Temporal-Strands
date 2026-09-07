"use client"

import type { ChatStatus } from "ai"
import {
  ExternalLinkIcon,
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

/** Browser handoff modes: agent→stopping→human→instructions→resuming→agent.
 *  The iframe stays mounted across all of them (no query-string control flag);
 *  control is toggled only through same-origin postMessage. */
export type BrowserControlMode =
  | "agent"
  | "stopping"
  | "human"
  | "instructions"
  | "resuming"

const MODES_LOCKING_CLOSE: ReadonlySet<BrowserControlMode> = new Set([
  "stopping",
  "human",
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
  instructions: { label: "Paused", className: AMBER_BADGE },
  resuming: { label: "Resuming", className: BLURPLE_BADGE },
}

const HEADER_BUTTON =
  "h-7 shrink-0 gap-1.5 px-2.5 text-[11px] font-medium"

export type ComputerUsePreviewPanelProps = {
  preview: ComputerUsePreviewState
  isStreaming: boolean
  status: ChatStatus
  pendingApproval: string
  onApprove: () => void
  onDeny: () => void
  onClose: () => void
  onTakeControl: () => Promise<void>
  onGiveControl: (message: PromptInputMessage) => Promise<void>
  className?: string
}

/** Browser-takeover preview panel for live Computer Use sessions. Composes the
 *  native Artifact / WebPreview / PromptInput primitives on the IDE's ide-glass
 *  tokens; it never reimplements CDP — reload is forwarded as
 *  {type:'browser-command', method:'Page.reload'} and only while human. */
export function ComputerUsePreviewPanel({
  preview,
  pendingApproval,
  onApprove,
  onDeny,
  onClose,
  onTakeControl,
  onGiveControl,
  className,
}: ComputerUsePreviewPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [mode, setMode] = useState<BrowserControlMode>("agent")
  const [handoffText, setHandoffText] = useState("")
  const [handoffError, setHandoffError] = useState("")

  // Session reset via derived state: a new CDP session adopts agent control on
  // the same render instead of a setState-in-effect cascade.
  const [seenSession, setSeenSession] = useState(preview.sessionId)
  if (preview.sessionId !== seenSession) {
    setSeenSession(preview.sessionId)
    setMode("agent")
    setHandoffText("")
    setHandoffError("")
  }

  const iframeSrc = preview.livePreviewUrl || preview.url
  const closeDisabled = MODES_LOCKING_CLOSE.has(mode)

  // Same-origin control handshake, sent on mode change and every iframe
  // (re)load. `enabled` is true ONLY in human mode, so transient states and
  // error fallbacks never leave the iframe interactive.
  const postControl = useCallback((enabled: boolean) => {
    try {
      iframeRef.current?.contentWindow?.postMessage(
        { type: "browser-control", enabled },
        window.location.origin
      )
    } catch {
      // Cross-origin or dead viewer: interaction stays disabled.
    }
  }, [])

  useEffect(() => {
    postControl(mode === "human")
  }, [mode, postControl])

  const onIframeLoad = useCallback(
    () => postControl(mode === "human"),
    [mode, postControl]
  )

  // Parent-viewer contract: browser commands are honoured only while human.
  // A dead viewer (postMessage no-op) falls back to a plain remount.
  const reconnectViewer = useCallback(() => {
    if (mode === "human") {
      try {
        iframeRef.current?.contentWindow?.postMessage(
          { type: "browser-command", method: "Page.reload" },
          window.location.origin
        )
      } catch {
        // Viewer owns CDP; a rejected postMessage leaves the UI unchanged.
      }
    }
    setReloadKey((key) => key + 1)
  }, [mode])

  const openDevtools = useCallback(() => {
    if (preview.devtoolsFrontendUrl) {
      window.open(preview.devtoolsFrontendUrl, "_blank", "noopener,noreferrer")
    }
  }, [preview.devtoolsFrontendUrl])

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

  // agent→stopping→human. No optimistic enabling: the iframe is interactive
  // only after the backend ack resolves (turn lock released). Failure stays in
  // agent mode and says so explicitly.
  const takeControl = useCallback(async () => {
    if (mode !== "agent") return
    setHandoffError("")
    setMode("stopping")
    try {
      await onTakeControl()
      setMode("human")
    } catch (error) {
      setMode("agent")
      setHandoffError(handoffFailure(error, "take control"))
    }
  }, [mode, onTakeControl])

  // instructions→resuming→agent. The handoff awaits the backend (continue-as-
  // new + successor ready); failure returns to instructions with the user's
  // text preserved and the iframe still paused.
  const giveControl = useCallback(
    async (message: PromptInputMessage) => {
      if (mode !== "instructions" || !message.text?.trim()) return
      setHandoffError("")
      setMode("resuming")
      try {
        await onGiveControl(message)
        setHandoffText("")
        setMode("agent")
      } catch (error) {
        setMode("instructions")
        setHandoffError(handoffFailure(error, "resume the agent"))
      }
    },
    [mode, onGiveControl]
  )

  const badge = BADGE[mode]

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
              Browser
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
                onClick={() => { postControl(false); setMode("instructions") }}
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
          <WebPreview className="min-h-0 flex-1 rounded-none border-0 bg-transparent" defaultUrl={preview.url}>
            <WebPreviewNavigation className="ide-glass-edge h-11 border-b bg-black/20 px-2">
              <WebPreviewNavigationButton
                aria-label="Reconnect viewer"
                data-testid="viewer-reconnect"
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
                aria-label="Open DevTools"
                data-testid="open-devtools"
                disabled={!preview.devtoolsFrontendUrl}
                onClick={openDevtools}
                tooltip="Open DevTools"
              >
                <ExternalLinkIcon className="size-4" />
              </WebPreviewNavigationButton>
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

            <WebPreviewBody
              className={cn(mode === "human" ? "" : "pointer-events-none")}
              data-control-mode={mode}
              key={`${preview.sessionId}-${iframeSrc}-${reloadKey}`}
              onLoad={onIframeLoad}
              ref={iframeRef}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-presentation"
              src={iframeSrc}
            />

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
