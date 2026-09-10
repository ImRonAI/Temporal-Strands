import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { DesktopGuard } from "../.kilo/plugins/desktop-guard"
import type { PluginInput, Hooks } from "../.kilo/node_modules/@kilocode/plugin/dist/index.js"

const cleanup: Array<() => void | Promise<void>> = []
async function setup() {
  const directory = mkdtempSync(path.join(os.tmpdir(), "desktop-hooks-"))
  mkdirSync(path.join(directory, "orchestrator"))
  mkdirSync(path.join(directory, "components/v0"), { recursive: true })
  mkdirSync(path.join(directory, "lib"))
  symlinkSync(path.resolve("node_modules"), path.join(directory, "node_modules"), "dir")
  writeFileSync(path.join(directory, "tsconfig.json"), JSON.stringify({ compilerOptions: {
    strict: true, jsx: "react-jsx", target: "ES2022", module: "esnext", moduleResolution: "bundler", skipLibCheck: true,
  }, include: ["components/**/*.tsx", "lib/**/*.ts"] }))
  const hooks = await DesktopGuard({ directory } as PluginInput)
  cleanup.push(async () => { await hooks.dispose?.(); rmSync(directory, { recursive: true, force: true }) })
  return { root: directory, hooks }
}
afterEach(async () => { for (const dispose of cleanup.splice(0)) await dispose() })
const input = { sessionID: "test-session", callID: "call", tool: "write" }
const patchInput = { ...input, tool: "apply_patch" }
const afterOutput = () => ({ title: "edited", output: "saved", metadata: {} })
const patch = (file: string) => ({ patchText: `*** Begin Patch\n*** Update File: ${file}\n@@\n-old\n+new\n*** End Patch` })

describe("native Kilo regression-only contract hooks", () => {
  it("blocks a proven violation before a full-file write without writing anything", async () => {
    const { hooks } = await setup()
    await expect(hooks["tool.execute.before"]!(input, { args: {
      filePath: "orchestrator/runtime.py", content: "browser.launch(headless=True)",
    } })).rejects.toThrow("headed-desktop")
  })

  it("permits repairs and emits no warning about unrelated baseline failures", async () => {
    const { hooks, root } = await setup()
    writeFileSync(path.join(root, "orchestrator/runtime.py"), "browser.launch(headless=True)")
    writeFileSync(path.join(root, "orchestrator/unrelated.py"), "args = ['--no-sandbox']")
    const args = { filePath: "orchestrator/runtime.py", content: "browser.launch(headless=False)" }
    await hooks["tool.execute.before"]!(input, { args })
    writeFileSync(path.join(root, args.filePath), args.content)
    const output = afterOutput()
    await hooks["tool.execute.after"]!({ ...input, args }, output)
    expect(output.output).toBe("saved")
  })

  it("does not run scans or rewrite text for ordinary commands/conversation", async () => {
    const { hooks, root } = await setup()
    // Would fail any source scan; bash must remain entirely outside the hooks.
    symlinkSync(path.join(root, "lib"), path.join(root, "orchestrator/runtime.py"))
    const output = afterOutput()
    await hooks["tool.execute.before"]!({ ...input, tool: "bash" }, { args: { command: "git status" } })
    await hooks["tool.execute.after"]!({ ...input, tool: "bash", args: {} }, output)
    expect(output.output).toBe("saved")
    expect(hooks["experimental.text.complete"]).toBeUndefined()
    expect(hooks["experimental.chat.system.transform"]).toBeUndefined()
  })

  it("ignores unrelated non-code edits", async () => {
    const { hooks } = await setup()
    await expect(hooks["tool.execute.before"]!(input, { args: { filePath: "notes.md", content: "headless=True" } })).resolves.toBeUndefined()
    const output = afterOutput()
    await hooks["tool.execute.after"]!({ ...input, args: {} }, output)
    expect(output.output).toBe("saved")
  })

  it("catches duplicate violations, but tolerates line shifts of an existing one", async () => {
    const { hooks, root } = await setup()
    writeFileSync(path.join(root, "orchestrator/runtime.py"), "browser.launch(headless=True)")
    await expect(hooks["tool.execute.before"]!(input, { args: {
      filePath: "orchestrator/runtime.py", content: "# moved\nbrowser.launch(headless=True)",
    } })).resolves.toBeUndefined()
    await expect(hooks["tool.execute.before"]!(input, { args: {
      filePath: "orchestrator/runtime.py", content: "browser.launch(headless=True)\nbrowser.launch(headless=True)",
    } })).rejects.toThrow("headed-desktop")
  })

  it("checks config changes against desktop routing, not unrelated source", async () => {
    const { hooks, root } = await setup()
    writeFileSync(path.join(root, "orchestrator/config.py"), 'DESKTOP_BROWSER_TASK_QUEUE = "desktop-browser"\nBROWSER_RETRY_POLICY = RetryPolicy(maximum_attempts=1)')
    writeFileSync(path.join(root, "orchestrator/workflow.py"), 'tool = activity_as_tool(browser_activity, task_queue=DESKTOP_BROWSER_TASK_QUEUE, retry_policy=BROWSER_RETRY_POLICY)')
    await expect(hooks["tool.execute.before"]!(input, { args: {
      filePath: "orchestrator/config.py", content: 'DESKTOP_BROWSER_TASK_QUEUE = "perplexity-orchestrator"\nBROWSER_RETRY_POLICY = RetryPolicy(maximum_attempts=1)',
    } })).rejects.toThrow("native-browser-routing")
  })

  it("protects vendor paths for patch edits and renames", async () => {
    const { hooks } = await setup()
    for (const patchText of [
      "*** Begin Patch\n*** Update File: components/ai-elements/task.tsx\n@@\n+x\n*** End Patch",
      "*** Begin Patch\n*** Update File: lib/example.ts\n*** Move to: components/ai-elements/task.tsx\n*** End Patch",
    ]) {
      await expect(hooks["tool.execute.before"]!(patchInput, { args: { patchText } })).rejects.toThrow("protected")
    }
  })

  it("detects deleted required boundaries after patches without silently restoring files", async () => {
    const { hooks, root } = await setup()
    writeFileSync(path.join(root, "orchestrator/think_activity.py"), "# native think binding")
    const args = { patchText: "*** Begin Patch\n*** Delete File: orchestrator/think_activity.py\n*** End Patch" }
    await hooks["tool.execute.before"]!(patchInput, { args })
    rmSync(path.join(root, "orchestrator/think_activity.py"))
    const output = afterOutput()
    await hooks["tool.execute.after"]!({ ...patchInput, args }, output)
    expect(output.output).toContain("required-boundary")
  })

  it("blocks SDK signature and literal BrowserInput violations using installed Python packages", async () => {
    const { hooks, root } = await setup()
    symlinkSync(path.resolve("orchestrator/.venv"), path.join(root, "orchestrator/.venv"), "dir")
    // Interpreter selection is made when the plugin loads, as in a real project.
    const native = await DesktopGuard({ directory: root } as PluginInput)
    cleanup.push(async () => { await native.dispose?.() })
    for (const content of [
      'from temporalio.worker import Worker\nWorker(client, task_queue="q", task_queu="typo")',
      "from strands_tools.browser.models import BrowserInput\nBrowserInput(action={'type':'navigate','session_name':'native-contract'})",
    ]) {
      await expect(native["tool.execute.before"]!(input, { args: { filePath: "orchestrator/custom_activity.py", content } })).rejects.toThrow("python-call-contract")
    }
    expect(hooks["experimental.text.complete"]).toBeUndefined()
  })

  it("does not reject partial patch hunks, reports actual introduced errors afterward", async () => {
    const { hooks, root } = await setup()
    writeFileSync(path.join(root, "orchestrator/runtime.py"), "browser.launch(headless=False)")
    const args = patch("orchestrator/runtime.py")
    await hooks["tool.execute.before"]!(patchInput, { args })
    writeFileSync(path.join(root, "orchestrator/runtime.py"), "browser.launch(headless=True)")
    const output = afterOutput()
    await hooks["tool.execute.after"]!({ ...patchInput, args }, output)
    expect(output.output).toContain("NEW CONTRACT VIOLATIONS")
    expect(output.output).toContain("headed-desktop")
    expect(output.output).toContain("No files were reverted")
    expect(readFileSync(path.join(root, "orchestrator/runtime.py"), "utf8")).toContain("headless=True")
  })

  it("supports raw MCP content and underscore-qualified tool names", async () => {
    const { hooks, root } = await setup()
    const mcpInput = { ...input, tool: "filesystem_apply_patch" }
    const args = patch("orchestrator/runtime.py")
    await hooks["tool.execute.before"]!(mcpInput, { args })
    writeFileSync(path.join(root, "orchestrator/runtime.py"), "browser.launch(headless=True)")
    const raw = { content: [{ type: "text", text: "saved by MCP" }] }
    await hooks["tool.execute.after"]!({ ...mcpInput, args }, raw as unknown as Parameters<NonNullable<Hooks["tool.execute.after"]>>[1])
    expect(raw.content[0].text).toBe("saved by MCP")
    expect(raw.content[1].text).toContain("headed-desktop")
  })

  it("uses compiler errors for SDK calls without scanning unrelated broken files", async () => {
    const { hooks, root } = await setup()
    writeFileSync(path.join(root, "lib/sdk.ts"), "export function send(value: string) { return value }")
    writeFileSync(path.join(root, "lib/caller.ts"), 'import { send } from "./sdk"; send("valid")')
    writeFileSync(path.join(root, "lib/unrelated.ts"), "const broken: number = 'existing'")
    await expect(hooks["tool.execute.before"]!(input, { args: {
      filePath: "lib/sdk.ts", content: "export function send(value: number) { return value }",
    } })).rejects.toThrow("lib/caller.ts")
    await expect(hooks["tool.execute.before"]!(input, { args: {
      filePath: "lib/sdk.ts", content: "export function send(value: string) { return value.trim() }",
    } })).resolves.toBeUndefined()
  })

  it("reports compiler unavailability, never hides it as a pass or blocks repairs", async () => {
    const { hooks, root } = await setup()
    rmSync(path.join(root, "tsconfig.json"))
    const args = { filePath: "lib/new.ts", content: "export const value = 1" }
    await hooks["tool.execute.before"]!(input, { args })
    writeFileSync(path.join(root, "lib/new.ts"), args.content)
    const output = afterOutput()
    await hooks["tool.execute.after"]!({ ...input, args }, output)
    expect(output.output).toContain("CONTRACT REVIEW NEEDED")
    expect(output.output).toContain("tsconfig.json missing")
  })

  it("does not blame the edit for ambiguous concurrent content", async () => {
    const { hooks, root } = await setup()
    const args = { filePath: "orchestrator/runtime.py", content: "browser.launch(headless=False)" }
    await hooks["tool.execute.before"]!(input, { args })
    writeFileSync(path.join(root, "orchestrator/runtime.py"), "browser.launch(headless=True)")
    const output = afterOutput()
    await hooks["tool.execute.after"]!({ ...input, args }, output)
    expect(output.output).toContain("CONTRACT REVIEW NEEDED")
    expect(output.output).toContain("concurrent changes cannot be attributed safely")
  })

  it("isolates pending edits by both session and call ID", async () => {
    const { hooks, root } = await setup()
    await hooks["tool.execute.before"]!(patchInput, { args: patch("orchestrator/runtime.py") })
    writeFileSync(path.join(root, "orchestrator/runtime.py"), "browser.launch(headless=True)")
    const unrelated = afterOutput()
    await hooks["tool.execute.after"]!({ ...patchInput, sessionID: "another", args: {} }, unrelated)
    expect(unrelated.output).toBe("saved")
    const actual = afterOutput()
    await hooks["tool.execute.after"]!({ ...patchInput, args: {} }, actual)
    expect(actual.output).toContain("headed-desktop")
  })

  it("retains requirements during compaction without replacing conversation context", async () => {
    const { hooks } = await setup()
    const output = { context: ["existing"] }
    await hooks["experimental.session.compacting"]!({ sessionID: "test" }, output)
    expect(output.context[0]).toBe("existing")
    expect(output.context[1]).toContain("orchestrator/desktop/AGENTS.md")
  })
})
