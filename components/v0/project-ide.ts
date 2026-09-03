// Pure helpers for the project Artifact IDE: file tree + code + terminal +
// web-preview. Mirrors computer-use.ts — no React, no DOM. Vitest covers
// this module; project-ide-panel.tsx composes the AI Elements natives.

import { isDynamicToolUIPart, type UIMessage } from "ai"

export type ProjectIdeFile = {
  path: string
  contents: string
  url: string
  source: "sandbox_write_file" | "sandbox_read_file" | "share_file" | "file_write" | "editor"
}

export type ProjectIdePreview = {
  open: boolean
  sessionId: string
  files: ProjectIdeFile[]
  selectedPath: string
  previewUrl: string
  htmlDocument: string
  command: string
  stdout: string
  isStreaming: boolean
  isDevServer: boolean
}

const EMPTY: ProjectIdePreview = {
  open: false,
  sessionId: "",
  files: [],
  selectedPath: "",
  previewUrl: "",
  htmlDocument: "",
  command: "",
  stdout: "",
  isStreaming: false,
  isDevServer: false,
}

// Official community tool names from orchestrator/strands-tools TOOL_SPEC.
const FILE_WRITE_TOOLS = new Set(["file_write"])
const EDITOR_TOOLS = new Set(["editor"])
const SHELL_TOOLS = new Set(["shell"])

const PREVIEWABLE = /\.(html?|pdf)$/i
const HTML_FILE = /\.html?$/i

// npm run, pnpm dev, pnpm run, yarn/bun/vite/next, python -m http.server.
export const DEV_SERVER_COMMAND =
  /\b(?:npm|pnpm|yarn)\s+run(?:\s+\S+)?|\b(?:npm|pnpm|yarn|bun)\s+(?:dev|start|preview|serve)\b|\b(?:npx|bunx)\s+\S+|\bvite\b|\bnext\s+dev\b|\bpython(?:3)?\s+-m\s+http\.server\b/i

const LOCALHOST_URL =
  /https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d+\/?[^\s"'<>)\]]*/gi

export type FileTreeNode =
  | { kind: "folder"; name: string; path: string; children: FileTreeNode[] }
  | { kind: "file"; name: string; path: string }

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null
    try {
      const parsed: unknown = JSON.parse(trimmed)
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
    } catch {
      return null
    }
  }
  return null
}

function stringField(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key]
  return typeof value === "string" ? value : ""
}

/** Flatten `shell` TOOL_SPEC command: string | string[] | {command}[]. */
export function flattenShellCommand(command: unknown): string {
  if (typeof command === "string") return command
  if (Array.isArray(command)) {
    return command
      .map((item) =>
        typeof item === "string" ? item : stringField(asRecord(item), "command")
      )
      .filter(Boolean)
      .join("\n")
  }
  return stringField(asRecord(command), "command")
}

export function isDevServerCommand(command: string): boolean {
  return DEV_SERVER_COMMAND.test(command)
}

export function extractLocalhostUrl(text: string): string {
  const matches = text.match(LOCALHOST_URL)
  if (!matches?.length) return ""
  return matches[matches.length - 1].replace(
    /^(https?:\/\/)0\.0\.0\.0/i,
    "$1localhost"
  )
}

function unwrapText(value: unknown): string {
  if (typeof value === "string") return value
  const record = asRecord(value)
  if (!record) return ""
  if (Array.isArray(record.content)) {
    const texts: string[] = []
    for (const block of record.content) {
      const nested = asRecord(block)
      if (typeof nested?.text === "string") texts.push(nested.text)
    }
    if (texts.length) return texts.join("\n")
  }
  if (typeof record.text === "string") return record.text
  if (typeof record.output === "string") return record.output
  if (typeof record.stdout === "string") return record.stdout
  return ""
}

function upsertFile(
  files: Map<string, ProjectIdeFile>,
  next: ProjectIdeFile
) {
  const prev = files.get(next.path)
  files.set(next.path, {
    path: next.path,
    contents: next.contents || prev?.contents || "",
    url: next.url || prev?.url || "",
    source: next.source,
  })
}

function nativeData(part: UIMessage["parts"][number]): Record<string, unknown> | null {
  if (part.type !== "data-native-tool") return null
  const data = (part as { data?: unknown }).data
  return asRecord(data)
}

function ingestCommand(
  state: {
    command: string
    stdout: string
    isDevServer: boolean
    isStreaming: boolean
    previewUrl: string
  },
  command: string,
  stdout: string,
  streaming: boolean
) {
  if (!command && !stdout) return
  if (command) state.command = command
  if (stdout) state.stdout = state.stdout ? `${state.stdout}\n${stdout}` : stdout
  if (isDevServerCommand(command)) state.isDevServer = true
  const found = extractLocalhostUrl(`${command}\n${stdout}`)
  if (found) state.previewUrl = found
  if (streaming) state.isStreaming = true
}

/** Nested FileTree nodes from project paths (folders first, then files). */
export function fileTreeNodes(paths: string[]): FileTreeNode[] {
  type FolderAcc = {
    kind: "folder"
    name: string
    path: string
    children: Map<string, FolderAcc | { kind: "file"; name: string; path: string }>
  }
  type FileAcc = { kind: "file"; name: string; path: string }
  const root = new Map<string, FolderAcc | FileAcc>()

  for (const raw of paths) {
    const parts = raw.replace(/^\.?\//, "").split("/").filter(Boolean)
    let cursor = root
    let prefix = ""
    for (let i = 0; i < parts.length; i++) {
      const name = parts[i]
      prefix = prefix ? `${prefix}/${name}` : name
      if (i === parts.length - 1) {
        cursor.set(name, { kind: "file", name, path: raw })
      } else {
        const existing = cursor.get(name)
        let folder: FolderAcc
        if (existing && existing.kind === "folder") {
          folder = existing
        } else {
          folder = { kind: "folder", name, path: prefix, children: new Map() }
          cursor.set(name, folder)
        }
        cursor = folder.children
      }
    }
  }

  const toArray = (
    map: Map<string, FolderAcc | FileAcc>
  ): FileTreeNode[] =>
    [...map.values()]
      .sort((a, b) =>
        a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "folder" ? -1 : 1
      )
      .map((node) =>
        node.kind === "folder"
          ? { kind: "folder", name: node.name, path: node.path, children: toArray(node.children) }
          : node
      )

  return toArray(root)
}

export function folderPaths(nodes: FileTreeNode[]): string[] {
  const out: string[] = []
  const walk = (list: FileTreeNode[]) => {
    for (const node of list) {
      if (node.kind === "folder") {
        out.push(node.path)
        walk(node.children)
      }
    }
  }
  walk(nodes)
  return out
}

/** Latest project IDE state for the conversation. Opens when files, a
 *  shell command, or a previewable document/dev-server URL is present. */
export function projectIdePreview(
  parts: UIMessage["parts"] | undefined,
  isStreaming: boolean
): ProjectIdePreview {
  if (!parts?.length) return EMPTY

  const files = new Map<string, ProjectIdeFile>()
  const cmd = {
    command: "",
    stdout: "",
    isDevServer: false,
    isStreaming: false,
    previewUrl: "",
  }
  let sessionId = ""

  const rememberId = (id: string) => {
    if (!sessionId && id) sessionId = id
  }

  for (const part of parts) {
    const native = nativeData(part)
    if (native) {
      const type = stringField(native, "type")
      const callId = stringField(native, "call_id")
      if (callId) rememberId(callId)

      if (type === "sandbox_write_file") {
        const path = stringField(native, "file_path")
        if (path) {
          upsertFile(files, {
            path,
            contents: "",
            url: "",
            source: "sandbox_write_file",
          })
        }
      } else if (type === "sandbox_read_file") {
        const path = stringField(native, "file_path")
        if (path) {
          upsertFile(files, {
            path,
            contents: stringField(native, "content"),
            url: "",
            source: "sandbox_read_file",
          })
        }
      } else if (type === "share_file") {
        const path = stringField(native, "filename")
        const url = stringField(native, "url")
        if (path) {
          upsertFile(files, {
            path,
            contents: "",
            url,
            source: "share_file",
          })
          if (url && PREVIEWABLE.test(path) && !cmd.previewUrl) {
            cmd.previewUrl = url
          }
        }
      } else if (type === "sandbox_results") {
        const language = stringField(native, "language")
        const code = stringField(native, "code")
        const results = Array.isArray(native.results) ? native.results : []
        const output = results
          .map((row) => {
            const rec = asRecord(row)
            return [stringField(rec, "stdout"), stringField(rec, "stderr")]
              .filter(Boolean)
              .join("\n")
          })
          .filter(Boolean)
          .join("\n---\n")
        const streaming = stringField(native, "status") === "in_progress"
        if (language === "bash" || isDevServerCommand(code)) {
          ingestCommand(cmd, code, output, streaming)
        }
      } else if (type === "mcp_call") {
        const args = asRecord(native.arguments) ?? { raw: native.arguments }
        const command =
          flattenShellCommand(args.command) ||
          flattenShellCommand(args.cmd) ||
          (typeof native.arguments === "string" ? native.arguments : "")
        ingestCommand(
          cmd,
          command,
          stringField(native, "output") || unwrapText(native.output),
          false
        )
      }
      continue
    }

    if (!isDynamicToolUIPart(part)) continue
    rememberId(part.toolCallId)
    const input = asRecord(part.input)
    const streaming =
      part.state === "input-streaming" ||
      part.state === "input-available" ||
      part.state === "approval-requested"

    if (FILE_WRITE_TOOLS.has(part.toolName)) {
      const path = stringField(input, "path")
      if (path) {
        upsertFile(files, {
          path,
          contents: stringField(input, "content"),
          url: "",
          source: "file_write",
        })
      }
      continue
    }

    if (EDITOR_TOOLS.has(part.toolName)) {
      const path = stringField(input, "path")
      const command = stringField(input, "command")
      if (path && (command === "create" || command === "str_replace" || command === "insert")) {
        upsertFile(files, {
          path,
          contents: stringField(input, "file_text"),
          url: "",
          source: "editor",
        })
      }
      continue
    }

    if (SHELL_TOOLS.has(part.toolName)) {
      ingestCommand(
        cmd,
        flattenShellCommand(input?.command),
        unwrapText("output" in part ? part.output : undefined),
        streaming && part.state !== "output-available" && part.state !== "output-error"
      )
      continue
    }

    if (part.toolName === "mcp_client") {
      const action = stringField(input, "action")
      if (action === "call_tool") {
        const args = asRecord(input?.tool_args) ?? asRecord(input?.arguments)
        ingestCommand(
          cmd,
          flattenShellCommand(args?.command) || flattenShellCommand(args),
          unwrapText("output" in part ? part.output : undefined),
          streaming && part.state !== "output-available" && part.state !== "output-error"
        )
      }
    }
  }

  const fileList = [...files.values()]
  let htmlDocument = ""
  for (let i = fileList.length - 1; i >= 0; i--) {
    const file = fileList[i]
    if (HTML_FILE.test(file.path) && file.contents) {
      htmlDocument = file.contents
      break
    }
  }

  const selectedPath = fileList.at(-1)?.path ?? ""
  const open =
    fileList.length > 0 ||
    Boolean(cmd.command) ||
    Boolean(cmd.previewUrl) ||
    cmd.isDevServer ||
    Boolean(htmlDocument)

  if (!open) return EMPTY

  return {
    open,
    sessionId: sessionId || "project-ide",
    files: fileList,
    selectedPath,
    previewUrl: cmd.previewUrl,
    htmlDocument,
    command: cmd.command,
    stdout: cmd.stdout,
    isStreaming: cmd.isStreaming || (isStreaming && cmd.isDevServer),
    isDevServer: cmd.isDevServer,
  }
}
