"use client"

import { jsonSchema, type DynamicToolUIPart } from "ai"
import { BotIcon, BrainIcon, ChevronDownIcon, MessageSquareIcon, WrenchIcon } from "lucide-react"
import type { ReactNode } from "react"
import { Agent, AgentContent, AgentHeader, AgentInstructions, AgentTool, AgentTools } from "@/components/ai-elements/agent"
import { ChainOfThought, ChainOfThoughtContent, ChainOfThoughtHeader, ChainOfThoughtStep } from "@/components/ai-elements/chain-of-thought"
import { CodeBlock } from "@/components/ai-elements/code-block"
import { MessageResponse } from "@/components/ai-elements/message"
import { Shimmer } from "@/components/ai-elements/shimmer"
import { Task, TaskContent, TaskItem, TaskTrigger } from "@/components/ai-elements/task"
import { Tool, ToolContent, ToolHeader, ToolInput, ToolOutput } from "@/components/ai-elements/tool"
import { Badge } from "@/components/ui/badge"
import { parseJson, partInput } from "./agent-run"
import type { SkillRunSnapshot } from "./skill-run"

function resultText(value: unknown): string {
  const result = parseJson(value)
  if (Array.isArray(result?.content)) {
    return result.content.map(block => resultText(block.text ?? block.json ?? block)).join("\n")
  }
  return typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2)
}

export function SkillAgent({ part, run, isThinking, renderNative }: {
  part?: DynamicToolUIPart
  run?: SkillRunSnapshot
  isThinking: boolean
  renderNative: (event: Record<string, unknown>) => ReactNode
}) {
  const input = part ? partInput(part) : undefined
  const configuration = run?.configuration
  const name = run?.skillName ?? (typeof input?.skill_name === "string" ? input.skill_name : "Skill agent")
  const output = part?.state === "output-available" ? part.output : undefined
  const result = parseJson(output)
  const failed = part?.state === "output-error" || result?.status === "error" || run?.node.status === "failed"
  const denied = part?.state === "output-denied"
  const finished = failed || denied || part?.state === "output-available" || run?.node.status === "done"
  const running = !finished && isThinking
  const status = denied ? "Denied" : failed ? "Failed" : finished ? "Completed" : running ? "Running" : "No final result received"
  const request = configuration?.userPrompt ?? run?.request ?? (typeof input?.request === "string" ? input.request : "Prompt not received yet.")
  const skills = configuration?.skills ?? run?.skills ?? (Array.isArray(input?.skills) ? input.skills.filter((skill): skill is string => typeof skill === "string") : [])
  const finalText = resultText(run?.result ?? output)
  const errorText = part?.state === "output-error" ? part.errorText : failed ? resultText(output ?? run?.result) : undefined

  return (
    <Agent className="app-glass app-glass-edge min-w-0 overflow-hidden rounded-lg" data-skill-agent={part?.toolCallId ?? run?.toolUseId}>
      <AgentHeader name={`${name} · ${status}`} model={configuration?.model}
        className="app-glass-edge border-b bg-secondary/70 [&>div]:min-w-0 [&>div]:flex-wrap [&_span]:break-words" />
      <AgentContent className="min-w-0">
        <Task defaultOpen={false} className="app-glass app-glass-edge rounded-lg border px-3 py-2.5" data-testid="skill-system-prompt">
          <TaskTrigger title="System prompt" className="activity-trigger w-full text-left" />
          <TaskContent keepMounted>
            <AgentInstructions className="max-h-64 overflow-auto whitespace-pre-wrap break-words">
              {configuration?.systemPrompt ?? "System prompt not received. It will appear when the skill agent starts."}
            </AgentInstructions>
          </TaskContent>
        </Task>
        <Task defaultOpen={false} className="app-glass app-glass-edge rounded-lg border px-3 py-2.5" data-testid="skill-user-prompt">
          <TaskTrigger title="User prompt" className="activity-trigger w-full text-left" />
          <TaskContent keepMounted>
            <AgentInstructions className="max-h-64 overflow-auto whitespace-pre-wrap break-words">{request}</AgentInstructions>
          </TaskContent>
        </Task>
        {configuration ? (
          <AgentTools>
            {configuration.tools.map(tool => (
              <AgentTool key={tool.name} value={tool.name} tool={{
                description: `${tool.name}${tool.description ? `: ${tool.description}` : ""}`,
                inputSchema: jsonSchema(tool.inputSchema.json),
              }} />
            ))}
          </AgentTools>
        ) : <p className="text-sm text-muted-foreground">Tools: configuration not received yet.</p>}
        {configuration?.tools.length === 0 && <p className="text-sm text-muted-foreground">No tools assigned.</p>}
        <section aria-label="Assigned skills" className="space-y-2">
          <h4 className="text-sm font-medium text-muted-foreground">Skills</h4>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{name} (agent instructions)</Badge>
            {skills.map(skill => <Badge key={skill} variant="outline">{skill}</Badge>)}
          </div>
          {skills.length === 0 && <p className="text-xs text-muted-foreground">No additional inline skills assigned.</p>}
        </section>
        <Task
          defaultOpen
          className={running ? "activity-scan" : undefined}
          data-testid="skill-agent-task"
          data-activity-state={running ? "active" : "complete"}
        >
          <TaskTrigger title={`${name} execution`} className="activity-trigger w-full text-left">
            <div className="flex w-full items-center gap-2 text-sm text-muted-foreground">
              <span className="flex size-6 items-center justify-center rounded-full border border-blurple-bright/20 bg-blurple/10">
                <BotIcon className="size-3.5 text-blurple-bright" />
              </span>
              <span className="min-w-0 flex-1 text-left text-[13px] font-medium tracking-[0.01em] text-foreground">
                {running ? <Shimmer>{`${name} execution`}</Shimmer> : `${name} execution`}
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary/70 px-2 py-0.5 text-[11px] text-muted-foreground">
                <span className={`size-1.5 rounded-full ${running ? "animate-pulse-soft bg-blurple-bright" : "bg-muted-foreground/50"}`} />
                {status}
              </span>
              <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
            </div>
          </TaskTrigger>
          <TaskContent>
            <ChainOfThought defaultOpen className="border-l-2 border-l-blurple-bright/30 pl-3">
              <ChainOfThoughtHeader className="text-[13px] font-medium tracking-[0.01em]">
                {running ? <Shimmer>Agent working...</Shimmer> : "Agent activity"}
              </ChainOfThoughtHeader>
              <ChainOfThoughtContent className="max-h-[32rem] overflow-y-auto">
                {run && run.attempt > 1 && <TaskItem>Attempt {run.attempt}</TaskItem>}
                {!run?.timeline.length && <TaskItem>{running ? "Waiting for agent events..." : status}</TaskItem>}
                {run?.timeline.map((entry, index) => {
                  if (entry.kind === "native") return <TaskItem key={index}>{renderNative(entry.event)}</TaskItem>
                  if (entry.kind === "tool") {
                    const tool = run.node.tools.find(tool => tool.id === entry.id)
                    if (!tool) return null
                    const terminal = tool.status === "output-available" || tool.status === "output-error"
                    const toolStepStatus = terminal ? "complete" : running ? "active" : "pending"
                    return (
                      <TaskItem key={index} data-skill-tool={tool.id}>
                        <ChainOfThoughtStep icon={tool.name === "think" ? BrainIcon : WrenchIcon} label={tool.name}
                          status={toolStepStatus} className="activity-step" data-activity-status={toolStepStatus}>
                          <Tool defaultOpen>
                            <ToolHeader type="dynamic-tool" toolName={tool.name} state={tool.status} />
                            <ToolContent>
                              <ToolInput input={parseJson(tool.input) ?? tool.input} />
                              {terminal && <ToolOutput output={tool.output} errorText={tool.status === "output-error" ? tool.output : undefined} />}
                            </ToolContent>
                          </Tool>
                        </ChainOfThoughtStep>
                      </TaskItem>
                    )
                  }
                  const entryStatus = running && index === run.timeline.length - 1 ? "active" : "complete"
                  return <TaskItem key={index}>
                    <ChainOfThoughtStep icon={entry.kind === "reasoning" ? BrainIcon : MessageSquareIcon}
                      label={entry.kind === "reasoning" ? "Thinking" : "Agent update"}
                      status={entryStatus} className="activity-step" data-activity-status={entryStatus}>
                      <MessageResponse isAnimating={entryStatus === "active"}>{entry.text}</MessageResponse>
                    </ChainOfThoughtStep>
                  </TaskItem>
                })}
                {finalText && !failed && <TaskItem>
                  <ChainOfThoughtStep icon={MessageSquareIcon} label="Final output" status="complete"
                    className="activity-step" data-activity-status="complete">
                    <MessageResponse>{finalText}</MessageResponse>
                  </ChainOfThoughtStep>
                </TaskItem>}
                {errorText && <TaskItem><p role="alert" className="text-destructive whitespace-pre-wrap">{errorText}</p></TaskItem>}
                {run && run.events.length > 0 && <TaskItem>
                  <Task defaultOpen={false} className="app-glass app-glass-edge rounded-lg border px-3 py-2.5">
                    <TaskTrigger title={`All emitted events (${run.events.length})`} className="activity-trigger w-full text-left" />
                    <TaskContent keepMounted>
                      <CodeBlock code={JSON.stringify(run.events, null, 2)} language="json" />
                    </TaskContent>
                  </Task>
                </TaskItem>}
              </ChainOfThoughtContent>
            </ChainOfThought>
          </TaskContent>
        </Task>
      </AgentContent>
    </Agent>
  )
}
