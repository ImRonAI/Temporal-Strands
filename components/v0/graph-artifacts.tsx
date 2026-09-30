"use client"

import { CheckIcon, CopyIcon, NetworkIcon } from "lucide-react"
import { useState, type ReactNode } from "react"
import type { BundledLanguage } from "shiki"

import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactContent,
  ArtifactDescription,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact"
import { CodeBlock } from "@/components/ai-elements/code-block"
import { MessageResponse } from "@/components/ai-elements/message"
import type { GraphNodeState, GraphRunState } from "@/components/v0/graph-run"

// Every fenced code block inside a node's final output renders through the
// vendored CodeBlock; the prose around each fence stays markdown via
// MessageResponse so mixed output is never truncated. Fence tags map onto
// shiki BundledLanguage ids (same convention as CODE_EXTENSIONS in
// agent-activity.tsx) — unknown tags fall back to json, which shiki bundles.
const FENCE = /```(\w*)\n([\s\S]*?)```/g

const FENCE_LANGUAGES: Record<string, BundledLanguage> = {
  bash: "bash",
  css: "css",
  html: "html",
  javascript: "javascript",
  js: "javascript",
  json: "json",
  jsx: "jsx",
  markdown: "markdown",
  md: "markdown",
  py: "python",
  python: "python",
  sh: "bash",
  ts: "typescript",
  tsx: "tsx",
  typescript: "typescript",
}

function NodeOutput({ node }: { node: GraphNodeState }) {
  const blocks: ReactNode[] = []
  let lastIndex = 0
  let fenceIndex = 0
  for (const match of node.text.matchAll(FENCE)) {
    const matchIndex = match.index ?? 0
    const prose = node.text.slice(lastIndex, matchIndex)
    if (prose.trim()) {
      blocks.push(<MessageResponse key={`prose-${fenceIndex}`}>{prose}</MessageResponse>)
    }
    blocks.push(
      <CodeBlock
        key={`code-${fenceIndex}`}
        code={match[2]}
        language={FENCE_LANGUAGES[match[1]] ?? "json"}
      />
    )
    lastIndex = matchIndex + match[0].length
    fenceIndex++
  }
  const remaining = node.text.slice(lastIndex)
  if (remaining.trim()) {
    blocks.push(<MessageResponse key={`prose-${fenceIndex}`}>{remaining}</MessageResponse>)
  }
  return blocks.length > 0 ? <>{blocks}</> : <MessageResponse>{node.text}</MessageResponse>
}

/**
 * Completed-formation outputs as artifacts (GWEN-33), following the
 * ListFilesArtifacts composition pattern in agent-activity.tsx: native
 * Artifact subcomponents only. One artifact per node that produced output,
 * plus a summary artifact for the run's terminal result. File artifacts
 * produced by formation nodes travel through the existing share_file
 * pipeline (route.ts rewrites their URLs to /api/orchestrator/file?path=...)
 * and render via the pre-existing agent-activity surfaces — this component
 * does not open a second file-access path.
 */
export function GraphArtifacts({ run }: { run: GraphRunState }) {
  const [copied, setCopied] = useState<string | null>(null)
  if (run.status !== "done") return null

  const producing = run.nodes.filter((n) => n.text.trim().length > 0)

  if (producing.length === 0 && !run.resultText) return null

  const copy = (key: string, text: string) => {
    void navigator.clipboard.writeText(text)
    setCopied(key)
    setTimeout(() => setCopied(null), 1500)
  }

  return (
    <div className="space-y-2">
      {run.resultText && (
        <Artifact className="app-glass app-glass-edge border">
          <ArtifactHeader className="app-glass-edge bg-black/25">
            <div className="flex items-center gap-2">
              <NetworkIcon className="size-4 text-blurple-bright/80" />
              <ArtifactTitle>Formation result · {run.runId}</ArtifactTitle>
            </div>
            <ArtifactActions>
              <ArtifactAction
                icon={copied === "result" ? CheckIcon : CopyIcon}
                label="Copy result"
                onClick={() => copy("result", run.resultText ?? "")}
                tooltip="Copy result"
              />
            </ArtifactActions>
          </ArtifactHeader>
          <ArtifactContent>
            <ArtifactDescription>{run.resultText}</ArtifactDescription>
          </ArtifactContent>
        </Artifact>
      )}
      {producing.map((node) => (
        <Artifact
          key={node.id}
          className="app-glass app-glass-edge border"
        >
          <ArtifactHeader className="app-glass-edge bg-black/25">
            <div className="flex items-center gap-2">
              <NetworkIcon className="size-4 text-blurple-bright/80" />
              <ArtifactTitle>{node.id}</ArtifactTitle>
            </div>
            <ArtifactActions>
              <ArtifactAction
                icon={copied === node.id ? CheckIcon : CopyIcon}
                label={`Copy ${node.id} output`}
                onClick={() => copy(node.id, node.text)}
                tooltip="Copy output"
              />
            </ArtifactActions>
          </ArtifactHeader>
          <ArtifactContent>
            <NodeOutput node={node} />
          </ArtifactContent>
        </Artifact>
      ))}
    </div>
  )
}
