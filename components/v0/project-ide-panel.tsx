"use client"

import {
  AppWindowIcon,
  CodeIcon,
  EyeIcon,
  FileIcon,
  FolderIcon,
  FolderTreeIcon,
  Loader2Icon,
  Maximize2Icon,
  Minimize2Icon,
  TerminalSquareIcon,
  XIcon,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { KeyboardEvent, ReactNode } from "react"
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
  JSXPreview,
  JSXPreviewContent,
  JSXPreviewError,
} from "@/components/ai-elements/jsx-preview"
import { MessageResponse } from "@/components/ai-elements/message"
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

import { filePreviewKind, componentJsx } from "./file-preview"
import {
  fileTreeNodes,
  folderPaths,
  formatTranscript,
  resolveIdeView,
  type FileTreeNode,
  type ProjectIdePreview,
  type ProjectIdeView,
} from "./project-ide"

const CODE_LANGUAGES: Record<string, BundledLanguage> = {
  cjs: "javascript",
  css: "css",
  html: "html",
  htm: "html",
  xhtml: "html",
  js: "javascript",
  json: "json",
  jsx: "jsx",
  md: "markdown",
  markdown: "markdown",
  mdx: "mdx",
  mjs: "javascript",
  mts: "typescript",
  py: "python",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  sql: "sql",
  svg: "xml",
  toml: "toml",
  ts: "typescript",
  tsx: "tsx",
  xml: "xml",
  yaml: "yaml",
  yml: "yaml",
}

// Shiki's "text" is a special language (always available, no grammar to
// load); the vendored CodeBlock types its prop as BundledLanguage, so the
// one honest fallback needs a cast. Unknown files render as plain text —
// never mis-highlighted as markdown.
const PLAIN_TEXT = "text" as BundledLanguage

function languageForPath(path: string): BundledLanguage {
  const ext = path.split(".").pop()?.toLowerCase() ?? ""
  return CODE_LANGUAGES[ext] ?? PLAIN_TEXT
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
type IdeView = ProjectIdeView

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
      aria-label={view.label}
      aria-pressed={active}
      className={cn(
        "group flex w-full cursor-pointer flex-col items-center gap-1.5 rounded-xl px-2 py-3 text-[10px] font-medium tracking-wide transition-all duration-200",
        active
          ? "bg-blurple/25 text-blurple-bright shadow-[0_0_20px_-2px_oklch(0.62_0.17_250/0.5),inset_0_1px_0_0_oklch(0.86_0.08_235/0.25)]"
          : "text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
      )}
      onClick={() => onSelect(view.id)}
      type="button"
    >
      <span
        className={cn(
          "flex size-8 items-center justify-center rounded-lg transition-all duration-200",
          active
            ? "bg-blurple/30 shadow-[inset_0_1px_0_0_oklch(0.86_0.08_235/0.3)]"
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
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-60 motion-reduce:animate-none" />
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

/** Honest placeholder for content the panel cannot truthfully show. */
function EmptyPane({
  title,
  detail,
  loading = false,
  testId,
}: {
  title: string
  detail?: string
  loading?: boolean
  testId: string
}) {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-6 text-center"
      data-testid={testId}
      role="status"
    >
      {loading ? (
        <Loader2Icon className="size-5 animate-spin text-blurple-bright/70 motion-reduce:animate-none" />
      ) : null}
      <p className="text-sm font-medium text-foreground/85">{title}</p>
      {detail ? (
        <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
          {detail}
        </p>
      ) : null}
    </div>
  )
}

export type ProjectIdePanelProps = {
  ide: ProjectIdePreview
  previewForced?: boolean
  onClose: () => void
  /** Present on desktop split layouts: toggles the panel between the normal
   *  split and taking the whole chat area. Restore keeps all live state. */
  fullscreen?: boolean
  onFullscreenChange?: (fullscreen: boolean) => void
  /** Reports the resolved main-area view so the host can, e.g., exempt the
   *  Preview tab from idle auto-close. Fires whenever the view changes. */
  onViewChange?: (view: ProjectIdeView) => void
  className?: string
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="ide-glass-inset flex h-full min-h-0 flex-col overflow-hidden rounded-xl border backdrop-blur-md">
      {children}
    </div>
  )
}

/** Rendered preview of the SELECTED file (distinct from the project Preview
 *  view, which shows the dev server / shared document).
 *  - HTML renders in a sandboxed iframe (allow-scripts only, never
 *    allow-same-origin — the document cannot reach the parent window).
 *  - Markdown renders through the native MessageResponse (Streamdown).
 *  - JSX/TSX renders through the native JSXPreview (react-jsx-parser), a
 *    static JSX renderer, not a module runtime: componentJsx extracts one
 *    static component's returned JSX via the TypeScript AST without
 *    evaluating any code. Components needing imports, props, hooks, or
 *    module logic get an explicit "run the project" explanation instead of
 *    faked execution, and successful previews carry a static-preview notice.
 */
function ComponentPreview({ source }: { source: string }) {
  const [result, setResult] = useState<{source: string; jsx?: string; error?: string} | null>(null)
  useEffect(() => {
    let cancelled = false
    componentJsx(source).then(jsx => { if (!cancelled) setResult({source, jsx}) })
      .catch(error => { if (!cancelled) setResult({source, error: error instanceof Error ? error.message : "Unable to preview component"}) })
    return () => { cancelled = true }
  }, [source])
  if (result?.source !== source) return <EmptyPane testId="ide-file-preview-loading" title="Preparing JSX preview…" loading />
  if (result.error) {
    return (
      <EmptyPane
        testId="ide-file-preview-unsupported"
        title="This component needs the running project"
        detail={`${result.error} The static preview renders markup only — it never executes component code. Start the project (dev server) and use the Preview view for the real thing.`}
      />
    )
  }
  return <StaticJsxSurface jsx={result.jsx ?? ""} />
}

/** Pure success surface for the static JSX preview: the limitation notice
 *  plus the native JSXPreview composition (default error renderer — no
 *  function children, the vendored primitive's ComponentProps intersection
 *  rejects them). Exported for direct SSR test coverage, since effects
 *  (componentJsx's lazy parse) do not run under renderToStaticMarkup. */
export function StaticJsxSurface({ jsx }: { jsx: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="ide-file-preview-jsx">
      <p
        className="ide-glass-edge shrink-0 border-b bg-white/[0.02] px-3.5 py-1.5 text-[10px] leading-relaxed text-muted-foreground"
        data-testid="ide-file-preview-jsx-notice"
      >
        Static markup preview — styles, props, and interactivity are not
        executed. Run the project and use Preview for the real component.
      </p>
      <div className="min-h-0 flex-1 overflow-auto bg-white p-4 text-zinc-900">
        <JSXPreview jsx={jsx}><JSXPreviewError /><JSXPreviewContent /></JSXPreview>
      </div>
    </div>
  )
}

export function FilePreviewBody({ path, source }: { path: string; source: string }) {
  const kind = filePreviewKind(path)
  if (kind === "html") {
    return (
      <iframe
        className="ide-code-surface min-h-0 w-full flex-1 border-0 bg-white"
        data-testid="ide-file-preview-html"
        sandbox="allow-scripts allow-forms allow-popups"
        srcDoc={source}
        title={`Preview of ${path}`}
      />
    )
  }
  if (kind === "markdown") {
    return (
      <div
        className="min-h-0 flex-1 overflow-auto p-4"
        data-testid="ide-file-preview-markdown"
      >
        <MessageResponse isAnimating={false}>{source}</MessageResponse>
      </div>
    )
  }
  if (kind === "jsx") {
    return <ComponentPreview source={source} />
  }
  return null
}

type RemoteFetch = {
  url: string
  text: string
  state: "loading" | "done" | "error"
}

/**
 * Official AI Elements IDE composition:
 * FileTree | CodeBlock + Terminal (+ WebPreview).
 * https://elements.ai-sdk.dev/examples/ide
 *
 * Layout: a slim activity rail switches the main area between Code (editor),
 * Preview (web), and Terminal, each taking the full available real estate;
 * the FileTree stays as the left rail for Code/Terminal on wide containers
 * and folds away on narrow ones so the editor never collapses to zero width.
 *
 * View policy: the stream's activeView drives the main area; an explicit user
 * view click overrides it until the NEXT real activity (a new activityId)
 * arrives, then the stream re-engages (resolveIdeView). File selection
 * follows the same rule.
 */
export function ProjectIdePanel({
  ide,
  previewForced = false,
  onClose,
  fullscreen = false,
  onFullscreenChange,
  onViewChange,
  className,
}: ProjectIdePanelProps) {
  const tree = useMemo(
    () => fileTreeNodes(ide.files.map((file) => file.path)),
    [ide.files]
  )
  const defaultExpanded = useMemo(() => new Set(folderPaths(tree)), [tree])
  const [expandedPaths, setExpandedPaths] = useState(defaultExpanded)
  // User choices are stamped with the activityId they were made against and
  // expire when the next real activity arrives.
  const [viewOverride, setViewOverride] = useState<{
    view: IdeView
    at: string | undefined
  } | null>(null)
  const [manualSelection, setManualSelection] = useState<{
    path: string
    at: string | undefined
  } | null>(null)
  const [remote, setRemote] = useState<RemoteFetch | null>(null)
  // Terminal clear hides all operations logged so far; later operations
  // append below the cleared point. Index-based (record() finalizes running
  // entries IN PLACE, so a prefix-string comparison would un-hide them the
  // moment their status text changed).
  const [clearedThrough, setClearedThrough] = useState(0)
  // Narrow-container Files drawer (the persistent tree rail is hidden below
  // 36rem; this keeps file selection reachable on small screens).
  const [drawerOpen, setDrawerOpen] = useState(false)
  // Desktop file-tree toggle: hides the persistent tree rail so the editor
  // can take the full width. Independent of the narrow-container drawer.
  const [treeHidden, setTreeHidden] = useState(false)
  // Code view render toggle: show the selected file rendered (sandboxed HTML /
  // Markdown / static JSX) instead of its source. Stamped per activity so the
  // next real activity returns to source, like the view override.
  const [renderFile, setRenderFile] = useState<{
    on: boolean
    at: string | undefined
  } | null>(null)

  // Session resets use the derived-state pattern: when the incoming session
  // changes, the next render adopts fresh state instead of syncing via an
  // effect (react.dev: you might not need an effect).
  const [seenSession, setSeenSession] = useState(ide.sessionId)
  if (ide.sessionId !== seenSession) {
    setSeenSession(ide.sessionId)
    setExpandedPaths(defaultExpanded)
    setViewOverride(null)
    setManualSelection(null)
    setRemote(null)
    setClearedThrough(0)
    setDrawerOpen(false)
    setTreeHidden(false)
    setRenderFile(null)
  }
  // New folders from later activity auto-expand without collapsing what the
  // user closed/opened; keyed by CONTENT (ide.files is a fresh array every
  // render, so the memoized Set identity is not a stable signal).
  const treeKey = useMemo(() => [...defaultExpanded].sort().join("\n"), [defaultExpanded])
  const [seenTreeKey, setSeenTreeKey] = useState(treeKey)
  if (treeKey !== seenTreeKey) {
    setSeenTreeKey(treeKey)
    setExpandedPaths(new Set([...expandedPaths, ...defaultExpanded]))
  }

  // previewForced is the user's own explicit "Preview" request from the
  // composer; it beats the stream default but not a newer in-panel click.
  const autoView: IdeView = previewForced
    ? "preview"
    : (ide.activeView ?? "code")
  const resolved = resolveIdeView({
    auto: autoView,
    override: viewOverride?.view ?? null,
    overrideAt: viewOverride?.at,
    activityId: ide.activityId,
  })
  if (viewOverride && resolved.overrideExpired) setViewOverride(null)
  const view = resolved.view

  // Let the host track which main-area view is engaged (drives the Preview
  // tab's exemption from idle auto-close).
  useEffect(() => {
    onViewChange?.(view)
  }, [view, onViewChange])

  // File selection follows the stream's active file unless the user picked
  // one; the pick expires with the next activity, like the view override.
  const followedPath =
    manualSelection && manualSelection.at === ide.activityId
      ? manualSelection.path
      : ide.selectedPath
  const selected =
    ide.files.find((file) => file.path === followedPath) ?? ide.files.at(-1)

  // Remote file contents (share_file proxy URLs). The response is stamped
  // with the URL it answered, so a stale response for a previously selected
  // file can never render under the current one (fetch-race safety). While no
  // stamped response matches the current URL the pane derives "loading" —
  // no synchronous setState inside the effect body.
  const fetchUrl = selected?.contents ? "" : (selected?.url ?? "")
  useEffect(() => {
    if (!fetchUrl) return
    let cancelled = false
    fetch(fetchUrl)
      .then((res) =>
        res.ok ? res.text() : Promise.reject(new Error(String(res.status)))
      )
      .then((text) => {
        if (!cancelled) setRemote({ url: fetchUrl, text, state: "done" })
      })
      .catch(() => {
        if (!cancelled) setRemote({ url: fetchUrl, text: "", state: "error" })
      })
    return () => {
      cancelled = true
    }
  }, [fetchUrl])
  const remoteForSelected: RemoteFetch | null = !fetchUrl
    ? null
    : remote?.url === fetchUrl
      ? remote
      : { url: fetchUrl, text: "", state: "loading" }

  const source =
    selected?.contents ||
    (remoteForSelected?.state === "done" ? remoteForSelected.text : "")
  // Rendered-file preview: only meaningful for previewable kinds with source
  // in hand, and only until the next real activity re-engages the source.
  const selectedPreviewKind = selected ? filePreviewKind(selected.path) : null
  const renderingFile = Boolean(
    renderFile?.on &&
      renderFile.at === ide.activityId &&
      selectedPreviewKind &&
      source
  )
  const previewSrc = ide.previewUrl
  const previewDoc = !previewSrc ? ide.htmlDocument : ""

  // The terminal shows the FULL honest operation log — every command plus
  // every write/edit/patch/read/share, in order, with the tools' own final
  // outputs (there is no per-byte live stdout transport; running entries say
  // they are waiting).
  const visibleOps = ide.transcript.slice(
    Math.min(clearedThrough, ide.transcript.length)
  )
  const terminalOutput = formatTranscript(visibleOps)

  const fileCount = ide.files.length

  // Escape dismisses the innermost layer first — scoped to this subtree: it
  // only fires while focus is inside the panel, never as a document-level
  // hijack of Escape (the chat's own dialogs and menus keep their Escape
  // behavior). Order: Files drawer → fullscreen restore → close.
  function onPanelKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape" || event.defaultPrevented) return
    event.preventDefault()
    event.stopPropagation()
    // An open Files drawer is the innermost dismissable layer.
    if (drawerOpen) {
      setDrawerOpen(false)
      return
    }
    if (fullscreen && onFullscreenChange) {
      onFullscreenChange(false)
      return
    }
    onClose()
  }

  return (
    <Artifact
      data-testid="project-ide"
      onKeyDown={onPanelKeyDown}
      className={cn(
        "@container/ide ide-glass flex h-full min-h-0 flex-col overflow-hidden rounded-xl",
        className
      )}
    >
      <ArtifactHeader className="ide-glass-edge border-b bg-white/[0.03] px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex size-7 items-center justify-center rounded-lg bg-blurple/30 shadow-[0_0_16px_-2px_oklch(0.62_0.17_250/0.6),inset_0_1px_0_0_oklch(0.86_0.08_235/0.3)]">
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
          <div className="mr-1 hidden items-center gap-0.5 rounded-full border border-white/10 bg-black/20 p-1 shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.3)] @min-[30rem]/ide:flex">
            {VIEWS.map((v) => {
              const Icon = v.icon
              const active = view === v.id
              return (
                <button
                  aria-label={v.label}
                  aria-pressed={active}
                  className={cn(
                    "flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] font-medium transition-all duration-200",
                    active
                      ? "bg-blurple/40 text-white shadow-[0_0_16px_-2px_oklch(0.62_0.17_250/0.7),inset_0_1px_0_0_oklch(0.9_0.06_240/0.4)]"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  data-testid={`ide-view-${v.id}`}
                  key={v.id}
                  onClick={() =>
                    setViewOverride({ view: v.id, at: ide.activityId })
                  }
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
          {onFullscreenChange ? (
            <ArtifactAction
              aria-label={fullscreen ? "Exit fullscreen" : "Fullscreen"}
              aria-pressed={fullscreen}
              data-testid="ide-fullscreen"
              icon={fullscreen ? Minimize2Icon : Maximize2Icon}
              label={fullscreen ? "Exit fullscreen" : "Fullscreen"}
              onClick={() => onFullscreenChange(!fullscreen)}
              tooltip={fullscreen ? "Exit fullscreen" : "Fullscreen"}
            />
          ) : null}
          <ArtifactClose
            aria-label="Close project IDE"
            data-testid="ide-close"
            onClick={onClose}
            className="w-auto gap-1 px-2.5 text-[11px] font-medium"
          >
            <span aria-hidden>Close</span>
          </ArtifactClose>
        </ArtifactActions>
      </ArtifactHeader>
      <ArtifactContent className="flex min-h-0 flex-1 flex-col overflow-hidden p-0">
        <div className="flex h-full min-h-0 w-full">
          {/* Activity rail: the persistent switcher (the header pill row
              collapses away on narrow containers, this rail never does). */}
          <div className="ide-glass-edge flex w-14 shrink-0 flex-col items-center gap-1.5 border-r bg-black/20 px-1.5 py-3 @min-[48rem]/ide:w-16 @min-[48rem]/ide:px-2">
            {VIEWS.map((v) => (
              <ViewButton
                active={view === v.id}
                key={v.id}
                onSelect={(next) => {
                  setViewOverride({ view: next, at: ide.activityId })
                  if (next === "preview") setDrawerOpen(false)
                }}
                view={v}
              />
            ))}
            {/* Files drawer toggle: only where the persistent tree rail is
                hidden (below 36rem) and there are files to choose from. */}
            {fileCount > 0 && view !== "preview" ? (
              <button
                aria-expanded={drawerOpen}
                aria-label={drawerOpen ? "Hide file explorer" : "Show file explorer"}
                className={cn(
                  "group flex w-full cursor-pointer flex-col items-center gap-1.5 rounded-xl px-2 py-3 text-[10px] font-medium tracking-wide transition-all duration-200 @min-[36rem]/ide:hidden",
                  drawerOpen
                    ? "bg-blurple/25 text-blurple-bright"
                    : "text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
                )}
                data-testid="ide-files-toggle"
                onClick={() => setDrawerOpen((open) => !open)}
                type="button"
              >
                <span
                  className={cn(
                    "flex size-8 items-center justify-center rounded-lg transition-all duration-200",
                    drawerOpen
                      ? "bg-blurple/30"
                      : "bg-white/[0.04] group-hover:bg-white/[0.08]"
                  )}
                >
                  <FolderTreeIcon className="size-4" />
                </span>
                Files
              </button>
            ) : null}
            {/* Desktop tree toggle: where the persistent tree rail exists
                (36rem and up), hide or show it so the editor can take the
                full width. Independent of the narrow-container drawer. */}
            {fileCount > 0 && view !== "preview" ? (
              <button
                aria-expanded={!treeHidden}
                aria-label={treeHidden ? "Show file tree" : "Hide file tree"}
                aria-pressed={!treeHidden}
                className={cn(
                  "group hidden w-full cursor-pointer flex-col items-center gap-1.5 rounded-xl px-2 py-3 text-[10px] font-medium tracking-wide transition-all duration-200 @min-[36rem]/ide:flex",
                  !treeHidden
                    ? "bg-blurple/25 text-blurple-bright"
                    : "text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
                )}
                data-testid="ide-tree-toggle"
                onClick={() => setTreeHidden((hidden) => !hidden)}
                type="button"
              >
                <span
                  className={cn(
                    "flex size-8 items-center justify-center rounded-lg transition-all duration-200",
                    !treeHidden
                      ? "bg-blurple/30"
                      : "bg-white/[0.04] group-hover:bg-white/[0.08]"
                  )}
                >
                  <FolderTreeIcon className="size-4" />
                </span>
                Files
              </button>
            ) : null}
            <div className="mt-auto flex flex-col items-center gap-2 pb-1">
              <StatusDot active={ide.isStreaming} />
            </div>
          </div>

          {/* The file tree folds away on narrow containers so the editor is
              never squeezed to zero width; the code header still names the
              open file. The desktop rail toggle can also hide it outright. */}
          {view !== "preview" && fileCount > 0 && !treeHidden ? (
            <div className="ide-glass-edge hidden w-44 shrink-0 flex-col border-r @min-[36rem]/ide:flex @min-[48rem]/ide:w-64">
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
                    if (!ide.files.some((file) => file.path === path)) return
                    setManualSelection({ path, at: ide.activityId })
                    if (view !== "code") {
                      setViewOverride({ view: "code", at: ide.activityId })
                    }
                  }}
                  selectedPath={selected?.path}
                >
                  <TreeNodes nodes={tree} />
                </FileTree>
              </div>
            </div>
          ) : null}

          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            {/* Narrow-container Files drawer: below 36rem the persistent tree
                rail is hidden, so this overlay keeps file selection reachable.
                Same native FileTree, same selection/expansion state. */}
            {view !== "preview" && fileCount > 0 && drawerOpen ? (
              <div
                className="ide-glass absolute inset-y-0 left-0 z-10 flex w-56 max-w-[85%] flex-col border-r @min-[36rem]/ide:hidden"
                data-testid="ide-files-drawer"
              >
                <div className="ide-glass-edge flex h-9 shrink-0 items-center gap-2 border-b bg-white/[0.02] px-3.5">
                  <FileIcon className="size-3.5 text-blurple-bright/70" />
                  <span className="font-semibold text-[11px] text-foreground/80 tracking-wider">
                    Files
                  </span>
                  <button
                    aria-label="Close file explorer"
                    className="ml-auto flex size-6 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-white/[0.08] hover:text-foreground"
                    data-testid="ide-files-drawer-close"
                    onClick={() => setDrawerOpen(false)}
                    type="button"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                </div>
                <div className="flex-1 overflow-auto p-1.5">
                  <FileTree
                    className="border-none bg-transparent"
                    expanded={expandedPaths}
                    onExpandedChange={setExpandedPaths}
                    onSelect={(path) => {
                      if (!ide.files.some((file) => file.path === path)) return
                      setManualSelection({ path, at: ide.activityId })
                      if (view !== "code") {
                        setViewOverride({ view: "code", at: ide.activityId })
                      }
                      setDrawerOpen(false)
                    }}
                    selectedPath={selected?.path}
                  >
                    <TreeNodes nodes={tree} />
                  </FileTree>
                </div>
              </div>
            ) : null}
            {view === "preview" ? (
              previewSrc || previewDoc ? (
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
              ) : (
                <EmptyPane
                  testId="ide-preview-empty"
                  title="No preview yet"
                  detail="A preview appears when the agent serves the project or shares a previewable document."
                  loading={ide.isStreaming}
                />
              )
            ) : view === "terminal" ? (
              <div className="flex min-h-0 flex-1 flex-col p-3">
                <Terminal
                  className="ide-glass-inset min-h-0 flex-1 overflow-hidden rounded-xl border"
                  isStreaming={ide.isStreaming}
                  onClear={() => setClearedThrough(ide.transcript.length)}
                  output={terminalOutput}
                >
                  <TerminalHeader className="ide-glass-edge border-b bg-black/30">
                    <TerminalTitle />
                    <TerminalActions>
                      <TerminalStatus>
                        <span className="size-1.5 animate-pulse rounded-full bg-emerald-400 motion-reduce:animate-none" />
                        running
                      </TerminalStatus>
                      <TerminalCopyButton aria-label="Copy terminal output" />
                      <TerminalClearButton aria-label="Clear terminal output" />
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
                    {selected ? (
                      <span className="ml-auto rounded-full border border-blurple/25 bg-blurple/15 px-2.5 py-0.5 font-mono text-[10px] font-medium text-blurple-bright">
                        {languageForPath(selected.path)}
                      </span>
                    ) : null}
                    {/* Source/Rendered toggle for previewable files (HTML in a
                        sandboxed iframe, Markdown via Streamdown, static JSX
                        via JSXPreview). Only offered when source is held. */}
                    {selected && selectedPreviewKind && source ? (
                      <button
                        aria-label={
                          renderingFile ? "Show source" : selectedPreviewKind === "html" ? "HTML Preview" : selectedPreviewKind === "markdown" ? "Markdown Preview" : "JSX Preview"
                        }
                        aria-pressed={renderingFile}
                        className={cn(
                          "flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-medium transition-all duration-200",
                          renderingFile
                            ? "border-blurple/40 bg-blurple/30 text-white"
                            : "border-white/10 bg-white/[0.04] text-muted-foreground hover:text-foreground"
                        )}
                        data-testid="ide-file-render-toggle"
                        onClick={() =>
                          setRenderFile({
                            on: !renderingFile,
                            at: ide.activityId,
                          })
                        }
                        type="button"
                      >
                        <EyeIcon className="size-3" />
                        {renderingFile ? "Source" : selectedPreviewKind === "html" ? "HTML Preview" : selectedPreviewKind === "markdown" ? "Markdown Preview" : "JSX Preview"}
                      </button>
                    ) : null}
                  </div>
                  {source && renderingFile && selected ? (
                    <FilePreviewBody path={selected.path} source={source} />
                  ) : source ? (
                    <CodeBlock
                      className="ide-code-surface min-h-0 flex-1 overflow-auto rounded-none border-0"
                      code={source}
                      language={languageForPath(selected?.path ?? "")}
                      showLineNumbers
                    />
                  ) : !selected ? (
                    <EmptyPane
                      testId="ide-code-empty"
                      title="No files yet"
                      detail="Files appear here as the agent writes them."
                      loading={ide.isStreaming}
                    />
                  ) : remoteForSelected?.state === "loading" ? (
                    <EmptyPane
                      testId="ide-code-loading"
                      title={`Loading ${selected.path}…`}
                      loading
                    />
                  ) : remoteForSelected?.state === "error" ? (
                    <EmptyPane
                      testId="ide-code-error"
                      title="Couldn't load this file"
                      detail="The shared file could not be fetched from the orchestrator."
                    />
                  ) : (
                    <EmptyPane
                      testId="ide-code-unavailable"
                      title="Current content not available"
                      detail="This file changed in the sandbox and its latest content was not sent back. The Terminal log shows exactly what the tool reported."
                      loading={ide.isStreaming}
                    />
                  )}
                </Frame>
              </div>
            )}
          </div>
        </div>
      </ArtifactContent>
    </Artifact>
  )
}
