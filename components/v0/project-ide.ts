// Pure helpers for the project Artifact IDE: file tree + code + terminal +
// web-preview. Mirrors computer-use.ts — no React, no DOM. Vitest covers
// this module; project-ide-panel.tsx composes the AI Elements natives.

import { isDynamicToolUIPart, type UIMessage } from "ai"
import { shellFileWrites } from "./shell-file-writes"
export { shellFileWrites, type ShellFileWrite } from "./shell-file-writes"

import {
  nativeFromRunEvent,
  proxyFileUrl,
  type AgentRunSnapshot,
} from "./agent-run"

export type ProjectIdeFile = {
  path: string
  contents: string
  url: string
  source:
    | "sandbox_write_file"
    | "sandbox_read_file"
    | "sandbox_edit_file"
    | "sandbox_apply_patch"
    | "share_file"
    | "file_write"
    | "editor"
    | "write_file"
    | "read_file"
    | "shell_write"
}

// One honest entry in the operation log: what the agent actually ran or
// touched, with the tool's own reported output — never fabricated stdout.
export type ProjectIdeOperationKind =
  | "command"
  | "write"
  | "edit"
  | "patch"
  | "read"
  | "share"

export type ProjectIdeOperationStatus = "running" | "success" | "error"

export type ProjectIdeOperation = {
  /** Stable per operation: toolCallId or the native payload's call_id. */
  id: string
  kind: ProjectIdeOperationKind
  /** The command line for commands; the file path for file operations. */
  label: string
  /** The tool's actual reported output/result/error text. */
  output: string
  status: ProjectIdeOperationStatus
}

export type ProjectIdeView = "code" | "terminal" | "preview"

export type ProjectIdePreview = {
  open: boolean
  sessionId: string
  files: ProjectIdeFile[]
  selectedPath: string
  previewUrl: string
  htmlDocument: string
  command: string
  stdout: string
  /** Ordered honest operation log (commands + file operations). */
  transcript: ProjectIdeOperation[]
  /** Which main-area view the latest activity engages. */
  activeView?: ProjectIdeView
  /** Stable id of the currently-active (else latest) operation. */
  activityId?: string
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
  transcript: [],
  isStreaming: false,
  isDevServer: false,
}

// Official community tool names from orchestrator/strands-tools TOOL_SPEC.
const FILE_WRITE_TOOLS = new Set(["file_write"])
const EDITOR_TOOLS = new Set(["editor"])
const SHELL_TOOLS = new Set(["shell"])
// strands-shell MCP server tools (orchestrator/mcp.json "shell" server,
// schemas verified live): write_file/read_file take `file_path`, not `path`.
const MCP_WRITE_TOOLS = new Set(["write_file"])
const MCP_READ_TOOLS = new Set(["read_file"])

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

// Verified transport reality (read-only audit of orchestrator/route.ts and
// the shell MCP/community TOOL_SPECs): tools report their output ONCE, when
// they finish — there is no per-byte live stdout stream. The transcript
// therefore labels in-flight work as waiting, never pretends to tail output.
const RUNNING_LINE = "[running] waiting for the tool to finish…"

/** Render the honest operation log as terminal text: `$` prefixes real
 *  commands, bracketed verbs mark file operations, outputs are the tools'
 *  own final reports (verbatim), and in-flight work says it is waiting —
 *  never a fabricated live tail. */
export function formatTranscript(ops: ProjectIdeOperation[]): string {
  const blocks: string[] = []
  for (const op of ops) {
    const lines = [
      op.kind === "command" ? `$ ${op.label}` : `[${op.kind}] ${op.label}`,
    ]
    if (op.status === "error") {
      lines.push(op.output ? `[failed] ${op.output}` : "[failed]")
    } else if (op.status === "running") {
      // Partial output can exist (sandbox_results snapshots); show what the
      // tool actually reported so far, then the honest waiting line.
      if (op.output) lines.push(op.output)
      lines.push(RUNNING_LINE)
    } else if (op.output) {
      lines.push(op.output)
    }
    blocks.push(lines.join("\n"))
  }
  return blocks.join("\n\n")
}

/** The main-area view the panel should show: the user's explicit choice wins
 *  until the NEXT real activity (a new activityId) arrives, then the stream's
 *  own activeView re-engages. Pure so the expiry rule is unit-testable. */
export function resolveIdeView(options: {
  auto: ProjectIdeView
  override: ProjectIdeView | null
  overrideAt: string | undefined
  activityId: string | undefined
}): { view: ProjectIdeView; overrideExpired: boolean } {
  const { auto, override, overrideAt, activityId } = options
  if (!override) return { view: auto, overrideExpired: false }
  if (overrideAt !== activityId) return { view: auto, overrideExpired: true }
  return { view: override, overrideExpired: false }
}

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

// The strands-shell MCP read_file result prefixes every line with a
// right-aligned line number and a tab ("     1\thello"). Decode that known
// format back to file contents — only when every non-empty line matches, so
// arbitrary text is never mangled.
export function decodeNumberedLines(text: string): string {
  const lines = text.split("\n")
  const body = lines.filter((line) => line !== "")
  if (!body.length || !body.every((line) => /^\s*\d+\t/.test(line))) return text
  return lines.map((line) => line.replace(/^\s*\d+\t/, "")).join("\n")
}

function unwrapText(value: unknown): string {
  if (typeof value === "string") return value
  // app/api/orchestrator/route.ts's tool_results handler emits an ARRAY when
  // the Strands ToolResult carried more than one content block (community
  // shell: summary + per-command blocks; MCP shell: stdout + stderr blocks).
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === "string" ? item : unwrapText(item)))
      .filter(Boolean)
      .join("\n")
  }
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
  next: ProjectIdeFile,
  options?: { invalidate?: boolean }
) {
  const prev = files.get(next.path)
  files.set(next.path, {
    path: next.path,
    // A successful write/edit whose new content we do not hold makes any
    // previously-read contents stale — invalidate instead of showing them.
    contents:
      next.contents || (options?.invalidate ? "" : (prev?.contents ?? "")),
    // A mutation also makes any previously-shared artifact URL stale: fetching
    // it would present the PRE-edit file as current. Invalidation drops it; a
    // later share/read refills.
    url: next.url || (options?.invalidate ? "" : (prev?.url ?? "")),
    source: next.source,
  })
}

function nativeData(part: UIMessage["parts"][number]): Record<string, unknown> | null {
  if (part.type !== "data-native-tool") return null
  const data = (part as { data?: unknown }).data
  return asRecord(data)
}

function agentRunData(part: UIMessage["parts"][number]): AgentRunSnapshot | null {
  if (part.type !== "data-agent-run") return null
  const data = (part as { data?: unknown }).data
  if (!data || typeof data !== "object" || Array.isArray(data)) return null
  const snapshot = data as AgentRunSnapshot
  return Array.isArray(snapshot.events) ? snapshot : null
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
  const transcript: ProjectIdeOperation[] = []
  let sessionId = ""
  let nativeSeq = 0

  const rememberId = (id: string) => {
    if (!sessionId && id) sessionId = id
  }

  // Upsert by op id: progress snapshots of the SAME operation (a native
  // sandbox call_id emitting in_progress then completed, or a dynamic tool
  // part observed running then finished) replace the earlier entry in place
  // instead of leaving a "running" ghost behind the finished one.
  const opIndex = new Map<string, number>()
  const record = (op: ProjectIdeOperation) => {
    const at = opIndex.get(op.id)
    if (at !== undefined) {
      transcript[at] = op
    } else {
      opIndex.set(op.id, transcript.length)
      transcript.push(op)
    }
  }

  const ingestCommand = (command: string, stdout: string, streaming: boolean) => {
    if (!command && !stdout) return
    if (command) cmd.command = command
    if (stdout) cmd.stdout = cmd.stdout ? `${cmd.stdout}\n${stdout}` : stdout
    if (isDevServerCommand(command)) cmd.isDevServer = true
    const found = extractLocalhostUrl(`${command}\n${stdout}`)
    if (found) cmd.previewUrl = found
    if (streaming) cmd.isStreaming = true
  }

  // Failed/denied writes deliberately do NOT touch the file map: a path we
  // never knew about stays out (the operation produced no file we can
  // honestly show), and a path we DID know keeps its previous contents —
  // the failed or denied operation never executed, so what we held is still
  // accurate. Each failure branch below documents this with a comment
  // instead of calling a no-op helper.

  // One native Agent API output item (outer data-native-tool part or an item
  // inside a nested agent run). Field names are the Agent API's own — see the
  // OutputItem union in perplexity/types/output_item.py.
  const ingestNative = (native: Record<string, unknown>, idPrefix: string) => {
    const type = stringField(native, "type")
    const callId = stringField(native, "call_id")
    if (callId) rememberId(callId)
    const error = stringField(native, "error")
    const opId = callId
      ? `${idPrefix}${type}-${callId}`
      : `${idPrefix}${type}-${nativeSeq++}`

    if (type === "sandbox_write_file") {
      const path = stringField(native, "file_path")
      if (!path) return
      if (error) {
        // Failed write: file map untouched (see failure-semantics note above).
      } else {
        // A successful sandbox write replaces the file with content we do not
        // hold — join the tree and invalidate any stale earlier read.
        upsertFile(
          files,
          { path, contents: "", url: "", source: "sandbox_write_file" },
          { invalidate: true }
        )
      }
      record({
        id: opId,
        kind: "write",
        label: path,
        output: error,
        status: error ? "error" : "success",
      })
    } else if (type === "sandbox_read_file") {
      const path = stringField(native, "file_path")
      if (!path) return
      if (!error) {
        upsertFile(files, {
          path,
          contents: stringField(native, "content"),
          url: "",
          source: "sandbox_read_file",
        })
      }
      record({
        id: opId,
        kind: "read",
        label: path,
        output: error,
        status: error ? "error" : "success",
      })
    } else if (type === "sandbox_edit_file") {
      // Edits report only path + message (content lives on disk in the
      // sandbox). A successful edit makes any contents we held stale —
      // invalidate them; a later read/share refills. A failed edit left the
      // file untouched, so held contents stay.
      const path = stringField(native, "file_path")
      if (path && !error) {
        upsertFile(
          files,
          { path, contents: "", url: "", source: "sandbox_edit_file" },
          { invalidate: true }
        )
      }
      record({
        id: opId,
        kind: "edit",
        label: path,
        output: error || stringField(native, "message"),
        status: error ? "error" : "success",
      })
    } else if (type === "sandbox_apply_patch") {
      // apply_patch touches added/modified/deleted path lists. Added and
      // modified files join the tree (modified contents invalidate); deleted
      // paths drop out so the IDE never shows a file that no longer exists.
      const lists: Record<"added" | "modified" | "deleted", string[]> = {
        added: [],
        modified: [],
        deleted: [],
      }
      for (const key of ["added", "modified", "deleted"] as const) {
        const list = native[key]
        if (!Array.isArray(list)) continue
        for (const item of list) {
          if (typeof item === "string" && item) lists[key].push(item)
        }
      }
      if (!error) {
        for (const path of lists.added) {
          upsertFile(files, { path, contents: "", url: "", source: "sandbox_apply_patch" })
        }
        for (const path of lists.modified) {
          upsertFile(
            files,
            { path, contents: "", url: "", source: "sandbox_apply_patch" },
            { invalidate: true }
          )
        }
        for (const path of lists.deleted) files.delete(path)
      }
      const summary = (["added", "modified", "deleted"] as const)
        .filter((key) => lists[key].length)
        .map((key) => `${key}: ${lists[key].join(", ")}`)
        .join("\n")
      record({
        id: opId,
        kind: "patch",
        label: [...lists.added, ...lists.modified, ...lists.deleted].join(", "),
        output: error || summary,
        status: error ? "error" : "success",
      })
    } else if (type === "share_file") {
      const path = stringField(native, "filename")
      const url = stringField(native, "url")
      if (path && !error) {
        upsertFile(files, { path, contents: "", url, source: "share_file" })
        if (url && PREVIEWABLE.test(path) && !cmd.previewUrl) {
          cmd.previewUrl = url
        }
      }
      if (path || error) {
        record({
          id: opId,
          kind: "share",
          label: path,
          output: error,
          status: error ? "error" : "success",
        })
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
      const status = stringField(native, "status")
      const streaming = status === "in_progress"
      if (language === "bash" || isDevServerCommand(code)) {
        if (projectShellWrites(code, opId, output, streaming ? "running" : status === "completed" ? "success" : "error")) return
        ingestCommand(code, output, streaming)
        record({
          id: opId,
          kind: "command",
          label: code,
          output,
          status: streaming
            ? "running"
            : status === "completed"
              ? "success"
              : "error",
        })
      }
    } else if (type === "mcp_call") {
      // mcp_call carries the remote tool's own name + JSON-encoded arguments
      // (McpCallOutputItem). When that tool is one this projection already
      // understands (strands-shell write_file/read_file/shell, or community
      // names), route it through the shared projection instead of treating
      // every call as a bare command.
      const mcpId = `${idPrefix}mcp-${stringField(native, "id") || nativeSeq++}`
      const args = asRecord(native.arguments)
      const name = stringField(native, "name")
      const output = stringField(native, "output") || unwrapText(native.output)
      const projected =
        args !== null &&
        projectToolCall(name, args, {
          id: mcpId,
          failed: Boolean(error),
          succeeded: !error,
          running: false,
          errorText: error,
          outputText: error ? "" : output,
        })
      if (!projected) {
        // Only a genuinely command-shaped argument (a `command`/`cmd` string
        // field) may surface as a command — plus, for SHELL-NAMED tools only,
        // a malformed raw-string arguments payload. NEVER fabricate one from
        // raw arguments of other tools: a data tool called with '{}'
        // (confirmed in-browser: DataCommons get_child_observations /
        // get_variable_metadata) would log "$ {}" and dump its JSON result
        // into the terminal. Command-less MCP calls are not project
        // operations — AgentActivity owns their display.
        const isShellName = SHELL_TOOLS.has(name)
        const command =
          flattenShellCommand(args?.command) ||
          flattenShellCommand(args?.cmd) ||
          (isShellName && typeof native.arguments === "string"
            ? native.arguments
            : "")
        if (command) {
          // Only genuine shell executions feed the dev-server/localhost
          // inference; an unknown remote tool's output is arbitrary data and
          // must not hijack the preview with an incidental localhost URL.
          if (isShellName) {
            ingestCommand(command, error ? "" : output, false)
          }
          record({
            id: mcpId,
            kind: "command",
            label: command,
            output: error || output,
            status: error ? "error" : "success",
          })
        }
      }
    }
  }

  // Shared projection for one client-executed tool call, whether it arrives
  // as a dynamic tool part, wrapped in mcp_client(action="call_tool")
  // (tool_name + tool_args, per the official mcp_client TOOL_SPEC), or as a
  // native mcp_call output item (name + arguments). Returns false when the
  // tool name is not one this projection understands.
  function projectShellWrites(command: string, id: string, output: string, status: ProjectIdeOperationStatus): boolean {
    const writes = shellFileWrites(command)
    if (!writes.length) return false
    for (const [index, write] of writes.entries()) {
      if (status !== "error") {
        const previous = files.get(write.path)
        const contents = write.contents === null ? "" : write.append
          ? previous?.contents ? previous.contents + write.contents : ""
          : write.contents
        upsertFile(files, { path: write.path, contents, url: "", source: "shell_write" }, { invalidate: true })
      }
      record({ id: `${id}:write:${index}`, kind: "write", label: write.path, output, status })
    }
    return true
  }

  function projectToolCall(
    toolName: string,
    input: Record<string, unknown> | null,
    op: {
      id: string
      failed: boolean
      succeeded: boolean
      running: boolean
      errorText: string
      outputText: string
    }
  ): boolean {
    const { id, failed, succeeded, running, errorText, outputText } = op
    const opStatus: ProjectIdeOperationStatus = failed
      ? "error"
      : succeeded
        ? "success"
        : "running"

    if (FILE_WRITE_TOOLS.has(toolName) || MCP_WRITE_TOOLS.has(toolName)) {
      // Community file_write uses `path`; the strands-shell MCP server's
      // write_file uses `file_path`. Both carry `content`.
      const isMcp = MCP_WRITE_TOOLS.has(toolName)
      const path = stringField(input, isMcp ? "file_path" : "path")
      if (path) {
        if (failed) {
          // Failed/denied write: file map untouched (failure-semantics note).
        } else {
          // The write's own content is authoritative — an empty `content`
          // means the file was truncated to empty, so invalidate any prior
          // contents (and stale share URL) instead of preserving them.
          upsertFile(
            files,
            {
              path,
              contents: stringField(input, "content"),
              url: "",
              source: isMcp ? "write_file" : "file_write",
            },
            { invalidate: true }
          )
        }
        record({
          id,
          kind: "write",
          label: path,
          output: failed ? errorText : outputText,
          status: opStatus,
        })
      }
      return true
    }

    if (MCP_READ_TOOLS.has(toolName)) {
      const path = stringField(input, "file_path")
      if (path) {
        if (succeeded && outputText) {
          upsertFile(files, {
            path,
            contents: decodeNumberedLines(outputText),
            url: "",
            source: "read_file",
          })
        }
        record({
          id,
          kind: "read",
          label: path,
          output: failed ? errorText : "",
          status: opStatus,
        })
      }
      return true
    }

    if (EDITOR_TOOLS.has(toolName)) {
      const path = stringField(input, "path")
      const command = stringField(input, "command")
      const mutating =
        command === "create" ||
        command === "str_replace" ||
        command === "insert" ||
        command === "pattern_replace"
      if (path && mutating) {
        if (failed) {
          // A failed create produced no file (map untouched keeps it out); a
          // failed str_replace/insert left the file as-is, so held contents
          // stay accurate. Failure-semantics note above.
        } else if (command === "create") {
          // create replaces the whole file: its file_text is authoritative,
          // even when empty — invalidate prior contents and stale share URL.
          upsertFile(
            files,
            {
              path,
              contents: stringField(input, "file_text"),
              url: "",
              source: "editor",
            },
            { invalidate: true }
          )
        } else if (succeeded && command === "str_replace") {
          // Verified tool semantics: content.replace(old_str, new_str)
          // replaces EVERY occurrence. Apply only when we hold contents that
          // contain old_str; otherwise the held contents were already stale —
          // invalidate rather than invent.
          const prev = files.get(path)
          const oldStr = stringField(input, "old_str")
          const newStr = input?.new_str
          if (
            prev?.contents &&
            oldStr &&
            typeof newStr === "string" &&
            prev.contents.includes(oldStr)
          ) {
            upsertFile(files, {
              path,
              contents: prev.contents.split(oldStr).join(newStr),
              url: "",
              source: "editor",
            })
          } else {
            upsertFile(
              files,
              { path, contents: "", url: "", source: "editor" },
              { invalidate: true }
            )
          }
        } else if (succeeded) {
          // insert / pattern_replace succeeded: the file changed in ways we
          // cannot honestly reconstruct — join the tree, drop stale contents.
          upsertFile(
            files,
            { path, contents: "", url: "", source: "editor" },
            { invalidate: true }
          )
        } else {
          // Still running: tree membership only; the edit has not landed, so
          // previously-held contents are still accurate.
          upsertFile(files, { path, contents: "", url: "", source: "editor" })
        }
        record({
          id,
          kind: command === "create" ? "write" : "edit",
          label: path,
          output: failed ? errorText : outputText,
          status: opStatus,
        })
      }
      return true
    }

    if (SHELL_TOOLS.has(toolName)) {
      // Community shell (command: string|string[]|{command}[]) and the
      // strands-shell MCP server's shell ({command: string}) share the name
      // and both flatten here.
      const command = flattenShellCommand(input?.command)
      if (projectShellWrites(command, id, failed ? errorText : outputText, opStatus)) return true
      ingestCommand(command, outputText, running)
      if (command) {
        record({
          id,
          kind: "command",
          label: command,
          output: failed ? errorText : outputText,
          status: opStatus,
        })
      }
      return true
    }

    return false
  }

  for (const part of parts) {
    const native = nativeData(part)
    if (native) {
      ingestNative(native, "")
      continue
    }

    // Nested Agent API preset runs: the reconciled data-agent-run snapshot
    // carries the run's verbatim stream events. Recognized native output
    // items (nativeFromRunEvent — the same projection agent-run.ts uses for
    // its own timeline) feed the identical ingestion path. share_file URLs
    // are the API's relative paths; proxy them the same way route.ts does
    // for the outer turn's parts.
    const run = agentRunData(part)
    if (run) {
      for (const { event } of run.events) {
        const item = nativeFromRunEvent(event)
        if (!item) continue
        const proxied =
          item.type === "share_file" && typeof item.url === "string"
            ? { ...item, url: proxyFileUrl(item.url) }
            : item
        ingestNative(proxied, `run-${run.activityId}-`)
      }
      continue
    }

    if (!isDynamicToolUIPart(part)) continue
    rememberId(part.toolCallId)
    const input = asRecord(part.input)
    const running =
      part.state === "input-streaming" ||
      part.state === "input-available" ||
      part.state === "approval-requested"
    const reportedOutput = "output" in part ? asRecord(part.output) : null
    const failed = part.state === "output-error" || part.state === "output-denied" ||
      reportedOutput?.status === "error" || reportedOutput?.isError === true
    // A part can reach output-available with a tool-reported {status:"error"}
    // payload; that is a FAILURE. succeeded must exclude it, or the read_file
    // branch would store the error text as file contents.
    const succeeded = part.state === "output-available" && !failed
    const errorText =
      "errorText" in part && typeof part.errorText === "string"
        ? part.errorText
        : part.state === "output-denied" ? "Tool execution denied" : unwrapText(reportedOutput)
    const outputText = succeeded
      ? unwrapText("output" in part ? part.output : undefined)
      : ""
    const opState = {
      id: part.toolCallId,
      failed,
      succeeded,
      running,
      errorText,
      outputText,
    }

    if (projectToolCall(part.toolName, input, opState)) continue

    if (part.toolName === "mcp_client") {
      // Official mcp_client TOOL_SPEC (strands_tools.mcp_client): call_tool
      // carries tool_name + tool_args. The wrapped remote tool goes through
      // the same projection as a directly-registered tool of that name;
      // unknown remote tools with a command-shaped argument still surface as
      // commands.
      const action = stringField(input, "action")
      if (action === "call_tool") {
        const toolName = stringField(input, "tool_name")
        const args = asRecord(input?.tool_args) ?? asRecord(input?.arguments)
        if (toolName && projectToolCall(toolName, args, opState)) continue
        // Same rule as native mcp_call: only a command-shaped argument
        // (`command`/`cmd` string field) surfaces as a command; wrapped data
        // tools without one are not project operations. Everything reaching
        // here is non-shell (shell names went through projectToolCall), so
        // the command is recorded but its arbitrary output is never scanned
        // for localhost URLs / dev-server inference.
        const command =
          flattenShellCommand(args?.command) || flattenShellCommand(args?.cmd)
        if (command) {
          record({
            id: part.toolCallId,
            kind: "command",
            label: command,
            output: failed ? errorText : outputText,
            status: failed ? "error" : succeeded ? "success" : "running",
          })
        }
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

  const open =
    fileList.length > 0 ||
    Boolean(cmd.command) ||
    Boolean(cmd.previewUrl) ||
    cmd.isDevServer ||
    Boolean(htmlDocument) ||
    transcript.length > 0

  if (!open) return EMPTY

  // Focus follows the LATEST recorded operation. record() upserts progress
  // snapshots in place, so transcript order is creation order and lastOp is
  // the newest real activity. Keying focus off "last running op" instead
  // would snap BACKWARDS to an older long-running operation the moment a
  // newer one completed — regressing activityId and expiring the user's
  // manual choice without any new activity.
  const lastOp = transcript.at(-1)
  const anyRunning = transcript.some((op) => op.status === "running")
  const activityId = lastOp?.id
  const focusOp = lastOp
  const selectedPath = files.has(focusOp?.label ?? "")
    ? focusOp!.label
    : [...transcript].reverse().find((op) => files.has(op.label))?.label ?? fileList.at(-1)?.path ?? ""
  // Active-tool view selection: a RUNNING command engages the terminal (its
  // output is what is happening right now, even for a dev server that already
  // printed its URL); a completed command hands off to the preview ONLY when
  // that command itself produced it (dev-server command or a localhost URL in
  // its own output) — a completed `ls` must not switch to an older HTML
  // preview. File operations engage the editor; with only shares, a
  // previewable artifact engages the preview.
  const commandMadePreview =
    focusOp?.kind === "command" &&
    (isDevServerCommand(focusOp.label) ||
      Boolean(extractLocalhostUrl(focusOp.output)))
  const activeView: ProjectIdeView =
    focusOp?.kind === "command"
      ? focusOp.status !== "running" && commandMadePreview && cmd.previewUrl
        ? "preview"
        : "terminal"
      : focusOp && focusOp.kind !== "share"
        ? "code"
        : cmd.previewUrl || htmlDocument ? "preview" : "code"

  return {
    open,
    sessionId: sessionId || "project-ide",
    files: fileList,
    selectedPath,
    previewUrl: cmd.previewUrl,
    htmlDocument,
    command: cmd.command,
    stdout: cmd.stdout,
    transcript,
    activeView,
    activityId,
    isStreaming: isStreaming && anyRunning,
    isDevServer: cmd.isDevServer,
  }
}
