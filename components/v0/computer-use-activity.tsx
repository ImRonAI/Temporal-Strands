"use client"

import { isDynamicToolUIPart, isReasoningUIPart, isTextUIPart, type DynamicToolUIPart, type UIMessage } from "ai"
import { BrainIcon, ChevronDownIcon, MessageSquareIcon, MousePointer2Icon } from "lucide-react"
import Image from "next/image"

import {
  ChainOfThought, ChainOfThoughtContent, ChainOfThoughtHeader,
  ChainOfThoughtImage, ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought"
import { MessageResponse } from "@/components/ai-elements/message"
import { Shimmer } from "@/components/ai-elements/shimmer"
import { Task, TaskContent, TaskItem, TaskTrigger } from "@/components/ai-elements/task"
import { ToolInput, ToolOutput, getStatusBadge } from "@/components/ai-elements/tool"
import { COMPUTER_USE_TOOL_NAMES, computerUseFields, stripComputerUseScreenshot, toolPresentation, unstreamedThinkOutput } from "./computer-use"

type Part = UIMessage["parts"][number]
export type ActivitySegment =
  | { kind: "single"; part: Part; index: number }
  | { kind: "computer-use"; parts: UIMessage["parts"]; index: number }

/** Preserve emitted order. Text/reasoning between browser calls belongs to the
 * loop; final prose and unrelated tool runs remain outside it. No fabricated steps.
 */
export function buildActivitySegments(parts: UIMessage["parts"], streaming = false): ActivitySegment[] {
  const segments: ActivitySegment[] = []
  const computer = (part: Part) => isDynamicToolUIPart(part) && COMPUTER_USE_TOOL_NAMES.has(part.toolName)
  for (let index = 0; index < parts.length;) {
    if (!computer(parts[index])) {
      segments.push({ kind: "single", part: parts[index], index })
      index++
      continue
    }
    let end = index + 1
    for (let scan = end; scan < parts.length; scan++) {
      const part = parts[scan]
      if (computer(part)) end = scan + 1
      else if (!(isTextUIPart(part) || isReasoningUIPart(part) || part.type === "step-start" ||
        (isDynamicToolUIPart(part) && part.toolName === "think"))) break
      else if (streaming) end = scan + 1
    }
    segments.push({ kind: "computer-use", parts: parts.slice(index, end), index })
    index = end
  }
  return segments
}

export function computerUseTextIndices(parts: UIMessage["parts"], streaming = false): Set<number> {
  const indices = new Set<number>()
  for (const segment of buildActivitySegments(parts, streaming)) {
    if (segment.kind !== "computer-use") continue
    segment.parts.forEach((part, index) => {
      if (isTextUIPart(part)) indices.add(segment.index + index)
    })
  }
  return indices
}

/** One native Task for a browser loop, containing its ordered emitted events.
 * Uses native expansion, status badges, Streamdown and step animations unchanged.
 */
export function ComputerUseActivity({ parts, isThinking, sessionId }: {
  parts: UIMessage["parts"]
  isThinking: boolean
  /** Durable chat ID from data-session, never a tool call or browser session name. */
  sessionId?: string
}) {
  const tools = parts.filter(isDynamicToolUIPart).filter(part => part.toolName !== "think")
  const terminal = tools.filter(part => ["output-available", "output-error", "output-denied"].includes(part.state)).length
  const title = `Computer use · ${terminal}/${tools.length} actions finished`
  return (
    <Task
      defaultOpen
      className={`app-glass app-glass-edge rounded-lg border px-3 py-2.5 ${isThinking ? "activity-scan" : ""}`}
      data-testid="computer-use-activity"
      data-activity-state={isThinking ? "active" : "complete"}
    >
      <TaskTrigger title={title} className="activity-trigger w-full text-left">
        <div className="flex w-full items-center gap-2 text-muted-foreground text-sm">
          <span className="flex size-6 items-center justify-center rounded-full border border-blurple-bright/20 bg-blurple/10">
            <MousePointer2Icon className="size-3.5 text-blurple-bright" />
          </span>
          <span className="min-w-0 flex-1 text-left text-[13px] font-medium tracking-[0.01em] text-foreground">
            {isThinking ? <Shimmer>Computer use</Shimmer> : "Computer use"}
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-black/20 px-2 py-0.5 text-[11px] text-muted-foreground">
            <span className={`size-1.5 rounded-full ${isThinking ? "animate-pulse-soft bg-blurple-bright" : "bg-muted-foreground/50"}`} />
            {terminal}/{tools.length} actions finished
          </span>
          <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
        </div>
      </TaskTrigger>
      <TaskContent>
        <ChainOfThought defaultOpen className="border-l-2 border-l-blurple-bright/30 pl-3">
          <ChainOfThoughtHeader className="text-[13px] font-medium tracking-[0.01em]">
            {isThinking ? <Shimmer>Browser activity</Shimmer> : "Browser activity"}
          </ChainOfThoughtHeader>
          <ChainOfThoughtContent>
            {parts.map((part, index) => {
              if (isTextUIPart(part) || isReasoningUIPart(part)) {
                if (!part.text) return null
                const reasoning = isReasoningUIPart(part)
                const streaming = isThinking && part.state === "streaming"
                const status = streaming ? "active" : "complete"
                return (
                  <ChainOfThoughtStep key={`message-${index}`} icon={reasoning ? BrainIcon : MessageSquareIcon}
                    label={reasoning ? "Thinking" : "Agent update"} status={status}
                    className="activity-step" data-activity-status={status}>
                    <MessageResponse isAnimating={streaming}>{part.text}</MessageResponse>
                  </ChainOfThoughtStep>
                )
              }
              if (!isDynamicToolUIPart(part)) return null
              const { action, intent, url, observation } = computerUseFields(part)
              const result = toolPresentation(part)
              if (part.toolName === "think") {
                // Returned output is shown only where the live stream did not already carry it.
                const missing = result.terminal && !result.error ? unstreamedThinkOutput(result.text, parts) : ""
                if (result.terminal && !result.error && !missing) return null
                if (!result.terminal && isThinking) return null
                const thinkStatus = result.terminal ? "complete" : "pending"
                return (
                  <ChainOfThoughtStep key={part.toolCallId} icon={BrainIcon}
                    label={result.error
                      ? result.state === "output-denied" ? "Thinking denied" : "Thinking failed"
                      : missing ? "Thinking result" : "Thinking"}
                    status={thinkStatus} role={result.error ? "alert" : undefined}
                    className={result.error ? "activity-step text-destructive" : "activity-step"} data-activity-status={thinkStatus}>
                    {(result.error || missing) && <MessageResponse>{result.error || missing}</MessageResponse>}
                    {!result.terminal && !isThinking && <p className="text-xs text-amber-600">Stream ended before Think returned a result.</p>}
                  </ChainOfThoughtStep>
                )
              }
              const label = intent || (action === "navigate" && url ? `navigate: ${url}` : action.replaceAll("_", " "))
              const state = result.state
              const finished = result.terminal
              const status = finished ? "complete" : isThinking ? "active" : "pending"
              return (
                <TaskItem key={part.toolCallId} data-computer-action={part.toolName === "think" ? undefined : part.toolCallId} data-action-state={state}>
                  <ChainOfThoughtStep icon={MousePointer2Icon} label={label} role={result.error ? "alert" : undefined}
                    status={status} className="activity-step" data-activity-status={status}>
                    {!finished && !isThinking ? (
                      <p className="text-xs text-amber-600">No final result received. Execution may still be running; do not repeat this action blindly.</p>
                    ) : null}
                    {getStatusBadge(state)}
                    {result.error ? <MessageResponse className="text-destructive">{result.error}</MessageResponse>
                      : result.text ? <MessageResponse>{result.text}</MessageResponse> : null}
                    <Task defaultOpen={false}>
                      <TaskTrigger title="Action details" />
                      <TaskContent keepMounted>
                        {/* Native Tool primitives render the real arguments/result;
                            only legacy inline pixels are stripped before display. */}
                        <ToolInput input={stripComputerUseScreenshot(part.input ?? {})} />
                        {result.output !== undefined ? (
                          <ToolOutput output={stripComputerUseScreenshot(result.output) as DynamicToolUIPart["output"]}
                            errorText={undefined} />
                        ) : null}
                      </TaskContent>
                    </Task>
                    {observation && sessionId ? (
                      <ChainOfThoughtImage caption={label}>
                        <Image alt={label} width={observation.width} height={observation.height}
                          className="h-auto max-h-[20rem] max-w-full object-contain" unoptimized
                          src={`/api/orchestrator/desktop-image?${new URLSearchParams({ session_id: sessionId, artifact_id: observation.artifact_id })}`} />
                      </ChainOfThoughtImage>
                    ) : null}
                  </ChainOfThoughtStep>
                </TaskItem>
              )
            })}
          </ChainOfThoughtContent>
        </ChainOfThought>
      </TaskContent>
    </Task>
  )
}
