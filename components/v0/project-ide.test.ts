import { describe, expect, it } from "vitest"

import type { DynamicToolUIPart, UIMessage } from "ai"

import {
  extractLocalhostUrl,
  fileTreeNodes,
  flattenShellCommand,
  isDevServerCommand,
  projectIdePreview,
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
  })
})
