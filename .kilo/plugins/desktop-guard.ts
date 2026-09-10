import type { Plugin } from "@kilocode/plugin"
import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs"
import path from "node:path"
import { checkSources, checkPythonContracts, formatFindings, inScope, LIVE_GATES, REQUIRED } from "../../scripts/desktop-guard.mjs"
import { createContractChecker, isTypedSource, newDiagnostics } from "../../scripts/framework-contracts.mjs"

type Change = { file: string; before: string | null; after?: string | null }
type Finding = { file: string; line: number; rule: string; message: string; severity?: string }

// Native Kilo hooks only. No file rollback, product runtime imports, shell
// execution inspection, speculative wrapper bans, or response rewriting.
export const DesktopGuard: Plugin = async ({ directory }) => {
  const root = realpathSync(directory)
  const python = path.join(root, "orchestrator/.venv/bin/python")
  const interpreter = existsSync(python) ? python : "python3"
  const compiler = createContractChecker(root)
  const pending = new Map<string, Change[]>()
  const structuralCache = new Map<string, Finding[]>()
  const limit = 1024 * 1024
  const reminder = "Desktop work must follow orchestrator/desktop/AGENTS.md and components/v0/AGENTS.md. " + LIVE_GATES

  function relative(file: string) {
    const absolute = path.resolve(root, file)
    const result = path.relative(root, absolute).split(path.sep).join("/")
    if (result.startsWith("../") || path.isAbsolute(result)) return null
    return result
  }

  function source(file: string): string | null {
    let current = root
    for (const name of file.split("/")) {
      current = path.join(current, name)
      if (existsSync(current) && lstatSync(current).isSymbolicLink()) {
        throw new Error(`Guard cannot attribute edits through symlinks: ${file}`)
      }
    }
    if (!existsSync(current)) return null
    const stat = lstatSync(current)
    if (!stat.isFile() || stat.size > limit) throw new Error(`Guard cannot inspect oversized/non-file source: ${file}`)
    return readFileSync(current, "utf8")
  }

  function protect(file: string) {
    if (file.startsWith("components/ai-elements/") || file.startsWith("orchestrator/.venv/")) {
      throw new Error(`Desktop guard: installed frameworks and vendored AI Elements are protected: ${file}. Compose their documented APIs instead.`)
    }
  }

  function relevant(file: string) {
    return isTypedSource(file) || inScope(file)
  }

  // Full source snapshots avoid parsing diff hunks as Python/TS programs. Patches
  // are checked after the tool writes them; a full write/edit can be preflighted
  // through an in-memory compiler overlay without modifying the user's files.
  function changes(tool: string, args: Record<string, unknown>): Change[] {
    const files = new Set<string>()
    if (tool === "apply_patch") {
      const patch = args.patchText ?? args.patch
      if (typeof patch !== "string") return []
      for (const match of patch.matchAll(/^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$/gm)) {
        const file = relative(match[1])
        if (file) { protect(file); if (relevant(file)) files.add(file) }
      }
      return [...files].map(file => ({ file, before: source(file) }))
    }
    if (!["write", "edit"].includes(tool)) return []
    const name = args.filePath ?? args.file_path ?? args.path
    if (typeof name !== "string") return []
    const file = relative(name)
    if (!file) return []
    protect(file)
    if (!relevant(file)) return []
    const before = source(file)
    if (tool === "write" && typeof args.content === "string") return [{ file, before, after: args.content }]
    const oldText = args.oldString ?? args.old_string
    const newText = args.newString ?? args.new_string
    if (before !== null && typeof oldText === "string" && typeof newText === "string" && oldText.length) {
      const occurrences = before.split(oldText).length - 1
      const all = args.replaceAll ?? args.replace_all
      if (occurrences === 1 || (all && occurrences > 0)) {
        return [{ file, before, after: all ? before.split(oldText).join(newText) : before.replace(oldText, () => newText) }]
      }
    }
    return [{ file, before }]
  }

  function structuralSnapshot(changed: Change[], version: "before" | "after") {
    const scoped = changed.filter(change => inScope(change.file))
    if (!scoped.length) return []
    const selected = new Set(scoped.map(change => change.file))
    const config = "orchestrator/config.py"
    if (scoped.some(change => change.file.endsWith(".py"))) selected.add(config)
    if (selected.has(config) && scoped.some(change => change.file === config)) {
      selected.add("orchestrator/workflow.py")
      selected.add("orchestrator/desktop_worker.py")
    }
    const files: Record<string, string> = {}
    for (const file of selected) {
      const text = source(file)
      if (text !== null) files[file] = text
    }
    for (const change of scoped) {
      const value = change[version]
      if (value === null) delete files[change.file]
      else if (value !== undefined) files[change.file] = value
    }
    const key = JSON.stringify(files)
    const cached = structuralCache.get(key)
    if (cached) return cached
    const findings = checkSources(files, interpreter, REQUIRED.filter(file => scoped.some(change => change.file === file))) as Finding[]
    try {
      findings.push(...checkPythonContracts(files, interpreter))
    } catch (error) {
      findings.push({ file: "desktop-guard", line: 1, rule: "check-unavailable", severity: "warning", message: String(error) })
    }
    if (structuralCache.size >= 16) structuralCache.delete(structuralCache.keys().next().value!)
    structuralCache.set(key, findings)
    return findings
  }

  function regressions(changed: Change[]): Finding[] {
    const warnings: Finding[] = []
    let before: Finding[] = []
    let after: Finding[] = []
    try {
      before = [...structuralSnapshot(changed, "before")]
      after = [...structuralSnapshot(changed, "after")]
    } catch (error) {
      warnings.push({ file: "desktop-guard", line: 1, rule: "check-unavailable", severity: "warning", message: String(error) })
    }
    const typed = changed.filter(change => isTypedSource(change.file))
    if (typed.length) {
      try {
        const files = typed.map(change => change.file)
        const base = compiler.diagnostics(files, Object.fromEntries(typed.map(change => [change.file, change.before])))
        const next = compiler.diagnostics(files, Object.fromEntries(typed.map(change => [change.file, change.after])))
        if ([...base, ...next].some(item => item.file === "tsconfig.json")) {
          warnings.push({ file: "tsconfig.json", line: 1, rule: "check-unavailable", severity: "warning",
            message: "Invalid TypeScript configuration: source contracts could not be verified" })
        }
        before.push(...base)
        after.push(...next)
      } catch (error) {
        warnings.push({ file: "desktop-guard", line: 1, rule: "check-unavailable", severity: "warning", message: String(error) })
      }
    }
    // An unavailable validator is not a baseline code error. Even when the
    // before/after warning is identical, the edit has NOT been validated.
    const unavailable = after.filter(item => item.rule.includes("unavailable"))
    return [...newDiagnostics(before, after).filter((item: Finding) => !item.rule.includes("unavailable")), ...unavailable, ...warnings]
  }

  const instruction = "Correct only the offending change using the installed SDK/component contract; preserve concurrent edits. No files were reverted."
  const toolName = (name: string) => {
    const last = name.split(/[.:]/).pop() ?? name
    return ["apply_patch", "edit", "write"].find(tool => last === tool || last.endsWith(`_${tool}`))
  }
  function appendDiagnostic(output: unknown, text: string) {
    if (!output || typeof output !== "object") return
    // MCP dispatch passes the raw {content} result despite the SDK declaration
    // advertising {output}. Mutate the original object in either case.
    if ("content" in output && Array.isArray(output.content)) output.content.push({ type: "text", text })
    else if ("output" in output && typeof output.output === "string") output.output += `\n\n${text}`
  }
  return {
    dispose: async () => { compiler.dispose(); pending.clear(); structuralCache.clear() },
    "experimental.session.compacting": async (_input, output) => { output.context.push(reminder) },
    "tool.execute.before": async (input, output) => {
      const tool = toolName(input.tool)
      if (!tool) return
      const args = output.args as Record<string, unknown>
      const edits = changes(tool, args)
      const key = `${input.sessionID}:${input.callID}`
      pending.delete(key)
      if (!edits.length) return
      const known = edits.filter(change => change.after !== undefined && change.before !== change.after)
      const findings = known.length ? regressions(known) : []
      const errors = findings.filter(item => item.severity !== "warning")
      if (errors.length) throw new Error(`New contract violations reject this edit:\n${formatFindings(errors)}\n${instruction}`)
      // Bound abandoned entries from tools that fail without an after event.
      if (pending.size >= 128) pending.delete(pending.keys().next().value!)
      pending.set(key, edits)
    },
    "tool.execute.after": async (input, output) => {
      const key = `${input.sessionID}:${input.callID}`
      const edits = pending.get(key)
      pending.delete(key)
      if (!edits) return
      try {
        const actual = edits.map(change => ({ ...change, after: source(change.file) }))
          .filter(change => change.before !== change.after)
        if (!actual.length) return
        // A full edit producing different content may include a concurrent writer
        // or tool normalization. Diagnose it, but don't attribute it as certain.
        const concurrent = edits.some(change => change.after !== undefined && source(change.file) !== change.after)
        const findings = regressions(actual)
        if (findings.length) {
          const review = concurrent || findings.every(item => item.severity === "warning")
          appendDiagnostic(output, `${review ? "CONTRACT REVIEW NEEDED" : "NEW CONTRACT VIOLATIONS"}:\n${formatFindings(findings)}\n${instruction}` +
            (concurrent ? "\nActual content differs from the proposed edit; concurrent changes cannot be attributed safely." : ""))
        }
      } catch (error) {
        appendDiagnostic(output, `CONTRACT CHECK UNAVAILABLE: ${String(error)}. Not a pass; run pnpm check:desktop and npx tsc --noEmit.`)
      }
    },
  }
}
