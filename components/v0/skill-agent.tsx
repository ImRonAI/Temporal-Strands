"use client"

import { jsonSchema, type DynamicToolUIPart } from "ai"
import { BotIcon, BrainIcon, ChevronDownIcon, MessageSquareIcon, WrenchIcon } from "lucide-react"
import type { ReactNode } from "react"
import { Agent, AgentContent, AgentHeader, AgentInstructions, AgentTool, AgentTools } from "@/components/ai-elements/agent"
import { ChainOfThought, ChainOfThoughtContent, ChainOfThoughtHeader, ChainOfThoughtStep } from "@/components/ai-elements/chain-of-thought"
import { MessageResponse } from "@/components/ai-elements/message"
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
    <Agent className="app-glass app-glass-edge min-w-0" data-skill-agent={part?.toolCallId ?? run?.toolUseId}>
      <AgentHeader name={`${name} · ${status}`} model={configuration?.model}
        className="[&>div]:min-w-0 [&>div]:flex-wrap [&_span]:break-words" />
      <AgentContent className="min-w-0">
        <details>
          <summary className="cursor-pointer text-sm font-medium">System prompt</summary>
          <AgentInstructions className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words">
            {configuration?.systemPrompt ?? "System prompt not received. It will appear when the skill agent starts."}
          </AgentInstructions>
        </details>
        <details>
          <summary className="cursor-pointer text-sm font-medium">User prompt</summary>
          <AgentInstructions className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words">{request}</AgentInstructions>
        </details>
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
        <Task defaultOpen data-testid="skill-agent-task">
          <TaskTrigger title={`${name} execution`}>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <BotIcon className="size-4" /><span>{name} execution</span>
              <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
            </div>
          </TaskTrigger>
          <TaskContent>
            <ChainOfThought defaultOpen>
              <ChainOfThoughtHeader>{running ? "Agent working..." : "Agent activity"}</ChainOfThoughtHeader>
              <ChainOfThoughtContent className="max-h-[32rem] overflow-y-auto">
                {run && run.attempt > 1 && <TaskItem>Attempt {run.attempt}</TaskItem>}
                {!run?.timeline.length && <TaskItem>{running ? "Waiting for agent events..." : status}</TaskItem>}
                {run?.timeline.map((entry, index) => {
                  if (entry.kind === "native") return <TaskItem key={index}>{renderNative(entry.event)}</TaskItem>
                  if (entry.kind === "tool") {
                    const tool = run.node.tools.find(tool => tool.id === entry.id)
                    if (!tool) return null
                    const terminal = tool.status === "output-available" || tool.status === "output-error"
                    return (
                      <TaskItem key={index} data-skill-tool={tool.id}>
                        <ChainOfThoughtStep icon={tool.name === "think" ? BrainIcon : WrenchIcon} label={tool.name}
                          status={terminal ? "complete" : running ? "active" : "pending"}>
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
                  return <TaskItem key={index}>
                    <ChainOfThoughtStep icon={entry.kind === "reasoning" ? BrainIcon : MessageSquareIcon}
                      label={entry.kind === "reasoning" ? "Thinking" : "Agent update"}
                      status={running && index === run.timeline.length - 1 ? "active" : "complete"}>
                      <MessageResponse isAnimating={running && index === run.timeline.length - 1}>{entry.text}</MessageResponse>
                    </ChainOfThoughtStep>
                  </TaskItem>
                })}
                {finalText && !failed && <TaskItem>
                  <ChainOfThoughtStep icon={MessageSquareIcon} label="Final output" status="complete">
                    <MessageResponse>{finalText}</MessageResponse>
                  </ChainOfThoughtStep>
                </TaskItem>}
                {errorText && <TaskItem><p role="alert" className="text-destructive whitespace-pre-wrap">{errorText}</p></TaskItem>}
                {run && run.events.length > 0 && <TaskItem>
                  <details>
                    <summary className="cursor-pointer">All emitted events ({run.events.length})</summary>
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(run.events, null, 2)}</pre>
                  </details>
                </TaskItem>}
              </ChainOfThoughtContent>
            </ChainOfThought>
          </TaskContent>
        </Task>
      </AgentContent>
    </Agent>
  )
}
