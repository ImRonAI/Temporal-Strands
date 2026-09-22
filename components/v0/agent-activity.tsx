"use client"

import {
  isDynamicToolUIPart,
  isReasoningUIPart,
  type DynamicToolUIPart,
  type UIMessage,
} from "ai"
import { createElement, Fragment, useEffect, useState } from "react"
import type { BundledLanguage } from "shiki"
import {
  BotIcon,
  BrainIcon,
  ClockIcon,
  CodeIcon,
  CreditCardIcon,
  DownloadIcon,
  FileEditIcon,
  FileIcon,
  FilePlusIcon,
  FileSearchIcon,
  FolderSearchIcon,
  ImageIcon,
  LinkIcon,
  ListIcon,
  MapPinIcon,
  PackageIcon,
  SearchIcon,
  ServerIcon,
  TerminalSquareIcon,
  UsersIcon,
  WrenchIcon,
} from "lucide-react"

import {
  Agent,
  AgentContent,
  AgentHeader,
  AgentInstructions,
} from "@/components/ai-elements/agent"
import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
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
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockCopyButton,
  CodeBlockFilename,
  CodeBlockHeader,
  CodeBlockTitle,
} from "@/components/ai-elements/code-block"
import { FileTree, FileTreeFile } from "@/components/ai-elements/file-tree"
import {
  JSXPreview,
  JSXPreviewContent,
  JSXPreviewError,
} from "@/components/ai-elements/jsx-preview"
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
import {
  Task,
  TaskContent,
  TaskItem,
  TaskItemFile,
  TaskTrigger,
} from "@/components/ai-elements/task"
import {
  Terminal,
  TerminalActions,
  TerminalContent,
  TerminalCopyButton,
  TerminalHeader,
  TerminalStatus,
  TerminalTitle,
} from "@/components/ai-elements/terminal"
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"
import {
  WebPreview,
  WebPreviewBody,
  WebPreviewNavigation,
  WebPreviewUrl,
} from "@/components/ai-elements/web-preview"

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
  parseJson,
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
import { ComputerUseActivity, buildActivitySegments } from "./computer-use-activity"
import { toolPresentation, thinkSummaryWasStreamed } from "./computer-use"
import { SkillAgent } from "./skill-agent"
import type { SkillRunSnapshot } from "./skill-run"

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
      connector_id?: string | null
      server_label: string
      tools: Array<{ name: string; description?: string | null }>
      error?: string | null
    }
  | {
      type: "mcp_call"
      connector_id?: string | null
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

// ---------------------------------------------------------------------------
// Documented compositions. Each helper below is one AI Elements example
// (elements.ai-sdk.dev/components/<name>) applied to this app's data. None
// re-implements a primitive's own row, badge, animation, or scroll behavior.
// ---------------------------------------------------------------------------

// Chain of Thought example: results are ChainOfThoughtSearchResults >
// ChainOfThoughtSearchResult. `render` is the Badge's base-ui composition
// prop, so a result with a URL is the anchor itself.
function SearchResults({ items }: { items: Array<{ title: string; url?: string }> }) {
  if (items.length === 0) return null
  return (
    <ChainOfThoughtSearchResults>
      {items.map((item, i) => (
        <ChainOfThoughtSearchResult
          key={item.url ? `${item.url}-${i}` : `${item.title}-${i}`}
          className="max-w-full truncate border-border bg-white/[0.035] text-muted-foreground shadow-[inset_0_1px_0_0_oklch(0.9_0.04_285/0.05)] [a]:hover:border-blurple-bright/40 [a]:hover:bg-blurple/15 [a]:hover:text-foreground"
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

// Tool example (output-error): a native Agent API item that reports `error`
// is a failed tool call, so it renders with the documented output-error
// header state and ToolOutput errorText.
function NativeToolError({
  toolName,
  input,
  error,
}: {
  toolName: string
  input?: unknown
  error: string
}) {
  return (
    <Tool className="app-glass app-glass-edge overflow-hidden rounded-lg border-destructive/30" defaultOpen>
      <ToolHeader
        type="dynamic-tool"
        toolName={toolName}
        state="output-error"
        className="bg-destructive/[0.06]"
      />
      <ToolContent className="border-t border-destructive/20 bg-black/20">
        {input !== undefined && <ToolInput input={input} />}
        <ToolOutput output={undefined} errorText={error} />
      </ToolContent>
    </Tool>
  )
}

// Task example ("Found project files"): TaskTrigger title + TaskContent of
// TaskItems, where a file mention is text followed by a TaskItemFile chip.
function FileTask({
  title,
  files,
  defaultOpen = true,
}: {
  title: string
  files: Array<{ label?: string; path: string }>
  defaultOpen?: boolean
}) {
  return (
    <Task className="app-glass app-glass-edge rounded-lg border px-3 py-2.5" defaultOpen={defaultOpen}>
      <TaskTrigger title={title} className="w-full text-left" />
      <TaskContent>
        {files.map((file, i) => (
          <TaskItem key={`${file.path}-${i}`}>
            <span className="inline-flex flex-wrap items-center gap-1.5">
              {file.label}
              <TaskItemFile className="max-w-full border-border bg-black/25 font-mono text-[12px] tracking-[0.01em] text-foreground/90 shadow-[inset_0_1px_0_0_oklch(0.9_0.04_285/0.05)]">
                <FileIcon className="size-3.5 text-blurple-bright/80" />
                <span className="truncate">{file.path}</span>
              </TaskItemFile>
            </span>
          </TaskItem>
        ))}
      </TaskContent>
    </Task>
  )
}

// Chain of Thought example: an image step is ChainOfThoughtImage with a
// caption, holding the image element itself. Remote URLs are plain <img>
// because the AI Elements Image component takes a generated base64 payload.
function ImageStep({
  src,
  alt,
  caption,
  href,
}: {
  src: string
  alt: string
  caption?: string
  href?: string
}) {
  const image = (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} className="h-auto max-h-[22rem] max-w-full rounded-md" />
  )
  return (
    <ChainOfThoughtImage caption={caption}>
      {href ? (
        <a href={href} rel="noreferrer" target="_blank">
          {image}
        </a>
      ) : (
        image
      )}
    </ChainOfThoughtImage>
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

function codeLanguageFor(path: string): BundledLanguage | undefined {
  return CODE_EXTENSIONS[path.split(".").pop()?.toLowerCase() ?? ""]
}

// Office documents and HTML pages the browser can render inline.
const PREVIEWABLE = /\.(html?|pdf)$/i
const IMAGE_FILE = /\.(png|jpe?g|gif|webp|svg)$/i


/**
 * A non-image file shared out of the sandbox — the Artifact example:
 * ArtifactHeader (title + description, actions) over ArtifactContent. Source
 * files stream into a CodeBlock (Code Block example: header with filename and
 * copy button); html/pdf render live through WebPreview (Web Preview example:
 * navigation with URL bar over the iframe body).
 */
function ShareFileArtifact({ name, url }: { name: string; url: string | null }) {
  const codeLanguage = codeLanguageFor(name)
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
      <ArtifactHeader className="app-glass-edge bg-black/25">
        <div>
          <ArtifactTitle className="font-mono text-[13px] tracking-[0.01em]">{name}</ArtifactTitle>
          <ArtifactDescription className="text-xs">
            {url ? "Produced in the sandbox" : "Produced in the sandbox · no download URL"}
          </ArtifactDescription>
        </div>
        {url && (
          <ArtifactActions>
            <ArtifactAction
              icon={DownloadIcon}
              label="Download"
              tooltip="Download file"
              onClick={() => window.open(url, "_blank", "noopener,noreferrer")}
            />
          </ArtifactActions>
        )}
      </ArtifactHeader>
      <ArtifactContent className="space-y-3 p-0">
        {codeLanguage && source !== null && (
          <CodeBlock className="border-none" code={source} language={codeLanguage} showLineNumbers>
            <CodeBlockHeader>
              <CodeBlockTitle>
                <FileIcon size={14} />
                <CodeBlockFilename>{name}</CodeBlockFilename>
              </CodeBlockTitle>
              <CodeBlockActions>
                <CodeBlockCopyButton />
              </CodeBlockActions>
            </CodeBlockHeader>
          </CodeBlock>
        )}
        {preview && (
          <WebPreview defaultUrl={url} className="h-80 border-none">
            <WebPreviewNavigation>
              <WebPreviewUrl />
            </WebPreviewNavigation>
            <WebPreviewBody src={url} />
          </WebPreview>
        )}
      </ArtifactContent>
    </Artifact>
  )
}

// list_agent_response_files: one Artifact per file, header-only (title +
// byte-count description), as the Artifact docs describe.
function ListFilesArtifacts({ part }: { part: DynamicToolUIPart }) {
  if (part.state !== "output-available") return null
  const parsed = partOutputJson(part)
  const files = parsed?.data as { filename?: string; bytes?: number }[] | undefined
  if (!files?.length) return null

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {files.map((file, i) => (
        <Artifact key={`${file.filename}-${i}`} className="app-glass app-glass-edge">
          <ArtifactHeader className="app-glass-edge border-b-0 bg-black/25">
            <div className="min-w-0">
              <ArtifactTitle className="truncate font-mono text-[13px] tracking-[0.01em]">{file.filename ?? "file"}</ArtifactTitle>
              <ArtifactDescription className="text-xs tabular-nums">
                {typeof file.bytes === "number" ? `${file.bytes.toLocaleString()} bytes` : "size unknown"}
              </ArtifactDescription>
            </div>
          </ArtifactHeader>
        </Artifact>
      ))}
    </div>
  )
}

// The run's live timeline: a nested ChainOfThought (Chain of Thought example)
// built from the same ChainOfThoughtStep renderers the outer chain uses —
// reasoning text, native tool calls/results, skill-loaded steps, sandbox /
// MCP / search / fetch / finance outputs, and share-file artifacts, in the
// API's sequence order.
function RunChain({ run, running }: { run: AgentRunSnapshot; running: boolean }) {
  const timeline = projectRunTimeline(run.events)
  return (
    <ChainOfThought defaultOpen className="border-l-2 border-l-blurple-bright/30 pl-3">
      <ChainOfThoughtHeader className="text-[13px] font-medium tracking-[0.01em]">
        {running ? "Sub-agent working…" : "Sub-agent activity"}
      </ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {run.attempt > 1 && (
          <ChainOfThoughtStep
            icon={ClockIcon}
            label={`Reconnected · attempt ${run.attempt}`}
            status="complete"
          />
        )}
        {timeline.map((entry) =>
          entry.kind === "reasoning" ? (
            <ChainOfThoughtStep
              icon={BrainIcon}
              key={entry.key}
              label="Thinking"
              status={running ? "active" : "complete"}
            >
              <MessageResponse isAnimating={running}>{entry.text}</MessageResponse>
            </ChainOfThoughtStep>
          ) : (
            <NativeToolStep key={entry.key} native={entry.native as NativeTool} />
          )
        )}
      </ChainOfThoughtContent>
    </ChainOfThought>
  )
}

// Tool example, one per lifecycle call (retrieve / list / download): the
// header carries the SDK state, ToolInput the parameters, ToolOutput the
// result or error.
function LifecycleTool({ part }: { part: DynamicToolUIPart }) {
  return (
    <Tool className="app-glass app-glass-edge overflow-hidden rounded-lg" defaultOpen={false}>
      <ToolHeader type="dynamic-tool" toolName={part.toolName} state={part.state} className="hover:bg-white/[0.02]" />
      <ToolContent className="app-glass-edge border-t bg-black/20">
        <ToolInput input={part.input} />
        {(part.state === "output-available" || part.state === "output-error") && (
          <ToolOutput
            output={"output" in part ? part.output : undefined}
            errorText={part.state === "output-error" ? part.errorText : undefined}
          />
        )}
      </ToolContent>
    </Tool>
  )
}


/**
 * One preset sub-agent run — the Agent example: Agent > AgentHeader (name +
 * model badge) > AgentContent > AgentInstructions, followed here by the run's
 * nested ChainOfThought, its streamed final MessageResponse, any delivered
 * files, and a collapsed Task holding one Tool per lifecycle call.
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

  const lifecycle = [...chain.polls, ...chain.downloads]
  const runLabel = run?.preset ? `Sub-agent · ${run.preset}` : "Sub-agent"

  return (
    <Agent className="app-glass app-glass-edge overflow-hidden rounded-lg">
      <AgentHeader model={modelLabel} name={`${runLabel} · ${status}`} className="app-glass-edge border-b bg-black/25" />
      <AgentContent>
        <AgentInstructions>
          {input?.instructions ?? input?.input ?? input?.task ?? "…"}
        </AgentInstructions>

        {run && <RunChain run={run} running={running} />}

        {finalText && <MessageResponse isAnimating={running}>{finalText}</MessageResponse>}

        {downloads.map(({ part, meta }) =>
          meta.url && meta.contentType?.startsWith("image/") ? (
            <ImageStep key={part.toolCallId} src={meta.url} alt={meta.filename} caption={meta.filename} />
          ) : (
            <ShareFileArtifact key={part.toolCallId} name={meta.filename} url={meta.url} />
          )
        )}

        {errorText && (
          <NativeToolError toolName={chain.toolName} input={input} error={errorText} />
        )}

        {lifecycle.length > 0 && (
          <Task className="app-glass app-glass-edge rounded-lg border px-3 py-2.5" defaultOpen={false}>
            <TaskTrigger title={`${lifecycle.length} lifecycle call(s)`} className="w-full text-left" />
            <TaskContent>
              {lifecycle.map((part) => (
                <TaskItem key={part.toolCallId}>
                  <LifecycleTool part={part} />
                </TaskItem>
              ))}
            </TaskContent>
          </Task>
        )}
      </AgentContent>
    </Agent>
  )
}

// A run whose create tool part hasn't arrived or bound yet: the same Agent
// composition, rendered from the snapshot alone so the stream is visible from
// the very first frame.
function UnboundRunCard({ run }: { run: AgentRunSnapshot }) {
  const running = !isTerminalRunStatus(run.status)
  return (
    <Agent className="app-glass app-glass-edge overflow-hidden rounded-lg">
      <AgentHeader
        model={run.model ?? run.preset}
        name={`Sub-agent · ${run.preset} · ${run.status}`}
        className="app-glass-edge border-b bg-black/25"
      />
      <AgentContent>
        <RunChain run={run} running={running} />
        {run.text && <MessageResponse isAnimating={running}>{run.text}</MessageResponse>}
        {run.error && (
          <NativeToolError toolName={run.activity} error={run.error} />
        )}
      </AgentContent>
    </Agent>
  )
}


// A completed dynamic tool call that carried a DataCommons / PopHIVE
// observation payload: the dynamic `mcp_client` tool (action call_tool with
// connection_id + tool_name) and direct MCP tools registered under the remote
// tool's own name (get_observations, get_data, …). parseDataObservation is
// strict — anything unrecognized returns null and the call renders through
// the normal Tool, so no output is ever hidden.
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

// ChainOfThoughtStep.status is `complete | active | pending` and nothing
// else: a call still running while the turn streams is `active`; a call that
// finished (in any state) is `complete` — failures surface through the
// native Tool output-error state inside the step, never a bent status.
function stepStatus(
  part: DynamicToolUIPart,
  isThinking: boolean
): "complete" | "active" | "pending" {
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

// The sandbox calls Web Search, Fetch URL Content and People Search itself
// (docs.perplexity.ai/docs/agent-api/tools/sandbox). From bash it uses the pplx
// CLI (docs/cli/overview), which prints `search web` as {hits: [{url, title}]}
// and `content snippets` as {results: [{url}]}. From python it uses the
// preinstalled pplx_sdk (search.web / search.people / content.snippets), where
// printing the hits gives a bare list of {url, title}. Live capture:
// output/playwright/agent-response/pplx_scan2.txt.
const PPLX_CLI = /\bpplx\s+(search\s+web|content\s+snippets)\s+["']([^"']+)["']/
const PPLX_SDK = /\bpplx_sdk\.(search\.web|search\.people|content\.snippets)\(\s*(?:query=)?(?:["']([^"']+)["'])?/

function pplxLinks(stdouts: string[]): Array<{ title: string; url: string }> {
  return stdouts.flatMap((stdout) => {
    try {
      const json = JSON.parse(stdout)
      const items: Array<{ url?: string; title?: string }> = Array.isArray(json)
        ? json
        : (json.hits ?? json.results ?? [])
      return items.flatMap((r) => (r.url ? [{ title: r.title || r.url, url: r.url }] : []))
    } catch {
      return []
    }
  })
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


// Maps JS Place Photos for grounding placeIds. The model never sees this;
// placeId is already on grounding_chunks[].maps.
// https://developers.google.com/maps/documentation/javascript/place-photos
type MapsPlaceCtor = new (opts: { id: string }) => {
  fetchFields: (opts: { fields: string[] }) => Promise<void>
  displayName?: unknown
  formattedAddress?: string
  googleMapsURI?: string
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

// One grounding place → one ChainOfThoughtImage once its Places photo
// resolves. Nothing renders before that: no invented placeholder tile.
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
    caption: string
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
          fields: ["photos", "displayName", "formattedAddress", "googleMapsURI"],
        })
        const shot = place.photos?.[0]
        if (!shot || cancelled) return
        const name = placeText(place.displayName) || title || "Place"
        const credit = shot.authorAttributions?.[0]?.displayName
        setPhoto({
          src: shot.getURI({ maxHeight: 1600 }),
          title: name,
          url: place.googleMapsURI || url,
          caption: [name, place.formattedAddress, credit ? `Photo: ${credit}` : undefined]
            .filter(Boolean)
            .join(" · "),
        })
      } catch {
        // Maps JS missing or Places photo request failed; the badge stays.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [placeId, title, url])
  if (!photo) return null
  return <ImageStep src={photo.src} alt={photo.title} caption={photo.caption} href={photo.url} />
}

function uniquePlaces(
  places: Array<{ title?: string; uri?: string; placeId?: string }>
): Array<{ title?: string; uri?: string; placeId: string }> {
  const seen = new Set<string>()
  const unique: Array<{ title?: string; uri?: string; placeId: string }> = []
  for (const place of places) {
    if (!place.placeId || seen.has(place.placeId)) continue
    seen.add(place.placeId)
    unique.push({ ...place, placeId: place.placeId })
  }
  return unique
}


/**
 * One native Agent API item → one ChainOfThoughtStep, composed the way the
 * Chain of Thought example composes its steps: icon + label + status on the
 * step, and the documented child primitive inside it — search results as
 * ChainOfThoughtSearchResults, images as ChainOfThoughtImage, code as
 * Sandbox, shell output as Terminal, files as Task / Artifact, MCP calls as
 * Tool, and reasoning as MessageResponse.
 */
function NativeToolStep({ native }: { native: NativeTool }) {
  switch (native.type) {
    case "google_maps": {
      const token = native.google_maps_widget_context_token
      const places = native.places ?? []
      return (
        <ChainOfThoughtStep icon={MapPinIcon} label="Google Maps" status="complete">
          <SearchResults
            items={places.map((place) => ({
              title: place.title || place.uri || "Place",
              url: place.uri,
            }))}
          />
          {token ? (
            <JSXPreview
              className="min-h-80 overflow-hidden"
              components={GMP_MAP}
              jsx={`<gmp-place-contextual context-token=${JSON.stringify(token)}></gmp-place-contextual>`}
            >
              <JSXPreviewContent />
              <JSXPreviewError />
            </JSXPreview>
          ) : null}
          {uniquePlaces(places).map((place) => (
            <MapsPlaceImage
              key={place.placeId}
              placeId={place.placeId}
              title={place.title}
              url={place.uri}
            />
          ))}
        </ChainOfThoughtStep>
      )
    }

    case "google_search": {
      const queries = native.queries ?? []
      return (
        <ChainOfThoughtStep
          icon={SearchIcon}
          label={queries.length === 1 ? `Google Search · ${queries[0]}` : "Google Search"}
          description={queries.length > 1 ? queries.join(" · ") : undefined}
          status="complete"
        >
          <SearchResults
            items={(native.results ?? []).map((result) => ({
              title: result.title || result.uri || "",
              url: result.uri,
            }))}
          />
          {(native.images ?? []).map((image, i) => (
            <ImageStep
              key={`${image.image_uri}-${i}`}
              src={image.image_uri}
              alt={image.title || "Search image"}
              caption={image.title}
              href={image.source_uri}
            />
          ))}
        </ChainOfThoughtStep>
      )
    }

    // A search in flight: the step is active and lists what is being looked
    // up as search-result badges (Chain of Thought example, "Searching for
    // recent work..." step).
    case "response.reasoning.search_queries":
      return (
        <ChainOfThoughtStep
          icon={SearchIcon}
          label={`Searching the web · ${native.queries.length} quer${native.queries.length === 1 ? "y" : "ies"}`}
          status="active"
        >
          <SearchResults items={native.queries.map((q) => ({ title: q }))} />
        </ChainOfThoughtStep>
      )

    case "response.reasoning.fetch_url_queries":
      return (
        <ChainOfThoughtStep
          icon={LinkIcon}
          label={`Fetching pages · ${native.urls.length} URL${native.urls.length === 1 ? "" : "s"}`}
          status="active"
        >
          <SearchResults items={native.urls.map((u) => ({ title: u, url: u }))} />
        </ChainOfThoughtStep>
      )

    case "response.reasoning.finance_search_queries":
      return (
        <ChainOfThoughtStep
          icon={CreditCardIcon}
          label={`Looking up markets · ${(native.categories ?? ["quote"]).join(", ")}`}
          status="active"
        >
          <SearchResults items={(native.tickers ?? []).map((t) => ({ title: t }))} />
        </ChainOfThoughtStep>
      )

    case "response.reasoning.finance_search_results":
    case "finance_results":
      return (
        <ChainOfThoughtStep
          icon={CreditCardIcon}
          label={
            "tickers" in native && native.tickers?.length
              ? `Market data · ${native.tickers.join(", ")}`
              : "Market data"
          }
          status="complete"
        >
          <SearchResults
            items={native.results.flatMap((r) =>
              (r.sources ?? []).map((url) => ({ title: r.category, url }))
            )}
          />
          {native.results.map((r, i) => (
            <MessageResponse key={`${r.category}-${i}`}>{r.content}</MessageResponse>
          ))}
        </ChainOfThoughtStep>
      )

    case "response.reasoning.search_results":
    case "search_results":
      return (
        <ChainOfThoughtStep icon={SearchIcon} label="Web results" status="complete">
          <SearchResults
            items={native.results.map((r) => ({ title: r.title || r.url, url: r.url }))}
          />
        </ChainOfThoughtStep>
      )

    case "people_search_results":
      return (
        <ChainOfThoughtStep icon={UsersIcon} label="People" status="complete">
          <SearchResults
            items={native.results.map((r) => ({ title: r.title || r.url, url: r.url }))}
          />
        </ChainOfThoughtStep>
      )

    case "response.reasoning.fetch_url_results":
    case "fetch_url_results":
      return (
        <ChainOfThoughtStep icon={LinkIcon} label="Fetched pages" status="complete">
          <SearchResults
            items={native.contents.map((c) => ({ title: c.title || c.url, url: c.url }))}
          />
        </ChainOfThoughtStep>
      )


    // MCP connector discovery: the tools the server exposes, as badges.
    case "mcp_list_tools": {
      if (native.error) {
        return (
          <ChainOfThoughtStep icon={ServerIcon} label={native.server_label} status="complete">
            <NativeToolError toolName="mcp_list_tools" error={native.error} />
          </ChainOfThoughtStep>
        )
      }
      return (
        <ChainOfThoughtStep
          icon={ServerIcon}
          label={`${native.server_label} · ${native.tools.length} tool${native.tools.length === 1 ? "" : "s"}`}
          status="complete"
        >
          <SearchResults items={native.tools.map((t) => ({ title: t.name }))} />
        </ChainOfThoughtStep>
      )
    }

    // An MCP tool call is a tool call: Tool > ToolHeader > ToolInput +
    // ToolOutput (Tool example). DataCommons / PopHIVE observations render
    // the data panel above it; the verbatim payload stays in the collapsed
    // Tool so the projection is never the only copy of the data.
    case "mcp_call": {
      const label = `${native.server_label} · ${native.name}`
      const input = parseJson(native.arguments) ?? native.arguments
      if (native.error) {
        return (
          <ChainOfThoughtStep icon={ServerIcon} label={label} status="complete">
            <NativeToolError toolName={native.name} input={input} error={native.error} />
          </ChainOfThoughtStep>
        )
      }
      const observation = parseDataObservation(native.server_label, native.name, native.output)
      return (
        <ChainOfThoughtStep icon={ServerIcon} label={label} status="complete">
          {observation && <DataObservationPanel observation={observation} />}
          <Tool className="app-glass app-glass-edge overflow-hidden rounded-lg" defaultOpen={!observation}>
            <ToolHeader type="dynamic-tool" toolName={native.name} state="output-available" className="hover:bg-white/[0.02]" />
            <ToolContent className="app-glass-edge border-t bg-black/20">
              <ToolInput input={input} />
              <ToolOutput
                output={parseJson(native.output) ?? native.output ?? undefined}
                errorText={undefined}
              />
            </ToolContent>
          </Tool>
        </ChainOfThoughtStep>
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
      const running = native.status === "in_progress"
      const status = running ? "active" : "complete"

      // A search or fetch run from the sandbox is still a search or fetch:
      // the operation, not the sandbox, picks the UI, so it gets the same
      // Chain of Thought step as the native tools.
      const pplx = native.code.match(native.language === "bash" ? PPLX_CLI : PPLX_SDK)
      if (pplx) {
        const [, op, query] = pplx
        const [icon, verb] = op.includes("people")
          ? ([UsersIcon, "Searching people"] as const)
          : op.startsWith("search")
            ? ([SearchIcon, "Searching the web"] as const)
            : ([LinkIcon, "Fetching pages"] as const)
        const links = pplxLinks(native.results.map((r) => r.stdout))
        return (
          <ChainOfThoughtStep icon={icon} label={query ? `${verb} · ${query}` : verb} status={status}>
            {(links.length > 0 || query) && (
              <SearchResults items={links.length > 0 ? links : [{ title: query }]} />
            )}
          </ChainOfThoughtStep>
        )
      }

      if (native.language === "bash") {
        // Terminal example: ANSI-framed output (cyan $ prompt, red ✗ on a
        // failed exit), header with title / status / copy, content body.
        const exitCode = native.results.find((r) => r.exit_code !== 0)?.exit_code
        const ansi =
          `\u001B[36m$\u001B[0m ${native.code}\n${output}` +
          (failed ? `\n\u001B[31m✗\u001B[0m exit ${exitCode ?? 1}` : "")
        return (
          <ChainOfThoughtStep icon={TerminalSquareIcon} label="Running commands" status={status}>
            <Terminal autoScroll isStreaming={running} output={ansi}>
              <TerminalHeader>
                <TerminalTitle>bash</TerminalTitle>
                <div className="flex items-center gap-1">
                  <TerminalStatus />
                  <TerminalActions>
                    <TerminalCopyButton />
                  </TerminalActions>
                </div>
              </TerminalHeader>
              <TerminalContent />
            </Terminal>
          </ChainOfThoughtStep>
        )
      }

      // Sandbox example: header carries the tool state, tabs switch between
      // the code and its output, each a CodeBlock with a hover copy button.
      return (
        <ChainOfThoughtStep icon={CodeIcon} label="Ran code" status={status}>
          <Sandbox className="app-glass app-glass-edge rounded-lg">
            <SandboxHeader
              state={sandboxStateFromStatus(native.status, failed)}
              title={`sandbox.${native.language === "python" ? "py" : native.language}`}
              className="app-glass-edge border-b bg-black/25"
            />
            <SandboxContent>
              <SandboxTabs defaultValue={failed ? "output" : "code"}>
                <SandboxTabsBar>
                  <SandboxTabsList>
                    <SandboxTabsTrigger value="code">Code</SandboxTabsTrigger>
                    <SandboxTabsTrigger value="output">Output</SandboxTabsTrigger>
                  </SandboxTabsList>
                </SandboxTabsBar>
                <SandboxTabContent value="code">
                  <CodeBlock className="border-0" code={native.code} language="python">
                    <CodeBlockCopyButton
                      className="absolute top-2 right-2 opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                      size="sm"
                    />
                  </CodeBlock>
                </SandboxTabContent>
                <SandboxTabContent value="output">
                  <CodeBlock className="border-0" code={output} language="log">
                    <CodeBlockCopyButton
                      className="absolute top-2 right-2 opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                      size="sm"
                    />
                  </CodeBlock>
                </SandboxTabContent>
              </SandboxTabs>
            </SandboxContent>
          </Sandbox>
        </ChainOfThoughtStep>
      )
    }

    // Files the sandbox produced. Images render through ChainOfThoughtImage;
    // everything else is an Artifact (with CodeBlock / WebPreview inside when
    // the browser can show it).
    case "share_file": {
      const name = native.filename ?? "file"
      if (native.error) {
        return (
          <ChainOfThoughtStep icon={FileIcon} label={name} status="complete">
            <NativeToolError toolName="share_file" input={{ filename: name }} error={native.error} />
          </ChainOfThoughtStep>
        )
      }
      if (IMAGE_FILE.test(name) && native.url) {
        return (
          <ChainOfThoughtStep icon={ImageIcon} label={name} status="complete">
            <ImageStep src={native.url} alt={name} caption={name} />
          </ChainOfThoughtStep>
        )
      }
      return (
        <ChainOfThoughtStep icon={FileIcon} label={name} status="complete">
          <ShareFileArtifact name={name} url={native.url ?? null} />
        </ChainOfThoughtStep>
      )
    }


    // Sandbox file operations — the Task example: a titled Task whose items
    // are file chips.
    case "sandbox_glob":
    case "sandbox_grep": {
      const verb = native.type === "sandbox_glob" ? "Finding files" : "Searching files"
      const files = native.files ?? []
      const count = native.count ?? files.length
      const label = `${verb} · ${count} match${count === 1 ? "" : "es"}`
      return (
        <ChainOfThoughtStep icon={FolderSearchIcon} label={label} status="complete">
          {native.error ? (
            <NativeToolError toolName={native.type} error={native.error} />
          ) : files.length > 0 ? (
            <FileTask
              title={native.truncated ? `${label} (truncated)` : label}
              files={files.map((path) => ({ path }))}
              defaultOpen={false}
            />
          ) : null}
        </ChainOfThoughtStep>
      )
    }

    case "sandbox_read_file":
      return (
        <ChainOfThoughtStep icon={FileSearchIcon} label={`Read ${native.file_path}`} status="complete">
          {native.error ? (
            <NativeToolError
              toolName="sandbox_read_file"
              input={{ file_path: native.file_path }}
              error={native.error}
            />
          ) : (
            <FileTask
              title={`Read ${native.file_path}`}
              files={[
                {
                  label:
                    typeof native.total_lines === "number"
                      ? `Read ${native.total_lines} lines from`
                      : "Read",
                  path: native.file_path,
                },
              ]}
              defaultOpen={false}
            />
          )}
        </ChainOfThoughtStep>
      )

    case "sandbox_write_file":
      return (
        <ChainOfThoughtStep icon={FilePlusIcon} label={`Wrote ${native.file_path}`} status="complete">
          {native.error ? (
            <NativeToolError
              toolName="sandbox_write_file"
              input={{ file_path: native.file_path }}
              error={native.error}
            />
          ) : (
            <FileTask
              title={`Wrote ${native.file_path}`}
              files={[
                {
                  label:
                    typeof native.size_bytes === "number"
                      ? `Wrote ${native.size_bytes.toLocaleString()} bytes to`
                      : "Wrote",
                  path: native.file_path,
                },
              ]}
              defaultOpen={false}
            />
          )}
        </ChainOfThoughtStep>
      )

    case "sandbox_edit_file": {
      const path = native.file_path ?? "file"
      return (
        <ChainOfThoughtStep
          icon={FileEditIcon}
          label={`Edited ${path}`}
          description={native.message ?? undefined}
          status="complete"
        >
          {native.error ? (
            <NativeToolError
              toolName="sandbox_edit_file"
              input={{ file_path: native.file_path }}
              error={native.error}
            />
          ) : (
            <FileTask
              title={`Edited ${path}`}
              files={[{ label: "Edited", path }]}
              defaultOpen={false}
            />
          )}
        </ChainOfThoughtStep>
      )
    }

    case "sandbox_apply_patch": {
      const touched = [
        ...(native.added ?? []).map((path) => ({ label: "Added", path })),
        ...(native.modified ?? []).map((path) => ({ label: "Modified", path })),
        ...(native.deleted ?? []).map((path) => ({ label: "Deleted", path })),
      ]
      const label = `Applied patch · ${touched.length} file${touched.length === 1 ? "" : "s"}`
      return (
        <ChainOfThoughtStep icon={ListIcon} label={label} status="complete">
          {native.error ? (
            <NativeToolError toolName="sandbox_apply_patch" error={native.error} />
          ) : touched.length > 0 ? (
            <FileTask title={label} files={touched} />
          ) : null}
        </ChainOfThoughtStep>
      )
    }

    case "response.skill.loaded":
    case "skill_loaded":
      return <ChainOfThoughtStep icon={PackageIcon} label={`Loaded ${native.name}`} status="complete" />

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


const TERMINAL_TOOL_NAMES = new Set([
  "shell", "bash", "terminal", "execute", "execute_command", "execute_code", "exec_command", "run_command",
])

// Code Block example: header with file icon + filename + copy button.
function FileCodeBlock({ path, content }: { path: string; content: string }) {
  return (
    <CodeBlock
      className="border-none"
      code={content}
      language={codeLanguageFor(path) ?? "log"}
      showLineNumbers
    >
      <CodeBlockHeader>
        <CodeBlockTitle>
          <FileIcon size={14} />
          <CodeBlockFilename>{path}</CodeBlockFilename>
        </CodeBlockTitle>
        <CodeBlockActions>
          <CodeBlockCopyButton />
        </CodeBlockActions>
      </CodeBlockHeader>
    </CodeBlock>
  )
}

// Terminal example applied to a shell tool's result: stdout/stderr verbatim,
// a red ✗ line on a non-zero exit.
function ShellOutput({
  toolName,
  text,
  exitCode,
  isStreaming,
}: {
  toolName: string
  text: string
  exitCode: number | undefined
  isStreaming: boolean
}) {
  const ansi = [
    text,
    exitCode !== undefined && exitCode !== 0 ? `\u001B[31m✗\u001B[0m exit ${exitCode}` : "",
  ].filter(Boolean).join("\n")
  return (
    <Terminal autoScroll isStreaming={isStreaming} output={ansi}>
      <TerminalHeader>
        <TerminalTitle>{toolName}</TerminalTitle>
        <div className="flex items-center gap-1">
          <TerminalStatus />
          <TerminalActions>
            <TerminalCopyButton />
          </TerminalActions>
        </div>
      </TerminalHeader>
      <TerminalContent />
    </Terminal>
  )
}

// Artifact example applied to file_read results: a verified `find` listing
// is a FileTree; file contents are one CodeBlock per file.
function FoundFilesArtifact({ paths }: { paths: string[] }) {
  const [selectedFile, setSelectedFile] = useState<string>()
  return (
    <Artifact className="app-glass-edge bg-black/20 shadow-none">
      <ArtifactHeader className="app-glass-edge bg-black/25">
        <div>
          <ArtifactTitle>Files found</ArtifactTitle>
          <ArtifactDescription className="text-xs tabular-nums">{paths.length} file{paths.length === 1 ? "" : "s"}</ArtifactDescription>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="p-0">
        {paths.length ? (
          <FileTree className="border-none" onSelect={setSelectedFile} selectedPath={selectedFile}>
            {paths.map((filePath, index) => (
              <FileTreeFile key={`${filePath}-${index}`} name={filePath} path={filePath} />
            ))}
          </FileTree>
        ) : (
          <ArtifactDescription className="p-4">No matching files.</ArtifactDescription>
        )}
      </ArtifactContent>
    </Artifact>
  )
}

function FileContentArtifact({ files }: { files: Array<{ path: string; content: string }> }) {
  return (
    <Artifact className="app-glass-edge bg-black/20 shadow-none">
      <ArtifactHeader className="app-glass-edge bg-black/25">
        <div className="min-w-0">
          <ArtifactTitle>File content</ArtifactTitle>
          <ArtifactDescription className="truncate font-mono text-xs tracking-[0.01em]">{files.map((file) => file.path).join(", ")}</ArtifactDescription>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="space-y-3 p-0">
        {files.map((file, index) => (
          <FileCodeBlock key={`${file.path}-${index}`} path={file.path} content={file.content} />
        ))}
      </ArtifactContent>
    </Artifact>
  )
}


/**
 * A dynamic (Strands) tool call — the Tool example, verbatim: Tool >
 * ToolHeader(type, toolName, state) > ToolContent > ToolInput + ToolOutput.
 * Three documented result shapes get their documented primitive as the
 * ToolOutput body: shell stdio → Terminal, file contents → Artifact with a
 * CodeBlock per file, a verified find listing → Artifact with a FileTree.
 * Everything else is ToolOutput's own default (text, or a JSON CodeBlock),
 * so no output is ever hidden or paraphrased.
 */
function DynamicTool({ part }: { part: DynamicToolUIPart }) {
  const input = partInput(part)
  const view = toolPresentation(part)
  const output = parseJson(view.output)
  const blocks: unknown[] = Array.isArray(output?.content) ? output.content : []
  const record = (blocks.length === 1 ? parseJson(parseJson(blocks[0])?.json) : null)
    ?? parseJson(view.text) ?? output
  const isShell = TERMINAL_TOOL_NAMES.has(part.toolName)
  const exitCode = isShell && typeof record?.exit_code === "number" ? record.exit_code : undefined
  const error = view.error || (exitCode !== undefined && exitCode !== 0 ? `Command exited with code ${exitCode}.` : undefined)
  const preliminary = part.state === "output-available" && part.preliminary === true
  const isStreaming = !error && (part.state === "input-streaming" || preliminary)
  const state: DynamicToolUIPart["state"] = error && view.state === "output-available" ? "output-error" : view.state
  const terminal = view.terminal && !preliminary

  let body: DynamicToolUIPart["output"] = undefined

  if (isShell && view.output !== undefined) {
    const hasStdio = typeof record?.stdout === "string" || typeof record?.stderr === "string"
    const text = hasStdio
      ? [record?.stdout, record?.stderr].filter((value) => typeof value === "string" && value.length > 0).join("\n")
      : view.text || JSON.stringify(view.output, null, 2)
    body = <ShellOutput toolName={part.toolName} text={text} exitCode={exitCode} isStreaming={isStreaming} />
  } else if (part.toolName === "file_read" && !error && view.output !== undefined) {
    const path = typeof input?.path === "string" ? input.path : typeof input?.file_path === "string" ? input.file_path : ""
    const mode = input?.mode
    const fileTexts = blocks.length
      ? blocks.map((block) => parseJson(block)?.text).filter((text): text is string => typeof text === "string")
      : typeof record?.content === "string" ? [record.content] : typeof view.output === "string" ? [view.output] : []
    // Stock file_read view mode labels each content block; stats/search are not source files.
    const files = fileTexts.flatMap((text) => {
      const labeled = /^Content of ([^\n]+):\n([\s\S]*)$/.exec(text)
      if ((mode === "view" || mode === undefined) && labeled) return [{ path: labeled[1], content: labeled[2] }]
      if (path && (mode === undefined || mode === "lines" || mode === "chunk")) return [{ path, content: text }]
      if (path && mode === "view" && typeof record?.content === "string") return [{ path, content: text }]
      return []
    })
    const found = mode === "find" ? /^Found (\d+) files:\n([\s\S]*)$/.exec(view.text) : null
    const paths = found ? found[2].split("\n").filter(Boolean) : []
    if (found && Number(found[1]) === paths.length) {
      body = <FoundFilesArtifact paths={paths} />
    } else if (files.length) {
      body = <FileContentArtifact files={files} />
    }
  }

  if (body === undefined && !error && terminal) {
    // ToolOutput's own rendering: a string or an object becomes a CodeBlock;
    // markdown text goes through MessageResponse as in the Tool example.
    const text = view.text && !parseJson(view.text) ? view.text : ""
    body = text
      ? <MessageResponse>{text}</MessageResponse>
      : view.output === undefined
        ? "Tool finished without output."
        : (record ?? view.output)
  }

  return (
    <Tool
      className={error ? "app-glass overflow-hidden rounded-lg border-destructive/30" : "app-glass app-glass-edge overflow-hidden rounded-lg"}
      defaultOpen={terminal || Boolean(error)}
    >
      <ToolHeader
        type="dynamic-tool"
        toolName={part.toolName}
        state={state}
        className={error ? "bg-destructive/[0.06]" : "hover:bg-white/[0.02]"}
      />
      <ToolContent className={error ? "border-t border-destructive/20 bg-black/20" : "app-glass-edge border-t bg-black/20"}>
        <ToolInput input={input ?? part.input} />
        <ToolOutput output={body} errorText={error} />
        {part.toolName === "list_agent_response_files" && !error && <ListFilesArtifacts part={part} />}
      </ToolContent>
    </Tool>
  )
}

export function AgentActivity({
  parts,
  isThinking,
  sessionId,
}: {
  parts: UIMessage["parts"]
  isThinking: boolean
  sessionId?: string
}) {
  const [isOpen, setIsOpen] = useState(true)
  const reasoningParts = parts.filter(isReasoningUIPart)
  // Think tokens arrive as native reasoning parts. Its tool part is only
  // needed for pending status, failures, or a conclusion absent from the stream.
  const dynamicTools = parts.filter(isDynamicToolUIPart)
  const thinkParts = dynamicTools.filter((p) => p.toolName === "think")
  const toolParts = dynamicTools.filter((p) => p.toolName !== "think")
  const reasoningText = reasoningParts.map((p) => p.text).join("")
  const skillRuns = new Map<string, SkillRunSnapshot>()
  for (const part of parts) {
    if (part.type === "data-skill-run") {
      const run = part.data as SkillRunSnapshot
      skillRuns.set(run.toolUseId, run)
    }
  }
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
    agentRuns.length === 0 &&
    skillRuns.size === 0
  ) {
    if (!isThinking) return null
    return (
      <ChainOfThought open={isThinking || isOpen} onOpenChange={setIsOpen} className="app-glass rounded-xl border px-4 py-3.5">
        <ChainOfThoughtHeader disabled={isThinking} className="text-[13px] font-medium tracking-[0.01em]">Thinking…</ChainOfThoughtHeader>
        <ChainOfThoughtContent>
          <ChainOfThoughtStep icon={BrainIcon} label="Working" status="active" />
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

  const segments = buildActivitySegments(parts, isThinking)

  return (
    <ChainOfThought open={isThinking || isOpen} onOpenChange={setIsOpen} className="app-glass rounded-xl border px-4 py-3.5">
      <ChainOfThoughtHeader disabled={isThinking} className="text-[13px] font-medium tracking-[0.01em]">{isThinking ? "Thinking…" : "Chain of Thought"}</ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {segments.map((segment) => {
          if (segment.kind === "computer-use") {
            return (
              <ComputerUseActivity
                key={`computer-use-${segment.index}`}
                parts={segment.parts}
                isThinking={isThinking}
                sessionId={sessionId}
              />
            )
          }

          const part = segment.part
          const i = segment.index

          if (part.type === "data-skill-run") {
            const run = part.data as SkillRunSnapshot
            if (dynamicTools.some((tool) => tool.toolName === "use_skill" && tool.toolCallId === run.toolUseId)) return null
            return (
              <SkillAgent
                key={`skill-${run.toolUseId}`}
                run={skillRuns.get(run.toolUseId)}
                isThinking={isThinking}
                renderNative={(event) => <NativeToolStep native={event as NativeTool} />}
              />
            )
          }

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
            return <NativeToolStep key={(part as NativeToolPart).id ?? `native-${i}`} native={native} />
          }

          if (part.type === "data-agent-run") {
            const run = (part as AgentRunPart).data
            if (boundRuns.has(run.activityId)) return null
            return (
              <ChainOfThoughtStep
                icon={BotIcon}
                key={`agent-run-${run.activityId}`}
                label={run.activity}
                status={isTerminalRunStatus(run.status) ? "complete" : "active"}
              >
                <UnboundRunCard run={run} />
              </ChainOfThoughtStep>
            )
          }

          if (isReasoningUIPart(part)) {
            if (!part.text) return null
            const streaming = part.state === "streaming" && isThinking
            return (
              <ChainOfThoughtStep
                icon={BrainIcon}
                key={`reasoning-${i}`}
                label="Thinking"
                status={streaming ? "active" : "complete"}
              >
                <MessageResponse isAnimating={streaming}>{part.text}</MessageResponse>
              </ChainOfThoughtStep>
            )
          }

          if (isDynamicToolUIPart(part)) {
            return <Fragment key={part.toolCallId}>{renderDynamicTool(part)}</Fragment>
          }

          return null
        })}
      </ChainOfThoughtContent>
    </ChainOfThought>
  )


  // Render directly so native components retain their identity and open state
  // across streamed updates. A component defined here remounts on every token.
  function renderDynamicTool(part: DynamicToolUIPart) {
    if (part.toolName === "use_skill") {
      return (
        <SkillAgent
          part={part}
          run={skillRuns.get(part.toolCallId)}
          isThinking={isThinking}
          renderNative={(event) => <NativeToolStep native={event as NativeTool} />}
        />
      )
    }

    if (part.toolName === "think") {
      const result = toolPresentation(part)
      const showConclusion = result.terminal && result.text && !thinkSummaryWasStreamed(result.text, parts)
      if (result.terminal && !result.error && !showConclusion) return null
      if (!result.terminal && isThinking && reasoningText) return null
      return (
        <ChainOfThoughtStep
          icon={BrainIcon}
          label={result.error
            ? result.state === "output-denied" ? "Thinking denied" : "Thinking failed"
            : showConclusion ? "Thinking result" : "Thinking"}
          status={result.terminal ? "complete" : isThinking ? "active" : "pending"}
          role={result.error ? "alert" : undefined}
          className={result.error ? "text-destructive" : undefined}
        >
          {(result.error || showConclusion) && <MessageResponse>{result.error || result.text}</MessageResponse>}
          {!result.terminal && !isThinking && <p>Stream ended before Think returned a result.</p>}
        </ChainOfThoughtStep>
      )
    }

    const chain = chainByCreateId.get(part.toolCallId)
    if (chain) {
      return (
        <ChainOfThoughtStep icon={BotIcon} label={chain.toolName} status={stepStatus(part, isThinking)}>
          <AgentChainCard chain={chain} run={runByChainKey.get(chain.key)} />
        </ChainOfThoughtStep>
      )
    }
    if (absorbedIds.has(part.toolCallId)) return null

    // DataCommons / PopHIVE observations arriving through the dynamic
    // mcp_client tool or a direct MCP tool render the data panel above the
    // native Tool card; everything else is the Tool card alone.
    const observation = observationFromDynamicTool(part)
    return (
      <ChainOfThoughtStep
        icon={observation ? ServerIcon : WrenchIcon}
        label={observation ? `${observation.server} · ${observation.tool}` : part.toolName}
        status={stepStatus(part, isThinking)}
      >
        {observation && <DataObservationPanel observation={observation} />}
        <DynamicTool part={part} />
      </ChainOfThoughtStep>
    )
  }
}
