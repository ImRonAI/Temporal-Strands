"use client"

import {
  AppWindowIcon,
  CodeIcon,
  FileIcon,
  FolderIcon,
  TerminalSquareIcon,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import type { BundledLanguage } from "shiki"

import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactClose,
  ArtifactContent,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact"
import { CodeBlock } from "@/components/ai-elements/code-block"
import {
  FileTree,
  FileTreeFile,
  FileTreeFolder,
} from "@/components/ai-elements/file-tree"
import {
  Terminal,
  TerminalActions,
  TerminalClearButton,
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
import { cn } from "@/lib/utils"

import {
  fileTreeNodes,
  folderPaths,
  type FileTreeNode,
  type ProjectIdePreview,
} from "./project-ide"

const CODE_LANGUAGES: Record<string, BundledLanguage> = {
  css: "css",
  html: "html",
  htm: "html",
  js: "javascript",
  json: "json",
  jsx: "jsx",
  md: "markdown",
  py: "python",
  sh: "bash",
  ts: "typescript",
  tsx: "tsx",
}

function languageForPath(path: string): BundledLanguage {
  const ext = path.split(".").pop()?.toLowerCase() ?? ""
  return CODE_LANGUAGES[ext] ?? "markdown"
}

function TreeNodes({ nodes }: { nodes: FileTreeNode[] }) {
  return (
    <>
      {nodes.map((node) =>
        node.kind === "folder" ? (
          <FileTreeFolder key={node.path} name={node.name} path={node.path}>
            <TreeNodes nodes={node.children} />
          </FileTreeFolder>
        ) : (
          <FileTreeFile key={node.path} name={node.name} path={node.path} />
        )
      )}
    </>
  )
}

// The main-area views. "files" is not a view of its own — the tree is the
// persistent left rail; these switch what the rest of the real estate shows.
type IdeView = "code" | "preview" | "terminal"

const VIEWS: Array<{
  id: IdeView
  label: string
  icon: typeof CodeIcon
}> = [
  { id: "code", label: "Code", icon: CodeIcon },
  { id: "preview", label: "Preview", icon: AppWindowIcon },
  { id: "terminal", label: "Terminal", icon: TerminalSquareIcon },
]

function ViewButton({
  view,
  active,
  onSelect,
}: {
  view: (typeof VIEWS)[number]
  active: boolean
  onSelect: (view: IdeView) => void
}) {
  const Icon = view.icon
  return (
    <button
      aria-pressed={active}
      className={cn(
        "flex w-full cursor-pointer flex-col items-center gap-1.5 rounded-lg px-2 py-2.5 text-[10px] font-medium tracking-wide transition-colors",
        active
          ? "bg-blurple/15 text-blurple-bright"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
      )}
      onClick={() => onSelect(view.id)}
      type="button"
    >
      <Icon className="size-4" />
      {view.label}
    </button>
  )
}

function StatusDot({ active }: { active: boolean }) {
  return (
    <span className="relative flex size-2">
      {active ? (
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-60" />
      ) : null}
      <span
        className={cn(
          "relative inline-flex size-2 rounded-full",
          active ? "bg-emerald-400" : "bg-zinc-500"
        )}
      />
    </span>
  )
}

export type ProjectIdePanelProps = {
  ide: ProjectIdePreview
  previewForced?: boolean
  onClose: () => void
  className?: string
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-white/10 bg-white/[0.02] backdrop-blur-sm">
      {children}
    </div>
  )
}

/**
 * Official AI Elements IDE composition:
 * FileTree | CodeBlock + Terminal (+ WebPreview).
 * https://elements.ai-sdk.dev/examples/ide
 *
 * Layout: a slim activity rail switches the main area between Code (editor),
 * Preview (web), and Terminal, each taking the full available real estate;
 * the FileTree stays as the left rail for Code/Terminal and yields to the
 * preview when a dev server or previewable document is up.
 */
export function ProjectIdePanel({
  ide,
  previewForced = false,
  onClose,
  className,
}: ProjectIdePanelProps) {
  const tree = useMemo(
    () => fileTreeNodes(ide.files.map((file) => file.path)),
    [ide.files]
  )
  const defaultExpanded = useMemo(() => new Set(folderPaths(tree)), [tree])
  const [expandedPaths, setExpandedPaths] = useState(defaultExpanded)
  const [selectedPath, setSelectedPath] = useState(ide.selectedPath)
  const [userView, setUserView] = useState<IdeView | null>(null)
  const [fetched, setFetched] = useState("")
  const [terminalKey, setTerminalKey] = useState(0)

  // Session/selection resets use the derived-state pattern: when the incoming
  // session changes, the next render adopts the new selection and expanded set
  // instead of syncing via an effect (react.dev: you might not need an effect).
  const [seenSession, setSeenSession] = useState(ide.sessionId)
  if (ide.sessionId !== seenSession) {
    setSeenSession(ide.sessionId)
    setSelectedPath(ide.selectedPath)
    setExpandedPaths(defaultExpanded)
    setFetched("")
  }

  const selected =
    ide.files.find((file) => file.path === selectedPath) ?? ide.files.at(-1)
  const autoView: IdeView =
    previewForced || ide.isDevServer || Boolean(ide.previewUrl) || Boolean(ide.htmlDocument)
      ? "preview"
      : "code"
  const view = userView ?? autoView

  const fetchUrl = selected?.contents ? "" : (selected?.url ?? "")
  useEffect(() => {
    if (!fetchUrl) return
    let cancelled = false
    fetch(fetchUrl)
      .then((res) => (res.ok ? res.text() : ""))
      .then((text) => {
        if (!cancelled) setFetched(text)
      })
      .catch(() => {
        if (!cancelled) setFetched("")
      })
    return () => {
      cancelled = true
    }
  }, [fetchUrl])

  const source = selected?.contents || (fetchUrl ? fetched : "")
  const previewSrc = ide.previewUrl
  const previewDoc = !previewSrc ? ide.htmlDocument : ""
  const output = [ide.command && `$ ${ide.command}`, ide.stdout]
    .filter(Boolean)
    .join("\n")

  const fileCount = ide.files.length

  return (
    <Artifact
      data-testid="project-ide"
      className={cn(
        "@container/ide flex h-full min-h-0 flex-col border-white/10 bg-white/[0.02] shadow-[0_24px_80px_-32px_oklch(0.55_0.22_277/0.35)] backdrop-blur-md",
        className
      )}
    >
      <ArtifactHeader className="border-white/10 bg-white/[0.03] px-3 py-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex size-6 items-center justify-center rounded-md bg-blurple/20">
            <FolderIcon className="size-3.5 text-blurple-bright" />
          </span>
          <ArtifactTitle className="truncate">Project</ArtifactTitle>
          {fileCount > 0 ? (
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
              {fileCount} file{fileCount === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>
        <ArtifactActions>
          <div className="mr-1 flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] p-0.5">
            {VIEWS.map((v) => {
              const Icon = v.icon
              const active = view === v.id
              return (
                <button
                  aria-pressed={active}
                  className={cn(
                    "flex cursor-pointer items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors",
                    active
                      ? "bg-blurple/25 text-blurple-bright"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  data-testid={`ide-view-${v.id}`}
                  key={v.id}
                  onClick={() => setUserView(v.id)}
                  type="button"
                >
                  <Icon className="size-3.5" />
                  <span className="hidden @min-[48rem]/ide:inline">{v.label}</span>
                </button>
              )
            })}
          </div>
          <ArtifactAction
            aria-label={ide.isStreaming ? "Working" : "Idle"}
            className="pointer-events-none"
            label={ide.isStreaming ? "Working" : "Idle"}
            tooltip={ide.isStreaming ? "Working" : "Idle"}
          >
            <StatusDot active={ide.isStreaming} />
          </ArtifactAction>
          <ArtifactClose onClick={onClose} />
        </ArtifactActions>
      </ArtifactHeader>
      <ArtifactContent className="flex min-h-0 flex-1 flex-col overflow-hidden p-0">
        <div className="flex h-full min-h-0 w-full bg-background/60">
          {/* Activity rail: the whole switcher UI. */}
          <div className="flex w-14 shrink-0 flex-col items-center gap-1 border-white/10 border-r bg-white/[0.02] px-1.5 py-2">
            {VIEWS.map((v) => (
              <ViewButton
                active={view === v.id}
                key={v.id}
                onSelect={setUserView}
                view={v}
              />
            ))}
            <div className="mt-auto flex flex-col items-center gap-2 pb-1">
              <StatusDot active={ide.isStreaming} />
            </div>
          </div>

          {view !== "preview" ? (
            <div className="flex w-40 shrink-0 flex-col border-white/10 border-r @min-[48rem]/ide:w-60">
              <div className="flex h-8 shrink-0 items-center gap-1.5 border-white/10 border-b px-3">
                <FileIcon className="size-3.5 text-muted-foreground" />
                <span className="font-medium text-[11px] text-muted-foreground uppercase tracking-wider">
                  Files
                </span>
              </div>
              <div className="flex-1 overflow-auto p-1">
                <FileTree
                  className="border-none bg-transparent"
                  expanded={expandedPaths}
                  onExpandedChange={setExpandedPaths}
                  onSelect={(path) => {
                    setSelectedPath(path)
                    if (view !== "code") setUserView("code")
                  }}
                  selectedPath={selected?.path}
                >
                  <TreeNodes nodes={tree} />
                </FileTree>
              </div>
            </div>
          ) : null}

          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            {view === "preview" ? (
              <WebPreview
                className="min-h-0 flex-1 rounded-none border-0 bg-transparent"
                defaultUrl={previewSrc}
                key={previewSrc || "html-doc"}
              >
                <WebPreviewNavigation className="border-white/10">
                  <WebPreviewUrl />
                </WebPreviewNavigation>
                <WebPreviewBody
                  src={previewSrc || undefined}
                  srcDoc={previewDoc || undefined}
                />
              </WebPreview>
            ) : view === "terminal" ? (
              <Terminal
                className="min-h-0 flex-1 rounded-none border-0"
                isStreaming={ide.isStreaming}
                key={terminalKey}
                onClear={() => setTerminalKey((k) => k + 1)}
                output={output}
              >
                <TerminalHeader className="border-white/10">
                  <TerminalTitle />
                  <TerminalActions>
                    <TerminalStatus>
                      <span className="size-1.5 animate-pulse rounded-full bg-emerald-400" />
                      streaming
                    </TerminalStatus>
                    <TerminalCopyButton />
                    <TerminalClearButton />
                  </TerminalActions>
                </TerminalHeader>
                <TerminalContent className="max-h-full flex-1" />
              </Terminal>
            ) : (
              <Frame>
                <div className="flex h-8 shrink-0 items-center gap-2 border-white/10 border-b px-3">
                  <FileIcon className="size-3.5 text-muted-foreground" />
                  <span className="truncate font-mono text-[11px] text-muted-foreground">
                    {selected?.path ?? "No file selected"}
                  </span>
                  <span className="ml-auto rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {languageForPath(selected?.path ?? "")}
                  </span>
                </div>
                <CodeBlock
                  className="min-h-0 flex-1 overflow-auto rounded-none border-0"
                  code={source}
                  language={languageForPath(selected?.path ?? "")}
                  showLineNumbers
                />
              </Frame>
            )}
          </div>
        </div>
      </ArtifactContent>
    </Artifact>
  )
}
