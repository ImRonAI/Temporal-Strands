import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"
import ts from "typescript"
import { createContractChecker, isTypedSource } from "./framework-contracts.mjs"

const PYTHON_CHECK = fileURLToPath(new URL("./check_desktop_python.py", import.meta.url))
export const REQUIRED = [
  "orchestrator/browser_activity.py", "orchestrator/desktop_worker.py",
  "orchestrator/workflow.py", "orchestrator/run_worker.py", "orchestrator/think_activity.py",
  "orchestrator/desktop/Dockerfile", "orchestrator/desktop/start-desktop.sh",
  "components/v0/computer-use.ts", "components/v0/computer-use-activity.tsx",
  "components/v0/computer-use-preview.tsx",
]
export const LIVE_GATES = "Static checks cannot prove: model screenshot pixels, native VNC revocation, human input, same-session live continuation, or complete real-time action delivery. The real UI acceptance sequence in orchestrator/desktop/AGENTS.md is still required."

export function inScope(file) {
  return /^(orchestrator\/[^/]+\.py|orchestrator\/desktop\/|components\/v0\/(?:computer-use(?:-activity|-preview)?|agent-activity|agent-chat)\.[tj]sx?$|public\/.*computer.*\.html$)/.test(file)
    && !/\.test\.[tj]sx?$/.test(file) && !file.endsWith("AGENTS.md")
}

export function scanSource(file, source) {
  const findings = []
  const add = (rule, message, pos = 0) => findings.push({ file, line: source.slice(0, pos).split("\n").length, rule, message })
  if (/^(orchestrator|desktop)\/(?:remote_browser\.py|browser_observation\.py|desktop-control\.mjs)$/.test(file)) {
    add("removed-facade", "Review this former facade boundary; a filename alone cannot prove redundant implementation")
    findings.at(-1).severity = "warning"
  }
  if (/\.tsx?$/.test(file)) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith("tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
    const imports = new Map()
    for (const stmt of tree.statements) {
      if (ts.isImportDeclaration(stmt) && ts.isStringLiteral(stmt.moduleSpecifier)) {
        const bindings = stmt.importClause?.namedBindings
        if (bindings && ts.isNamedImports(bindings)) {
          for (const element of bindings.elements) imports.set(element.name.text, {
            name: element.propertyName?.text ?? element.name.text, from: stmt.moduleSpecifier.text,
          })
        }
      }
    }
    const nativeTags = new Set()
    function visit(node) {
      if (ts.isStringLiteral(node) && (/computer-use-live\.html|Page\.startScreencast/.test(node.text))) {
        add("novnc-viewer", "No CDP screenshot player in the desktop preview", node.pos)
      }
      if (ts.isAsExpression(node) && node.type.kind === ts.SyntaxKind.AnyKeyword) {
        add("native-types", "Do not cast away component or transport types with as any", node.pos)
      }
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        const imported = imports.get(node.tagName.getText(tree))
        if (imported?.from.startsWith("@/components/ai-elements/")) {
          const name = imported.name
          nativeTags.add(name)
          for (const prop of node.attributes.properties) {
            if (!ts.isJsxAttribute(prop)) continue
            if (name === "WebPreviewBody" && prop.name.text === "src" && prop.initializer &&
                prop.initializer.getText(tree) === "{preview.url}") {
              add("novnc-viewer", "Iframe the noVNC viewer, not the visited website", prop.pos)
            }
          }
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(tree)
    const required = file === "components/v0/computer-use-activity.tsx"
      ? ["Task", "TaskTrigger", "TaskContent", "TaskItem", "ChainOfThought", "ChainOfThoughtStep", "ToolInput", "ToolOutput"]
      : file === "components/v0/computer-use-preview.tsx" ? ["WebPreview", "WebPreviewBody", "PromptInput", "PromptInputTextarea", "PromptInputSubmit"] : []
    for (const name of required) if (!nativeTags.has(name)) {
      add("native-composition", `Review native ${name} composition: it may have been removed or moved to a child component`)
      findings.at(-1).severity = "warning"
    }
    for (const match of source.matchAll(/@ts-(?:ignore|nocheck)/g)) add("native-types", "Do not suppress component type checking", match.index)
  }
  if (/\/(?:Dockerfile[^/]*|[^/]+\.sh)$/.test(file)) {
    const lines = source.split("\n")
    lines.forEach((line, index) => {
      if (line.trimStart().startsWith("#")) return
      if (/STRANDS_BROWSER_HEADLESS\s*=\s*["']?(?:true|1)\b|--headless\b|--no-sandbox\b/.test(line)) {
        findings.push({ file, line: index + 1, rule: "headed-sandbox", message: "Require headed, sandboxed Chromium in Linux" })
      }
      if (/^\s*COPY\s+\.\s+\.\s*$/i.test(line)) {
        findings.push({ file, line: index + 1, rule: "source-only-image", message: "Use explicit source COPY entries; do not copy the entire orchestrator including runtime/credentials" })
      }
    })
  }
  return findings
}

export function collectSources(root) {
  const files = {}
  const walk = (relative) => {
    const absolute = path.join(root, relative)
    if (!fs.existsSync(absolute)) return
    const stat = fs.lstatSync(absolute)
    if (stat.isSymbolicLink()) throw new Error(`Guard refuses symlink source: ${relative}`)
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(absolute)) {
        if (name.startsWith(".") || ["node_modules", "tests", "screenshots", "__pycache__", "strands-tools"].includes(name)) continue
        if (relative === "orchestrator" && name !== "desktop" && fs.lstatSync(path.join(absolute, name)).isDirectory()) continue
        walk(`${relative}/${name}`)
      }
    } else if (inScope(relative) && /(?:\.[tj]sx?|\.py|\.sh|\.mjs|\/Dockerfile[^/]*)$/.test(relative)) {
      if (stat.size > 1024 * 1024) throw new Error(`Guard source exceeds 1 MiB: ${relative}`)
      files[relative] = fs.readFileSync(absolute, "utf8")
    }
  }
  for (const dir of ["orchestrator", "components/v0"]) walk(dir)
  // Removed files are checked by existence without reading their contents.
  for (const file of ["desktop/desktop-control.mjs", "public/computer-use-live.html"]) {
    if (fs.existsSync(path.join(root, file))) files[file] = ""
  }
  return files
}

export function checkPythonContracts(files, python) {
  if (!Object.keys(files).some(file => file.endsWith(".py"))) return []
  const result = spawnSync(python, ["-I", PYTHON_CHECK, "--contracts"], {
    input: JSON.stringify(files), encoding: "utf8", timeout: 15000, maxBuffer: 1024 * 1024,
  })
  if (result.error || result.status !== 0) throw new Error(`Python native contract check unavailable: ${result.error?.message ?? result.stderr}`)
  return JSON.parse(result.stdout)
}

export function checkSources(files, python = "python3", required = REQUIRED) {
  const findings = []
  for (const file of required) if (!(file in files)) findings.push({ file, line: 1, rule: "required-boundary", message: "Required desktop boundary missing; do not remove functionality to pass compliance" })
  for (const [file, source] of Object.entries(files)) findings.push(...scanSource(file, source))
  if ("public/computer-use-live.html" in files) findings.push({ file: "public/computer-use-live.html", line: 1, rule: "novnc-viewer", message: "Removed CDP screenshot viewer must not return" })
  const result = spawnSync(python, ["-I", PYTHON_CHECK], { input: JSON.stringify(files), encoding: "utf8", timeout: 15000, maxBuffer: 1024 * 1024 })
  if (result.error || result.status !== 0) throw new Error(`Python AST guard failed: ${result.error?.message ?? result.stderr}`)
  findings.push(...JSON.parse(result.stdout))
  return findings.sort((a, b) => `${a.file}:${a.line}:${a.rule}`.localeCompare(`${b.file}:${b.line}:${b.rule}`))
}

export function checkRepository(root) {
  const python = path.join(root, "orchestrator/.venv/bin/python")
  return checkSources(collectSources(root), fs.existsSync(python) ? python : "python3")
}

export function checkContracts(root, files) {
  if (!files.length) throw new Error("Provide explicit changed source paths for contract checking")
  files = files.map(file => {
    const relative = path.relative(root, path.resolve(root, file)).split(path.sep).join("/")
    if (relative.startsWith("../") || path.isAbsolute(relative) || !(isTypedSource(relative) || (inScope(relative) && relative.endsWith(".py")))) {
      throw new Error(`Not an allowed contract source: ${file}`)
    }
    return relative
  })
  const checker = createContractChecker(root)
  try {
    const typed = files.filter(isTypedSource)
    const findings = typed.length ? checker.diagnostics(typed) : []
    const sources = {}
    for (const file of files.filter(file => file.endsWith(".py"))) {
      const absolute = path.resolve(root, file)
      if (fs.realpathSync(absolute) !== absolute || fs.statSync(absolute).size > 1024 * 1024) throw new Error(`Unsafe contract source: ${file}`)
      sources[file] = fs.readFileSync(absolute, "utf8")
    }
    const python = path.join(root, "orchestrator/.venv/bin/python")
    return [...findings, ...checkPythonContracts(sources, fs.existsSync(python) ? python : "python3")]
  } finally { checker.dispose() }
}

export function formatFindings(findings) {
  return findings.map(item => `${item.file}:${item.line} [${item.rule}] ${item.message}`).join("\n")
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2)
    const findings = args[0] === "--contracts" ? checkContracts(process.cwd(), args.slice(1)) : checkRepository(process.cwd())
    console.log(findings.length ? formatFindings(findings) : `${args[0] === "--contracts" ? "Scoped compiler/SDK contract" : "Desktop structural"} checks passed (not feature acceptance).`)
    console.log(LIVE_GATES)
    process.exitCode = findings.some(item => item.rule.includes("unavailable")) ? 2 : findings.some(item => item.severity !== "warning") ? 1 : 0
  } catch (error) {
    console.error(`Desktop guard failed closed: ${error.message}`)
    process.exitCode = 2
  }
}
