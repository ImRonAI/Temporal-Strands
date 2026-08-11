"use client"

import {
  DefaultGeneratedFile,
  isDynamicToolUIPart,
  isReasoningUIPart,
  type DynamicToolUIPart,
  type UIMessage,
} from "ai"
import { type ReactNode, useEffect, useState } from "react"
import type { BundledLanguage } from "shiki"
import {
  BrainIcon,
  FileIcon,
  PlugIcon,
  SparklesIcon,
  TerminalIcon,
  WrenchIcon,
} from "lucide-react"

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
import { Image } from "@/components/ai-elements/image"
import {
  JSXPreview,
  JSXPreviewContent,
  JSXPreviewError,
} from "@/components/ai-elements/jsx-preview"
import { MessageResponse } from "@/components/ai-elements/message"
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
  TaskTrigger,
} from "@/components/ai-elements/task"
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"

function parseJson(value: unknown): Record<string, unknown> | null {
  // Tool output is already an object whenever the Strands content block
  // carried `json` -- app/api/orchestrator/route.ts writes `c.json` straight
  // through. Only string-testing it returned null for exactly those cases, so
  // groupToolParts never saw a response id and every sub-agent card stayed
  // stuck on "queued" with no results.
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
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

type NativeToolPart = { type: "data-native-tool"; id?: string; data: NativeTool }

// SandboxResultsOutputItem.status is its own real enum ("in_progress" |
// "completed" | "failed" | "timed_out" — verified against the installed
// perplexity SDK's output_item.py), distinct from ToolUIPart["state"]
// (which SandboxHeader actually requires). Mapping rather than hardcoding
// one state regardless of what really happened.

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

  // Keyed by the create call's own id, plus the ids this grouping absorbed,
  // so a single ordered walk over parts can render each chain at the position
  // of its create call and skip the polls it already contains.
  const chainByCreateId = new Map(chains.map((c) => [c.create.toolCallId, c]))
  const absorbedIds = new Set(
    chains.flatMap((c) =>
      [...c.polls, ...c.downloads].map((p) => p.toolCallId)
    )
  )

  return { chains, standalone, chainByCreateId, absorbedIds }
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
          <ChainOfThoughtStep icon={BrainIcon} label="Sub-agent reasoning">
            <MessageResponse>{reasoningSummary}</MessageResponse>
          </ChainOfThoughtStep>
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
              <SandboxHeader title="Sandbox execution" state={sandboxStateFromStatus(item.status ?? "", false)} />
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
            // ChainOfThoughtImage is the SDK's own wrapper for imagery inside
            // Chain of Thought: it supplies the framing and the caption slot
            // (chain-of-thought.tsx:205-214). Image stays as the child --
            // it is the primitive that actually renders the bytes.
            <ChainOfThoughtImage
              key={part.toolCallId}
              caption="File produced by the sub-agent"
            >
              <Image
                base64={file.base64}
                uint8Array={file.uint8Array}
                mediaType={file.mediaType}
                alt="File produced by the sub-agent"
              />
            </ChainOfThoughtImage>
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

// A search call in flight — the queries or URLs the model asked for, before
// any results come back. Task streams the high-level "what's being searched";
// TaskTrigger's built-in SearchIcon is the visual cue.
function CallTask({ title, items }: { title: string; items: string[] }) {
  return (
    <Task className="border-white/10 bg-white/[0.02]">
      <TaskTrigger title={title} />
      <TaskContent>
        {items.map((item, i) => (
          <TaskItem key={`${item}-${i}`}>{item}</TaskItem>
        ))}
      </TaskContent>
    </Task>
  )
}

// Search results — every search-shaped native renders as the Chain of Thought
// search subcomponents nested inside a Task, and nothing else: no Tool cards,
// no Sandbox, no step chrome around them.
function SearchResultsTask({
  title,
  items,
  children,
}: {
  title: string
  items: Array<{ title: string; url?: string }>
  children?: ReactNode
}) {
  return (
    <Task className="border-white/10 bg-white/[0.02]">
      <TaskTrigger title={title} />
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
    <Artifact className="border-white/10 bg-white/[0.02] backdrop-blur-sm">
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
              className="border-white/10 bg-white/[0.02]"
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
          <WebPreview defaultUrl={url} className="h-80 border-white/10 bg-white/[0.02]">
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

function NativeToolStep({ native }: { native: NativeTool }) {
  switch (native.type) {
    // Searches (web / url / people / finance) render ONLY as the Chain of
    // Thought search subcomponents nested inside a Task: the Task title
    // streams the high-level "what's being searched", the results render as
    // ChainOfThoughtSearchResults. No Tool cards, no Sandbox, no step chrome.
    case "response.reasoning.search_queries":
      return (
        <CallTask
          title={`Searching the web · ${native.queries.length} quer${native.queries.length === 1 ? "y" : "ies"}`}
          items={native.queries}
        />
      )

    case "response.reasoning.fetch_url_queries":
      return (
        <CallTask
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
          title={`Looking up markets · ${(native.categories ?? ["quote"]).join(", ")}`}
          items={native.tickers ?? []}
        />
      )

    case "response.reasoning.finance_search_results":
      return (
        <SearchResultsTask
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
          title="Web results"
          items={native.results.map((r) => ({ title: r.title || r.url, url: r.url }))}
        />
      )

    case "people_search_results":
      return (
        <SearchResultsTask
          title="People"
          items={native.results.map((r) => ({ title: r.title || r.url, url: r.url }))}
        />
      )

    case "finance_results":
      return (
        <SearchResultsTask
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
          title="Fetched pages"
          items={native.contents.map((c) => ({ title: c.title || c.url, url: c.url }))}
        />
      )

    case "mcp_list_tools":
      return (
        <ChainOfThoughtStep
          icon={PlugIcon}
          label={native.server_label}
        >
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : (
            <ResultBadges items={native.tools.map((t) => ({ title: t.name }))} />
          )}
        </ChainOfThoughtStep>
      )

    case "mcp_call":
      return (
        <ChainOfThoughtStep
          icon={PlugIcon}
          label={`${native.server_label} · ${native.name}`}
        >
          <Tool className="border-white/10 bg-white/[0.02] backdrop-blur-sm">
            <ToolHeader
              state={native.error ? "output-error" : "output-available"}
              type="dynamic-tool"
              toolName={native.name}
            />
            <ToolContent>
              <ToolInput input={parseJson(native.arguments) ?? native.arguments} />
              <ToolOutput
                errorText={native.error ?? undefined}
                output={native.output ?? undefined}
              />
            </ToolContent>
          </Tool>
        </ChainOfThoughtStep>
      )

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
            <Task className="border-white/10 bg-white/[0.02]">
              <TaskTrigger title={`Searching the ${kind || "web"} · ${query}`} />
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
          <ChainOfThoughtStep
            icon={TerminalIcon}
            label="Running commands"
            status={native.status === "in_progress" ? "active" : "complete"}
          >
            <Terminal
              autoScroll
              className="border-white/10"
              output={ansi}
              isStreaming={native.status === "in_progress"}
            >
              <TerminalHeader>
                <TerminalTitle>bash</TerminalTitle>
                <div className="flex items-center gap-1">
                  <TerminalStatus>Running…</TerminalStatus>
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

      // Python code execution is the one thing Sandbox is for.
      return (
        <ChainOfThoughtStep
          icon={TerminalIcon}
          label="Ran code"
          status={native.status === "in_progress" ? "active" : "complete"}
        >
          {/* A failed run is nearly always the model's own retry loop — it
              writes code, hits an error, and corrects itself. Showing that
              expanded puts a raw traceback in front of the user as if it were
              the result. Collapsed by default; still one click away. */}
          <SandboxPanel
            failed={failed}
            className="border-white/10 bg-white/[0.03]"
          >
            <SandboxHeader
              title="Sandbox · python"
              state={sandboxStateFromStatus(native.status, failed)}
            />
            <SandboxContent>
              {/* Always opens on Code. Switching to Output only when a run
                  failed made two adjacent sandbox blocks disagree about which
                  tab was showing, which reads as broken. The code is the
                  stable thing to lead with; Output is one click away. */}
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
        </ChainOfThoughtStep>
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
        // Any image produced by an execution renders immediately, no card.
        return (
          <ChainOfThoughtImage caption={name}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={native.url} alt={name} className="h-auto max-w-full" />
          </ChainOfThoughtImage>
        )
      }
      return (
        <ChainOfThoughtStep icon={FileIcon} label={name}>
          {native.error ? (
            <p className="text-destructive text-xs">{native.error}</p>
          ) : (
            <ShareFileArtifact name={name} url={native.url ?? null} />
          )}
        </ChainOfThoughtStep>
      )
    }

    case "response.skill.loaded":
    case "skill_loaded":
      return <ChainOfThoughtStep icon={SparklesIcon} label={`Loaded ${native.name}`} />

    // reasoning.started / reasoning.stopped carry an optional thought only.
    default: {
      const thought = "thought" in native ? native.thought : null
      if (!thought) return null
      return (
        <ChainOfThoughtStep icon={BrainIcon} label="Thinking">
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
  return (
    <Tool className={cn("border-white/10 backdrop-blur-sm", className ?? "bg-white/[0.02]")}>
      <ToolHeader state={part.state} type="dynamic-tool" toolName={part.toolName} />
      <ToolContent>
        {input !== undefined && <ToolInput input={input} />}
        {jsx ? (
          // Documented usage (ai-sdk.dev/elements/components/jsx-preview):
          // JSXPreview wraps JSXPreviewContent + JSXPreviewError; the parser
          // renders the string, the error child surfaces parse failures.
          <JSXPreview jsx={jsx} className="p-4">
            <JSXPreviewContent />
            <JSXPreviewError />
          </JSXPreview>
        ) : (
          (part.state === "output-available" || part.state === "output-error") && (
            <ToolOutput
              errorText={part.state === "output-error" ? part.errorText : undefined}
              output={"output" in part ? part.output : undefined}
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


  if (
    !reasoningText &&
    toolParts.length === 0 &&
    thinkParts.length === 0 &&
    nativeTools.length === 0
  ) {
    return null
  }

  const { chainByCreateId, absorbedIds } = groupToolParts(toolParts)

  return (
    <ChainOfThought
      // The component's own uncontrolled mode (useControllableState with
      // defaultOpen, chain-of-thought.tsx:50-54): open by default so finished
      // reasoning stays visible, user toggles freely after that. An earlier
      // controlled `open={userOpen ?? isThinking}` auto-collapsed the block
      // at turn end and hid the entire chain of thought behind a 70px stub.
      defaultOpen
      className="rounded-xl border border-white/10 bg-white/[0.02] p-4 backdrop-blur-md"
    >
      <ChainOfThoughtHeader>{isThinking ? "Thinking…" : "Chain of Thought"}</ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {/* One pass over parts in arrival order. ChainOfThoughtContent is a
            plain space-y-3 stack that renders children as given, and
            message.parts is already ordered by the AI SDK, so walking it once
            is what makes the reasoning stage and the orchestrator read as a
            single agent working through a problem. Rendering by category --
            all tools, then all reasoning -- regrouped the timeline and made
            the handoff visible. */}
        {parts.map((part, i) => {
          // GWEN-6: route.ts emits one reconciled data-retry part (stable id
          // "retry") when the workflow's model retry policy re-runs a turn.
          // The attempt counter updates in place as retries accumulate.
          if (part.type === "data-retry") {
            const attempt = (part as { data?: { attempt?: number } }).data?.attempt
            return (
              <ChainOfThoughtStep
                key="retry"
                icon={WrenchIcon}
                label={`Retrying${typeof attempt === "number" ? ` · attempt ${attempt}` : ""}`}
                status={isThinking ? "active" : "complete"}
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

          if (isReasoningUIPart(part)) {
            if (!part.text) return null
            return (
              <ChainOfThoughtStep
                key={`reasoning-${i}`}
                icon={BrainIcon}
                label="Thinking"
                status={
                  isThinking && i === parts.length - 1 ? "active" : "complete"
                }
              >
                <MessageResponse>{part.text}</MessageResponse>
              </ChainOfThoughtStep>
            )
          }

          if (isDynamicToolUIPart(part)) {
            // The reasoning stage streams its notes as text deltas on the
            // "thinking" topic, which route.ts turns into reasoning parts —
            // the thought blocks rendered above, interleaved with its tool
            // activity in true order across every event-loop cycle. The
            // think tool's own output is the stage's final message, whose
            // text already streamed as those same deltas, so rendering it
            // here would print the handoff twice. Only a failure has no
            // other surface (a real, live case: KeyError 'model_id'), so
            // only the error state renders.
            if (part.toolName === "think") {
              if (part.state === "output-error") {
                return (
                  <ChainOfThoughtStep
                    key={part.toolCallId}
                    icon={BrainIcon}
                    label="Thinking"
                    status="complete"
                  >
                    <p className="text-destructive text-xs">{part.errorText}</p>
                  </ChainOfThoughtStep>
                )
              }
              return null
            }

            const chain = chainByCreateId.get(part.toolCallId)
            if (chain) {
              const latestState = chain.polls.at(-1)?.state ?? chain.create.state
              const done =
                latestState === "output-error" ||
                latestJson(chain)?.status === "completed"
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
            }
            // Polls belong to the card their create call already rendered.
            if (absorbedIds.has(part.toolCallId)) return null

            return (
              <ChainOfThoughtStep
                key={part.toolCallId}
                icon={WrenchIcon}
                label={part.toolName}
                status={
                  part.state === "output-available" ||
                  part.state === "output-error"
                    ? "complete"
                    : "active"
                }
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
