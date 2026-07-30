import { existsSync, lstatSync, readFileSync } from "node:fs"
import { basename, isAbsolute, relative, resolve } from "node:path"

export type TaskState =
  | "pending"
  | "red_verified"
  | "implementing"
  | "controller_verified"
  | "spec_approved"
  | "quality_approved"
  | "completed"
  | "blocked_user"

export type LedgerTask = {
  id: number
  state: TaskState
  allowed_files: string[]
}

export type Ledger = {
  schema_version: number
  active_task: number | null
  blocked: unknown | null
  tasks: LedgerTask[]
}

const PRODUCT_EDIT_STATES = new Set<TaskState>(["red_verified", "implementing"])
const CONTROL_PLANE_AGENT = "plan-controller"
const PRODUCT_AGENT = "task-implementer"
const PROTECTED_PATHS = [
  ".env",
  ".env.local",
  "orchestrator/graph_tool.py",
  "app/api/orchestrator/route.ts",
  "app/api/orchestrator/approval/route.ts",
  "app/api/orchestrator/end/route.ts",
  "app/api/compare/route.ts",
]

const ALWAYS_ALLOWED_COMMANDS = [
  /^git status --short(?: --branch)?$/,
  /^git diff --check(?: -- .+)?$/,
  /^git diff -- .+$/,
]

const TASK_COMMANDS: Record<number, RegExp[]> = {
  1: [
    /^uv pip install --python \.venv\/bin\/python -r requirements\.txt$/,
    /^uv pip check --python \.venv\/bin\/python$/,
    /^\.venv\/bin\/python -m pytest tests\/test_config\.py -q$/,
  ],
  2: [/^\.venv\/bin\/python -m pytest tests\/test_perplexity_model\.py -q$/],
  3: [/^\.venv\/bin\/python -m pytest tests\/test_perplexity_operations\.py -q$/],
  4: [/^\.venv\/bin\/python -m pytest tests\/test_memory\.py -q$/],
  5: [
    /^\.venv\/bin\/python -m pytest tests\/test_mcp_config\.py tests\/test_pophive_sync\.py -q$/,
    /^pnpm lint$/,
  ],
  6: [
    /^\.venv\/bin\/python -c "from graph_tool import graph; print\(graph\.tool_name, graph\.tool_spec\)"$/,
    /^\.venv\/bin\/python -m pytest tests\/test_graph_activity\.py -q$/,
    /^pnpm vitest run app\/api\/orchestrator\/route\.test\.ts$/,
  ],
  7: [/^\.venv\/bin\/python -m pytest tests\/test_agent_runtime\.py -q$/],
  8: [/^\.venv\/bin\/python -m pytest tests\/test_workflow\.py -q$/],
  9: [/^\.venv\/bin\/python -m pytest tests\/test_compare_workflow\.py -q$/],
  10: [/^\.venv\/bin\/python -m pytest tests\/test_run_worker\.py -q$/],
  11: [
    /^\.venv\/bin\/python -m pytest tests\/test_server\.py -q$/,
    /^pnpm vitest run app\/api\/orchestrator\/route\.test\.ts$/,
  ],
  12: [
    /^\.venv\/bin\/python -m pytest tests\/test_replay\.py -q$/,
    /^\.venv\/bin\/python -m pytest tests\/test_restart_integration\.py --capture-histories -q$/,
    /^\.venv\/bin\/python -m pytest tests\/test_restart_integration\.py tests\/test_replay\.py -q$/,
  ],
  13: [
    /^\.venv\/bin\/python -m pytest tests\/test_smoke_client\.py -q$/,
    /^bash -n \.\.\/scripts\/sync-pophive\.sh$/,
  ],
  14: [
    /^\.venv\/bin\/python -m pytest -q$/,
    /^\.venv\/bin\/python -m pytest tests\/test_replay\.py -q$/,
    /^uv pip check --python \.venv\/bin\/python$/,
    /^pnpm vitest run app\/api\/orchestrator\/route\.test\.ts$/,
    /^pnpm lint$/,
    /^pnpm build$/,
  ],
}

const FORBIDDEN_COMMANDS = [
  /(?:^|\s)git\s+(?:add|commit|push|reset|restore|checkout|clean|stash|merge|rebase|cherry-pick|switch)\b/,
  /(?:^|\s)(?:rm|mv|cp|install|truncate|tee|dd)\b/,
  /(?:^|[^>])>{1,2}(?:\s|$)/,
  /(?:^|\s)(?:sed|perl|python|python3|node|ruby)\b.*\s-i\b/,
  /(?:^|\s)(?:cat|printf|echo)\b.*>/,
  /(?:^|\s)(?:curl|wget)\b/,
]

const SHELL_CONTROL_SYNTAX = /[\n\r;&|`<>]|\$\(|\$\{/

function normalizePath(path: string, root: string): string {
  const absolute = isAbsolute(path) ? resolve(path) : resolve(root, path)
  const normalized = relative(root, absolute).replaceAll("\\", "/")
  if (normalized === ".." || normalized.startsWith("../")) {
    throw new Error(`Plan guard denied path outside the worktree: ${path}`)
  }
  let current = resolve(root)
  for (const component of normalized.split("/")) {
    current = resolve(current, component)
    if (existsSync(current) && lstatSync(current).isSymbolicLink()) {
      throw new Error(`Plan guard denied symlink path: ${path}`)
    }
  }
  return normalized
}

function activeTask(ledger: Ledger): LedgerTask {
  const task = ledger.tasks.find((candidate) => candidate.id === ledger.active_task)
  if (!task) throw new Error("Plan guard found no active task in the ledger")
  return task
}

function matches(pattern: string, path: string): boolean {
  if (!pattern.includes("*")) return pattern === path
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&")
  return new RegExp(`^${escaped.replaceAll("**", ".*").replaceAll("*", "[^/]*")}$`).test(path)
}

export function assertEditAllowed(
  path: string,
  ledger: Ledger,
  root = process.cwd(),
  agent?: string,
): void {
  const normalized = normalizePath(path, root)
  if (normalized.startsWith(".opencode/")) {
    if (agent !== CONTROL_PLANE_AGENT) {
      throw new Error(`Plan guard denied control-plane edit by ${agent ?? "unknown agent"}`)
    }
    return
  }
  if (agent !== PRODUCT_AGENT) {
    throw new Error(`Plan guard denied product edit by ${agent ?? "unknown agent"}`)
  }
  if (ledger.blocked !== null) throw new Error("Plan guard froze product edits in blocked_user")
  const filename = basename(normalized)
  if (filename === ".env" || filename.startsWith(".env.") || filename === ".envrc") {
    throw new Error(`Plan guard denied protected path: ${normalized}`)
  }
  if (
    PROTECTED_PATHS.some(
      (protectedPath) =>
        normalized === protectedPath ||
        (protectedPath === ".env" && normalized.startsWith(".env.")),
    )
  ) {
    throw new Error(`Plan guard denied protected path: ${normalized}`)
  }
  const task = activeTask(ledger)
  if (
    task.state === "pending" &&
    normalized.startsWith("orchestrator/tests/") &&
    task.allowed_files.some((pattern) => matches(pattern, normalized))
  ) {
    return
  }
  if (!PRODUCT_EDIT_STATES.has(task.state)) {
    throw new Error(`Plan guard denied product edit while Task ${task.id} is ${task.state}`)
  }
  if (!task.allowed_files.some((pattern) => matches(pattern, normalized))) {
    throw new Error(`Plan guard denied out-of-scope edit for Task ${task.id}: ${normalized}`)
  }
}

export function assertCommandAllowed(command: string, ledger: Ledger): void {
  if (SHELL_CONTROL_SYNTAX.test(command)) {
    throw new Error("Plan guard denied shell control syntax")
  }
  const normalized = command.trim().replace(/\s+/g, " ")
  if (FORBIDDEN_COMMANDS.some((pattern) => pattern.test(normalized))) {
    throw new Error(`Plan guard denied unsafe or mutating command: ${normalized}`)
  }
  if (ALWAYS_ALLOWED_COMMANDS.some((pattern) => pattern.test(normalized))) return
  if (ledger.blocked !== null) throw new Error("Plan guard froze product commands in blocked_user")
  const task = activeTask(ledger)
  if (!(TASK_COMMANDS[task.id] ?? []).some((pattern) => pattern.test(normalized))) {
    throw new Error(`Plan guard denied command outside Task ${task.id}: ${normalized}`)
  }
}

function readLedger(root: string): Ledger {
  return JSON.parse(
    readFileSync(resolve(root, ".opencode/state/ledger.json"), "utf8"),
  ) as Ledger
}

export const PlanGuard = async ({ worktree, directory }: { worktree?: string; directory: string }) => {
  const root = worktree ?? directory
  const sessionAgents = new Map<string, string>()
  return {
    "chat.message": async (input: { sessionID: string; agent?: string }) => {
      if (input.agent) sessionAgents.set(input.sessionID, input.agent)
      else sessionAgents.delete(input.sessionID)
    },
    "tool.execute.before": async (
      input: { tool: string; sessionID: string },
      output: { args: Record<string, unknown> },
    ) => {
      const ledger = readLedger(root)
      if (input.tool === "bash") {
        assertCommandAllowed(String(output.args.command ?? ""), ledger)
        return
      }
      if (input.tool === "edit" || input.tool === "write") {
        const path = output.args.filePath ?? output.args.path
        if (typeof path !== "string") {
          throw new Error(`Plan guard could not determine path for ${input.tool}`)
        }
        assertEditAllowed(path, ledger, root, sessionAgents.get(input.sessionID))
        return
      }
      if (input.tool === "apply_patch") {
        throw new Error(
          "Plan guard requires path-aware edit/write tools for product changes; apply_patch cannot be safely scoped",
        )
      }
    },
  }
}

export default PlanGuard
