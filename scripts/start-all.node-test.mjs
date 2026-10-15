import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { afterEach, test } from "node:test"
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const roots = []
afterEach(() => roots.splice(0).forEach((root) => rmSync(root, { recursive: true, force: true })))

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "gwen-start-all-"))
  roots.push(root)
  for (const dir of ["scripts", "bin", "node_modules", "orchestrator/.venv/bin"]) mkdirSync(join(root, dir), { recursive: true })
  copyFileSync(new URL("./start-all.sh", import.meta.url), join(root, "scripts/start-all.sh"))
  writeFileSync(join(root, "orchestrator/.venv/bin/python"), "#!/bin/sh\nprintf 'python %s\\n' \"$*\" >> \"$MOCK_LOG\"\nexit 0\n", { mode: 0o755 })
  writeFileSync(join(root, "scripts/free-ports.sh"), "printf 'clean\\n' >> \"$MOCK_LOG\"\nexit \"${MOCK_CLEAN_EXIT:-0}\"\n")
  writeFileSync(join(root, "scripts/reset-dev-state.mjs"), 'import { appendFileSync } from "node:fs"; appendFileSync(process.env.MOCK_LOG, "reset-state\\n")\n')
  writeFileSync(join(root, "scripts/run-desktop.sh"), "printf 'desktop %s\\n' \"$*\" >> \"$MOCK_LOG\"\nexit \"${MOCK_BUILD_EXIT:-0}\"\n")
  for (const name of ["pnpm", "uv", "temporal", "docker", "colima"]) {
    writeFileSync(join(root, "bin", name), `#!/bin/bash
printf '${name} %s\\n' "$*" >> "$MOCK_LOG"
if [[ '${name}' == docker && "$*" == *info ]]; then
  if [[ "$MOCK_DAEMON_STUCK" == yes || ( "$MOCK_DAEMON_DOWN" == yes && ! -e "$MOCK_UP" ) ]]; then exit 1; fi
fi
if [[ '${name}' == colima ]]; then touch "$MOCK_UP"; fi
if [[ '${name}' == pnpm && "$1" == install ]]; then exit "\${MOCK_INSTALL_EXIT:-0}"; fi
if [[ '${name}' == pnpm && "$1" == exec ]]; then
  printf 'endpoints %s %s %s\\n' "$TEMPORAL_ADDRESS" "$DESKTOP_TEMPORAL_ADDRESS" "$ORCHESTRATOR_URL" >> "$MOCK_LOG"
fi
`, { mode: 0o755 })
  }
  return (overrides = {}) => {
    const log = join(root, "calls.log")
    const result = spawnSync("bash", [join(root, "scripts/start-all.sh")], {
      encoding: "utf8", timeout: 10_000,
      env: { ...process.env, PATH: `${join(root, "bin")}:${process.env.PATH}`,
        MOCK_LOG: log, MOCK_UP: join(root, "daemon-up"), DESKTOP_DOCKER_CONTEXT: "colima",
        TEMPORAL_ADDRESS: "remote:7233", DESKTOP_TEMPORAL_ADDRESS: "remote:7233",
        ORCHESTRATOR_URL: "http://remote:8787", ...overrides },
    })
    assert.equal(result.error, undefined)
    return { ...result, calls: readFileSync(log, "utf8").trim().split("\n") }
  }
}

test("warm starts reset once, sync dependencies, prepare image, then launch fresh local stack", () => {
  const result = fixture()()
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(result.calls.slice(0, 7), [
    "docker --context colima info", "clean", "reset-state", "pnpm install --frozen-lockfile",
    "uv pip install --python .venv/bin/python -r requirements.txt",
    "uv pip check --python orchestrator/.venv/bin/python", "desktop prepare",
  ])
  assert.match(result.calls[7], /concurrently --kill-others --kill-timeout 15000 --teardown bash scripts\/free-ports.sh --desktop-only/)
  assert.equal(result.calls[8], "endpoints localhost:7233 host.lima.internal:7233 http://127.0.0.1:8787")
  assert.equal(result.calls.filter((call) => call === "clean").length, 1)
})

test("a stopped daemon starts before the single cleanup", () => {
  const result = fixture()({ MOCK_DAEMON_DOWN: "yes" })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(result.calls.slice(0, 5), [
    "docker --context colima info", "colima start --profile default",
    "docker --context colima info", "clean", "reset-state",
  ])
})

test("an already-running but unreachable Colima VM reports recovery before changing project state", () => {
  const result = fixture()({ MOCK_DAEMON_STUCK: "yes" })
  assert.equal(result.status, 1)
  assert.match(result.stderr, /Colima.*Docker.*unreachable/)
  assert.match(result.stderr, /colima stop --profile 'default'/)
  assert.match(result.stderr, /images and volumes/)
  assert.ok(result.calls.every((call) => !/clean|reset-state|pnpm install|desktop prepare|concurrently/.test(call)))
  assert.equal(result.calls.filter((call) => call.startsWith("colima ")).length, 1,
    "Never automatically restart a shared VM or delete its data")
})

for (const [name, env, forbidden] of [
  ["cleanup", { MOCK_CLEAN_EXIT: "1" }, /reset-state|pnpm install|desktop prepare|concurrently/],
  ["dependency install", { MOCK_INSTALL_EXIT: "1" }, /desktop prepare|concurrently/],
  ["image preparation", { MOCK_BUILD_EXIT: "1" }, /concurrently/],
]) {
  test(`${name} failure never starts a partial stack`, () => {
    const result = fixture()(env)
    assert.equal(result.status, 1)
    assert.ok(result.calls.every((call) => !forbidden.test(call)), result.calls.join("\n"))
  })
}
