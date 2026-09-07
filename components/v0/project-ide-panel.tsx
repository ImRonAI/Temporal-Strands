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
        "group flex w-full cursor-pointer flex-col items-center gap-1.5 rounded-xl px-2 py-3 text-[10px] font-medium tracking-wide transition-all duration-200",
        active
          ? "bg-blurple/25 text-blurple-bright shadow-[0_0_20px_-2px_oklch(0.62_0.205_277/0.5),inset_0_1px_0_0_oklch(0.85_0.1_285/0.25)]"
          : "text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
      )}
      onClick={() => onSelect(view.id)}
      type="button"
    >
      <span
        className={cn(
          "flex size-8 items-center justify-center rounded-lg transition-all duration-200",
          active
            ? "bg-blurple/30 shadow-[inset_0_1px_0_0_oklch(0.85_0.1_285/0.3)]"
            : "bg-white/[0.04] group-hover:bg-white/[0.08]"
        )}
      >
        <Icon className="size-4" />
      </span>
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
          active
            ? "bg-emerald-400 shadow-[0_0_8px_2px_oklch(0.7_0.19_160/0.6)]"
            : "bg-zinc-500"
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
    <div className="ide-glass-inset flex h-full min-h-0 flex-col overflow-hidden rounded-xl border backdrop-blur-md">
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
        "@container/ide ide-glass flex h-full min-h-0 flex-col overflow-hidden rounded-xl",
        className
      )}
    >
      <ArtifactHeader className="ide-glass-edge border-b bg-white/[0.03] px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex size-7 items-center justify-center rounded-lg bg-blurple/30 shadow-[0_0_16px_-2px_oklch(0.62_0.205_277/0.6),inset_0_1px_0_0_oklch(0.85_0.1_285/0.3)]">
            <FolderIcon className="size-4 text-blurple-bright" />
          </span>
          <ArtifactTitle className="truncate font-medium tracking-tight">
            Project
          </ArtifactTitle>
          {fileCount > 0 ? (
            <span className="rounded-full border border-blurple/25 bg-blurple/15 px-2.5 py-0.5 font-mono text-[10px] font-medium text-blurple-bright">
              {fileCount} file{fileCount === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>
        <ArtifactActions>
          <div className="mr-1 flex items-center gap-0.5 rounded-full border border-white/10 bg-black/20 p-1 shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.3)]">
            {VIEWS.map((v) => {
              const Icon = v.icon
              const active = view === v.id
              return (
                <button
                  aria-pressed={active}
                  className={cn(
                    "flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] font-medium transition-all duration-200",
                    active
                      ? "bg-blurple/40 text-white shadow-[0_0_16px_-2px_oklch(0.62_0.205_277/0.7),inset_0_1px_0_0_oklch(0.9_0.08_285/0.4)]"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  data-testid={`ide-view-${v.id}`}
                  key={v.id}
                  onClick={() => setUserView(v.id)}
                  type="button"
                >
                  <Icon className="size-3.5" />
                  <span className="hidden @min-[48rem]/ide:inline">
                    {v.label}
                  </span>
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
        <div className="flex h-full min-h-0 w-full">
          {/* Activity rail: the whole switcher UI. */}
          <div className="ide-glass-edge flex w-16 shrink-0 flex-col items-center gap-1.5 border-r bg-black/20 px-2 py-3">
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
            <div className="ide-glass-edge flex w-44 shrink-0 flex-col border-r @min-[48rem]/ide:w-64">
              <div className="flex h-9 shrink-0 items-center gap-2 border-white/10 border-b bg-white/[0.02] px-3.5">
                <FileIcon className="size-3.5 text-blurple-bright/70" />
                <span className="font-semibold text-[11px] text-foreground/80 tracking-wider">
                  Files
                </span>
              </div>
              <div className="flex-1 overflow-auto p-1.5">
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
                <WebPreviewNavigation className="ide-glass-edge border-b bg-white/[0.02]">
                  <WebPreviewUrl />
                </WebPreviewNavigation>
                <WebPreviewBody
                  src={previewSrc || undefined}
                  srcDoc={previewDoc || undefined}
                />
              </WebPreview>
            ) : view === "terminal" ? (
              <div className="flex min-h-0 flex-1 flex-col p-3">
                <Terminal
                  className="ide-glass-inset min-h-0 flex-1 overflow-hidden rounded-xl border"
                  isStreaming={ide.isStreaming}
                  key={terminalKey}
                  onClear={() => setTerminalKey((k) => k + 1)}
                  output={output}
                >
                  <TerminalHeader className="ide-glass-edge border-b bg-black/30">
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
              </div>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col p-3">
                <Frame>
                  <div className="ide-glass-edge flex h-9 shrink-0 items-center gap-2 border-b bg-black/30 px-3.5">
                    <FileIcon className="size-3.5 text-blurple-bright/70" />
                    <span className="truncate font-mono text-[11px] text-foreground/80">
                      {selected?.path ?? "No file selected"}
                    </span>
                    <span className="ml-auto rounded-full border border-blurple/25 bg-blurple/15 px-2.5 py-0.5 font-mono text-[10px] font-medium text-blurple-bright">
                      {languageForPath(selected?.path ?? "")}
                    </span>
                  </div>
                  <CodeBlock
                    className="ide-code-surface min-h-0 flex-1 overflow-auto rounded-none border-0"
                    code={source}
                    language={languageForPath(selected?.path ?? "")}
                    showLineNumbers
                  />
                </Frame>
              </div>
            )}
          </div>
        </div>
      </ArtifactContent>
    </Artifact>
  )
}
