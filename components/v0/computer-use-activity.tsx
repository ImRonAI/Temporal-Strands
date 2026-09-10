"use client"

import { isDynamicToolUIPart, isReasoningUIPart, isTextUIPart, type UIMessage } from "ai"
import { BrainIcon, MessageSquareIcon, MousePointer2Icon } from "lucide-react"
import Image from "next/image"

import {
  ChainOfThought, ChainOfThoughtContent, ChainOfThoughtHeader,
  ChainOfThoughtImage, ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought"
import { MessageResponse } from "@/components/ai-elements/message"
import { Task, TaskContent, TaskItem, TaskTrigger } from "@/components/ai-elements/task"
import { Tool, ToolContent, ToolHeader, ToolInput, ToolOutput } from "@/components/ai-elements/tool"
import { COMPUTER_USE_TOOL_NAMES, computerUseFailed, computerUseFields, stripComputerUseScreenshot } from "./computer-use"

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
  return (
    <Task defaultOpen className="app-glass app-glass-edge" data-testid="computer-use-activity">
      <TaskTrigger title={`Computer use · ${terminal}/${tools.length} actions finished`} />
      <TaskContent>
        <ChainOfThought defaultOpen>
          <ChainOfThoughtHeader>Browser activity</ChainOfThoughtHeader>
          <ChainOfThoughtContent>
            {parts.map((part, index) => {
              if (isTextUIPart(part) || isReasoningUIPart(part)) {
                if (!part.text) return null
                const reasoning = isReasoningUIPart(part)
                const streaming = isThinking && part.state === "streaming"
                return (
                  <ChainOfThoughtStep key={`message-${index}`} icon={reasoning ? BrainIcon : MessageSquareIcon}
                    label={reasoning ? "Thinking" : "Agent update"} status={streaming ? "active" : "complete"}>
                    <MessageResponse isAnimating={streaming}>{part.text}</MessageResponse>
                  </ChainOfThoughtStep>
                )
              }
              if (!isDynamicToolUIPart(part)) return null
              const { action, intent, url, observation } = computerUseFields(part)
              const label = part.toolName === "think" ? "Think" : intent || (action === "navigate" && url ? `navigate: ${url}` : action.replaceAll("_", " "))
              const failed = part.state === "output-error" || (part.state === "output-available" && computerUseFailed(part.output))
              const state = failed ? "output-error" : part.state
              const finished = ["output-available", "output-error", "output-denied"].includes(state)
              return (
                <TaskItem key={part.toolCallId} data-computer-action={part.toolName === "think" ? undefined : part.toolCallId} data-action-state={state}>
                  <ChainOfThoughtStep icon={part.toolName === "think" ? BrainIcon : MousePointer2Icon} label={label}
                    status={finished ? "complete" : isThinking ? "active" : "pending"}>
                    {!finished && !isThinking ? (
                      <p className="text-xs text-amber-600">No final result received. Execution may still be running; do not repeat this action blindly.</p>
                    ) : null}
                    <Tool defaultOpen>
                      <ToolHeader type="dynamic-tool" toolName={part.toolName} title={label} state={state} />
                      <ToolContent>
                        <ToolInput input={stripComputerUseScreenshot(part.input ?? {})} />
                        {finished ? <ToolOutput output={"output" in part ? stripComputerUseScreenshot(part.output) : undefined}
                          errorText={part.state === "output-error" ? part.errorText : failed ? "Browser action failed" : undefined} /> : null}
                      </ToolContent>
                    </Tool>
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
