// Called only after free-ports.sh has stopped the local workers and container.
// Explicit cache/state allowlist: never traverse dependencies or user outputs.
import { lstatSync, readdirSync, realpathSync, rmSync } from "node:fs"
import { homedir, tmpdir } from "node:os"
import { basename, join, relative, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const root = realpathSync(fileURLToPath(new URL("..", import.meta.url)))
const dist = process.env.NEXT_DIST_DIR || ".next"
if (!/^\.next(?:-[a-zA-Z0-9_-]+)?$/.test(dist)) {
  throw new Error("NEXT_DIST_DIR must be a checkout-local .next or .next-<name> for dev:all cleanup")
}
const artifacts = resolve(process.env.DESKTOP_ARTIFACT_ROOT || join(tmpdir(), "kilo/gwen-desktop-artifacts"))
const targets = [
  join(root, dist),
  join(root, "orchestrator/__pycache__"),
  join(root, "orchestrator/.models/__pycache__"),
  join(root, "orchestrator/.runtime/browser-observations"),
]

function stat(path) {
  try {
    return lstatSync(path)
  } catch (error) {
    if (error.code === "ENOENT") return undefined
    throw error
  }
}

// Validate every target before deleting anything. Do not follow a symlinked
// cache/runtime parent into a different checkout or a user directory.
for (const path of targets) {
  let current = root
  for (const part of relative(root, path).split("/")) {
    current = join(current, part)
    if (stat(current)?.isSymbolicLink()) throw new Error(`Refusing symlinked cleanup path: ${current}`)
  }
}
if (stat(artifacts)) {
  if (stat(artifacts).isSymbolicLink()) throw new Error("Refusing symlinked DESKTOP_ARTIFACT_ROOT")
  const actual = realpathSync(artifacts)
  if (["/", homedir(), realpathSync(tmpdir()), root, join(root, "orchestrator")].includes(actual)
      || !relative(actual, root).startsWith("..")) {
    throw new Error("DESKTOP_ARTIFACT_ROOT must be a dedicated runtime directory")
  }
  for (const name of readdirSync(artifacts)) {
    if (["runtime.json", "state.lock", "action.lock", "handoff.lock", "desktop-worker.lock"].includes(name)
        || /^[a-f0-9]{64}$/.test(name)) targets.push(join(artifacts, name))
  }
}

for (const path of targets) {
  if (stat(path)) {
    rmSync(path, { recursive: true, force: true })
    console.log(`  cleared ${relative(root, path).startsWith("..") ? `desktop/${basename(path)}` : relative(root, path)}`)
  }
}
console.log("Transient dev state cleared; source, credentials, dependencies and generated reports preserved.")
