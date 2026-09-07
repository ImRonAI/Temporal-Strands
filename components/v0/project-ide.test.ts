import { describe, expect, it } from "vitest"

import type { DynamicToolUIPart, UIMessage } from "ai"

import {
  decodeNumberedLines,
  extractLocalhostUrl,
  fileTreeNodes,
  flattenShellCommand,
  formatTranscript,
  isDevServerCommand,
  projectIdePreview,
  resolveIdeView,
  shellFileWrites,
  type ProjectIdeOperation,
} from "./project-ide"

const tool = (
  toolName: string,
  overrides: Record<string, unknown> = {}
): DynamicToolUIPart =>
  ({
    type: "dynamic-tool",
    toolName,
    toolCallId: "io-1",
    state: "input-available",
    input: {},
    ...overrides,
  }) as unknown as DynamicToolUIPart

const native = (data: Record<string, unknown>): UIMessage["parts"][number] =>
  ({ type: "data-native-tool", id: "n-1", data }) as UIMessage["parts"][number]

describe("shell file writes engage Code", () => {
  const command = "cat > src/App.tsx <<'EOF'\nexport default function App() { return <h1>Hello</h1> }\nEOF"
  it("recovers literal heredoc contents and selects code, not terminal", () => {
    const ide = projectIdePreview([tool("shell", { input: { command }, state: "output-available", output: "" })], false)
    expect(ide.activeView).toBe("code")
    expect(ide.selectedPath).toBe("src/App.tsx")
    expect(ide.files[0].contents).toContain("<h1>Hello</h1>")
    expect(ide.transcript[0].kind).toBe("write")
  })
  it("handles native sandbox bash writes identically", () => {
    const ide = projectIdePreview([native({ type: "sandbox_results", language: "bash", code: command, status: "completed", results: [] })], false)
    expect(ide.activeView).toBe("code")
    expect(ide.files[0].contents).toContain("export default")
  })
  it("does not guess shell-expanded bodies or dynamic paths", () => {
    expect(shellFileWrites("cat > output.md <<EOF\n$SECRET\nEOF")[0].contents).toBeNull()
    expect(shellFileWrites("cat > $TARGET <<'EOF'\nhello\nEOF")).toEqual([])
    expect(shellFileWrites("python app.py")).toEqual([])
  })
  it("recognizes tee, literal echo redirection, and append", () => {
    expect(shellFileWrites("tee docs.md <<'EOF'\n# Docs\nEOF")[0]).toMatchObject({ path: "docs.md", contents: "# Docs\n" })
    expect(shellFileWrites("echo 'export const a = 1;' > a.ts")[0]).toMatchObject({ path: "a.ts", append: false })
    expect(shellFileWrites("cat >> a.ts <<'EOF'\nmore\nEOF")[0].append).toBe(true)
  })
  it("does not replace prior files on failed shell writes", () => {
    const ide = projectIdePreview([tool("write_file", { toolCallId: "a", input: { file_path: "src/App.tsx", content: "original" }, state: "output-available" }), tool("shell", { toolCallId: "b", input: { command }, state: "output-error", errorText: "denied" })], false)
    expect(ide.files[0].contents).toBe("original")
  })
})

describe("isDevServerCommand", () => {
  it("matches npm / pnpm / yarn / vite / next / http.server", () => {
    expect(isDevServerCommand("pnpm dev")).toBe(true)
    expect(isDevServerCommand("npm run dev")).toBe(true)
    expect(isDevServerCommand("pnpm run start")).toBe(true)
    expect(isDevServerCommand("yarn start")).toBe(true)
    expect(isDevServerCommand("npx vite")).toBe(true)
    expect(isDevServerCommand("next dev")).toBe(true)
    expect(isDevServerCommand("python3 -m http.server 8765")).toBe(true)
    expect(isDevServerCommand("ls -la")).toBe(false)
  })
})

describe("extractLocalhostUrl", () => {
  it("rewrites 0.0.0.0 to localhost and keeps the last match", () => {
    expect(
      extractLocalhostUrl("Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/)")
    ).toBe("http://localhost:8000/")
    expect(
      extractLocalhostUrl("Local: http://localhost:3000\n➜  Local: http://localhost:5173/")
    ).toBe("http://localhost:5173/")
  })
})

describe("flattenShellCommand", () => {
  it("accepts string, string[], and object[]", () => {
    expect(flattenShellCommand("pnpm dev")).toBe("pnpm dev")
    expect(flattenShellCommand(["cd app", "pnpm dev"])).toBe("cd app\npnpm dev")
    expect(
      flattenShellCommand([{ command: "npm run start", work_dir: "/tmp" }])
    ).toBe("npm run start")
  })
})

describe("fileTreeNodes", () => {
  it("nests folders and sorts folders before files", () => {
    const tree = fileTreeNodes(["index.html", "src/app.tsx", "src/lib/util.ts"])
    expect(tree.map((n) => n.name)).toEqual(["src", "index.html"])
    const src = tree[0]
    expect(src.kind).toBe("folder")
    if (src.kind === "folder") {
      expect(src.children.map((n) => n.name)).toEqual(["lib", "app.tsx"])
    }
  })
})

describe("projectIdePreview", () => {
  it("follows edits to older files instead of letting an HTML preview steal focus", () => {
    const preview = projectIdePreview([
      tool("write_file", { toolCallId: "a", state: "output-available", input: { file_path: "a.ts", content: "let a = 1" } }),
      tool("write_file", { toolCallId: "b", state: "output-available", input: { file_path: "index.html", content: "<h1>Hello</h1>" } }),
      tool("editor", { toolCallId: "c", input: { command: "str_replace", path: "a.ts", old_str: "1", new_str: "2" } }),
    ], true)
    expect(preview.selectedPath).toBe("a.ts")
    expect(preview.activeView).toBe("code")
    expect(preview.isStreaming).toBe(true)
  })

  it("does not invent files for explicit output-denied parts", () => {
    const preview = projectIdePreview([tool("write_file", {
      state: "output-denied", input: { file_path: "denied.ts", content: "no" },
    })], false)
    expect(preview.files).toEqual([])
    expect(preview.transcript[0].status).toBe("error")
  })

  it("returns empty when there is no project activity", () => {
    expect(projectIdePreview([], false)).toEqual(
      expect.objectContaining({ open: false, files: [] })
    )
  })

  it("collects file_write and editor create into the tree", () => {
    const preview = projectIdePreview(
      [
        tool("file_write", {
          toolCallId: "w1",
          state: "output-available",
          input: { path: "index.html", content: "<h1>Hi</h1>" },
          output: { status: "success" },
        }),
        tool("editor", {
          toolCallId: "e1",
          state: "input-available",
          input: { command: "create", path: "src/app.tsx", file_text: "export {}" },
        }),
      ],
      false
    )
    expect(preview.open).toBe(true)
    expect(preview.sessionId).toBe("w1")
    expect(preview.files.map((f) => f.path)).toEqual(["index.html", "src/app.tsx"])
    expect(preview.htmlDocument).toBe("<h1>Hi</h1>")
    expect(preview.files[0].contents).toBe("<h1>Hi</h1>")
  })

  it("brings edited and patched files into the tree, dropping deletions", () => {
    const preview = projectIdePreview(
      [
        native({
          type: "sandbox_write_file",
          call_id: "w1",
          file_path: "src/app.tsx",
        }),
        native({
          type: "sandbox_edit_file",
          call_id: "e1",
          file_path: "src/lib/util.ts",
        }),
        native({
          type: "sandbox_apply_patch",
          call_id: "p1",
          added: ["README.md"],
          modified: ["src/app.tsx"],
          deleted: ["src/old.ts"],
        }),
        // A path that was only ever deleted must not appear.
        native({
          type: "sandbox_apply_patch",
          call_id: "p2",
          deleted: ["src/ghost.ts"],
        }),
      ],
      false
    )
    expect(preview.open).toBe(true)
    expect(preview.files.map((f) => f.path)).toEqual([
      "src/app.tsx",
      "src/lib/util.ts",
      "README.md",
    ])
    expect(preview.files.map((f) => f.path)).not.toContain("src/old.ts")
    expect(preview.files.map((f) => f.path)).not.toContain("src/ghost.ts")
    // The edit and patch keep any prior contents rather than clobbering.
    expect(
      preview.files.find((f) => f.path === "src/app.tsx")?.source
    ).toBe("sandbox_apply_patch")
  })

  it("opens preview from share_file html and sandbox writes", () => {
    const preview = projectIdePreview(
      [
        native({
          type: "sandbox_write_file",
          call_id: "sw1",
          file_path: "docs/notes.md",
        }),
        native({
          type: "share_file",
          call_id: "sf1",
          filename: "index.html",
          url: "/api/orchestrator/file?path=%2Fv1%2Fresponses%2Fr1%2Ffiles%2Ff1%2Fcontent",
        }),
      ],
      false
    )
    expect(preview.open).toBe(true)
    expect(preview.previewUrl).toContain("/api/orchestrator/file")
    expect(preview.files.map((f) => f.path)).toEqual(["docs/notes.md", "index.html"])
  })

  it("treats pnpm dev as a project run and reads localhost from stdout", () => {
    const preview = projectIdePreview(
      [
        tool("shell", {
          toolCallId: "sh1",
          state: "output-available",
          input: { command: "pnpm dev" },
          output: {
            status: "success",
            content: [{ text: "  ➜  Local:   http://localhost:5173/" }],
          },
        }),
      ],
      false
    )
    expect(preview.open).toBe(true)
    expect(preview.isDevServer).toBe(true)
    expect(preview.command).toBe("pnpm dev")
    expect(preview.previewUrl).toBe("http://localhost:5173/")
    expect(preview.stdout).toContain("5173")
  })

  it("opens from native sandbox bash running a static server", () => {
    const preview = projectIdePreview(
      [
        native({
          type: "sandbox_results",
          call_id: "sb1",
          language: "bash",
          code: "python3 -m http.server 8765",
          status: "in_progress",
          results: [
            {
              stdout: "Serving HTTP on 0.0.0.0 port 8765 (http://0.0.0.0:8765/) ...",
              stderr: "",
              exit_code: 0,
              duration_ms: 12,
            },
          ],
        }),
      ],
      true
    )
    expect(preview.open).toBe(true)
    expect(preview.isDevServer).toBe(true)
    expect(preview.isStreaming).toBe(true)
    expect(preview.previewUrl).toBe("http://localhost:8765/")
    expect(preview.activeView).toBe("terminal")
    expect(preview.activityId).toBe("sandbox_results-sb1")
    expect(preview.transcript).toEqual([
      expect.objectContaining({
        kind: "command",
        label: "python3 -m http.server 8765",
        status: "running",
      }),
    ])
  })

  it("accumulates a transcript of commands and file operations in order", () => {
    const preview = projectIdePreview(
      [
        tool("file_write", {
          toolCallId: "w1",
          state: "output-available",
          input: { path: "index.html", content: "<h1>Hi</h1>" },
          output: { status: "success", content: [{ text: "wrote" }] },
        }),
        tool("shell", {
          toolCallId: "sh1",
          state: "output-available",
          input: { command: "ls" },
          output: { status: "success", content: [{ text: "index.html" }] },
        }),
        native({
          type: "sandbox_edit_file",
          call_id: "e1",
          file_path: "index.html",
          message: "edited",
        }),
      ],
      false
    )
    expect(preview.transcript.map((op) => [op.kind, op.label, op.status])).toEqual([
      ["write", "index.html", "success"],
      ["command", "ls", "success"],
      ["edit", "index.html", "success"],
    ])
    expect(preview.transcript[1].output).toContain("index.html")
    expect(preview.transcript[2].output).toBe("edited")
    // Stable per-operation ids: toolCallId / type-call_id.
    expect(preview.transcript.map((op) => op.id)).toEqual([
      "w1",
      "sh1",
      "sandbox_edit_file-e1",
    ])
    // No live dev server or preview: last op is an edit, so code view engages.
    expect(preview.activeView).toBe("code")
    expect(preview.activityId).toBe("sandbox_edit_file-e1")
  })

  it("keeps failed and denied semantics: errors are logged, files are not invented", () => {
    const preview = projectIdePreview(
      [
        tool("file_write", {
          toolCallId: "w1",
          state: "output-error",
          input: { path: "blocked.txt", content: "nope" },
          errorText: "denied by user",
        }),
        native({
          type: "sandbox_write_file",
          call_id: "w2",
          file_path: "broken.txt",
          error: "disk full",
        }),
      ],
      false
    )
    // Neither failed write may add a file to the tree.
    expect(preview.files).toEqual([])
    expect(preview.transcript.map((op) => [op.kind, op.label, op.status, op.output])).toEqual([
      ["write", "blocked.txt", "error", "denied by user"],
      ["write", "broken.txt", "error", "disk full"],
    ])
    // The transcript alone justifies opening the IDE (engaged view).
    expect(preview.open).toBe(true)
  })

  it("invalidates stale contents on metadata-only edits instead of showing old text", () => {
    const preview = projectIdePreview(
      [
        native({
          type: "sandbox_read_file",
          call_id: "r1",
          file_path: "app.py",
          content: "print('v1')",
        }),
        native({
          type: "sandbox_edit_file",
          call_id: "e1",
          file_path: "app.py",
        }),
      ],
      false
    )
    const file = preview.files.find((f) => f.path === "app.py")
    expect(file?.source).toBe("sandbox_edit_file")
    // The edit changed the file; the old read is stale, never shown as current.
    expect(file?.contents).toBe("")
  })

  it("keeps held contents when an edit fails (file untouched)", () => {
    const preview = projectIdePreview(
      [
        native({
          type: "sandbox_read_file",
          call_id: "r1",
          file_path: "app.py",
          content: "print('v1')",
        }),
        native({
          type: "sandbox_edit_file",
          call_id: "e1",
          file_path: "app.py",
          error: "old_str not found",
        }),
      ],
      false
    )
    const file = preview.files.find((f) => f.path === "app.py")
    expect(file?.contents).toBe("print('v1')")
    expect(preview.transcript.at(-1)).toEqual(
      expect.objectContaining({ kind: "edit", status: "error" })
    )
  })

  it("applies editor str_replace to held contents (replace-all semantics)", () => {
    const preview = projectIdePreview(
      [
        tool("editor", {
          toolCallId: "e0",
          state: "output-available",
          input: { command: "create", path: "a.txt", file_text: "foo bar foo" },
          output: { status: "success", content: [{ text: "created" }] },
        }),
        tool("editor", {
          toolCallId: "e1",
          state: "output-available",
          input: {
            command: "str_replace",
            path: "a.txt",
            old_str: "foo",
            new_str: "baz",
          },
          output: { status: "success", content: [{ text: "replaced" }] },
        }),
      ],
      false
    )
    expect(preview.files[0].contents).toBe("baz bar baz")
  })

  it("invalidates contents when str_replace succeeds but held text lacks old_str", () => {
    const preview = projectIdePreview(
      [
        tool("editor", {
          toolCallId: "e0",
          state: "output-available",
          input: { command: "create", path: "a.txt", file_text: "hello" },
          output: { status: "success", content: [{ text: "created" }] },
        }),
        tool("editor", {
          toolCallId: "e1",
          state: "output-available",
          input: {
            command: "str_replace",
            path: "a.txt",
            old_str: "missing",
            new_str: "x",
          },
          output: { status: "success", content: [{ text: "replaced" }] },
        }),
      ],
      false
    )
    // Held text did not contain old_str: server-side state diverged from ours,
    // so contents are dropped rather than invented.
    expect(preview.files[0].contents).toBe("")
  })

  it("collects strands-shell MCP write_file and read_file (file_path schema)", () => {
    const preview = projectIdePreview(
      [
        tool("write_file", {
          toolCallId: "mw1",
          state: "output-available",
          input: { file_path: "notes.md", content: "# hi" },
          output: { status: "success", content: [{ text: "Wrote 4 bytes to notes.md" }] },
        }),
        tool("read_file", {
          toolCallId: "mr1",
          state: "output-available",
          input: { file_path: "src/app.py" },
          output: {
            status: "success",
            content: [{ text: "     1\tprint('hi')\n     2\tprint('yo')\n" }],
          },
        }),
      ],
      false
    )
    expect(preview.files.map((f) => [f.path, f.contents])).toEqual([
      ["notes.md", "# hi"],
      ["src/app.py", "print('hi')\nprint('yo')\n"],
    ])
    expect(preview.transcript.map((op) => op.kind)).toEqual(["write", "read"])
  })

  it("unwraps array tool outputs (multi-block ToolResult content)", () => {
    const preview = projectIdePreview(
      [
        tool("shell", {
          toolCallId: "sh1",
          state: "output-available",
          input: { command: "pnpm dev" },
          // route.ts tool_results with 2+ content blocks arrives as an array.
          output: ["Execution Summary: 1 ok", "Local: http://localhost:5173/"],
        }),
      ],
      false
    )
    expect(preview.previewUrl).toBe("http://localhost:5173/")
    expect(preview.stdout).toContain("Execution Summary")
  })

  it("shows terminal view for a running shell command and stable activityId", () => {
    const preview = projectIdePreview(
      [
        tool("shell", {
          toolCallId: "sh1",
          state: "input-available",
          input: { command: "pytest -q" },
        }),
      ],
      true
    )
    expect(preview.activeView).toBe("terminal")
    expect(preview.activityId).toBe("sh1")
    expect(preview.transcript[0].status).toBe("running")
  })

  it("ingests nested agent-run sandbox file events with proxied share urls", () => {
    const agentRun = {
      type: "data-agent-run",
      id: "agent-run-a1",
      data: {
        activity: "create_fast_agent_response",
        activityId: "a1",
        preset: "fast",
        responseId: "r1",
        attempt: 1,
        status: "completed",
        model: null,
        text: "",
        error: null,
        events: [
          {
            sequence: 1,
            event: {
              type: "response.output_item.done",
              item: {
                type: "sandbox_write_file",
                call_id: "nw1",
                file_path: "nested/report.md",
              },
            },
          },
          {
            sequence: 2,
            event: {
              type: "response.output_item.done",
              item: {
                type: "share_file",
                call_id: "ns1",
                filename: "report.html",
                url: "/v1/responses/r1/files/f1/content",
              },
            },
          },
        ],
      },
    } as unknown as UIMessage["parts"][number]

    const preview = projectIdePreview([agentRun], false)
    expect(preview.files.map((f) => f.path)).toEqual([
      "nested/report.md",
      "report.html",
    ])
    expect(preview.previewUrl).toBe(
      "/api/orchestrator/file?path=%2Fv1%2Fresponses%2Fr1%2Ffiles%2Ff1%2Fcontent"
    )
    expect(preview.transcript.map((op) => op.id)).toEqual([
      "run-a1-sandbox_write_file-nw1",
      "run-a1-share_file-ns1",
    ])
  })

  it("projects mcp_client call_tool wrappers through the shared tool projection", () => {
    const preview = projectIdePreview(
      [
        tool("mcp_client", {
          toolCallId: "mc1",
          state: "output-available",
          input: {
            action: "call_tool",
            connection_id: "shell",
            tool_name: "write_file",
            tool_args: { file_path: "wrapped.md", content: "# wrapped" },
          },
          output: { status: "success", content: [{ text: "Wrote 9 bytes" }] },
        }),
        tool("mcp_client", {
          toolCallId: "mc2",
          state: "output-available",
          input: {
            action: "call_tool",
            connection_id: "shell",
            tool_name: "read_file",
            tool_args: { file_path: "src/x.py" },
          },
          output: {
            status: "success",
            content: [{ text: "     1\tprint(1)\n" }],
          },
        }),
        tool("mcp_client", {
          toolCallId: "mc3",
          state: "output-available",
          input: {
            action: "call_tool",
            connection_id: "shell",
            tool_name: "editor",
            tool_args: {
              command: "create",
              path: "made.txt",
              file_text: "via editor",
            },
          },
          output: { status: "success", content: [{ text: "created" }] },
        }),
      ],
      false
    )
    expect(preview.files.map((f) => [f.path, f.contents, f.source])).toEqual([
      ["wrapped.md", "# wrapped", "write_file"],
      ["src/x.py", "print(1)\n", "read_file"],
      ["made.txt", "via editor", "editor"],
    ])
    expect(preview.transcript.map((op) => [op.id, op.kind, op.status])).toEqual([
      ["mc1", "write", "success"],
      ["mc2", "read", "success"],
      ["mc3", "write", "success"],
    ])
  })

  it("keeps unknown mcp_client call_tool targets as commands and failed wrapped writes out of the tree", () => {
    const preview = projectIdePreview(
      [
        tool("mcp_client", {
          toolCallId: "mc1",
          state: "output-available",
          input: {
            action: "call_tool",
            connection_id: "shell",
            tool_name: "shell",
            tool_args: { command: "make build" },
          },
          output: { status: "success", content: [{ text: "ok" }] },
        }),
        tool("mcp_client", {
          toolCallId: "mc2",
          state: "output-error",
          input: {
            action: "call_tool",
            connection_id: "shell",
            tool_name: "write_file",
            tool_args: { file_path: "denied.txt", content: "x" },
          },
          errorText: "permission denied",
        }),
        tool("mcp_client", {
          toolCallId: "mc3",
          state: "output-available",
          input: {
            action: "call_tool",
            connection_id: "other",
            tool_name: "run_query",
            tool_args: { command: "SELECT 1" },
          },
          output: { status: "success", content: [{ text: "1" }] },
        }),
      ],
      false
    )
    expect(preview.files).toEqual([])
    expect(preview.transcript.map((op) => [op.id, op.kind, op.label, op.status])).toEqual([
      ["mc1", "command", "make build", "success"],
      ["mc2", "write", "denied.txt", "error"],
      ["mc3", "command", "SELECT 1", "success"],
    ])
  })

  it("routes native mcp_call items for known tools through the shared projection", () => {
    const preview = projectIdePreview(
      [
        native({
          type: "mcp_call",
          id: "call-1",
          server_label: "shell",
          name: "write_file",
          arguments: JSON.stringify({ file_path: "remote.md", content: "# remote" }),
          output: "Wrote 8 bytes to remote.md",
        }),
        native({
          type: "mcp_call",
          id: "call-2",
          server_label: "shell",
          name: "read_file",
          arguments: JSON.stringify({ file_path: "remote.md" }),
          error: "no such file",
        }),
      ],
      false
    )
    expect(preview.files.map((f) => [f.path, f.source])).toEqual([
      ["remote.md", "write_file"],
    ])
    expect(preview.transcript.map((op) => [op.id, op.kind, op.status, op.output])).toEqual([
      ["mcp-call-1", "write", "success", "Wrote 8 bytes to remote.md"],
      ["mcp-call-2", "read", "error", "no such file"],
    ])
  })

  it("also skips metadata-only DataCommons get_variable_metadata '{}' calls mid-stream", () => {
    // Second live browser repro (02:12 HMR): a metadata-only call with empty
    // arguments arrived while streaming and re-opened the terminal as "$ {}".
    // The non-shell mcp_call fallback must never flatten native.arguments.
    const preview = projectIdePreview(
      [
        native({
          type: "mcp_call",
          id: "dc-meta-1",
          server_label: "datacommons",
          name: "get_variable_metadata",
          arguments: "{}",
          output: '{"metadata":{"dcid":"Count_Person","name":"Population"}}',
        }),
      ],
      true
    )
    expect(preview.open).toBe(false)
    expect(preview.transcript).toEqual([])
    expect(preview.command).toBe("")
    expect(preview.stdout).toBe("")
  })

  it("allows the raw-string arguments fallback ONLY for shell-named tools", () => {
    const preview = projectIdePreview(
      [
        // Malformed (non-JSON) arguments on the shell tool: the raw string is
        // the command line itself.
        native({
          type: "mcp_call",
          id: "sh-raw",
          server_label: "shell",
          name: "shell",
          arguments: "echo hello",
          output: "hello",
        }),
        // The same raw-string shape on a data tool must NOT become a command.
        native({
          type: "mcp_call",
          id: "dc-raw",
          server_label: "datacommons",
          name: "get_child_observations",
          arguments: "not json either",
          output: '{"observations":[]}',
        }),
      ],
      false
    )
    expect(preview.transcript.map((op) => [op.id, op.kind, op.label])).toEqual([
      ["mcp-sh-raw", "command", "echo hello"],
    ])
    expect(preview.stdout).toBe("hello")
  })

  it("records command-shaped unknown mcp_call payloads WITHOUT preview inference", () => {
    // An unknown remote tool whose arguments carry an explicit `command`
    // field is logged in the transcript — but its output is arbitrary data,
    // so it must never feed the dev-server/localhost preview inference (only
    // genuine shell executions do).
    const preview = projectIdePreview(
      [
        native({
          type: "mcp_call",
          id: "call-9",
          server_label: "datacommons",
          name: "run_shell",
          arguments: JSON.stringify({ command: "npm run dev" }),
          output: "Local: http://localhost:3000",
        }),
      ],
      false
    )
    expect(preview.isDevServer).toBe(false)
    expect(preview.previewUrl).toBe("")
    expect(preview.command).toBe("")
    expect(preview.stdout).toBe("")
    expect(preview.transcript).toEqual([
      expect.objectContaining({
        id: "mcp-call-9",
        kind: "command",
        label: "npm run dev",
        status: "success",
        output: "Local: http://localhost:3000",
      }),
    ])
  })

  it("shell-named native mcp_call executions DO feed preview inference", () => {
    // The strands-shell MCP server's own shell tool is a real execution: its
    // dev-server command and localhost output legitimately open the preview.
    const preview = projectIdePreview(
      [
        native({
          type: "mcp_call",
          id: "sh-5",
          server_label: "shell",
          name: "shell",
          arguments: JSON.stringify({ command: "npm run dev" }),
          output: "Local: http://localhost:3000",
        }),
      ],
      false
    )
    expect(preview.isDevServer).toBe(true)
    expect(preview.previewUrl).toBe("http://localhost:3000")
  })

  it("never stores a tool-reported error payload as read_file contents", () => {
    // A dynamic part can reach output-available carrying the tool's own
    // {status:"error"} ToolResult. succeeded must exclude that, or the error
    // text would be shown as the file's contents.
    const preview = projectIdePreview(
      [
        tool("read_file", {
          toolCallId: "r1",
          state: "output-available",
          input: { file_path: "app.py" },
          output: {
            status: "error",
            content: [{ text: "Error: no such file: app.py" }],
          },
        }),
      ],
      false
    )
    expect(preview.files.find((f) => f.path === "app.py")).toBeUndefined()
    expect(preview.transcript[0]).toEqual(
      expect.objectContaining({ kind: "read", status: "error" })
    )
  })

  it("drops a stale share URL when the file is later mutated", () => {
    // share_file gave index.html a proxy URL; a later successful edit makes
    // that artifact PRE-edit. Fetching it as current would be a lie — the
    // URL must be invalidated along with held contents.
    const preview = projectIdePreview(
      [
        native({
          type: "share_file",
          call_id: "sf1",
          filename: "index.html",
          url: "/api/orchestrator/file?path=old-artifact",
        }),
        native({
          type: "sandbox_edit_file",
          call_id: "e1",
          file_path: "index.html",
          message: "edited",
        }),
      ],
      false
    )
    const file = preview.files.find((f) => f.path === "index.html")
    expect(file?.url).toBe("")
    expect(file?.contents).toBe("")
  })

  it("preserves prior successful contents when a later write is denied", () => {
    // The denied write never executed: what we held is still the file's
    // actual state and must not be invalidated.
    const preview = projectIdePreview(
      [
        tool("write_file", {
          toolCallId: "w1",
          state: "output-available",
          input: { file_path: "app.py", content: "print('v1')" },
          output: { status: "success", content: [{ text: "ok" }] },
        }),
        tool("write_file", {
          toolCallId: "w2",
          state: "output-denied",
          input: { file_path: "app.py", content: "print('v2')" },
        }),
      ],
      false
    )
    const file = preview.files.find((f) => f.path === "app.py")
    expect(file?.contents).toBe("print('v1')")
    expect(preview.transcript.at(-1)).toEqual(
      expect.objectContaining({ kind: "write", status: "error" })
    )
  })

  it("clears prior contents when an authoritative write carries empty content", () => {
    // Writing "" truncates the file: preserving the old text would show
    // deleted content as current.
    const preview = projectIdePreview(
      [
        tool("file_write", {
          toolCallId: "w1",
          state: "output-available",
          input: { path: "notes.txt", content: "old text" },
          output: { status: "success" },
        }),
        tool("file_write", {
          toolCallId: "w2",
          state: "output-available",
          input: { path: "notes.txt", content: "" },
          output: { status: "success" },
        }),
      ],
      false
    )
    expect(preview.files.find((f) => f.path === "notes.txt")?.contents).toBe("")
  })

  it("keeps a completed non-preview command in the terminal despite an older HTML preview", () => {
    // `ls` finishing must not yank the view back to an HTML document written
    // earlier — only the command's OWN preview (dev server / localhost in its
    // output) may hand off.
    const preview = projectIdePreview(
      [
        tool("file_write", {
          toolCallId: "w1",
          state: "output-available",
          input: { path: "index.html", content: "<h1>Old</h1>" },
          output: { status: "success" },
        }),
        tool("shell", {
          toolCallId: "sh1",
          state: "output-available",
          input: { command: "ls" },
          output: { status: "success", content: [{ text: "index.html" }] },
        }),
      ],
      false
    )
    expect(preview.htmlDocument).toBe("<h1>Old</h1>")
    expect(preview.activeView).toBe("terminal")
  })

  it("keeps focus on the newest operation when an older long-running op is still live", () => {
    // Focus follows the LATEST recorded operation; a still-running older op
    // (e.g. a dev server) must not snap activityId backwards and expire the
    // user's manual choices without new activity.
    const preview = projectIdePreview(
      [
        tool("shell", {
          toolCallId: "dev-server",
          state: "input-available",
          input: { command: "pnpm dev" },
        }),
        tool("editor", {
          toolCallId: "edit-1",
          state: "output-available",
          input: { command: "create", path: "src/app.tsx", file_text: "x" },
          output: { status: "success", content: [{ text: "created" }] },
        }),
      ],
      true
    )
    expect(preview.activityId).toBe("edit-1")
    expect(preview.activeView).toBe("code")
    // The still-running server keeps the streaming indicator honest.
    expect(preview.isStreaming).toBe(true)
  })

  it("never stores a tool-reported error payload as read_file contents", () => {
    // Reviewer blocker: output-available with a Strands {status:"error"}
    // payload set BOTH failed and succeeded; the read branch's
    // `succeeded && outputText` then stored the error text as file contents.
    const preview = projectIdePreview(
      [
        tool("write_file", {
          toolCallId: "w1",
          state: "output-available",
          input: { file_path: "app.py", content: "print('hi')" },
          output: { status: "success", content: [{ text: "ok" }] },
        }),
        tool("read_file", {
          toolCallId: "r1",
          state: "output-available",
          input: { file_path: "app.py" },
          output: {
            status: "error",
            content: [{ text: "Error: EACCES permission denied" }],
          },
        }),
      ],
      false
    )
    const file = preview.files.find((f) => f.path === "app.py")
    // Held contents survive the failed read; the error never becomes content.
    expect(file?.contents).toBe("print('hi')")
    expect(preview.transcript.at(-1)).toEqual(
      expect.objectContaining({ id: "r1", kind: "read", status: "error" })
    )
  })

  it("preserves prior successful contents when a later write is denied", () => {
    // A denied/failed write never executed: the file on disk is unchanged,
    // so the previously written contents stay accurate and visible.
    const preview = projectIdePreview(
      [
        tool("write_file", {
          toolCallId: "w1",
          state: "output-available",
          input: { file_path: "app.py", content: "v1" },
          output: { status: "success" },
        }),
        tool("write_file", {
          toolCallId: "w2",
          state: "output-denied",
          input: { file_path: "app.py", content: "v2" },
        }),
      ],
      false
    )
    const file = preview.files.find((f) => f.path === "app.py")
    expect(file?.contents).toBe("v1")
    expect(preview.transcript.map((op) => [op.id, op.status])).toEqual([
      ["w1", "success"],
      ["w2", "error"],
    ])
  })

  it("treats an empty successful write as authoritative truncation, not preserved text", () => {
    const preview = projectIdePreview(
      [
        tool("write_file", {
          toolCallId: "w1",
          state: "output-available",
          input: { file_path: "notes.md", content: "old text" },
          output: { status: "success" },
        }),
        tool("write_file", {
          toolCallId: "w2",
          state: "output-available",
          input: { file_path: "notes.md", content: "" },
          output: { status: "success" },
        }),
      ],
      false
    )
    // The second write truncated the file; showing "old text" would be stale.
    expect(preview.files.find((f) => f.path === "notes.md")?.contents).toBe("")
  })

  it("drops a stale share URL when the file is subsequently mutated", () => {
    // Reviewer blocker: after an edit invalidated contents, the preserved
    // share URL would be fetched and presented the PRE-edit artifact as
    // current. Mutation must clear the URL too.
    const preview = projectIdePreview(
      [
        native({
          type: "share_file",
          call_id: "sf1",
          filename: "index.html",
          url: "/api/orchestrator/file?path=%2Fv1%2Fresponses%2Fr1%2Ffiles%2Ff1%2Fcontent",
        }),
        native({
          type: "sandbox_edit_file",
          call_id: "e1",
          file_path: "index.html",
          message: "edited",
        }),
      ],
      false
    )
    const file = preview.files.find((f) => f.path === "index.html")
    expect(file?.url).toBe("")
    expect(file?.contents).toBe("")
  })

  it("does not let a completed ls steal focus to an older HTML preview", () => {
    const preview = projectIdePreview(
      [
        tool("file_write", {
          toolCallId: "w1",
          state: "output-available",
          input: { path: "index.html", content: "<h1>Old</h1>" },
          output: { status: "success" },
        }),
        tool("shell", {
          toolCallId: "sh1",
          state: "output-available",
          input: { command: "ls" },
          output: { status: "success", content: [{ text: "index.html" }] },
        }),
      ],
      false
    )
    // ls produced nothing previewable; the terminal keeps focus.
    expect(preview.activeView).toBe("terminal")
    expect(preview.activityId).toBe("sh1")
  })

  it("keeps focus on the newest operation even when an older one finishes later", () => {
    // Reviewer blocker: keying focus off "last running op" snapped BACKWARDS
    // to the newest op only while something ran; once an OLDER long-running
    // op finished, activityId reverted to it, expiring the user's view
    // override without any new activity. Focus follows creation order.
    const running = projectIdePreview(
      [
        native({
          type: "sandbox_results",
          call_id: "slow",
          language: "bash",
          code: "pytest -q",
          status: "in_progress",
          results: [],
        }),
        native({
          type: "sandbox_write_file",
          call_id: "w-new",
          file_path: "src/new.ts",
        }),
      ],
      true
    )
    expect(running.activityId).toBe("sandbox_write_file-w-new")
    expect(running.isStreaming).toBe(true)

    const finished = projectIdePreview(
      [
        native({
          type: "sandbox_results",
          call_id: "slow",
          language: "bash",
          code: "pytest -q",
          status: "in_progress",
          results: [],
        }),
        native({
          type: "sandbox_write_file",
          call_id: "w-new",
          file_path: "src/new.ts",
        }),
        // The older command's terminal snapshot arrives LAST but upserts in
        // place — activityId must stay on the write, not revert to pytest.
        native({
          type: "sandbox_results",
          call_id: "slow",
          language: "bash",
          code: "pytest -q",
          status: "completed",
          results: [{ stdout: "2 passed", stderr: "", exit_code: 0 }],
        }),
      ],
      false
    )
    expect(finished.activityId).toBe("sandbox_write_file-w-new")
    expect(finished.activeView).toBe("code")
    expect(finished.isStreaming).toBe(false)
  })

  it("deduplicates progress snapshots so a finished op leaves no running ghost", () => {
    const preview = projectIdePreview(
      [
        native({
          type: "sandbox_results",
          call_id: "sb1",
          language: "bash",
          code: "pytest -q",
          status: "in_progress",
          results: [],
        }),
        native({
          type: "sandbox_results",
          call_id: "sb1",
          language: "bash",
          code: "pytest -q",
          status: "completed",
          results: [
            { stdout: "2 passed", stderr: "", exit_code: 0, duration_ms: 40 },
          ],
        }),
      ],
      false
    )
    // ONE transcript entry, final state — not a running ghost plus a result.
    expect(preview.transcript).toEqual([
      expect.objectContaining({
        id: "sandbox_results-sb1",
        kind: "command",
        status: "success",
        output: "2 passed",
      }),
    ])
    expect(preview.activityId).toBe("sandbox_results-sb1")
  })
})

describe("formatTranscript", () => {
  const ops: ProjectIdeOperation[] = [
    { id: "w1", kind: "write", label: "index.html", output: "wrote 12 bytes", status: "success" },
    { id: "sh1", kind: "command", label: "pnpm dev", output: "Local: http://localhost:5173/", status: "running" },
    { id: "e1", kind: "edit", label: "src/app.tsx", output: "old_str not found", status: "error" },
  ]

  it("renders every command AND file operation, in order, with honest markers", () => {
    const text = formatTranscript(ops)
    const lines = text.split("\n").filter(Boolean)
    expect(lines[0]).toBe("[write] index.html")
    expect(lines[1]).toBe("wrote 12 bytes")
    expect(lines[2]).toBe("$ pnpm dev")
    // Partial output the tool actually reported, then the honest waiting line.
    expect(lines[3]).toBe("Local: http://localhost:5173/")
    expect(lines[4]).toContain("[running]")
    expect(lines[5]).toBe("[edit] src/app.tsx")
    expect(lines[6]).toBe("[failed] old_str not found")
  })

  it("never invents output for silent successes or bare failures", () => {
    expect(
      formatTranscript([
        { id: "a", kind: "read", label: "x.py", output: "", status: "success" },
        { id: "b", kind: "write", label: "y.py", output: "", status: "error" },
      ])
    ).toBe("[read] x.py\n\n[write] y.py\n[failed]")
  })
})

describe("resolveIdeView", () => {
  it("keeps the user's override while the same activity is current", () => {
    expect(
      resolveIdeView({ auto: "code", override: "terminal", overrideAt: "op-1", activityId: "op-1" })
    ).toEqual({ view: "terminal", overrideExpired: false })
  })

  it("expires the override when a NEW activity arrives", () => {
    expect(
      resolveIdeView({ auto: "terminal", override: "code", overrideAt: "op-1", activityId: "op-2" })
    ).toEqual({ view: "terminal", overrideExpired: true })
  })

  it("follows the stream when there is no override", () => {
    expect(
      resolveIdeView({ auto: "preview", override: null, overrideAt: undefined, activityId: "op-1" })
    ).toEqual({ view: "preview", overrideExpired: false })
  })
})

describe("projectIdePreview activeView tool-follow", () => {
  it("hands a finished dev-server command off to the preview", () => {
    const preview = projectIdePreview(
      [
        tool("shell", {
          toolCallId: "sh1",
          state: "output-available",
          input: { command: "pnpm dev" },
          output: { status: "success", content: [{ text: "Local: http://localhost:5173/" }] },
        }),
      ],
      false
    )
    expect(preview.activeView).toBe("preview")
  })

  it("keeps the terminal engaged while the command still runs, even a dev server", () => {
    const preview = projectIdePreview(
      [
        tool("shell", {
          toolCallId: "sh1",
          state: "input-available",
          input: { command: "pnpm dev" },
        }),
      ],
      true
    )
    expect(preview.activeView).toBe("terminal")
  })

  it("keeps the terminal for a finished non-preview command", () => {
    const preview = projectIdePreview(
      [
        tool("shell", {
          toolCallId: "sh1",
          state: "output-available",
          input: { command: "ls" },
          output: { status: "success", content: [{ text: "a.txt" }] },
        }),
      ],
      false
    )
    expect(preview.activeView).toBe("terminal")
  })
})

describe("projectIdePreview across turns", () => {
  it("accumulates files and transcript from parts spanning multiple turns", () => {
    // agent-chat.tsx flattens EVERY assistant message's parts into one array;
    // files written in turn 1 must survive a turn 2 that only runs a command.
    const turn1 = [
      tool("file_write", {
        toolCallId: "t1-w",
        state: "output-available",
        input: { path: "app.py", content: "print('hi')" },
        output: { status: "success" },
      }),
    ]
    const turn2 = [
      tool("shell", {
        toolCallId: "t2-sh",
        state: "output-available",
        input: { command: "python app.py" },
        output: { status: "success", content: [{ text: "hi" }] },
      }),
    ]
    const preview = projectIdePreview([...turn1, ...turn2], false)
    expect(preview.files.map((f) => f.path)).toEqual(["app.py"])
    expect(preview.transcript.map((op) => op.id)).toEqual(["t1-w", "t2-sh"])
    expect(preview.activityId).toBe("t2-sh")
  })
})

describe("decodeNumberedLines", () => {
  it("strips strands-shell read_file line-number prefixes", () => {
    expect(decodeNumberedLines("     1\thello\n     2\tworld\n")).toBe(
      "hello\nworld\n"
    )
  })

  it("leaves ordinary text untouched", () => {
    expect(decodeNumberedLines("plain text\nno numbers")).toBe(
      "plain text\nno numbers"
    )
    expect(decodeNumberedLines("     1\tnumbered\nplain")).toBe(
      "     1\tnumbered\nplain"
    )
  })
})
