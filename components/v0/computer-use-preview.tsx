"use client"

import type { ChatStatus } from "ai"
import {
  HandIcon,
  Maximize2Icon,
  Minimize2Icon,
  MonitorIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  RotateCwIcon,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { z } from "zod"

import {
  Artifact,
  ArtifactActions,
  ArtifactClose,
  ArtifactContent,
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
  | "recovery"

const CONTROL_RETRY_BASE_MS = 1_000
const CONTROL_RETRY_MAX_MS = 15_000

const controlStatusSchema = z.object({
  ready: z.boolean(),
  mode: z.enum(["agent", "stopping", "human", "relinquishing", "instructions", "resuming", "recovery"]),
  // Nanosecond epochs exceed JS integer precision; require lossless wire strings.
  epoch: z.string().regex(/^[1-9][0-9]*$/),
})

const MODES_LOCKING_CLOSE: ReadonlySet<BrowserControlMode> = new Set([
  "stopping",
  "human",
  "relinquishing",
  "instructions",
  "resuming",
  "recovery",
])

/** One-word connection state rendered as a small dot beside the title. The
 *  label stays plain text so the toolbar reads as an instrument, not a badge
 *  wall. Green is reserved for a verified live link (agent streaming or the
 *  human holding control); amber is every transitional or unverified state. */
type Tone = "live" | "idle" | "busy" | "error"

const BADGE: Record<BrowserControlMode, { label: string; tone: Tone }> = {
  agent: { label: "Agent working", tone: "live" },
  stopping: { label: "Stopping", tone: "busy" },
  human: { label: "You have control", tone: "live" },
  relinquishing: { label: "Relinquishing", tone: "busy" },
  instructions: { label: "Paused", tone: "busy" },
  resuming: { label: "Resuming", tone: "busy" },
  recovery: { label: "Recovery required", tone: "busy" },
}

const TOOLBAR_ACTION = "h-7 shrink-0 gap-1.5 px-2 text-[11px] font-medium"
/** WebPreviewNavigationButton spreads className over its own, so restate the
 *  icon-button reset it would otherwise provide. */
const TOOLBAR_ICON = "size-7 p-0 text-white/85 hover:bg-white/15 hover:text-white"

/** The desktop is a black canvas; noVNC paints its own letterbox, so this is
 *  only what shows before the viewer connects. */
const DESKTOP_CANVAS = "bg-[oklch(0.955_0.009_55)]"

export type ComputerUsePreviewPanelProps = {
  preview: ComputerUsePreviewState
  /** Parent keys the panel by this durable chat ID, not a tool call ID. */
  sessionId?: string
  /** Presentation seed only; fresh server status is required before input. */
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
  /** In-app expansion: the parent hides the chat column so the desktop takes
   *  the whole row. Layout only — nothing about the session changes. */
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
  className?: string
}

/** Native noVNC iframe composed with Artifact, WebPreview and PromptInput.
 * view_only is a viewer preference; the backend must fence actual VNC input. */
export function ComputerUsePreviewPanel({
  preview,
  sessionId,
  initialControlMode = "recovery",
  isStreaming,
  status,
  pendingApproval,
  onApprove,
  onDeny,
  onClose,
  onTakeControl,
  onReleaseControl,
  onGiveControl,
  expanded = false,
  onExpandedChange,
  className,
}: ComputerUsePreviewPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const handoffPending = useRef(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [ownershipMode, setMode] = useState<BrowserControlMode>(initialControlMode)
  const [control, setControl] = useState<{ sessionId: string; epoch: string } | null>(null)
  const [checkingControl, setCheckingControl] = useState(true)
  const [transitionPending, setTransitionPending] = useState(false)
  const [controlError, setControlError] = useState("")
  const statusRequest = useRef<AbortController | null>(null)
  const [handoffText, setHandoffText] = useState("")
  const [handoffError, setHandoffError] = useState("")
  const mode = !checkingControl && sessionId && control?.sessionId === sessionId ? ownershipMode : "recovery"

  const refreshControl = useCallback(async () => {
    statusRequest.current?.abort()
    const controller = new AbortController()
    statusRequest.current = controller
    if (!sessionId) return
    return fetch(`/api/orchestrator/handoff?${new URLSearchParams({ sessionId })}`, {
      cache: "no-store",
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10_000)]),
    }).then(async response => {
      if (!response.ok) throw new Error(`Status request failed (${response.status})`)
      const data = controlStatusSchema.parse(await response.json())
      if (controller.signal.aborted) return
      const confirmedMode = data.ready ? data.mode : "recovery"
      setControl({ sessionId, epoch: data.epoch })
      setMode(confirmedMode)
      setControlError(confirmedMode === "recovery" ? "Desktop unavailable or recovery required. Input is disabled; reconnect to check status." : "")
      return confirmedMode
    }).catch(() => {
      if (controller.signal.aborted) return
      setControl(null)
      setMode("recovery")
      setControlError("Could not verify desktop control. Input is disabled; reconnect to retry.")
    }).finally(() => {
      if (!controller.signal.aborted) setCheckingControl(false)
    })
  }, [sessionId])

  useEffect(() => {
    void refreshControl()
    return () => { statusRequest.current?.abort() }
  }, [refreshControl])

  const observationId = preview.observation?.artifact_id
  const lastControlObservation = useRef("")
  useEffect(() => {
    if (!sessionId || !observationId || checkingControl || transitionPending || handoffPending.current) return
    const key = `${sessionId}:${observationId}`
    if (lastControlObservation.current === key) return
    lastControlObservation.current = key
    // The first action may claim ownership after the mount request returned 409.
    // Retry only on new evidence while unconfirmed, never on every healthy action.
    if (control?.sessionId !== sessionId || controlError) void refreshControl()
  }, [sessionId, observationId, checkingControl, transitionPending, control?.sessionId, controlError, refreshControl])

  // The mount request races the desktop worker's ownership claim: the panel
  // opens on the first browser tool part, before runtime.json names this
  // session, so that first GET legitimately answers 409/503. Keep asking with
  // backoff until the server confirms, then stop; a confirmed healthy state is
  // never re-polled on a timer.
  const unconfirmed = Boolean(sessionId) && !checkingControl && !transitionPending && (control?.sessionId !== sessionId || Boolean(controlError))
  useEffect(() => {
    if (!unconfirmed) return
    let attempt = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const schedule = () => {
      const delay = Math.min(CONTROL_RETRY_BASE_MS * 2 ** attempt, CONTROL_RETRY_MAX_MS)
      attempt += 1
      timer = setTimeout(() => {
        if (handoffPending.current) { schedule(); return }
        void refreshControl()?.then(schedule)
      }, delay)
    }
    schedule()
    return () => { if (timer !== undefined) clearTimeout(timer) }
  }, [unconfirmed, refreshControl])

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
  const takeoverAvailable = Boolean(mode === "agent" && iframeSrc && onReleaseControl && preview.controlAvailable !== false)

  const reconnectViewer = useCallback(async () => {
    if (handoffPending.current) return
    setCheckingControl(true)
    setControl(null)
    setControlError("")
    setHandoffError("")
    if (await refreshControl()) setReloadKey((key) => key + 1)
  }, [refreshControl])

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
    setTransitionPending(true)
    const request = statusRequest.current
    setHandoffError("")
    setMode("stopping")
    try {
      await onTakeControl()
    } catch (error) {
      if (!request?.signal.aborted) setHandoffError(handoffFailure(error, "take control"))
    } finally {
      if (request && !request.signal.aborted) await refreshControl()
      handoffPending.current = false
      setTransitionPending(false)
    }
  }, [mode, onTakeControl, takeoverAvailable, refreshControl])

  // Preserve feedback until both resume and authoritative agent status succeed.
  const giveControl = useCallback(
    async (message: PromptInputMessage) => {
      if (mode !== "instructions" || !message.text?.trim() || handoffPending.current) return
      handoffPending.current = true
      setTransitionPending(true)
      const request = statusRequest.current
      setHandoffText(message.text)
      setHandoffError("")
      setMode("resuming")
      let failure: unknown
      try {
        await onGiveControl(message)
      } catch (error) {
        failure = error
      }
      const confirmed = request && !request.signal.aborted ? await refreshControl() : undefined
      handoffPending.current = false
      setTransitionPending(false)
      if (failure || confirmed !== "agent") {
        const error = failure ?? new Error("Resumption is unconfirmed; reconnect to check status")
        setHandoffError(handoffFailure(error, "resume the agent"))
        // Reject native submit so unconfirmed resumption never clears feedback.
        throw error
      }
      setHandoffText("")
    },
    [mode, onGiveControl, refreshControl]
  )

  const releaseControl = async () => {
    if (mode !== "human" || handoffPending.current) return
    handoffPending.current = true
    setTransitionPending(true)
    const request = statusRequest.current
    setHandoffError("")
    setMode("relinquishing")
    try {
      if (!onReleaseControl) throw new Error("Native desktop release unavailable")
      await onReleaseControl()
    } catch (error) {
      if (!request?.signal.aborted) setHandoffError(handoffFailure(error, "release control"))
    } finally {
      if (request && !request.signal.aborted) await refreshControl()
      handoffPending.current = false
      setTransitionPending(false)
    }
  }

  // Viewer connection and chat-stream status are separate facts: an idle or
  // interrupted chat never claims the desktop is live, and vice versa.
  const badge: { label: string; tone: Tone } = checkingControl && sessionId
    ? { label: "Checking control", tone: "busy" }
    : mode === "agent" && !isStreaming
    ? status === "error"
      ? { label: "Stream interrupted", tone: "error" }
      : { label: "Chat idle", tone: "idle" }
    : BADGE[mode]

  const hint = mode === "human"
    ? "Your input is enabled. Relinquish to hand instructions back to the agent."
    : mode === "recovery"
    ? "Input disabled until desktop control is verified."
    : "Take control to interact; agent screenshots appear in the task timeline."

  const showSteering = mode === "instructions" || mode === "resuming" || (mode === "recovery" && Boolean(handoffText))

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col" ref={rootRef}>
      <Artifact
        className={cn(
          "@container/browser flex h-full min-h-0 flex-col overflow-hidden rounded-xl border-border/70 shadow-lift",
          DESKTOP_CANVAS,
          className
        )}
        data-testid="computer-use-preview"
      >
        <ArtifactContent className="flex min-h-0 flex-1 flex-col overflow-hidden p-0">
          <WebPreview className="min-h-0 flex-1 rounded-none border-0 bg-transparent">
            {/* The only chrome: one 40px instrument strip. Identity + state on
                the left, the visited URL as a quiet monospace readout, and
                every control on the right. Nothing else sits between the
                operator and the live desktop. */}
            <WebPreviewNavigation
              className="glass-tangerine h-12 gap-1.5 border-b border-white/25 px-3 py-0"
              data-testid="desktop-toolbar"
            >
              <div className="flex min-w-0 shrink-0 items-center gap-2 pl-1" title={hint}>
                <span aria-hidden className="text-[1.05rem] leading-none drop-shadow-[0_1px_1px_oklch(0.4_0.12_30/0.35)]">
                  💻
                </span>
                <ArtifactTitle className="truncate font-editorial text-[1.05rem] font-medium italic leading-none tracking-[-0.02em] text-white">
                  Gwen&apos;s Computer
                </ArtifactTitle>
                {/* Control state stays announced for assistive tech and the
                    hover hint; it is no longer drawn as a visible badge. */}
                <span className="sr-only" data-testid="browser-control-badge">
                  {badge.label}
                </span>
              </div>

              <WebPreviewUrl
                aria-label="Visited page"
                className="pointer-events-none hidden h-7 min-w-0 flex-1 truncate rounded-full border-transparent bg-white/18 px-3 font-mono text-[11px] text-white/90 shadow-none placeholder:text-white/60 @min-[36rem]/browser:block md:text-[11px]"
                readOnly
                tabIndex={-1}
                title={preview.url}
                value={preview.url}
              />
              <span className="min-w-0 flex-1 @min-[36rem]/browser:hidden" />

              <ArtifactActions className="shrink-0 gap-1">
                <WebPreviewNavigationButton
                  aria-label="Reconnect viewer"
                  className={TOOLBAR_ICON}
                  data-testid="viewer-reconnect"
                  disabled={!sessionId || checkingControl || transitionPending}
                  onClick={reconnectViewer}
                  tooltip="Reconnect viewer"
                >
                  <RotateCwIcon className="size-3.5" />
                </WebPreviewNavigationButton>
                {mode === "agent" || mode === "recovery" ? (
                  <Button
                    className={cn(TOOLBAR_ACTION, "rounded-full border-white/70 bg-white text-blurple-bright hover:bg-white/90 hover:text-blurple-bright")}
                    data-testid="take-control"
                    disabled={!takeoverAvailable || transitionPending}
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
                    className={cn(TOOLBAR_ACTION, "rounded-full border-white/70 bg-white text-emerald-700 hover:bg-white/90 hover:text-emerald-700")}
                    data-testid="relinquish"
                    disabled={transitionPending || !onReleaseControl}
                    onClick={releaseControl}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    <HandIcon className="size-3.5" />
                    Relinquish
                  </Button>
                ) : null}
                {onExpandedChange ? (
                  <WebPreviewNavigationButton
                    aria-label={expanded ? "Show chat" : "Hide chat"}
                    aria-pressed={expanded}
                    className={TOOLBAR_ICON}
                    data-testid="browser-expand"
                    onClick={() => onExpandedChange(!expanded)}
                    tooltip={expanded ? "Show chat" : "Hide chat"}
                  >
                    {expanded ? (
                      <PanelLeftOpenIcon className="size-3.5" />
                    ) : (
                      <PanelLeftCloseIcon className="size-3.5" />
                    )}
                  </WebPreviewNavigationButton>
                ) : null}
                <WebPreviewNavigationButton
                  aria-label={fullscreen ? "Exit fullscreen" : "Fullscreen"}
                  className={TOOLBAR_ICON}
                  data-testid="browser-fullscreen"
                  onClick={toggleFullscreen}
                  tooltip={fullscreen ? "Exit fullscreen" : "Fullscreen"}
                >
                  {fullscreen ? (
                    <Minimize2Icon className="size-3.5" />
                  ) : (
                    <Maximize2Icon className="size-3.5" />
                  )}
                </WebPreviewNavigationButton>
                <ArtifactClose
                  aria-label="Close desktop preview"
                  className={TOOLBAR_ICON}
                  data-testid="browser-close"
                  disabled={closeDisabled}
                  onClick={onClose}
                  title={closeDisabled ? "Finish the control handoff before closing" : "Close"}
                />
              </ArtifactActions>
            </WebPreviewNavigation>

            {/* The desktop owns everything below the toolbar. Approvals and
                failures float over it so the framebuffer never shrinks; the
                intent readout is a corner chip for the same reason. */}
            <div className={cn("relative flex min-h-0 flex-1 flex-col", DESKTOP_CANVAS)}>
              {mode === "human" ? (
                <span
                  aria-hidden
                  className="pointer-events-none absolute inset-0 z-10 ring-1 ring-inset ring-emerald-400/40"
                  data-testid="human-control-ring"
                />
              ) : null}

              {pendingApproval && mode === "agent" ? (
                <Alert
                  className="absolute inset-x-2 top-2 z-20 mx-auto max-w-xl border-amber-500/30 bg-background/92 shadow-lift backdrop-blur-md"
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

              {handoffError || controlError ? (
                <Alert
                  className="absolute inset-x-2 top-2 z-20 mx-auto max-w-xl border-destructive/40 bg-background/92 shadow-lift backdrop-blur-md"
                  data-testid="browser-handoff-error"
                  role="alert"
                  variant="destructive"
                >
                  <AlertTitle>{controlError ? "Desktop control unavailable" : "Handoff failed"}</AlertTitle>
                  <AlertDescription>{controlError || handoffError}</AlertDescription>
                </Alert>
              ) : null}

              {iframeSrc ? (
                <WebPreviewBody
                  title="Linux desktop via noVNC"
                  className={cn("bg-transparent", mode === "human" ? "" : "pointer-events-none")}
                  data-control-mode={mode}
                  key={`${sessionId ?? ""}-${control?.epoch ?? ""}-${reloadKey}`}
                  sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-presentation"
                  src={iframeSrc}
                />
              ) : (
                <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center" role="status">
                  <MonitorIcon aria-hidden className="size-8 text-muted-foreground/50" />
                  <p className="max-w-xs text-sm text-muted-foreground">
                    Desktop viewer unavailable. A native noVNC URL is required.
                  </p>
                </div>
              )}

              {preview.intent && mode === "agent" ? (
                <p
                  className="pointer-events-none absolute bottom-2 left-2 z-10 max-w-[calc(100%-1rem)] truncate rounded-md border border-border bg-background/85 px-2 py-1 font-mono text-[11px] text-muted-foreground backdrop-blur-sm"
                  data-testid="desktop-intent"
                >
                  <span className="font-medium text-foreground">{preview.action}</span>
                  {" · "}
                  {preview.intent}
                </p>
              ) : null}
            </div>

            {showSteering ? (
              <div className="shrink-0 border-t border-border bg-background/85 p-2 backdrop-blur-sm">
                <PromptInput className="w-full" onSubmit={giveControl}>
                  <PromptInputBody>
                    <PromptInputTextarea
                      className="min-h-12"
                      disabled={mode !== "instructions" || transitionPending}
                      onChange={(event) => setHandoffText(event.currentTarget.value)}
                      placeholder="What you changed, or instructions for the agent…"
                      value={handoffText}
                    />
                  </PromptInputBody>
                  <PromptInputFooter className="justify-end">
                    <PromptInputSubmit
                      data-testid="give-control-submit"
                      disabled={mode !== "instructions" || transitionPending || !handoffText.trim()}
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
