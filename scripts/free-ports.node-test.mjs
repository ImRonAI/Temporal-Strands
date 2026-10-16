import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { afterEach, test } from "node:test"
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const temporaryRoots = []
afterEach(() => {
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true })
})

// Only the desktop-container branch is exercised: host ports and process
// patterns are stubbed out so the test never signals anything real.
function fixture() {
  const root = mkdtempSync(join(tmpdir(), "free-ports-"))
  temporaryRoots.push(root)
  mkdirSync(join(root, "bin"), { recursive: true })
  copyFileSync(new URL("./free-ports.sh", import.meta.url), join(root, "free-ports.sh"))
  writeFileSync(join(root, "bin/lsof"), "#!/usr/bin/env bash\nexit 1\n", { mode: 0o755 })
  writeFileSync(join(root, "bin/pgrep"), "#!/usr/bin/env bash\nexit 1\n", { mode: 0o755 })
  writeFileSync(join(root, "bin/docker"), `#!/usr/bin/env node
const fs = require("node:fs")
const args = process.argv.slice(2)
fs.appendFileSync(process.env.MOCK_LOG, JSON.stringify(args) + "\\n")
if (process.env.MOCK_DAEMON === "down") process.exit(1)
if (args[0] !== "--context" || args[1] !== "mock-desktop") process.exit(99)
const command = args.slice(2)
if (command[0] === "info") process.exit(0)
// MOCK_STATE lists existing gwen-desktop-* container names, one per line.
const existing = () => fs.existsSync(process.env.MOCK_STATE)
  ? fs.readFileSync(process.env.MOCK_STATE, "utf8").split("\\n").filter(Boolean) : []
const forget = (name) => fs.writeFileSync(process.env.MOCK_STATE, existing().filter((n) => n !== name).join("\\n"))
if (command[0] === "ps") {
  if (!command.includes("-a") || !command.includes("name=^gwen-desktop-")) process.exit(99)
  console.log(existing().join("\\n"))
  process.exit(0)
}
if (command[0] === "container" && command[1] === "inspect") {
  if (!command.at(-1).startsWith("gwen-desktop-")) process.exit(99)
  if (!existing().includes(command.at(-1))) process.exit(1)
  console.log("exited")
  process.exit(0)
}
if (command[0] === "stop") {
  if (!command.at(-1).startsWith("gwen-desktop-")) process.exit(99)
  if (process.env.MOCK_STOP_REMOVES === "yes") forget(command.at(-1))
  process.exit(0)
}
if (command[0] === "rm") {
  if (!command.at(-1).startsWith("gwen-desktop-")) process.exit(99)
  if (process.env.MOCK_RM_FAILS === "yes") process.exit(1)
  forget(command.at(-1))
  process.exit(0)
}
process.exit(99)
`, { mode: 0o755 })
  const log = join(root, "docker.jsonl")
  const state = join(root, "container-state")
  function run(overrides = {}, args = []) {
    writeFileSync(log, "")
    const result = spawnSync("bash", [join(root, "free-ports.sh"), ...args], {
      encoding: "utf8",
      timeout: 10_000,
      env: {
        ...process.env,
        PATH: `${join(root, "bin")}:${process.env.PATH}`,
        DESKTOP_DOCKER_CONTEXT: "mock-desktop",
        // Removal is synchronous in the mock daemon; skip the real grace wait.
        DESKTOP_REMOVE_GRACE_SECONDS: "0",
        MOCK_LOG: log, MOCK_STATE: state,
        MOCK_DAEMON: "up", MOCK_STOP_REMOVES: "yes", MOCK_RM_FAILS: "no",
        ...overrides,
      },
    })
    assert.equal(result.error, undefined)
    return {
      ...result,
      calls: readFileSync(log, "utf8").trim().split("\n").filter(Boolean).map(JSON.parse),
    }
  }
  return { root, run, state }
}

function commands(result) {
  return result.calls.map((args) => args[2])
}

test("pnpm dev:all resets the stack before preparing or starting desktop", () => {
  const { scripts } = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"))
  assert.equal(scripts["dev:clean"], "bash scripts/free-ports.sh")
  assert.equal(scripts["clean"], "bash scripts/free-ports.sh")
  assert.equal(scripts["dev:all"], "bash scripts/start-all.sh")
  const startAll = readFileSync(new URL("./start-all.sh", import.meta.url), "utf8")
  const firstClean = startAll.indexOf("scripts/free-ports.sh")
  const prepare = startAll.indexOf("run-desktop.sh prepare")
  const startDesktop = startAll.indexOf("pnpm dev:desktop")
  assert.ok(firstClean >= 0, "start-all.sh must reset via free-ports.sh")
  assert.ok(prepare > firstClean, "desktop prepare must run after reset")
  assert.ok(startDesktop > prepare, "desktop service must start after prepare")
})

test("concurrently color flag is still this checkout's stack", () => {
  const f = fixture()
  const pid = "987654321"
  const command = "node ./node_modules/.bin/../concurrently/dist/bin/index.js --kill-others --names temporal,worker,api,desktop,web -c magenta,yellow,cyan,blue,green pnpm dev:temporal"
  writeFileSync(join(f.root, "bin/pgrep"), "#!/usr/bin/env bash\n[[ \"$1\" == -f ]] && printf '%s\\n' 987654321\nexit 0\n", { mode: 0o755 })
  writeFileSync(join(f.root, "bin/ps"), `#!/usr/bin/env bash
if [[ "$1" == "-p" && "$2" == "${pid}" && "$4" == "command=" ]]; then printf '%s\\n' '${command}'; exit 0; fi
exit 1
`, { mode: 0o755 })
  writeFileSync(join(f.root, "bin/lsof"), `#!/usr/bin/env bash
if [[ "$*" == *"-d cwd"* ]]; then printf 'n%s\\n' "${f.root}"; exit 0; fi
exit 1
`, { mode: 0o755 })
  const result = f.run({ GWEN_PROJECT_ROOT: f.root })
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /stopping services owned by this checkout: 987654321/)
})

test("a host-run desktop worker is reset like any other worker", () => {
  const f = fixture()
  const pid = "987654322"
  const command = ".venv/bin/python -u desktop_worker.py"
  writeFileSync(join(f.root, "bin/pgrep"), "#!/usr/bin/env bash\n[[ \"$1\" == -f ]] && printf '%s\\n' 987654322\nexit 0\n", { mode: 0o755 })
  writeFileSync(join(f.root, "bin/ps"), `#!/usr/bin/env bash
if [[ "$1" == "-p" && "$2" == "${pid}" && "$4" == "command=" ]]; then printf '%s\\n' '${command}'; exit 0; fi
exit 1
`, { mode: 0o755 })
  writeFileSync(join(f.root, "bin/lsof"), `#!/usr/bin/env bash
if [[ "$*" == *"-d cwd"* ]]; then printf 'n%s\\n' "${f.root}"; exit 0; fi
exit 1
`, { mode: 0o755 })
  const result = f.run({ GWEN_PROJECT_ROOT: f.root })
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /stopping services owned by this checkout: 987654322/)
})

test("no desktop containers: list only, nothing stopped or removed", () => {
  const result = fixture().run()
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(commands(result), ["info", "ps"])
  assert.doesNotMatch(result.stdout, /desktop container/)
  assert.match(result.stdout, /ports clear/)
})

test("every orphaned per-session and warm desktop container is stopped", () => {
  const f = fixture()
  writeFileSync(f.state, "gwen-desktop-chat-1\ngwen-desktop-idle-ab12\n")
  const result = f.run()
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /desktop container gwen-desktop-chat-1 -> stopping/)
  assert.match(result.stdout, /desktop container gwen-desktop-idle-ab12 -> stopping/)
  assert.deepEqual(commands(result), ["info", "ps", "stop", "rm", "container", "stop", "rm", "container"])
  assert.deepEqual(result.calls[2].slice(2), ["stop", "-t", "15", "gwen-desktop-chat-1"])
  assert.ok(result.calls.every((args) => args[0] === "--context" && args[1] === "mock-desktop"))
  assert.doesNotMatch(result.stderr, /WARNING/)
})

test("an exited container that survives stop is removed by name", () => {
  const f = fixture()
  writeFileSync(f.state, "gwen-desktop-chat-1\n")
  const result = f.run({ MOCK_STOP_REMOVES: "no" })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(commands(result), ["info", "ps", "stop", "rm", "container"])
  assert.doesNotMatch(result.stderr, /WARNING/)
})

test("a container that cannot be removed fails the reset instead of continuing", () => {
  const f = fixture()
  writeFileSync(f.state, "gwen-desktop-chat-1\n")
  const result = f.run({ MOCK_STOP_REMOVES: "no", MOCK_RM_FAILS: "yes" })
  assert.equal(result.status, 1)
  assert.match(result.stderr, /ERROR: gwen-desktop-chat-1 still exists in context mock-desktop/)
})

test("an unreachable Docker daemon is skipped without failing the cleanup", () => {
  const result = fixture().run({ MOCK_DAEMON: "down" })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(commands(result), ["info"])
  assert.match(result.stdout, /ports clear/)
})

test("cleanup never kills a matching launcher ancestor", () => {
  const f = fixture()
  // This test runner is an ancestor of cleanup, not an old stack. If ancestry
  // filtering regresses, use an impossible PID for signalling via the ps seam.
  writeFileSync(join(f.root, "bin/ps"), `#!/bin/bash
if [[ "$4" == ppid= ]]; then
  if [[ "$2" == 987654321 ]]; then printf '1\\n'; else printf '987654321\\n'; fi
else printf 'node concurrently pnpm dev:worker\\n'; fi
`, { mode: 0o755 })
  writeFileSync(join(f.root, "bin/pgrep"), '#!/bin/bash\n[[ "$1" == -f ]] && printf "987654321\\n"\nexit 0\n', { mode: 0o755 })
  writeFileSync(join(f.root, "bin/lsof"), `#!/bin/bash
[[ "$*" == *"-d cwd"* ]] && printf 'n%s\\n' '${f.root}'
exit 1
`, { mode: 0o755 })
  const result = f.run({ GWEN_PROJECT_ROOT: f.root })
  assert.equal(result.status, 0, result.stderr)
  assert.doesNotMatch(result.stdout, /stopping services/)
})

test("nested worktree services are not part of this checkout's cleanup", () => {
  const f = fixture()
  writeFileSync(join(f.root, "bin/pgrep"), '#!/bin/bash\n[[ "$1" == -f ]] && printf "987654321\\n"\nexit 0\n', { mode: 0o755 })
  writeFileSync(join(f.root, "bin/ps"), '#!/bin/bash\n[[ "$4" == command= ]] && printf "python run_worker.py\\n"\nexit 0\n', { mode: 0o755 })
  writeFileSync(join(f.root, "bin/lsof"), `#!/bin/bash
if [[ "$*" == *"-d cwd"* ]]; then printf 'n%s\\n' '${f.root}/.kilo/worktrees/other/orchestrator'; exit 0; fi
exit 1
`, { mode: 0o755 })
  const result = f.run({ GWEN_PROJECT_ROOT: f.root })
  assert.equal(result.status, 0, result.stderr)
  assert.doesNotMatch(result.stdout, /stopping services/)
})

test("MCP descendants are captured before their worker parent is stopped", () => {
  const f = fixture()
  writeFileSync(join(f.root, "bin/pgrep"), `#!/bin/bash
if [[ "$1" == -f ]]; then printf '987654321\\n'; fi
if [[ "$1 $2" == '-P 987654321' ]]; then printf '987654322\\n'; fi
exit 0
`, { mode: 0o755 })
  writeFileSync(join(f.root, "bin/ps"), '#!/bin/bash\n[[ "$4" == command= ]] && printf "python run_worker.py\\n"\nexit 0\n', { mode: 0o755 })
  writeFileSync(join(f.root, "bin/lsof"), `#!/bin/bash
if [[ "$*" == *"-d cwd"* ]]; then printf 'n%s\\n' '${f.root}/orchestrator'; exit 0; fi
exit 1
`, { mode: 0o755 })
  const result = f.run({ GWEN_PROJECT_ROOT: f.root })
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /stopping services owned by this checkout: 987654321 987654322/)
})

test("unrelated listener aborts before stopping services or Docker", () => {
  const f = fixture()
  writeFileSync(join(f.root, "bin/lsof"), '#!/bin/bash\nprintf "987654321\\n"\n', { mode: 0o755 })
  writeFileSync(join(f.root, "bin/ps"), '#!/bin/bash\n[[ "$4" == command= ]] && printf "unrelated-app\\n"\nexit 0\n', { mode: 0o755 })
  const result = f.run()
  assert.equal(result.status, 1)
  assert.match(result.stderr, /belongs to another process/)
  assert.deepEqual(result.calls, [])
})

test("concurrently teardown only removes desktop, never sweeps the new host launcher", () => {
  const f = fixture()
  writeFileSync(f.state, "gwen-desktop-chat-1\n")
  writeFileSync(join(f.root, "bin/lsof"), '#!/bin/bash\nprintf "987654321\\n"\n', { mode: 0o755 })
  const result = f.run({}, ["--desktop-only"])
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(commands(result), ["info", "ps", "stop", "rm", "container"])
  assert.doesNotMatch(result.stdout, /stopping services/)
})
