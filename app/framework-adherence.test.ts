import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import { extname, join, normalize, relative, sep } from "node:path"

import { describe, expect, it } from "vitest"

// Framework-adherence suite (framework-adherence-reset, todo 18).
//
// Scans the frontend source for anti-patterns that the reset plan removes:
//   1. hand-rolled Web Streams / encoding (must use the AI SDK's streamable
//      text helpers, not `new ReadableStream` / `new TextEncoder` /
//      `.getReader` / `new EventSource`);
//   2. hardcoded model ids (preset:*, gemini-*, and vendor/model ids) — the
//      picker must discover models from the readiness catalog, never from
//      static strings. The sole allowed occurrence is `lib/perplexity.ts`'s
//      `DEFAULT_MODEL = "preset:high"` (the session default).
//   3. `asChild=` prop in components/ (the base-ui Button/Select must not be
//      used with Radix `asChild`); and
//   4. a vendored `reasoning.tsx` (ChainOfThought is the only reasoning UI).
//
// Each check asserts the TARGET (clean) state, so a check fails while its
// cleanup todo has not yet landed. That is expected mid-plan; the evidence
// file records per-check pass/fail honestly.

const ROOT = process.cwd()
const SCOPE_DIRS = ["app", "components/v0", "lib"]
const EXCLUDE_PARTS = [".worktrees", ".kilo", "node_modules"]

function isExcluded(dir: string): boolean {
  return EXCLUDE_PARTS.some((part) => dir.includes(part))
}

function collectFiles(dir: string, out: string[]): string[] {
  let entries: string[]
  try {
    entries = readdirSync(dir)
  } catch {
    return out
  }
  for (const entry of entries) {
    const full = join(dir, entry)
    if (isExcluded(full)) continue
    const stat = statSync(full)
    if (stat.isDirectory()) {
      collectFiles(full, out)
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      out.push(full)
    }
  }
  return out
}

function scopedSourceFiles(): string[] {
  const files: string[] = []
  for (const d of SCOPE_DIRS) {
    collectFiles(join(ROOT, d), files)
  }
  // Deduplicate and return as POSIX-ish repo-relative paths.
  return [...new Set(files)].map((f) => relative(ROOT, f)).sort()
}

// --- Check 1: no hand-rolled streaming / encoding primitives -----------------

const STREAM_TOKENS: Array<[label: string, re: RegExp]> = [
  ["new ReadableStream(", /new ReadableStream\(/],
  ["new TextEncoder(", /new TextEncoder\(/],
  [".getReader(", /\.getReader\(/],
  ["new EventSource(", /new EventSource\(/],
]

function streamViolations(files: string[]): Array<{ file: string; token: string; line: string }> {
  const hits: Array<{ file: string; token: string; line: string }> = []
  for (const file of files) {
    const content = readFileSync(join(ROOT, file), "utf8")
    for (const [token, re] of STREAM_TOKENS) {
      for (const line of content.split("\n")) {
        if (re.test(line)) {
          hits.push({ file, token, line: line.trim() })
        }
      }
    }
  }
  return hits
}

// --- Check 2: no hardcoded model ids except lib/perplexity.ts DEFAULT_MODEL ---

// Matches "preset:high", "gemini-3.8-flash", and "anthropic/claude-fable-5"
// style ids inside quotes.
const MODEL_ID_RE =
  /["'](preset:[a-z-]+|gemini-[0-9.]+-[a-z]+|(anthropic|openai|google|xai|perplexity)\/[a-z0-9.-]+)["']/

// The single permitted occurrence: lib/perplexity.ts line 4 (`DEFAULT_MODEL =
// "preset:high"`). Compute it from the live file so the exception cannot
// drift; if the line no longer matches, the check fails loudly.
function defaultModelLine(): { lineNo: number; text: string } | null {
  const file = join(ROOT, "lib", "perplexity.ts")
  if (!existsSync(file)) return null
  const lines = readFileSync(file, "utf8").split("\n")
  for (let i = 0; i < lines.length; i++) {
    const text = lines[i].trim()
    if (text.startsWith("export const DEFAULT_MODEL")) {
      return { lineNo: i + 1, text }
    }
  }
  return null
}

function modelIdViolations(files: string[]): Array<{ file: string; lineNo: number; line: string }> {
  const hits: Array<{ file: string; lineNo: number; line: string }> = []
  const allowed = defaultModelLine()
  for (const file of files) {
    const content = readFileSync(join(ROOT, file), "utf8")
    const lines = content.split("\n")
    for (let i = 0; i < lines.length; i++) {
      if (!MODEL_ID_RE.test(lines[i])) continue
      // Allow only the DEFAULT_MODEL line in lib/perplexity.ts.
      if (
        file === "lib/perplexity.ts" &&
        allowed &&
        i + 1 === allowed.lineNo &&
        lines[i] === allowed.text
      ) {
        continue
      }
      hits.push({ file, lineNo: i + 1, line: lines[i].trim() })
    }
  }
  return hits
}

// --- Check 3: no `asChild=` in components/** ----------------------------------

function asChildViolations(): Array<{ file: string; line: string }> {
  const hits: Array<{ file: string; line: string }> = []
  for (const file of scopedSourceFiles()) {
    if (!file.startsWith("components/")) continue
    const content = readFileSync(join(ROOT, file), "utf8")
    for (const line of content.split("\n")) {
      if (line.includes("asChild=")) hits.push({ file, line: line.trim() })
    }
  }
  return hits
}

// --- Check 4: no local reasoning.tsx -------------------------------------------

function reasoningTsxExists(): boolean {
  // Any `reasoning.tsx` under app/, components/, or lib/ (test files excluded).
  for (const file of scopedSourceFiles()) {
    const base = normalize(file).split(sep).pop() ?? ""
    if (base === "reasoning.tsx") return true
  }
  return false
}

describe("framework adherence", () => {
  const files = scopedSourceFiles()

  it("check 1: no new ReadableStream / new TextEncoder / .getReader / new EventSource", () => {
    const violations = streamViolations(files)
    // Known mid-plan violation: app/api/compare/route.ts (todo 17 deletes it).
    expect(violations, formatViolations("streaming primitives", violations.map((v) => `${v.file}: ${v.line}`))).toEqual([])
  })

  it("check 2: no hardcoded model ids except lib/perplexity.ts DEFAULT_MODEL", () => {
    // Sanity: the DEFAULT_MODEL line must still exist and match the model-id
    // shape, otherwise this exception is silently doing nothing.
    const allowed = defaultModelLine()
    expect(allowed, "lib/perplexity.ts must define export const DEFAULT_MODEL").not.toBeNull()
    expect(
      allowed?.text.match(MODEL_ID_RE),
      `DEFAULT_MODEL line must match a model id: ${allowed?.text}`
    ).not.toBeNull()

    const violations = modelIdViolations(files)
    // Known mid-plan violation: lib/perplexity.ts static preset/gemini lists
    // (todo 15 rewrites it). The DEFAULT_MODEL line itself is permitted.
    expect(violations, formatViolations("model ids", violations.map((v) => `${v.file}:${v.lineNo}: ${v.line}`))).toEqual([])
  })

  it("check 3: no asChild= in components/**", () => {
    const violations = asChildViolations()
    expect(violations, formatViolations("asChild", violations.map((v) => `${v.file}: ${v.line}`))).toEqual([])
  })

  it("check 4: no local reasoning.tsx file exists", () => {
    expect(reasoningTsxExists(), "found a reasoning.tsx under app/, components/, or lib/").toBe(false)
  })
})

function formatViolations(what: string, entries: string[]): string {
  if (entries.length === 0) return `expected no ${what} violations`
  return `found ${entries.length} ${what} violation(s):\n${entries.map((e) => `  - ${e}`).join("\n")}`
}