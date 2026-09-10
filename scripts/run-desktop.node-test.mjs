import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { afterEach, test } from "node:test"
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const sources = [
  ".dockerignore", "desktop/Dockerfile", "desktop/requirements.txt",
  "desktop/start-desktop.sh", "desktop/seccomp.json",
  "browser_activity.py", "computer_use_activity.py", "desktop_observation.py",
  "workspace_state.py", "desktop_worker.py", "config.py", "telemetry.py",
]
const temporaryRoots = []
afterEach(() => {
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true })
})

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "desktop-startup-"))
  temporaryRoots.push(root)
  for (const path of ["scripts", "orchestrator/desktop", "bin"]) {
    mkdirSync(join(root, path), { recursive: true })
  }
  copyFileSync(new URL("./run-desktop.sh", import.meta.url), join(root, "scripts/run-desktop.sh"))
  for (const source of sources) writeFileSync(join(root, "orchestrator", source), `${source}\n`)
  writeFileSync(join(root, "orchestrator", "not-allowlisted.txt"), "must not reach Docker")
  writeFileSync(join(root, "bin/id"), `#!/usr/bin/env node
process.stdout.write(process.env.MOCK_UID || "501")
`, { mode: 0o755 })
  writeFileSync(join(root, "bin/docker"), `#!/usr/bin/env node
const fs = require("node:fs")
const args = process.argv.slice(2)
fs.appendFileSync(process.env.MOCK_LOG, JSON.stringify({ args, buildkit: process.env.DOCKER_BUILDKIT }) + "\\n")
if (args[0] !== "--context" || args[1] !== "mock-desktop") process.exit(99)
const command = args.slice(2)
if (command[0] === "info") process.exit(0)
if (command[0] === "buildx") process.exit(process.env.MOCK_BUILDX === "no" ? 1 : 0)
if (command[0] === "build") {
  fs.writeFileSync(process.env.MOCK_TAR, fs.readFileSync(0))
  if (process.env.MOCK_BUILD_FAIL === "yes") process.exit(1)
  fs.writeFileSync(process.env.MOCK_IMAGE, command[command.indexOf("--label") + 1].split("=")[1])
  process.exit(0)
}
if (command[0] === "image" && command[1] === "inspect") {
  if (!fs.existsSync(process.env.MOCK_IMAGE)) process.exit(1)
  console.log("sha256:verified " + fs.readFileSync(process.env.MOCK_IMAGE, "utf8"))
  process.exit(0)
}
if (command[0] === "container" && command[1] === "inspect") process.exit(process.env.MOCK_COLLISION === "yes" ? 0 : 1)
if (command[0] === "run") process.exit(0)
process.exit(99)
`, { mode: 0o755 })
  const log = join(root, "docker.jsonl")
  const image = join(root, "image-label")
  const archive = join(root, "context.tar")
  function run(mode = "start", overrides = {}) {
    writeFileSync(log, "")
    const result = spawnSync("bash", [join(root, "scripts/run-desktop.sh"), mode], {
      encoding: "utf8",
      timeout: 10_000,
      env: {
        ...process.env,
        PATH: `${join(root, "bin")}:${process.env.PATH}`,
        DESKTOP_DOCKER_CONTEXT: "mock-desktop",
        DESKTOP_ARTIFACT_ROOT: join(root, "artifacts"),
        DESKTOP_TEMPORAL_ADDRESS: "mock-temporal:7233",
        MOCK_LOG: log, MOCK_IMAGE: image, MOCK_TAR: archive,
        MOCK_UID: "501", MOCK_BUILDX: "yes", MOCK_COLLISION: "no", MOCK_BUILD_FAIL: "no",
        DOCKER_BUILDKIT: "inherited",
        ...overrides,
      },
    })
    assert.equal(result.error, undefined)
    return {
      ...result,
      calls: readFileSync(log, "utf8").trim().split("\n").filter(Boolean).map(JSON.parse),
    }
  }
  return { root, run, image, archive }
}

function commands(result) {
  return result.calls.map(({ args }) => args[2])
}

test("pnpm exposes explicit preparation without adding a build to startup", () => {
  const { scripts } = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"))
  assert.equal(scripts["build:desktop"], "bash scripts/run-desktop.sh build")
  assert.match(scripts["dev:desktop"], /bash scripts\/run-desktop.sh$/)
  assert.doesNotMatch(scripts["dev:all"], /build:desktop/)
})

test("missing image fails clearly and never builds or runs", () => {
  const result = fixture().run()
  assert.equal(result.status, 1)
  assert.match(result.stderr, /image .*missing.*mock-desktop.*pnpm build:desktop/)
  assert.deepEqual(commands(result), ["info", "image"])
})

test("explicit build uses only the source allowlist, host UID and BuildKit when available", () => {
  const f = fixture()
  const result = f.run("build", { DOCKER_BUILDKIT: "0" })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(commands(result), ["info", "buildx", "build"])
  const build = result.calls.at(-1)
  assert.equal(build.buildkit, "1")
  assert.deepEqual(build.args.slice(3, 5), ["--build-arg", "DESKTOP_UID=501"])
  assert.match(build.args[build.args.indexOf("--label") + 1], /^io\.gwen\.desktop\.source-hash=[a-f0-9]{64}$/)
  assert.deepEqual(build.args.slice(-5), ["-t", "gwen-desktop:native", "-f", "desktop/Dockerfile", "-"])
  const archive = spawnSync("tar", ["-tf", f.archive], { encoding: "utf8" })
  assert.equal(archive.status, 0, archive.stderr)
  assert.deepEqual(archive.stdout.trim().split("\n").sort(), [...sources].sort())

  // Fail here if the real Dockerfile gains a COPY dependency outside the allowlist.
  const dockerfile = readFileSync(new URL("../orchestrator/desktop/Dockerfile", import.meta.url), "utf8")
  for (const match of dockerfile.matchAll(/^COPY (.+)$/gm)) {
    for (const source of match[1].split(/\s+/).slice(0, -1)) assert.ok(sources.includes(source), source)
  }
})

test("legacy fallback is confined to explicit builds without buildx", () => {
  const result = fixture().run("build", { MOCK_BUILDX: "no", DOCKER_BUILDKIT: "1" })
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.calls.at(-1).buildkit, "0")
  assert.match(result.stderr, /buildx is unavailable.*deprecated legacy builder.*explicit build only/)
  assert.deepEqual(commands(result), ["info", "buildx", "build"])
})

test("BuildKit errors fail instead of retrying with the legacy builder", () => {
  const result = fixture().run("build", { MOCK_BUILD_FAIL: "yes" })
  assert.equal(result.status, 1)
  assert.deepEqual(commands(result), ["info", "buildx", "build"])
  assert.equal(result.calls.at(-1).buildkit, "1")
})

test("verified startup never builds and retains isolated non-root runtime flags", () => {
  const f = fixture()
  assert.equal(f.run("build").status, 0)
  writeFileSync(join(f.root, "orchestrator/not-allowlisted.txt"), "unrelated source change")
  const result = f.run()
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(commands(result), ["info", "image", "container", "run"])
  assert.ok(result.calls.every(({ buildkit }) => buildkit === "inherited"))
  assert.deepEqual(result.calls.at(-1).args.slice(3), [
    "--pull=never", "--rm", "--name", "gwen-desktop", "--user", "501",
    "--shm-size=1g", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
    "--security-opt", `seccomp=${f.root}/orchestrator/desktop/seccomp.json`,
    "-p", "127.0.0.1:6080:6080",
    "--mount", `type=bind,source=${f.root}/artifacts,target=/var/lib/gwen-desktop-artifacts`,
    "-e", "DESKTOP_ARTIFACT_ROOT=/var/lib/gwen-desktop-artifacts",
    "-e", "TEMPORAL_ADDRESS=mock-temporal:7233", "sha256:verified",
  ])
})

test("each allowlisted dependency change blocks stale startup without building", () => {
  const f = fixture()
  assert.equal(f.run("build").status, 0)
  for (const source of sources) {
    const path = join(f.root, "orchestrator", source)
    const original = readFileSync(path)
    writeFileSync(path, "changed source")
    const result = f.run()
    assert.equal(result.status, 1, source)
    assert.match(result.stderr, /mismatch.*pnpm build:desktop/)
    assert.deepEqual(commands(result), ["info", "image"])
    writeFileSync(path, original)
  }
})

test("UID changes and unlabelled images require explicit preparation", () => {
  const f = fixture()
  assert.equal(f.run("build").status, 0)
  for (const overrides of [{ MOCK_UID: "502" }, {}]) {
    if (!overrides.MOCK_UID) writeFileSync(f.image, "<no value>")
    const result = f.run("start", overrides)
    assert.equal(result.status, 1)
    assert.match(result.stderr, /pnpm build:desktop/)
    assert.deepEqual(commands(result), ["info", "image"])
  }
})

test("container name collisions fail without stopping or removing any container", () => {
  const f = fixture()
  assert.equal(f.run("build").status, 0)
  const result = f.run("start", { MOCK_COLLISION: "yes" })
  assert.equal(result.status, 1)
  assert.match(result.stderr, /gwen-desktop is already in use.*will not stop or remove/)
  assert.deepEqual(commands(result), ["info", "image", "container"])
})

test("root and invalid modes fail before touching Docker", () => {
  const f = fixture()
  for (const [mode, env] of [["start", { MOCK_UID: "0" }], ["build", { MOCK_UID: "0" }], ["unknown", {}]]) {
    const result = f.run(mode, env)
    assert.equal(result.status, 1)
    assert.deepEqual(result.calls, [])
    assert.match(result.stderr, /non-root|Usage:/)
  }
})
