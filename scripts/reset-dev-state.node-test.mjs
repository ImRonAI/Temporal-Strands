import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { afterEach, test } from "node:test"
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"

const roots = []
afterEach(() => roots.splice(0).forEach((root) => rmSync(root, { recursive: true, force: true })))

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "gwen-reset-state-"))
  roots.push(root)
  mkdirSync(join(root, "scripts"))
  copyFileSync(new URL("./reset-dev-state.mjs", import.meta.url), join(root, "scripts/reset-dev-state.mjs"))
  const create = (file) => {
    mkdirSync(dirname(join(root, file)), { recursive: true })
    writeFileSync(join(root, file), "fixture")
  }
  const run = (overrides = {}) => spawnSync(process.execPath, [join(root, "scripts/reset-dev-state.mjs")], {
    encoding: "utf8", timeout: 10_000,
    env: { ...process.env, DESKTOP_ARTIFACT_ROOT: join(root, "artifacts"), NEXT_DIST_DIR: ".next", ...overrides },
  })
  return { root, create, run }
}

test("only disposable startup state is cleared, leaving sources and reports intact", () => {
  const f = fixture()
  const disposable = [".next/dev/lock", "orchestrator/__pycache__/workflow.pyc",
    "orchestrator/.models/__pycache__/perplexity.pyc", "orchestrator/.runtime/browser-observations/old.jpg",
    "artifacts/runtime.json", "artifacts/desktop-worker.lock", `artifacts/${"a".repeat(64)}/image.png`]
  const preserved = ["orchestrator/.models/perplexity.py", "orchestrator/.agent-files/report.pdf",
    "orchestrator/journal/research.txt", "orchestrator/subagent_packages.json",
    "orchestrator/.venv/bin/python", "node_modules/package/index.js", "orchestrator/tests/__pycache__/tracked.pyc",
    ".next-desktop-acceptance/cache", "artifacts/unrelated.txt"]
  for (const file of [...disposable, ...preserved]) f.create(file)
  const result = f.run()
  assert.equal(result.status, 0, result.stderr)
  for (const file of disposable) assert.equal(existsSync(join(f.root, file)), false, file)
  for (const file of preserved) assert.equal(existsSync(join(f.root, file)), true, file)
  assert.equal(f.run().status, 0, "cleanup must be idempotent")
})

test("custom Next dist is scoped to a cache name, never an arbitrary directory", () => {
  const f = fixture()
  f.create(".next-custom/dev/lock")
  f.create(".next/dev/lock")
  assert.equal(f.run({ NEXT_DIST_DIR: ".next-custom" }).status, 0)
  assert.equal(existsSync(join(f.root, ".next/dev/lock")), true)
  for (const dist of [".", "..", "orchestrator", "/tmp", ".next/../../source"]) {
    assert.equal(f.run({ NEXT_DIST_DIR: dist }).status, 1, dist)
  }
})

test("unsafe artifact directories and symlinked cache parents fail before deleting", () => {
  const f = fixture()
  f.create(".next/dev/lock")
  f.create("elsewhere/__pycache__/keep.pyc")
  symlinkSync(join(f.root, "elsewhere"), join(f.root, "orchestrator"))
  const result = f.run()
  assert.equal(result.status, 1)
  assert.match(result.stderr, /Refusing symlinked/)
  assert.equal(existsSync(join(f.root, ".next/dev/lock")), true)
  const other = fixture()
  other.create(".next/dev/lock")
  assert.equal(other.run({ DESKTOP_ARTIFACT_ROOT: other.root }).status, 1)
  assert.equal(existsSync(join(other.root, ".next/dev/lock")), true)
})
