"use client"

import { DefaultGeneratedFile, type DynamicToolUIPart, type ReasoningUIPart, type UIMessage } from "ai"
import { BrainIcon, FileIcon, GitBranchIcon, WrenchIcon } from "lucide-react"

import { cn } from "@/lib/utils"

import {
  Agent,
  AgentContent,
  AgentHeader,
  AgentInstructions,
} from "@/components/ai-elements/agent"
import {
  Artifact,
  ArtifactContent,
  ArtifactDescription,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact"
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtSearchResult,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought"
import { CodeBlock } from "@/components/ai-elements/code-block"
import { Image } from "@/components/ai-elements/image"
import { MessageResponse } from "@/components/ai-elements/message"
import {
  Sandbox,
  SandboxContent,
  SandboxHeader,
  SandboxTabContent,
  SandboxTabs,
  SandboxTabsBar,
  SandboxTabsList,
  SandboxTabsTrigger,
} from "@/components/ai-elements/sandbox"
import { Task, TaskContent, TaskTrigger } from "@/components/ai-elements/task"
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"

function parseJson(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "string" || !value) return null
  try {
    const parsed = JSON.parse(value)
    return typeof parsed === "object" && parsed !== null ? parsed : null
  } catch {
    return null
  }
}

// "input" is present on every DynamicToolUIPart state except
// "input-streaming" (confirmed against the type directly, including
// "output-error" — a real bug found live: this component used to only
// look at "input-available" / "output-available", so every failed call
// (real ones — the model guessing invalid model ids for the create call)
// fell back to a blank placeholder instead of showing what it actually
// tried and why it failed).
function partInput(part: DynamicToolUIPart): Record<string, unknown> | undefined {
  return "input" in part ? (part.input as Record<string, unknown>) : undefined
}

function partOutputJson(part: DynamicToolUIPart): Record<string, unknown> | null {
  if (part.state !== "output-available") return null
  return parseJson(part.output)
}

// "results" means something entirely different depending on item type —
// SearchResult[] on search_results, SandboxResultsOutputItemResult[] on
// sandbox_results (verified against the installed perplexity SDK's
// output_item.py) — so this has to discriminate on `type`, not treat
// "has a results array" as one shape everywhere.
type SearchResultsItem = {
  type: "search_results"
  results?: { title?: string; url?: string }[]
}
type SandboxResultsItem = {
  type: "sandbox_results"
  code?: string
  // Real constraint per SandboxResultsOutputItem.language: Literal["python", "bash"]
  language?: "python" | "bash"
  status?: string
  results?: { stdout?: string; stderr?: string; exit_code?: number; status?: string }[]
}
type OutputItem = SearchResultsItem | SandboxResultsItem | { type?: string; filename?: string }

type GraphEnvelope = {
  run_id: string
  node_path: Array<{ id: string; kind: string }>
  formation_kind: string
  node_kind: string
  event_type: string
  payload: unknown
}

type GraphEventPart = {
  type: "data-graph-event"
  id?: string
  data: GraphEnvelope
}

function graphEventStatus(eventType: string): "active" | "complete" {
  return eventType === "run.accepted" || eventType.endsWith(".started")
    ? "active"
    : "complete"
}

// SandboxResultsOutputItem.status is its own real enum ("in_progress" |
// "completed" | "failed" | "timed_out" — verified against the installed
// perplexity SDK's output_item.py), distinct from ToolUIPart["state"]
// (which SandboxHeader actually requires). Mapping rather than hardcoding
// one state regardless of what really happened.
function sandboxStatusToToolState(status: string | undefined): DynamicToolUIPart["state"] {
  switch (status) {
    case "completed":
      return "output-available"
    case "failed":
    case "timed_out":
      return "output-error"
    default:
      return "input-available"
  }
}

// One create_agent_response call plus every retrieve/list/download call
// that shares its response id — grouped so the UI shows ONE live-updating
// sub-agent card instead of a flat list of same-looking "Sub-task" steps
// with no visible relationship between the spawn and its polls.
interface AgentChain {
  key: string
  create: DynamicToolUIPart
  polls: DynamicToolUIPart[]
  downloads: DynamicToolUIPart[]
}

function groupToolParts(toolParts: DynamicToolUIPart[]) {
  const chains: AgentChain[] = []
  const chainByResponseId = new Map<string, AgentChain>()
  const standalone: DynamicToolUIPart[] = []

  for (const part of toolParts) {
    if (part.toolName === "create_agent_response") {
      const chain: AgentChain = { key: part.toolCallId, create: part, polls: [], downloads: [] }
      chains.push(chain)
      const json = partOutputJson(part)
      const responseId = json?.id
      if (typeof responseId === "string") chainByResponseId.set(responseId, chain)
      continue
    }

    const responseId = partInput(part)?.response_id
    const chain = typeof responseId === "string" ? chainByResponseId.get(responseId) : undefined
    if (!chain) {
      standalone.push(part)
      continue
    }
    if (part.toolName === "download_agent_response_file") {
      chain.downloads.push(part)
    } else {
      chain.polls.push(part)
    }
  }

  return { chains, standalone }
}

function latestJson(chain: AgentChain): Record<string, unknown> | null {
  for (let i = chain.polls.length - 1; i >= 0; i--) {
    const json = partOutputJson(chain.polls[i])
    if (json) return json
  }
  return partOutputJson(chain.create)
}

function AgentChainCard({ chain }: { chain: AgentChain }) {
  const input = partInput(chain.create) as
    | { instructions?: string; task?: string; preset?: string; model?: string; models?: string[] }
    | undefined
  const json = latestJson(chain)
  const status = (json?.status as string | undefined) ?? (chain.create.state === "output-error" ? "failed" : "queued")
  const outputItems = (json?.output as OutputItem[] | undefined) ?? []
  const searchResults = outputItems
    .filter((item): item is SearchResultsItem => item.type === "search_results")
    .flatMap((item) => item.results ?? [])
  const sandboxItems = outputItems.filter(
    (item): item is SandboxResultsItem => item.type === "sandbox_results"
  )
  const modelLabel = input?.model || input?.models?.[0] || input?.preset || "preset default"
  // Real field on the response object (json.reasoning.summary) — null
  // unless reasoning_effort is set high enough for Perplexity to populate
  // it. Not streamed like the outer turn's reasoning (this is a one-shot
  // activity call, not a live SSE connection), so this is the only
  // sub-agent "reasoning trace" available at all right now.
  const reasoningSummary = (json?.reasoning as { summary?: string } | undefined)?.summary

  const images = chain.downloads
    .map((d) => ({ part: d, json: partOutputJson(d) }))
    .filter(
      (d): d is { part: DynamicToolUIPart; json: { binary_content_base64: string; content_type: string } } =>
        typeof d.json?.binary_content_base64 === "string" &&
        typeof d.json?.content_type === "string" &&
        (d.json.content_type as string).startsWith("image/")
    )

  return (
    <Agent className="border-white/10 bg-white/[0.02] backdrop-blur-sm">
      <AgentHeader model={modelLabel} name={`Sub-agent · ${status}`} />
      <AgentContent>
        <AgentInstructions>{input?.instructions ?? input?.task ?? "…"}</AgentInstructions>

        {reasoningSummary && (
          <ChainOfThoughtStep
            icon={BrainIcon}
            label="Sub-agent reasoning"
            description={<MessageResponse>{reasoningSummary}</MessageResponse>}
          />
        )}

        {searchResults.length > 0 && (
          <ChainOfThoughtSearchResults>
            {searchResults.slice(0, 8).map((r, i) => (
              <ChainOfThoughtSearchResult key={`${r.url}-${i}`}>
                {r.title ?? r.url}
              </ChainOfThoughtSearchResult>
            ))}
          </ChainOfThoughtSearchResults>
        )}

        {sandboxItems.map((item, i) => {
          const output = (item.results ?? [])
            .map((r) => [r.stdout, r.stderr].filter(Boolean).join("\n"))
            .filter(Boolean)
            .join("\n---\n")
          return (
            <Sandbox key={i} className="border-white/10 bg-white/[0.03]">
              <SandboxHeader title="Sandbox execution" state={sandboxStatusToToolState(item.status)} />
              <SandboxContent>
                <SandboxTabs defaultValue="code">
                  <SandboxTabsBar>
                    <SandboxTabsList>
                      <SandboxTabsTrigger value="code">Code</SandboxTabsTrigger>
                      <SandboxTabsTrigger value="output">Output</SandboxTabsTrigger>
                    </SandboxTabsList>
                  </SandboxTabsBar>
                  <SandboxTabContent value="code">
                    <CodeBlock code={item.code ?? ""} language={item.language ?? "python"} />
                  </SandboxTabContent>
                  <SandboxTabContent value="output">
                    <CodeBlock code={output} language="log" />
                  </SandboxTabContent>
                </SandboxTabs>
              </SandboxContent>
            </Sandbox>
          )
        })}

        {images.map(({ part, json: imgJson }) => {
          const file = new DefaultGeneratedFile({
            data: imgJson.binary_content_base64,
            mediaType: imgJson.content_type,
          })
          return (
            <Image
              key={part.toolCallId}
              base64={file.base64}
              uint8Array={file.uint8Array}
              mediaType={file.mediaType}
              alt="File produced by the sub-agent"
            />
          )
        })}

        {chain.create.state === "output-error" && (
          <p className="text-destructive text-xs">{chain.create.errorText}</p>
        )}
        {chain.polls.some((p) => p.state === "output-error") && (
          <p className="text-destructive text-xs">
            {chain.polls.find((p) => p.state === "output-error")?.errorText}
          </p>
        )}

        <Task className="border-white/10 bg-white/[0.02]">
          <TaskTrigger title={`${1 + chain.polls.length + chain.downloads.length} call(s)`} />
          <TaskContent>
            {[chain.create, ...chain.polls, ...chain.downloads].map((part) => (
              <GenericTool key={part.toolCallId} part={part} className="bg-white/[0.03]" />
            ))}
          </TaskContent>
        </Task>
      </AgentContent>
    </Agent>
  )
}




function ListFilesArtifacts({ part }: { part: DynamicToolUIPart }) {
  if (part.state !== "output-available") return null
  const parsed = partOutputJson(part)
  const files = parsed?.data as { filename?: string; bytes?: number }[] | undefined
  if (!files?.length) return null

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {files.map((file, i) => (
        <Artifact
          key={`${file.filename}-${i}`}
          className="border-white/10 bg-white/[0.02] backdrop-blur-sm"
        >
          <ArtifactHeader>
            <div className="flex items-center gap-2">
              <FileIcon className="size-4 text-muted-foreground" />
              <ArtifactTitle>{file.filename ?? "file"}</ArtifactTitle>
            </div>
          </ArtifactHeader>
          <ArtifactContent>
            <ArtifactDescription>
              {typeof file.bytes === "number" ? `${file.bytes.toLocaleString()} bytes` : ""}
            </ArtifactDescription>
          </ArtifactContent>
        </Artifact>
      ))}
    </div>
  )
}

function GenericTool({ part, className }: { part: DynamicToolUIPart; className?: string }) {
  const input = partInput(part)
  return (
    <Tool className={cn("border-white/10 backdrop-blur-sm", className ?? "bg-white/[0.02]")}>
      <ToolHeader state={part.state} type="dynamic-tool" toolName={part.toolName} />
      <ToolContent>
        {input !== undefined && <ToolInput input={input} />}
        {(part.state === "output-available" || part.state === "output-error") && (
          <ToolOutput
            errorText={part.state === "output-error" ? part.errorText : undefined}
            output={"output" in part ? part.output : undefined}
          />
        )}
        {part.toolName === "list_agent_response_files" && <ListFilesArtifacts part={part} />}
      </ToolContent>
    </Tool>
  )
}

export function AgentActivity({
  parts,
  isThinking,
}: {
  parts: UIMessage["parts"]
  isThinking: boolean
}) {
  const reasoningParts = parts.filter((p): p is ReasoningUIPart => p.type === "reasoning")
  // The think tool gets its own step rather than a generic tool card: its
  // cycles already stream as data-thinking-cycle parts below, so a full
  // ToolOutput would repeat every cycle verbatim. It is NOT hidden, though —
  // it used to be filtered out entirely, which meant a think call that failed
  // (a real, live case: KeyError 'model_id') showed the user nothing at all.
  const thinkParts = parts.filter(
    (p): p is DynamicToolUIPart => p.type === "dynamic-tool" && p.toolName === "think"
  )
  const toolParts = parts.filter(
    (p): p is DynamicToolUIPart => p.type === "dynamic-tool" && p.toolName !== "think"
  )
  const reasoningText = reasoningParts.map((p) => p.text).join("")
  // Discrete, labeled steps (each thinking-tool cycle) — rendered as their
  // own ChainOfThoughtSteps, per AI Elements' own documented guidance:
  // "If your model outputs discrete, labeled steps... use Chain of
  // Thought instead" of the single-block Reasoning component. Kept
  // entirely separate from reasoningText above (the model's own native
  // reasoningContent, e.g. skill_loaded) — two different real sources,
  // never merged into one blob.
  const thinkingCycles = parts.filter(
    (p): p is { type: `data-thinking-cycle`; id?: string; data: { text: string } } =>
      p.type === "data-thinking-cycle"
  )
  const graphEvents = parts.filter(
    (p): p is GraphEventPart => p.type === "data-graph-event"
  )


  if (
    !reasoningText &&
    thinkingCycles.length === 0 &&
    graphEvents.length === 0 &&
    toolParts.length === 0 &&
    thinkParts.length === 0
  ) {
    return null
  }

  const { chains, standalone } = groupToolParts(toolParts)

  return (
    <ChainOfThought
      defaultOpen={false}
      className="rounded-xl border border-white/10 bg-white/[0.02] p-4 backdrop-blur-md"
    >
      <ChainOfThoughtHeader>{isThinking ? "Thinking…" : "Chain of Thought"}</ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {/* The think call itself — what it was asked to reason about, how
            deeply, and whether it succeeded. Its cycles follow as their own
            steps. */}
        {thinkParts.map((part) => {
          const input = partInput(part) as
            | { thought?: string; cycle_count?: number }
            | undefined
          const failed = part.state === "output-error"
          const cycles = input?.cycle_count
          return (
            <ChainOfThoughtStep
              key={part.toolCallId}
              icon={BrainIcon}
              label={`think${cycles ? ` · ${cycles} cycle${cycles === 1 ? "" : "s"}` : ""}`}
              description={input?.thought}
              status={
                failed || part.state === "output-available" ? "complete" : "active"
              }
            >
              {failed && (
                <p className="text-destructive text-xs">{part.errorText}</p>
              )}
            </ChainOfThoughtStep>
          )
        })}

        {thinkingCycles.map((cycle, i) => (
          <ChainOfThoughtStep
            key={cycle.id ?? `thinking-${i}`}
            icon={BrainIcon}
            label="Thinking"
            description={<MessageResponse>{cycle.data.text}</MessageResponse>}
            status={isThinking && i === thinkingCycles.length - 1 ? "active" : "complete"}
          />
        ))}

        {reasoningText && (
          <ChainOfThoughtStep
            icon={BrainIcon}
            label="Reasoning"
            description={<MessageResponse>{reasoningText}</MessageResponse>}
            status={isThinking && toolParts.length === 0 ? "active" : "complete"}
          />
        )}

        {graphEvents.map((part, i) => {
          const node = part.data.node_path.at(-1)
          return (
            <ChainOfThoughtStep
              key={part.id ?? `${part.data.run_id}-${i}`}
              icon={GitBranchIcon}
              label={node?.id ?? part.data.formation_kind}
              description={`${part.data.event_type} · ${node?.kind ?? part.data.node_kind}`}
              status={graphEventStatus(part.data.event_type)}
            />
          )
        })}


        {chains.map((chain) => {
          const latestState = chain.polls.at(-1)?.state ?? chain.create.state
          const done = latestState === "output-error" || (latestJson(chain)?.status === "completed")
          return (
            <ChainOfThoughtStep
              key={chain.key}
              icon={WrenchIcon}
              label="create_agent_response"
              status={done ? "complete" : "active"}
            >
              <AgentChainCard chain={chain} />
            </ChainOfThoughtStep>
          )
        })}

        {standalone.map((part) => (
          <ChainOfThoughtStep
            key={part.toolCallId}
            icon={WrenchIcon}
            label={part.toolName}
            status={part.state === "output-available" || part.state === "output-error" ? "complete" : "active"}
          >
            <GenericTool part={part} />
          </ChainOfThoughtStep>
        ))}
      </ChainOfThoughtContent>
    </ChainOfThought>
  )
}
