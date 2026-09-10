import assert from "node:assert/strict"
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs"
import path from "node:path"
import os from "node:os"
import { test } from "node:test"
import { createContractChecker, newDiagnostics } from "./framework-contracts.mjs"

function project() {
  const root = mkdtempSync(path.join(os.tmpdir(), "framework-contracts-"))
  mkdirSync(path.join(root, "components/v0"), { recursive: true })
  mkdirSync(path.join(root, "lib"))
  symlinkSync(path.resolve("node_modules"), path.join(root, "node_modules"), "dir")
  writeFileSync(path.join(root, "tsconfig.json"), JSON.stringify({
    compilerOptions: { strict: true, jsx: "react-jsx", target: "ES2022", module: "esnext", moduleResolution: "bundler", skipLibCheck: true, noEmit: true },
    include: ["components/**/*.tsx", "lib/**/*.ts"],
  }))
  const write = (file, source) => writeFileSync(path.join(root, file), source)
  const checker = createContractChecker(root)
  return { root, write, checker, close() { checker.dispose(); rmSync(root, { recursive: true, force: true }) } }
}

test("real React JSX typing respects aliases, spreads and feature-specific props", () => {
  const p = project()
  try {
    const file = "components/v0/sample.tsx"
    const base = 'import type { ComponentProps } from "react";\nfunction Feature(p: ComponentProps<"button"> & { domainStatus: string }) { return <button>{p.domainStatus}</button> }\n'
    const valid = `${base}const props = { domainStatus: "ready", disabled: false }; export const el = <Feature {...props} />`
    p.write(file, valid)
    assert.deepEqual(p.checker.diagnostics([file]), [])
    const errors = p.checker.diagnostics([file], { [file]: `${base}export const el = <Feature domainStatus="ready" madeUpProp="oops" />` })
    assert.ok(errors.some(error => error.message.includes("madeUpProp") && error.rule.startsWith("TS")))
    assert.deepEqual(p.checker.diagnostics([file]), []) // rejected overlay never changes disk
    assert.ok(p.checker.diagnostics([file], { [file]: `${base}export const el = <Feature />` }).some(error => error.message.includes("domainStatus")))
  } finally { p.close() }
})

test("changed exports check importers but not unrelated existing failures", () => {
  const p = project()
  try {
    p.write("lib/sdk.ts", "export function send(value: string) { return value }")
    p.write("lib/caller.ts", 'import { send as invoke } from "./sdk"; invoke("valid")')
    p.write("lib/unrelated.ts", "const broken: number = 'existing'")
    assert.deepEqual(p.checker.diagnostics(["lib/sdk.ts"]), [])
    const errors = p.checker.diagnostics(["lib/sdk.ts"], { "lib/sdk.ts": "export function send(value: number) { return value }" })
    assert.ok(errors.some(error => error.file === "lib/caller.ts"))
    assert.ok(!errors.some(error => error.file === "lib/unrelated.ts"))
  } finally { p.close() }
})

test("new sources, SDK calls, and syntax use compiler diagnostics", () => {
  const p = project()
  try {
    p.write("lib/sdk.ts", "export const sdk = { connect(options: { url: string }) {} }")
    const errors = p.checker.diagnostics(["lib/new.ts"], { "lib/new.ts": 'import { sdk } from "./sdk"; sdk.connect({ urll: "bad" }); sdk.notReal()' })
    assert.ok(errors.some(error => error.message.includes("urll")))
    assert.ok(errors.some(error => error.message.includes("notReal")))
    assert.ok(p.checker.diagnostics(["lib/new.ts"], { "lib/new.ts": "const = ;" }).length > 0)
  } finally { p.close() }
})

test("deleted dependency still checks prior importing callers", () => {
  const p = project()
  try {
    p.write("lib/sdk.ts", "export const value = 1")
    p.write("lib/caller.ts", 'import { value } from "./sdk"; console.log(value)')
    assert.deepEqual(p.checker.diagnostics(["lib/sdk.ts"]), [])
    const errors = p.checker.diagnostics(["lib/sdk.ts"], { "lib/sdk.ts": null })
    assert.ok(errors.some(error => error.file === "lib/caller.ts" && error.message.includes("Cannot find module")))
  } finally { p.close() }
})

test("actual vendored AI Elements declarations validate aliases and props without a parallel prop catalog", () => {
  const checker = createContractChecker(process.cwd())
  const file = "components/v0/guard-contract-fixture.tsx"
  try {
    const imports = 'import { Task as NativeTask, TaskTrigger } from "@/components/ai-elements/task";\nimport { ChainOfThoughtStep } from "@/components/ai-elements/chain-of-thought";\n'
    const valid = imports + 'const header = { title: "Browser" }; export const UI = () => <NativeTask defaultOpen><TaskTrigger {...header} /><ChainOfThoughtStep label="Navigate" status="active" /></NativeTask>'
    assert.deepEqual(checker.diagnostics([file], { [file]: valid }), [])
    const invalid = imports + 'export const UI = () => <NativeTask status="running"><TaskTrigger /><ChainOfThoughtStep label="Navigate" status="failed" /></NativeTask>'
    const errors = checker.diagnostics([file], { [file]: invalid })
    assert.ok(errors.some(error => error.message.includes("status") && error.message.includes("does not exist")))
    assert.ok(errors.some(error => error.message.includes("title") && error.message.includes("missing")))
    assert.ok(errors.some(error => error.message.includes("failed")))
  } finally { checker.dispose() }
})

test("dependency edits outside overlay invalidate cached declarations", () => {
  const p = project()
  try {
    p.write("lib/sdk.ts", "export const value: string = 'ok'")
    p.write("lib/caller.ts", 'import { value } from "./sdk"; const stringOnly: string = value')
    assert.deepEqual(p.checker.diagnostics(["lib/caller.ts"]), [])
    p.write("lib/sdk.ts", "export const value: number = 42")
    assert.ok(p.checker.diagnostics(["lib/caller.ts"]).length > 0)
  } finally { p.close() }
})

test("baseline comparison tolerates line movement but not duplicate or cross-file regressions", () => {
  const item = { file: "lib/one.ts", line: 1, rule: "TS123", message: "bad argument" }
  assert.deepEqual(newDiagnostics([item], [{ ...item, line: 30 }]), [])
  assert.equal(newDiagnostics([item], [item, { ...item, line: 31 }]).length, 1)
  assert.equal(newDiagnostics([item], [{ ...item, file: "lib/two.ts" }]).length, 1)
})

test("missing or invalid configuration never reports an implicit pass", () => {
  const p = project()
  try {
    p.write("lib/sdk.ts", "export const value = 1")
    writeFileSync(path.join(p.root, "tsconfig.json"), "not json")
    assert.ok(p.checker.diagnostics(["lib/sdk.ts"]).some(error => error.file === "tsconfig.json"))
    rmSync(path.join(p.root, "tsconfig.json"))
    assert.throws(() => p.checker.diagnostics(["lib/sdk.ts"]), /tsconfig.json missing/)
  } finally { p.close() }
})
