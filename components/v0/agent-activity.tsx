"use client"

import NextImage from "next/image"

import {
  isDynamicToolUIPart,
  isReasoningUIPart,
  type DynamicToolUIPart,
  type UIMessage,
} from "ai"
import { type ReactNode, createElement, useEffect, useState } from "react"
import type { BundledLanguage } from "shiki"
import {
  BotIcon,
  BrainIcon,
  ChevronDownIcon,
  ClockIcon,
  CodeIcon,
  CreditCardIcon,
  DownloadIcon,
  FileIcon,
  FileEditIcon,
  FilePlusIcon,
  FileSearchIcon,
  FileTextIcon,
  FilesIcon,
  FolderSearchIcon,
  GlobeIcon,
  ImageIcon,
  LinkIcon,
  ListIcon,
  Loader2Icon,
  MapPinIcon,
  MonitorIcon,
  PackageIcon,
  SearchIcon,
  TerminalSquareIcon,
  UsersIcon,
  WrenchIcon,
} from "lucide-react"

import { Image } from "@/components/ai-elements/image"
import { MessageResponse } from "@/components/ai-elements/message"
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
  ChainOfThoughtImage,
  ChainOfThoughtSearchResult,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought"
import { CodeBlock } from "@/components/ai-elements/code-block"
import {
  FileTree,
  FileTreeFile,
} from "@/components/ai-elements/file-tree"
import {
  JSXPreview,
  JSXPreviewContent,
  JSXPreviewError,
} from "@/components/ai-elements/jsx-preview"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Terminal,
  TerminalContent,
} from "@/components/ai-elements/terminal"
import {
  WebPreview,
  WebPreviewBody,
  WebPreviewNavigation,
  WebPreviewUrl,
} from "@/components/ai-elements/web-preview"
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
import {
  Task,
  TaskContent,
  TaskItem,
  TaskItemFile,
  TaskTrigger,
} from "@/components/ai-elements/task"
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"

// Pure grouping/binding/projection helpers live in agent-run.ts so the
// scoped Vitest suite covers them without a DOM test stack.
import {
  type AgentChain,
  type AgentRunSnapshot,
  type DownloadFileMeta,
  bindRunsToChains,
  downloadFileMeta,
  groupToolParts,
  isTerminalRunStatus,
  partInput,
  partOutputJson,
  projectRunTimeline,
} from "./agent-run"
import {
  type DataObservation,
  inferMcpServer,
  parseDataObservation,
} from "./data-observation"
import { DataObservationPanel } from "./data-observation-panel"
import {
  COMPUTER_USE_TOOL_NAMES,
  computerUseFields,
} from "./computer-use"

// Native Agent API tool payloads, forwarded verbatim by the orchestrator and
// emitted as `data-native-tool` parts by app/api/orchestrator/route.ts. Field
// names are the Agent API's own (docs.perplexity.ai, POST /v1/agent).
type SearchResult = {
  id: number
  url: string
  title: string
  snippet: string
  date?: string | null
}
type UrlContent = { url: string; title: string; snippet: string }

type NativeTool =
  | { type: "response.reasoning.search_queries"; queries: string[] }
  | { type: "response.reasoning.search_results"; results: SearchResult[] }
  | { type: "response.reasoning.fetch_url_queries"; urls: string[] }
  | { type: "response.reasoning.fetch_url_results"; contents: UrlContent[] }
  | {
      type: "response.reasoning.finance_search_queries"
      tickers?: string[]
      categories?: string[]
    }
  | {
      type: "response.reasoning.finance_search_results"
      results: Array<{ category: string; content: string; sources?: string[] }>
    }
  | { type: "response.reasoning.started" | "response.reasoning.stopped"; thought?: string | null }
  | { type: "response.skill.loaded" | "skill_loaded"; name: string }
  | { type: "search_results"; queries?: string[]; results: SearchResult[] }
  | { type: "people_search_results"; queries?: string[]; results: SearchResult[] }
  | {
      type: "finance_results"
      tickers?: string[]
      categories?: string[]
      results: Array<{ category: string; content: string; sources?: string[] }>
    }
  | { type: "fetch_url_results"; contents: UrlContent[] }
  | {
      type: "sandbox_results"
      call_id: string
      language: "python" | "bash"
      code: string
      status: "completed" | "timed_out" | "failed" | "in_progress"
      results: Array<{ stdout: string; stderr: string; exit_code: number; duration_ms: number }>
    }
  // Sandbox file-operation items (SDK output_item.py): per-invocation results
  // of the glob/grep/read/write/edit/apply_patch tools inside the sandbox.
  | {
      type: "sandbox_glob" | "sandbox_grep"
      call_id: string
      count?: number | null
      files?: string[] | null
      truncated?: boolean | null
      error?: string | null
    }
  | {
      type: "sandbox_read_file"
      call_id: string
      file_path: string
      content?: string | null
      start_line?: number | null
      total_lines?: number | null
      error?: string | null
    }
  | {
      type: "sandbox_write_file"
      call_id: string
      file_path: string
      size_bytes?: number | null
      error?: string | null
    }
  | {
      type: "sandbox_edit_file"
      call_id: string
      file_path?: string | null
      message?: string | null
      error?: string | null
    }
  | {
      type: "sandbox_apply_patch"
      call_id: string
      added?: string[] | null
      deleted?: string[] | null
      modified?: string[] | null
      error?: string | null
    }
  | {
      type: "mcp_list_tools"
      server_label: string
      tools: Array<{ name: string; description?: string | null }>
      error?: string | null
    }
  | {
      type: "mcp_call"
      server_label: string
      name: string
      arguments: string
      output?: string | null
      error?: string | null
    }
    | {
      type: "share_file"
      filename?: string | null
      file_id?: string | null
      url?: string | null
      error?: string | null
    }
  | {
      type: "google_maps"
      google_maps_widget_context_token?: string | null
      places?: Array<{ title?: string; uri?: string; placeId?: string }>
    }
  | {
      type: "google_search"
      queries?: string[]
      results?: Array<{ title?: string; uri?: string }>
      images?: Array<{ title?: string; image_uri: string; source_uri?: string }>
    }

type NativeToolPart = { type: "data-native-tool"; id?: string; data: NativeTool }

// Official Place Contextual element only. JSX Preview is the map widget;
// place photos and sources use Chain of Thought, not this parser.
// https://developers.google.com/maps/documentation/javascript/reference/places-widget#PlaceContextualElement
const GMP_MAP = {
  "gmp-place-contextual": (props: Record<string, unknown>) =>
    createElement("gmp-place-contextual", props),
}

// SandboxResultsOutputItem.status is its own real enum ("in_progress" |
// "completed" | "failed" | "timed_out" — verified against the installed
// perplexity SDK's output_item.py), distinct from ToolUIPart["state"]
// (which SandboxHeader actually requires). Mapping rather than hardcoding
// one state regardless of what really happened.

// One reconciled data-agent-run part per nested preset run, emitted by
// app/api/orchestrator/route.ts from the backend's agent_runs topic.
type AgentRunPart = { type: "data-agent-run"; id?: string; data: AgentRunSnapshot }

// The nested run's live status while its tool result hasn't landed yet.
function chainStatus(chain: AgentChain, run: AgentRunSnapshot | undefined): string {
  const json = partOutputJson(chain.create)
  const terminalStatus = json?.status
  if (typeof terminalStatus === "string") return terminalStatus
  if (chain.create.state === "output-error") return "failed"
  if (run) return run.status
  return "queued"
}

/**
 * One preset sub-agent run: the create tool call, its live agent_runs
 * snapshot, and the retrieve/list/download calls bound to it by response id.
 *
 * Composition is the native AI Elements chain the plan requires: the caller
 * renders this inside an outer ChainOfThoughtStep; here it is
 * Agent > AgentHeader + AgentContent, a Task for the run, and a nested
 * ChainOfThought inside TaskContent built from the same ChainOfThought*
 * subcomponents (and NativeToolStep renderers) the outer chain uses.
 */
function AgentChainCard({
  chain,
  run,
}: {
  chain: AgentChain
  run: AgentRunSnapshot | undefined
}) {
  const input = partInput(chain.create) as
    | {
        instructions?: string
        input?: string
        task?: string
        model?: string
        models?: string[]
      }
    | undefined
  const json = partOutputJson(chain.create)
  const status = chainStatus(chain, run)
  const running = !isTerminalRunStatus(status) && chain.create.state !== "output-error"
  // The actually-selected model from the run/response wins over the
  // requested override; the preset default is the last resort.
  const modelLabel =
    run?.model ||
    (typeof json?.model === "string" ? json.model : undefined) ||
    input?.model ||
    input?.models?.[0] ||
    run?.preset ||
    "preset default"
  // Streamed final markdown: the live snapshot while streaming, the terminal
  // tool result's output text once it lands. Rendered ONCE here (the caller's
  // own answer keeps its existing MessageResponse; this never feeds it).
  const finalText =
    run?.text ||
    (typeof json?.output_text === "string" ? json.output_text : "")
  const errorText =
    chain.create.state === "output-error"
      ? chain.create.errorText
      : (run?.error ??
        (typeof (json?.error as { message?: string } | null | undefined)
          ?.message === "string"
          ? (json?.error as { message: string }).message
          : undefined))

  // Files delivered by download_agent_response_file: bounded metadata plus a
  // browser-safe /api/orchestrator/file proxy URL — never base64 bytes.
  const downloads = chain.downloads
    .map((d) => ({ part: d, meta: downloadFileMeta(partOutputJson(d)) }))
    .filter(
      (d): d is { part: DynamicToolUIPart; meta: DownloadFileMeta } =>
        d.meta !== null
    )

  const runLabel = run?.preset ? `Sub-agent · ${run.preset}` : "Sub-agent"

  return (
    <Agent className="app-glass app-glass-edge">
      <AgentHeader model={modelLabel} name={`${runLabel} · ${status}`} />
      {/* Bounded: the nested timeline/output scrolls inside the card; page
          scrolling stays with the outer native Conversation. */}
      <AgentContent className="max-h-[28rem] overflow-y-auto">
        <AgentInstructions>
          {input?.instructions ?? input?.input ?? input?.task ?? "…"}
        </AgentInstructions>

        {run && <RunTask run={run} running={running} />}

        {/* The run's accumulated final output, streaming while live. It also
            reaches the caller as this preset tool's toolResult; it is never
            injected into the outer answer stream. */}
        {finalText && <MessageResponse>{finalText}</MessageResponse>}

        {downloads.map(({ part, meta }) =>
          meta.url && meta.contentType?.startsWith("image/") ? (
            <ChainOfThoughtImage key={part.toolCallId} caption={meta.filename}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={meta.url}
                alt={meta.filename}
                className="h-auto max-w-full"
              />
            </ChainOfThoughtImage>
          ) : (
            <ShareFileArtifact
              key={part.toolCallId}
              name={meta.filename}
              url={meta.url}
            />
          )
        )}

        {errorText && <p className="text-destructive text-xs">{errorText}</p>}
        {chain.polls.some((p) => p.state === "output-error") && (
          <p className="text-destructive text-xs">
            {chain.polls.find((p) => p.state === "output-error")?.errorText}
          </p>
        )}

        {(chain.polls.length > 0 || chain.downloads.length > 0) && (
          <Task className="app-glass app-glass-edge" defaultOpen={false}>
            <TaskTrigger
              title={`${chain.polls.length + chain.downloads.length} lifecycle call(s)`}
            >
              <div className="flex w-full cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground">
                {stepIcon("download")}
                <p className="text-sm">{`${chain.polls.length + chain.downloads.length} lifecycle call(s)`}</p>
                <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
              </div>
            </TaskTrigger>
            <TaskContent>
              {[...chain.polls, ...chain.downloads].map((part) => (
                <GenericTool
                  key={part.toolCallId}
                  part={part}
                  className="bg-white/[0.03]"
                />
              ))}
            </TaskContent>
          </Task>
        )}
      </AgentContent>
    </Agent>
  )
}

// The run's live timeline: a Task whose TaskContent holds a nested
// ChainOfThought built from the same native ChainOfThought* subcomponents and
// NativeToolStep renderers the outer chain uses — reasoning text, native tool
// calls/results, skill-loaded steps, sandbox/MCP/search/fetch/finance/people
// outputs, and share-file artifacts, in the API's sequence order.
function RunTask({ run, running }: { run: AgentRunSnapshot; running: boolean }) {
  const timeline = projectRunTimeline(run.events)
  return (
    <Task className="app-glass app-glass-edge" defaultOpen>
      <TaskTrigger
        title={`Run ${run.responseId ?? run.activityId} · ${run.events.length} event(s)`}
      >
        <div className="flex w-full cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground">
          {stepIcon("agent", { active: running })}
          <p className="text-sm">{`Run ${run.responseId ?? run.activityId} · ${run.events.length} event(s)`}</p>
          <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
        </div>
      </TaskTrigger>
      <TaskContent>
        <ChainOfThought defaultOpen>
          <ChainOfThoughtHeader>
            {running ? "Working…" : "Sub-agent activity"}
          </ChainOfThoughtHeader>
          <ChainOfThoughtContent>
            {run.attempt > 1 && (
              <ChainOfThoughtStep
                icon={ClockIcon}
                label={`Reconnected · attempt ${run.attempt}`}
                status="complete"
              />
            )}
            {timeline.map((entry) => {
              if (entry.kind === "reasoning") {
                return (
                  <ChainOfThoughtStep
                    icon={BrainIcon}
                    key={entry.key}
                    label="Thinking"
                    status={running ? "active" : "complete"}
                  >
                    <MessageResponse isAnimating={running}>{entry.text}</MessageResponse>
                  </ChainOfThoughtStep>
                )
              }
              return (
                <NativeToolStep
                  key={entry.key}
                  native={entry.native as NativeTool}
                />
              )
            })}
          </ChainOfThoughtContent>
        </ChainOfThought>
      </TaskContent>
    </Task>
  )
}

// A run whose create tool part hasn't arrived or bound yet: same Agent >
// AgentContent > Task composition, rendered from the snapshot alone so the
// stream is visible from the very first frame.
function UnboundRunCard({ run }: { run: AgentRunSnapshot }) {
  const running = !isTerminalRunStatus(run.status)
  return (
    <Agent className="app-glass app-glass-edge">
      <AgentHeader
        model={run.model ?? run.preset}
        name={`Sub-agent · ${run.preset} · ${run.status}`}
      />
      <AgentContent className="max-h-[28rem] overflow-y-auto">
        <RunTask run={run} running={running} />
        {run.text && <MessageResponse>{run.text}</MessageResponse>}
        {run.error && <p className="text-destructive text-xs">{run.error}</p>}
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
          className="app-glass app-glass-edge"
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

// Results from every search-shaped native tool render through the Chain of
// Thought search-result subcomponents, which is what they are for.
function ResultBadges({ items }: { items: Array<{ title: string; url?: string }> }) {
  if (items.length === 0) return null
  return (
    <ChainOfThoughtSearchResults>
      {items.map((item, i) => (
        // `render` is Badge's own base-ui composition prop: the badge BECOMES
        // the anchor instead of being wrapped in one. That also lets
        // badgeVariants' `[a]:hover:bg-secondary/80` actually apply, which it
        // never could while the anchor was the parent element.
        <ChainOfThoughtSearchResult
          key={item.url ? `${item.url}-${i}` : i}
          render={
            item.url ? (
              <a href={item.url} rel="noreferrer" target="_blank" />
            ) : undefined
          }
        >
          {item.title}
        </ChainOfThoughtSearchResult>
      ))}
    </ChainOfThoughtSearchResults>
  )
}

// Dynamic iconography: every TaskTrigger and ChainOfThoughtStep gets the icon
// of the step being done, not the default search glyph. `active` spins the
// loader; `failed` keeps the step icon but marks the row destructive.
export type StepIconKind =
  | "active"
  | "agent"
  | "code"
  | "download"
  | "edit"
  | "fetch"
  | "file"
  | "files"
  | "finance"
  | "folder"
  | "globe"
  | "image"
  | "maps"
  | "monitor"
  | "patch"
  | "people"
  | "read"
  | "retry"
  | "search"
  | "skill"
  | "terminal"
  | "think"
  | "write"

export function stepIcon(
  kind: StepIconKind,
  opts?: { active?: boolean; failed?: boolean }
) {
  const cls = cn("size-4", opts?.failed && "text-destructive")
  if (opts?.active) {
    return <Loader2Icon className={cn(cls, "animate-spin")} />
  }
  switch (kind) {
    case "agent":
      return <BotIcon className={cls} />
    case "code":
      return <CodeIcon className={cls} />
    case "download":
      return <DownloadIcon className={cls} />
    case "edit":
      return <FileEditIcon className={cls} />
    case "fetch":
      return <LinkIcon className={cls} />
    case "file":
      return <FileTextIcon className={cls} />
    case "files":
      return <FilesIcon className={cls} />
    case "finance":
      return <CreditCardIcon className={cls} />
    case "folder":
      return <FolderSearchIcon className={cls} />
    case "globe":
      return <GlobeIcon className={cls} />
    case "image":
      return <ImageIcon className={cls} />
    case "maps":
      return <MapPinIcon className={cls} />
    case "monitor":
      return <MonitorIcon className={cls} />
    case "patch":
      return <ListIcon className={cls} />
    case "people":
      return <UsersIcon className={cls} />
    case "read":
      return <FileSearchIcon className={cls} />
    case "retry":
      return <ClockIcon className={cls} />
    case "search":
      return <SearchIcon className={cls} />
    case "skill":
      return <PackageIcon className={cls} />
    case "terminal":
      return <TerminalSquareIcon className={cls} />
    case "think":
      return <BrainIcon className={cls} />
    case "write":
      return <FilePlusIcon className={cls} />
  }
}

// TaskItem renders text exactly as given; the docs' Task pattern parses file
// mentions into TaskItemFile chips with the file's icon. A fresh regex per
// call keeps the match immutable (no shared lastIndex).
function TaskItemBody({ text }: { text: string }) {
  const matches = [...text.matchAll(/[\w.@/-]+\.[a-z0-9]{1,10}/gi)].filter(
    (m) => m[0].includes("/") || m[0].split(".").length === 2
  )
  if (matches.length === 0) return <>{text}</>
  const out: ReactNode[] = []
  let cursor = 0
  matches.forEach((m, i) => {
    const token = m[0]
    const index = m.index ?? 0
    if (index > cursor) out.push(text.slice(cursor, index))
    out.push(
      <TaskItemFile key={`${token}-${i}`}>
        <FileIcon className="size-3.5" />
        <span>{token}</span>
      </TaskItemFile>
    )
    cursor = index + token.length
  })
  if (cursor < text.length) out.push(text.slice(cursor))
  return <>{out}</>
}

// A search call in flight — the queries or URLs the model asked for, before
// any results come back. Task streams the high-level "what's being searched";
// the trigger carries the step's own icon (docs: TaskTrigger children
// override the built-in SearchIcon row).
function CallTask({
  title,
  items = [],
  children,
  icon,
  active = false,
  failed = false,
}: {
  title: string
  items?: string[]
  children?: ReactNode
  icon?: StepIconKind
  active?: boolean
  failed?: boolean
}) {
  const hasBody = items.length > 0 || children != null
  return (
    <Task className="app-glass app-glass-edge">
      <TaskTrigger title={title}>
        <div
          className={cn(
            "flex w-full cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground",
            failed && "text-destructive hover:text-destructive"
          )}
        >
          {stepIcon(icon ?? "search", { active, failed })}
          <p className="text-sm">{title}</p>
          <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
        </div>
      </TaskTrigger>
      {hasBody ? (
        <TaskContent>
          {items.map((item, i) => (
            <TaskItem key={`${item}-${i}`}>
              <TaskItemBody text={item} />
            </TaskItem>
          ))}
          {children}
        </TaskContent>
      ) : null}
    </Task>
  )
}

// The verbatim tool payload behind a collapsed disclosure. Rendered under
// every DataObservationPanel so the projection never becomes the only copy of
// the data — metadata the panel does not chart (schema blocks, reproduce_with
// snippets, resolved filters) stays one click away, in full.
function RawPayload({ output }: { output: unknown }) {
  if (output === undefined || output === null) return null
  const text =
    typeof output === "string" ? output : JSON.stringify(output, null, 2)
  if (!text) return null
  return (
    <details className="ide-glass-inset rounded-lg border px-3 py-1.5">
      <summary className="cursor-pointer select-none text-[11px] text-muted-foreground transition-colors hover:text-foreground">
        Raw payload ({text.length.toLocaleString()} chars)
      </summary>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-[10px] text-foreground/80">
        {text}
      </pre>
    </details>
  )
}

// A completed dynamic tool call that carried a DataCommons / PopHIVE
// observation payload: the dynamic `mcp_client` tool (action call_tool with
// connection_id + tool_name) and direct MCP tools registered under the remote
// tool's own name (get_observations, get_data, …). parseDataObservation is
// strict — anything unrecognized returns null and the call renders through
// the normal GenericTool card, so no output is ever hidden.
function observationFromDynamicTool(
  part: DynamicToolUIPart
): DataObservation | null {
  if (part.state !== "output-available") return null
  const output = "output" in part ? part.output : undefined

  if (part.toolName === "mcp_client") {
    const input = partInput(part)
    if (input?.action !== "call_tool") return null
    const toolName = typeof input.tool_name === "string" ? input.tool_name : ""
    const connectionId =
      typeof input.connection_id === "string" ? input.connection_id : ""
    const server = connectionId || (toolName ? inferMcpServer(toolName) : null)
    if (!server) return null
    return parseDataObservation(server, toolName || part.toolName, output)
  }

  const server = inferMcpServer(part.toolName)
  if (!server) return null
  return parseDataObservation(server, part.toolName, output)
}

function dynamicToolStatus(
  part: DynamicToolUIPart,
  isThinking: boolean
): "complete" | "active" | "pending" {
  if (part.state === "output-error") return "pending"
  if (
    isThinking &&
    (part.state === "input-available" ||
      part.state === "input-streaming" ||
      part.state === "approval-requested")
  ) {
    return "active"
  }
  return "complete"
}

function computerUseStepLabel(part: DynamicToolUIPart): string {
  const { intent, action, url } = computerUseFields(part)
  const label = intent || action.replaceAll("_", " ") || part.toolName
  return url && action === "navigate" ? `${label}: ${url}` : label
}

/** One Task per computer-use run; steps are TaskItems (native task.tsx pattern). */
function ComputerUseTask({
  parts,
  isThinking,
}: {
  parts: DynamicToolUIPart[]
  isThinking: boolean
}) {
  const running = isThinking && parts.some((p) => dynamicToolStatus(p, true) === "active")
  const active = running || isThinking
  const [open, setOpen] = useState(active)
  const [seenActive, setSeenActive] = useState(active)
  if (active !== seenActive) {
    setSeenActive(active)
    if (active) setOpen(true)
  }
  return (
    <Task
      className="app-glass app-glass-edge"
      open={open}
      onOpenChange={setOpen}
    >
      <TaskTrigger title="Computer use">
        <div className="flex w-full cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground">
          {stepIcon("monitor", { active })}
          <p className="text-sm">Computer use</p>
          <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
        </div>
      </TaskTrigger>
      <TaskContent>
        {parts.map((part) => {
          const { screenshot, screenshotUrl } = computerUseFields(part)
          const label = computerUseStepLabel(part)
          const status = part.state === "output-error" ? "Failed"
            : part.state === "output-available" ? "Completed"
            : part.state === "approval-requested" ? "Awaiting approval"
            : "Running"
          return (
            <div key={part.toolCallId} className="space-y-2" data-computer-action={part.toolCallId}>
              <TaskItem>{label} <span className="text-xs text-muted-foreground">{status}</span></TaskItem>
              {screenshotUrl ? (
                <ChainOfThoughtImage caption={label}>
                  <NextImage alt={label} src={screenshotUrl} width={1440} height={900}
                    unoptimized className="h-auto max-w-full rounded-md" />
                </ChainOfThoughtImage>
              ) : screenshot ? (
                <ChainOfThoughtImage caption={label}>
                  <Image
                    alt={label}
                    base64={screenshot.base64}
                    mediaType={screenshot.mediaType}
                    uint8Array={new Uint8Array()}
                  />
                </ChainOfThoughtImage>
              ) : null}
              {part.state === "output-error" ? (
                <p className="text-destructive text-xs">{part.errorText}</p>
              ) : null}
            </div>
          )
        })}
      </TaskContent>
    </Task>
  )
}

type ActivitySegment =
  | { kind: "single"; part: UIMessage["parts"][number]; index: number }
  | { kind: "computer-use"; parts: DynamicToolUIPart[] }

function buildActivitySegments(parts: UIMessage["parts"]): ActivitySegment[] {
  const segments: ActivitySegment[] = []
  let i = 0
  while (i < parts.length) {
    const part = parts[i]
    if (
      isDynamicToolUIPart(part) &&
      part.toolName !== "think" &&
      COMPUTER_USE_TOOL_NAMES.has(part.toolName)
    ) {
      const group: DynamicToolUIPart[] = []
      while (i < parts.length) {
        const current = parts[i]
        if (
          !isDynamicToolUIPart(current) ||
          !COMPUTER_USE_TOOL_NAMES.has(current.toolName)
        ) {
          break
        }
        group.push(current)
        i++
      }
      segments.push({ kind: "computer-use", parts: group })
      continue
    }
    segments.push({ kind: "single", part, index: i })
    i++
  }
  return segments
}

// Search results — every search-shaped native renders as the Chain of Thought
// search subcomponents nested inside a Task, and nothing else: no Tool cards,
// no Sandbox, no step chrome around them.
function SearchResultsTask({
  title,
  items,
  children,
  icon = "search",
  active = false,
}: {
  title: string
  items: Array<{ title: string; url?: string }>
  children?: ReactNode
  icon?: StepIconKind
  active?: boolean
}) {
  return (
    <Task className="app-glass app-glass-edge">
      <TaskTrigger title={title}>
        <div className="flex w-full cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground">
          {stepIcon(icon, { active })}
          <p className="text-sm">{title}</p>
          <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
        </div>
      </TaskTrigger>
      <TaskContent>
        <ResultBadges items={items} />
        {children}
      </TaskContent>
    </Task>
  )
}

// pplx CLI search invocations inside sandbox bash: `pplx search web "query"`,
// also people/finance/url. These are web searches, not shell work.
const PPLX_SEARCH = /pplx\s+search\s+(\w+)?\s*["']([^"']+)["']/

// urls-with-titles printed to stdout (JSON, jq output, dict reprs) — the
// links a sandboxed search surfaced.
const URL_WITH_TITLE =
  /["']?url["']?\s*[:=]\s*["']([^"']+)["'][^}\n]*?["']?title["']?\s*[:=]\s*["']([^"']+)["']/g

function extractLinks(stdout: string): Array<{ title: string; url: string }> {
  const seen = new Set<string>()
  const links: Array<{ title: string; url: string }> = []
  URL_WITH_TITLE.lastIndex = 0
  for (const m of stdout.matchAll(URL_WITH_TITLE)) {
    const [, url, title] = m
    if (seen.has(url)) continue
    seen.add(url)
    links.push({ title, url })
  }
  return links
}

// `status` reports whether the CONTAINER ran, not whether the code worked: a
// script that raises still comes back "completed" with a non-zero exit_code.
// Both are needed, or the header claims success above a stack trace.
function sandboxStateFromStatus(
  status: string,
  failed: boolean
): DynamicToolUIPart["state"] {
  if (failed) return "output-error"
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

/**
 * A Sandbox whose open state follows the run, and stays clickable.
 *
 * The block re-renders while the sandbox streams and `failed` only flips once
 * a non-zero exit_code arrives, so `defaultOpen` is read before the answer is
 * known — Base UI then warns that an uncontrolled Collapsible's default
 * changed after init. Controlling `open` fixes that, but a bare `open` with no
 * handler freezes the disclosure. Local state seeded from `failed` and synced
 * when it changes gives both: correct default, still user-toggleable.
 */
function SandboxPanel({
  failed,
  className,
  children,
}: {
  failed: boolean
  className?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(!failed)
  const [seenFailed, setSeenFailed] = useState(failed)
  if (failed !== seenFailed) {
    setSeenFailed(failed)
    setOpen(!failed)
  }
  return (
    <Sandbox className={className} onOpenChange={setOpen} open={open}>
      {children}
    </Sandbox>
  )
}

// Values are shiki BundledLanguage ids — CodeBlock's language prop is typed
// against that union, so this map narrows instead of widening to string.
const CODE_EXTENSIONS: Record<string, BundledLanguage> = {
  css: "css",
  html: "html",
  js: "javascript",
  json: "json",
  jsx: "jsx",
  md: "markdown",
  py: "python",
  sh: "bash",
  ts: "typescript",
  tsx: "tsx",
}

// Office documents and HTML pages the browser can render inline.
const PREVIEWABLE = /\.(html?|pdf)$/i

/**
 * Non-image files shared out of the sandbox.
 *
 * Coding projects get the Artifact IDE: a FileTree naming the file, the
 * fetched source streaming into a CodeBlock, and — for html — a live
 * WebPreview of the page itself. Office docs (pdf/html) render through
 * WebPreview inside the Artifact. Anything else keeps the download card.
 */
function ShareFileArtifact({ name, url }: { name: string; url: string | null }) {
  const ext = name.split(".").pop()?.toLowerCase() ?? ""
  const codeLanguage = CODE_EXTENSIONS[ext]
  const [source, setSource] = useState<string | null>(null)

  useEffect(() => {
    if (!url || !codeLanguage) return
    let cancelled = false
    fetch(url)
      .then((res) => (res.ok ? res.text() : null))
      .then((text) => {
        if (!cancelled && text !== null) setSource(text)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [url, codeLanguage])

  const preview = url && PREVIEWABLE.test(name)

  return (
    <Artifact className="app-glass app-glass-edge">
      <ArtifactHeader>
        <div className="flex items-center gap-2">
          <FileIcon className="size-4 text-muted-foreground" />
          <ArtifactTitle>{name}</ArtifactTitle>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="space-y-3">
        {codeLanguage && (
          <>
            <FileTree
              className="app-glass app-glass-edge"
              selectedPath={name}
            >
              <FileTreeFile name={name} path={name} />
            </FileTree>
            {source !== null && (
              <CodeBlock code={source} language={codeLanguage} />
            )}
          </>
        )}
        {preview && (
          // Documented composition (ai-sdk.dev/elements/components/web-preview):
          // WebPreview defaultUrl + Navigation>Url + Body src.
          <WebPreview defaultUrl={url} className="app-glass app-glass-edge h-80">
            <WebPreviewNavigation>
              <WebPreviewUrl />
            </WebPreviewNavigation>
            <WebPreviewBody src={url} />
          </WebPreview>
        )}
        <ArtifactDescription>
          {url ? (
            <a href={url} className="underline" target="_blank" rel="noreferrer">
              Download
            </a>
          ) : (
            "Produced in the sandbox"
          )}
        </ArtifactDescription>
      </ArtifactContent>
    </Artifact>
  )
}

function GroundingImages({
  images,
}: {
  images: Array<{ title?: string; image_uri: string; source_uri?: string }>
}) {
  if (images.length === 0) return null
  return (
    <div className="grid w-full grid-cols-2 gap-2 min-[28rem]:grid-cols-3">
      {images.map((image, i) => (
        <SourcePhotoTile
          key={`${image.image_uri}-${i}`}
          src={image.image_uri}
          title={image.title || "Search image"}
          url={image.source_uri}
        />
      ))}
    </div>
  )
}

function SourcePhotoTile({
  src,
  title,
  url,
  website,
  address,
  rating,
  reviews,
  credit,
}: {
  src: string
  title: string
  url?: string
  website?: string
  address?: string
  rating?: number
  reviews?: number
  credit?: string
}) {
  const [open, setOpen] = useState(false)
  const meta = [
    address,
    typeof rating === "number"
      ? `${rating.toFixed(1)}${typeof reviews === "number" ? ` · ${reviews.toLocaleString()} reviews` : ""}`
      : undefined,
    credit ? `Photo: ${credit}` : undefined,
  ].filter(Boolean) as string[]

  return (
    <>
      <HoverCard open={open ? false : undefined}>
        <HoverCardTrigger
          render={
            <button
              type="button"
              className="group relative aspect-[4/3] w-full overflow-hidden rounded-xl bg-muted ring-1 ring-white/10"
              onClick={() => setOpen(true)}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={src}
                alt={title}
                className="size-full object-cover transition duration-300 ease-out group-hover:scale-[1.08] group-hover:brightness-110"
              />
              <span className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/80 via-black/10 to-transparent opacity-80 transition duration-300 group-hover:opacity-100" />
              <span className="pointer-events-none absolute inset-x-0 bottom-0 p-2.5 text-left font-medium text-white text-xs leading-snug drop-shadow-sm">
                {title}
              </span>
            </button>
          }
        />
        <HoverCardContent className="w-80 overflow-hidden p-0" side="top">
          <Card className="gap-0 py-0 ring-0" size="sm">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={title} className="aspect-video w-full object-cover" />
            <CardHeader className="px-3 pt-3">
              <CardTitle className="text-sm">{title}</CardTitle>
              {meta.length > 0 ? (
                <CardDescription className="space-y-0.5">
                  {meta.map((line) => (
                    <span key={line} className="block">
                      {line}
                    </span>
                  ))}
                </CardDescription>
              ) : null}
            </CardHeader>
            {url || website ? (
              <CardContent className="space-y-1 px-3 pb-3">
                {url ? (
                  <a
                    href={url}
                    rel="noreferrer"
                    target="_blank"
                    className="block truncate text-xs underline"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {url.replace(/^https?:\/\//, "").replace(/\/$/, "")}
                  </a>
                ) : null}
                {website && website !== url ? (
                  <a
                    href={website}
                    rel="noreferrer"
                    target="_blank"
                    className="block truncate text-xs underline"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
                  </a>
                ) : null}
              </CardContent>
            ) : null}
          </Card>
        </HoverCardContent>
      </HoverCard>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] w-[min(96vw,72rem)] max-w-none overflow-hidden p-0 sm:max-w-none">
          <DialogHeader className="sr-only">
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{address || title}</DialogDescription>
          </DialogHeader>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={title}
            className="max-h-[80vh] w-full bg-black object-contain"
          />
          <div className="flex flex-wrap items-end justify-between gap-2 px-4 py-3">
            <div className="min-w-0">
              <p className="font-medium text-sm">{title}</p>
              {meta.map((line) => (
                <p key={line} className="text-muted-foreground text-xs">
                  {line}
                </p>
              ))}
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              {url ? (
                <a href={url} rel="noreferrer" target="_blank" className="text-xs underline">
                  Open in Maps
                </a>
              ) : null}
              {website && website !== url ? (
                <a href={website} rel="noreferrer" target="_blank" className="text-xs underline">
                  Website
                </a>
              ) : null}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

function placeText(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value
  if (
    value &&
    typeof value === "object" &&
    "text" in value &&
    typeof (value as { text: unknown }).text === "string"
  ) {
    return (value as { text: string }).text
  }
  return undefined
}

// Maps JS Place Photos for grounding placeIds. The model never sees this;
// placeId is already on grounding_chunks[].maps.
// https://developers.google.com/maps/documentation/javascript/place-photos
type MapsPlaceCtor = new (opts: { id: string }) => {
  fetchFields: (opts: { fields: string[] }) => Promise<void>
  displayName?: unknown
  formattedAddress?: string
  googleMapsURI?: string
  websiteURI?: string
  rating?: number
  userRatingCount?: number
  photos?: Array<{
    getURI: (opts?: { maxHeight?: number; maxWidth?: number }) => string
    authorAttributions?: Array<{ displayName?: string }>
  }>
}

async function mapsPlaceLibrary(): Promise<{ Place: MapsPlaceCtor } | null> {
  const deadline = Date.now() + 8000
  while (Date.now() < deadline) {
    const importLibrary = (
      window as unknown as {
        google?: { maps?: { importLibrary?: (name: string) => Promise<unknown> } }
      }
    ).google?.maps?.importLibrary
    if (importLibrary) {
      return (await importLibrary("places")) as { Place: MapsPlaceCtor }
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  return null
}

function MapsPlaceImage({
  placeId,
  title,
  url,
}: {
  placeId: string
  title?: string
  url?: string
}) {
  const [photo, setPhoto] = useState<{
    src: string
    title: string
    url?: string
    website?: string
    address?: string
    rating?: number
    reviews?: number
    credit?: string
  } | null>(null)
  useEffect(() => {
    const id = placeId.replace(/^places\//, "")
    if (!id) return
    let cancelled = false
    void (async () => {
      try {
        const lib = await mapsPlaceLibrary()
        if (!lib || cancelled) return
        const place = new lib.Place({ id })
        await place.fetchFields({
          fields: [
            "photos",
            "displayName",
            "formattedAddress",
            "googleMapsURI",
            "websiteURI",
            "rating",
            "userRatingCount",
          ],
        })
        const shot = place.photos?.[0]
        if (!shot || cancelled) return
        setPhoto({
          src: shot.getURI({ maxHeight: 1600 }),
          title: placeText(place.displayName) || title || "Place",
          url: place.googleMapsURI || url,
          website: place.websiteURI,
          address: place.formattedAddress,
          rating: place.rating,
          reviews: place.userRatingCount,
          credit: shot.authorAttributions?.[0]?.displayName,
        })
      } catch {
        // Maps JS missing or Places photo request failed; leave the badge.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [placeId, title, url])
  if (!photo) {
    return (
      <div className="aspect-[4/3] animate-pulse rounded-xl bg-white/5 ring-1 ring-white/10" />
    )
  }
  return <SourcePhotoTile {...photo} />
}

function MapsPlaceImages({
  places,
}: {
  places: Array<{ title?: string; uri?: string; placeId?: string }>
}) {
  const seen = new Set<string>()
  const unique = places.filter((place) => {
    if (!place.placeId || seen.has(place.placeId)) return false
    seen.add(place.placeId)
    return true
  })
  if (unique.length === 0) return null
  return (
    <div className="grid w-full grid-cols-2 gap-2 min-[28rem]:grid-cols-3">
      {unique.map((place) => (
        <MapsPlaceImage
          key={place.placeId}
          placeId={place.placeId!}
          title={place.title}
          url={place.uri}
        />
      ))}
    </div>
  )
}

function NativeToolStep({ native }: { native: NativeTool }) {
  switch (native.type) {
    case "google_maps": {
      const token = native.google_maps_widget_context_token
      const items = (native.places ?? []).map((place) => ({
        title: place.title || place.uri || "Place",
        url: place.uri,
      }))
      return (
        <SearchResultsTask icon="maps" title="Google Maps" items={items}>
          {token ? (
            <JSXPreview
              className="min-h-80 overflow-hidden p-4"
              components={GMP_MAP}
              jsx={`<gmp-place-contextual context-token=${JSON.stringify(token)}></gmp-place-contextual>`}
            >
              <JSXPreviewContent />
              <JSXPreviewError />
            </JSXPreview>
          ) : null}
          <MapsPlaceImages places={native.places ?? []} />
        </SearchResultsTask>
      )
    }

    case "google_search": {
      const queries = native.queries ?? []
      const results = (native.results ?? []).map((result) => ({
        title: result.title || result.uri || "",
        url: result.uri,
      }))
      return (
        <SearchResultsTask
          icon="search"
          title={
            queries.length === 1
              ? `Google Search · ${queries[0]}`
              : "Google Search"
          }
          items={results}
        >
          <GroundingImages images={native.images ?? []} />
        </SearchResultsTask>
      )
    }

    // Searches (web / url / people / finance) render ONLY as the Chain of
    // Thought search subcomponents nested inside a Task: the Task title
    // streams the high-level "what's being searched", the results render as
    // ChainOfThoughtSearchResults. No Tool cards, no Sandbox, no step chrome.
    case "response.reasoning.search_queries":
      return (
        <CallTask
          active
          icon="search"
          title={`Searching the web · ${native.queries.length} quer${native.queries.length === 1 ? "y" : "ies"}`}
          items={native.queries}
        />
      )

    case "response.reasoning.fetch_url_queries":
      return (
        <CallTask
          active
          icon="fetch"
          title={`Fetching pages · ${native.urls.length} URL${native.urls.length === 1 ? "" : "s"}`}
          items={native.urls}
        />
      )

    // finance_search streams its own reasoning events before the terminal
    // finance_results item, with tickers/categories on the call and the same
    // results array on the response.
    case "response.reasoning.finance_search_queries":
      return (
        <CallTask
          active
          icon="finance"
          title={`Looking up markets · ${(native.categories ?? ["quote"]).join(", ")}`}
          items={native.tickers ?? []}
        />
      )

    case "response.reasoning.finance_search_results":
      return (
        <SearchResultsTask
          icon="finance"
          title="Market data"
          items={native.results.flatMap((r) =>
            (r.sources ?? []).map((url) => ({ title: r.category, url }))
          )}
        >
          {native.results.map((r, i) => (
            <MessageResponse key={i}>{r.content}</MessageResponse>
          ))}
        </SearchResultsTask>
      )

    case "response.reasoning.search_results":
    case "search_results":
      return (
        <SearchResultsTask
          icon="search"
          title="Web results"
          items={native.results.map((r) => ({ title: r.title || r.url, url: r.url }))}
        />
      )

    case "people_search_results":
      return (
        <SearchResultsTask
          icon="people"
          title="People"
          items={native.results.map((r) => ({ title: r.title || r.url, url: r.url }))}
        />
      )

    case "finance_results":
      return (
        <SearchResultsTask
          icon="finance"
          title={native.tickers?.length ? `Finance · ${native.tickers.join(", ")}` : "Finance"}
          items={native.results.flatMap((r) =>
            (r.sources ?? []).map((url) => ({ title: r.category, url }))
          )}
        >
          {native.results.map((r, i) => (
            <MessageResponse key={i}>{r.content}</MessageResponse>
          ))}
        </SearchResultsTask>
      )

    case "response.reasoning.fetch_url_results":
    case "fetch_url_results":
      return (
        <SearchResultsTask
          icon="fetch"
          title="Fetched pages"
          items={native.contents.map((c) => ({ title: c.title || c.url, url: c.url }))}
        />
      )

    case "mcp_list_tools":
      return (
        <CallTask failed={Boolean(native.error)} icon="folder" title={native.server_label}>
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : (
            <ResultBadges items={native.tools.map((t) => ({ title: t.name }))} />
          )}
        </CallTask>
      )

    case "mcp_call": {
      // DataCommons / PopHIVE observation payloads render as the blue-glass
      // data panel; anything unrecognized (other servers, malformed or
      // truncated JSON, unexpected shapes) falls back to the raw output so
      // nothing is ever hidden.
      const observation = native.error
        ? null
        : parseDataObservation(native.server_label, native.name, native.output)
      return (
        <CallTask
          failed={Boolean(native.error)}
          icon="terminal"
          title={`${native.server_label} · ${native.name}`}
        >
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : observation ? (
            <>
              <DataObservationPanel observation={observation} />
              <RawPayload output={native.output} />
            </>
          ) : (
            <TaskItem>
              <TaskItemBody text={native.output ?? native.arguments} />
            </TaskItem>
          )}
        </CallTask>
      )
    }

    case "sandbox_results": {
      const output = native.results
        .map((r) => [r.stdout, r.stderr].filter(Boolean).join("\n"))
        .filter(Boolean)
        .join("\n---\n")
      const failed =
        native.status === "failed" ||
        native.status === "timed_out" ||
        native.results.some((r) => r.exit_code !== 0)

      // The sandbox ships the pplx CLI, so the model searches through bash.
      // A `pplx search` IS a web search: it leads with Task and renders
      // ChainOfThoughtSearchResults, never the Terminal.
      if (native.language === "bash") {
        const search = native.code.match(PPLX_SEARCH)
        if (search) {
          const [, kind, query] = search
          const links = extractLinks(native.results.map((r) => r.stdout).join("\n"))
          return (
            <Task className="app-glass app-glass-edge">
              <TaskTrigger title={`Searching the ${kind || "web"} · ${query}`}>
                <div className="flex w-full cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground">
                  {stepIcon("search", { active: native.status === "in_progress" })}
                  <p className="text-sm">{`Searching the ${kind || "web"} · ${query}`}</p>
                  <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
                </div>
              </TaskTrigger>
              <TaskContent>
                {links.length > 0 ? (
                  <ResultBadges items={links} />
                ) : (
                  <TaskItem>{query}</TaskItem>
                )}
              </TaskContent>
            </Task>
          )
        }
        // ANSI framing per the upstream terminal example: cyan $ prompt,
        // red error tail on a failed exit.
        const exitCode = native.results.find((r) => r.exit_code !== 0)?.exit_code
        const ansi =
          `\u001B[36m$\u001B[0m ${native.code}\n${output}` +
          (failed ? `\n\u001B[31m✗\u001B[0m exit ${exitCode ?? 1}` : "")
        return (
          <CallTask
            active={native.status === "in_progress"}
            failed={failed}
            icon="terminal"
            title="Running commands"
          >
            <Terminal
              className="h-64 rounded-none border-0"
              output={ansi}
              isStreaming={native.status === "in_progress"}
            >
              <TerminalContent className="max-h-full" />
            </Terminal>
          </CallTask>
        )
      }

      // Python code execution is the one thing Sandbox is for.
      return (
        <CallTask
          active={native.status === "in_progress"}
          failed={failed}
          icon="code"
          title="Ran code"
        >
          <SandboxPanel
            failed={failed}
            className="border-white/10 bg-white/[0.03]"
          >
            <SandboxHeader
              title="Sandbox · python"
              state={sandboxStateFromStatus(native.status, failed)}
            />
            <SandboxContent>
              <SandboxTabs defaultValue="code">
                <SandboxTabsBar>
                  <SandboxTabsList>
                    <SandboxTabsTrigger value="code">Code</SandboxTabsTrigger>
                    <SandboxTabsTrigger value="output">Output</SandboxTabsTrigger>
                  </SandboxTabsList>
                </SandboxTabsBar>
                <SandboxTabContent value="code">
                  <CodeBlock code={native.code} language="python" />
                </SandboxTabContent>
                <SandboxTabContent value="output">
                  <CodeBlock code={output || "(no output)"} language="log" />
                </SandboxTabContent>
              </SandboxTabs>
            </SandboxContent>
          </SandboxPanel>
        </CallTask>
      )
    }

    // Files the sandbox produced. Images render immediately through
    // ChainOfThoughtImage; code files get the Artifact IDE (FileTree +
    // CodeBlock + WebPreview for html); office docs and everything else are
    // an Artifact card, with a WebPreview when the browser can render it.
    case "share_file": {
      const name = native.filename ?? "file"
      const isImage = /\.(png|jpe?g|gif|webp|svg)$/i.test(name)
      if (!native.error && isImage && native.url) {
        return (
          <CallTask icon="image" title={name}>
            <ChainOfThoughtImage caption={name}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={native.url} alt={name} className="h-auto max-w-full" />
            </ChainOfThoughtImage>
          </CallTask>
        )
      }
      return (
        <CallTask failed={Boolean(native.error)} icon="file" title={name}>
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : (
            <ShareFileArtifact name={name} url={native.url ?? null} />
          )}
        </CallTask>
      )
    }

    case "sandbox_glob":
    case "sandbox_grep": {
      const kind = native.type === "sandbox_glob" ? "Finding files" : "Searching files"
      const files = native.files ?? []
      return (
        <CallTask
          failed={Boolean(native.error)}
          icon="folder"
          title={`${kind} · ${native.count ?? files.length} match${(native.count ?? files.length) === 1 ? "" : "es"}`}
        >
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : files.length > 0 ? (
            <ResultBadges items={files.map((f) => ({ title: f }))} />
          ) : null}
        </CallTask>
      )
    }

    case "sandbox_read_file":
      return (
        <CallTask failed={Boolean(native.error)} icon="read" title={`Read ${native.file_path}`}>
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : (
            <TaskItem>
              <TaskItemBody text={native.file_path} />
            </TaskItem>
          )}
        </CallTask>
      )

    case "sandbox_write_file":
      return (
        <CallTask failed={Boolean(native.error)} icon="write" title={`Wrote ${native.file_path}`}>
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : (
            <TaskItem>
              <TaskItemBody text={native.file_path} />
            </TaskItem>
          )}
        </CallTask>
      )

    case "sandbox_edit_file":
      return (
        <CallTask
          failed={Boolean(native.error)}
          icon="edit"
          title={`Edited ${native.file_path ?? "file"}`}
        >
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : (
            <TaskItem>
              <TaskItemBody
                text={native.message ?? native.file_path ?? "file"}
              />
            </TaskItem>
          )}
        </CallTask>
      )

    case "sandbox_apply_patch": {
      const touched = [
        ...(native.added ?? []),
        ...(native.modified ?? []),
        ...(native.deleted ?? []),
      ]
      return (
        <CallTask
          failed={Boolean(native.error)}
          icon="patch"
          title={`Applied patch · ${touched.length} file${touched.length === 1 ? "" : "s"}`}
        >
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : touched.length > 0 ? (
            <>
              <ResultBadges items={touched.map((f) => ({ title: f }))} />
              <TaskItem>
                <span className="inline-flex flex-wrap items-center gap-1">
                  {touched.map((f) => (
                    <TaskItemFile key={f}>
                      <FileIcon className="size-3.5" />
                      <span>{f}</span>
                    </TaskItemFile>
                  ))}
                </span>
              </TaskItem>
            </>
          ) : null}
        </CallTask>
      )
    }

    case "response.skill.loaded":
    case "skill_loaded":
      return <CallTask icon="skill" title={`Loaded ${native.name}`} />

    case "response.reasoning.started":
    case "response.reasoning.stopped": {
      const thought = native.thought?.trim()
      if (!thought) return null
      return (
        <ChainOfThoughtStep icon={BrainIcon} label="Thinking" status="complete">
          <MessageResponse>{thought}</MessageResponse>
        </ChainOfThoughtStep>
      )
    }
  }
}

// GenUI: a tool whose string output IS a JSX fragment renders live through
// JSXPreview instead of as escaped text. Deliberately strict — a leading tag
// and a closing angle bracket — so prose or JSON never trips it.
function jsxOutput(part: DynamicToolUIPart): string | null {
  if (part.state !== "output-available") return null
  const out = "output" in part ? part.output : undefined
  if (typeof out !== "string") return null
  const trimmed = out.trim()
  return /^<[A-Za-z][^>]*>/.test(trimmed) && trimmed.endsWith(">") ? trimmed : null
}

function GenericTool({ part, className }: { part: DynamicToolUIPart; className?: string }) {
  const input = partInput(part)
  const jsx = jsxOutput(part)
  const output = "output" in part ? part.output : undefined
  return (
    <Tool className={cn("border-white/10 backdrop-blur-sm", className ?? "bg-white/[0.02]")}>
      <ToolHeader state={part.state} type="dynamic-tool" toolName={part.toolName} />
      <ToolContent>
        {input !== undefined && <ToolInput input={input} />}
        {jsx ? (
          // Documented usage (ai-sdk.dev/elements/components/jsx-preview):
          // JSXPreview wraps JSXPreviewContent + JSXPreviewError; the parser
          // renders the string, the error child surfaces parse failures.
          <JSXPreview
            className="min-h-80 p-4"
            components={GMP_MAP}
            jsx={jsx}
          >
            <JSXPreviewContent />
            <JSXPreviewError />
          </JSXPreview>
        ) : (
          (part.state === "output-available" || part.state === "output-error") && (
            <ToolOutput
              errorText={part.state === "output-error" ? part.errorText : undefined}
              output={output}
            />
          )
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
  const reasoningParts = parts.filter(isReasoningUIPart)
  // The think tool never renders as a generic tool card: the reasoning
  // stage's notes stream as reasoning parts (thought blocks above), so a
  // ToolOutput of its final message would repeat what already streamed.
  // Its part still matters twice below: a failed call renders its error (a
  // real, live case: KeyError 'model_id'), and its presence alone keeps the
  // Chain of Thought block mounted. isDynamicToolUIPart is the SDK's own
  // guard; the arrow keeps the narrowed DynamicToolUIPart type through the
  // additional toolName test.
  const dynamicTools = parts.filter(isDynamicToolUIPart)
  const thinkParts = dynamicTools.filter((p) => p.toolName === "think")
  const toolParts = dynamicTools.filter((p) => p.toolName !== "think")
  const reasoningText = reasoningParts.map((p) => p.text).join("")
  // Native Agent API server-side tools: web/people/finance search, URL fetch,
  // sandbox execution, MCP calls, and shared files.
  const nativeTools = parts.filter(
    (p): p is NativeToolPart => p.type === "data-native-tool"
  )
  // Nested preset sub-agent runs: one reconciled snapshot per run. The last
  // occurrence of each part id is the current snapshot (the AI SDK reconciles
  // data parts by id, so in practice there is one per id already).
  const agentRuns: AgentRunSnapshot[] = []
  {
    const runIndexById = new Map<string, number>()
    for (const part of parts) {
      if (part.type !== "data-agent-run") continue
      const runPart = part as AgentRunPart
      const key = runPart.id ?? runPart.data.activityId
      const existing = runIndexById.get(key)
      if (existing === undefined) {
        runIndexById.set(key, agentRuns.length)
        agentRuns.push(runPart.data)
      } else {
        agentRuns[existing] = runPart.data
      }
    }
  }

  if (
    !reasoningText &&
    toolParts.length === 0 &&
    thinkParts.length === 0 &&
    nativeTools.length === 0 &&
    agentRuns.length === 0
  ) {
    if (!isThinking) return null
    return (
      <ChainOfThought
        defaultOpen
        className="app-glass rounded-xl border p-4"
      >
        <ChainOfThoughtHeader>Thinking…</ChainOfThoughtHeader>
        <ChainOfThoughtContent>
          <ChainOfThoughtStep icon={Loader2Icon} label="Working" status="active" />
        </ChainOfThoughtContent>
      </ChainOfThought>
    )
  }

  const { chains, chainByCreateId, absorbedIds } = groupToolParts(toolParts)
  const runByChainKey = bindRunsToChains(chains, agentRuns)
  // Runs already rendered inside a chain's card; their standalone
  // data-agent-run parts render nothing.
  const boundRuns = new Set(
    [...runByChainKey.values()].map((run) => run.activityId)
  )

  const segments = buildActivitySegments(parts)

  return (
    <ChainOfThought
      // The component's own uncontrolled mode (useControllableState with
      // defaultOpen, chain-of-thought.tsx:50-54): open by default so finished
      // reasoning stays visible, user toggles freely after that. An earlier
      // controlled `open={userOpen ?? isThinking}` auto-collapsed the block
      // at turn end and hid the entire chain of thought behind a 70px stub.
      defaultOpen
      className="app-glass rounded-xl border p-4"
    >
      <ChainOfThoughtHeader>{isThinking ? "Thinking…" : "Chain of Thought"}</ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {segments.map((segment, segmentIndex) => {
          if (segment.kind === "computer-use") {
            return (
              <ComputerUseTask
                key={`computer-use-${segment.parts[0]?.toolCallId ?? segmentIndex}`}
                parts={segment.parts}
                isThinking={isThinking}
              />
            )
          }

          const part = segment.part
          const i = segment.index

          // GWEN-6: route.ts emits one reconciled data-retry part (stable id
          // "retry") when the workflow's model retry policy re-runs a turn.
          if (part.type === "data-retry") {
            const attempt = (part as { data?: { attempt?: number } }).data?.attempt
            return (
              <ChainOfThoughtStep
                icon={ClockIcon}
                key="retry"
                label={`Retrying${typeof attempt === "number" ? ` · attempt ${attempt}` : ""}`}
                status="complete"
              />
            )
          }

          if (part.type === "data-native-tool") {
            const native = (part as NativeToolPart).data
            return (
              <NativeToolStep
                key={(part as NativeToolPart).id ?? `native-${i}`}
                native={native}
              />
            )
          }

          if (part.type === "data-agent-run") {
            const run = (part as AgentRunPart).data
            if (boundRuns.has(run.activityId)) return null
            return (
              <ChainOfThoughtStep
                icon={BotIcon}
                key={`agent-run-${run.activityId}`}
                label={run.activity}
              >
                <UnboundRunCard run={run} />
              </ChainOfThoughtStep>
            )
          }

          if (isReasoningUIPart(part)) {
            if (!part.text) return null
            const streaming = part.state === "streaming"
            return (
              <ChainOfThoughtStep
                icon={BrainIcon}
                key={`reasoning-${i}`}
                label="Thinking"
                status={streaming && isThinking ? "active" : "complete"}
              >
                <MessageResponse isAnimating={streaming && isThinking}>
                  {part.text}
                </MessageResponse>
              </ChainOfThoughtStep>
            )
          }

          if (isDynamicToolUIPart(part)) {
            if (part.toolName === "think") {
              if (part.state === "output-error") {
                return (
                  <ChainOfThoughtStep
                    icon={BrainIcon}
                    key={part.toolCallId}
                    label="Thinking"
                    status="pending"
                  >
                    <p className="text-destructive text-xs">{part.errorText}</p>
                  </ChainOfThoughtStep>
                )
              }
              return null
            }

            const chain = chainByCreateId.get(part.toolCallId)
            if (chain) {
              const run = runByChainKey.get(chain.key)
              return (
                <ChainOfThoughtStep
                  icon={BotIcon}
                  key={chain.key}
                  label={chain.toolName}
                  status={dynamicToolStatus(part, isThinking)}
                >
                  <AgentChainCard chain={chain} run={run} />
                </ChainOfThoughtStep>
              )
            }
            if (absorbedIds.has(part.toolCallId)) return null

            // DataCommons / PopHIVE observations arriving through the dynamic
            // mcp_client tool or a direct MCP tool render as the data panel;
            // everything else keeps the generic Tool card.
            const dynamicObservation = observationFromDynamicTool(part)
            if (dynamicObservation) {
              return (
                <ChainOfThoughtStep
                  icon={TerminalSquareIcon}
                  key={part.toolCallId}
                  label={`${dynamicObservation.server} · ${dynamicObservation.tool}`}
                  status={dynamicToolStatus(part, isThinking)}
                >
                  <DataObservationPanel observation={dynamicObservation} />
                  <RawPayload
                    output={"output" in part ? part.output : undefined}
                  />
                </ChainOfThoughtStep>
              )
            }

            return (
              <ChainOfThoughtStep
                icon={WrenchIcon}
                key={part.toolCallId}
                label={part.toolName}
                status={dynamicToolStatus(part, isThinking)}
              >
                <GenericTool part={part} />
              </ChainOfThoughtStep>
            )
          }

          return null
        })}
      </ChainOfThoughtContent>
    </ChainOfThought>
  )
}
