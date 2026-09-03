"use client"

import { AppWindowIcon } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
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
import { Terminal, TerminalContent } from "@/components/ai-elements/terminal"
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

export type ProjectIdePanelProps = {
  ide: ProjectIdePreview
  previewForced?: boolean
  onClose: () => void
  className?: string
}

/**
 * Official AI Elements IDE composition:
 * FileTree | CodeBlock + Terminal
 * https://elements.ai-sdk.dev/examples/ide
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
  const [userPreview, setUserPreview] = useState<boolean | null>(null)
  const [fetched, setFetched] = useState("")

  useEffect(() => {
    setSelectedPath(ide.selectedPath)
    setExpandedPaths(defaultExpanded)
    setFetched("")
  }, [ide.sessionId, ide.selectedPath, defaultExpanded])

  const selected =
    ide.files.find((file) => file.path === selectedPath) ?? ide.files.at(-1)
  const autoPreview =
    previewForced ||
    ide.isDevServer ||
    Boolean(ide.previewUrl) ||
    Boolean(ide.htmlDocument)
  const showPreview = userPreview ?? autoPreview

  useEffect(() => {
    if (!selected?.url || selected.contents) {
      setFetched("")
      return
    }
    let cancelled = false
    fetch(selected.url)
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
  }, [selected?.url, selected?.contents, selected?.path])

  const source = selected?.contents || fetched
  const previewSrc = ide.previewUrl
  const previewDoc = !previewSrc ? ide.htmlDocument : ""
  const output = [ide.command && `$ ${ide.command}`, ide.stdout]
    .filter(Boolean)
    .join("\n")

  return (
    <Artifact
      data-testid="project-ide"
      className={cn("flex h-full min-h-0 flex-col", className)}
    >
      <ArtifactHeader>
        <ArtifactTitle>Project</ArtifactTitle>
        <ArtifactActions>
          <ArtifactAction
            data-testid="web-preview-button"
            tooltip="Web preview"
            label="Web preview"
            icon={AppWindowIcon}
            aria-pressed={showPreview}
            onClick={() => setUserPreview(!showPreview)}
          />
          <ArtifactClose onClick={onClose} />
        </ArtifactActions>
      </ArtifactHeader>
      <ArtifactContent className="flex min-h-0 flex-1 flex-col overflow-hidden p-0">
        <div className="flex h-full min-h-0 w-full bg-background">
          <div className="flex w-64 shrink-0 flex-col border-r">
            <div className="flex-1 overflow-auto p-1">
              <FileTree
                className="border-none"
                expanded={expandedPaths}
                onExpandedChange={setExpandedPaths}
                selectedPath={selected?.path}
                onSelect={setSelectedPath}
              >
                <TreeNodes nodes={tree} />
              </FileTree>
            </div>
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            {showPreview ? (
              <WebPreview
                key={previewSrc || "html-doc"}
                defaultUrl={previewSrc}
                className="min-h-0 flex-1 rounded-none border-0"
              >
                <WebPreviewNavigation>
                  <WebPreviewUrl />
                </WebPreviewNavigation>
                <WebPreviewBody
                  src={previewSrc || undefined}
                  srcDoc={previewDoc || undefined}
                />
              </WebPreview>
            ) : (
              <CodeBlock
                className="min-h-0 flex-1 overflow-auto rounded-none border-0"
                code={source}
                language={languageForPath(selected?.path ?? "")}
                showLineNumbers
              />
            )}
            <Terminal
              className="h-64 rounded-none border-0"
              isStreaming={ide.isStreaming}
              output={output}
            >
              <TerminalContent className="max-h-full" />
            </Terminal>
          </div>
        </div>
      </ArtifactContent>
    </Artifact>
  )
}
