import { describe, expect, test } from "bun:test"
import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

import {
  assertCommandAllowed,
  assertEditAllowed,
  PlanGuard,
  type Ledger,
} from "../plugins/plan-guard"

const ledger: Ledger = {
  schema_version: 1,
  active_task: 2,
  blocked: null,
  tasks: [
    {
      id: 2,
      state: "implementing",
      allowed_files: [
        "orchestrator/perplexity_model.py",
        "orchestrator/tests/test_perplexity_model.py",
      ],
    },
  ],
}

describe("edit policy", () => {
  test("allows active task files and controller control-plane maintenance", () => {
    expect(() =>
      assertEditAllowed(
        "orchestrator/perplexity_model.py",
        ledger,
        process.cwd(),
        "task-implementer",
      ),
    ).not.toThrow()
    expect(() =>
      assertEditAllowed(".opencode/state/ledger.json", ledger, process.cwd(), "plan-controller"),
    ).not.toThrow()
  })

  test("denies control-plane edits from implementers and unknown sessions", () => {
    expect(() =>
      assertEditAllowed(
        ".opencode/state/ledger.json",
        ledger,
        process.cwd(),
        "task-implementer",
      ),
    ).toThrow()
    expect(() =>
      assertEditAllowed(".opencode/plugins/plan-guard.ts", ledger),
    ).toThrow()
  })

  test("denies product edits from controllers, reviewers, and unknown sessions", () => {
    for (const agent of ["plan-controller", "spec-reviewer", "quality-reviewer", undefined]) {
      expect(() =>
        assertEditAllowed(
          "orchestrator/perplexity_model.py",
          ledger,
          process.cwd(),
          agent,
        ),
      ).toThrow()
    }
  })

  test("denies out-of-scope, protected, secret, and blocked edits", () => {
    expect(() => assertEditAllowed("orchestrator/server.py", ledger)).toThrow()
    expect(() => assertEditAllowed("orchestrator/graph_tool.py", ledger)).toThrow()
    expect(() => assertEditAllowed(".env.local", ledger)).toThrow()
    expect(() =>
      assertEditAllowed(
        "orchestrator/.env.local",
        {
          ...ledger,
          tasks: [{ ...ledger.tasks[0], allowed_files: ["orchestrator/**"] }],
        },
        process.cwd(),
        "task-implementer",
      ),
    ).toThrow()
    expect(() =>
      assertEditAllowed("orchestrator/perplexity_model.py", {
        ...ledger,
        blocked: { reason: "user decision required" },
      }),
    ).toThrow()
  })

  test("pending tasks can add tests but cannot edit implementation", () => {
    const pending = {
      ...ledger,
      tasks: ledger.tasks.map((task) => ({ ...task, state: "pending" as const })),
    }
    expect(() =>
      assertEditAllowed(
        "orchestrator/tests/test_perplexity_model.py",
        pending,
        process.cwd(),
        "task-implementer",
      ),
    ).not.toThrow()
    expect(() =>
      assertEditAllowed(
        "orchestrator/perplexity_model.py",
        pending,
        process.cwd(),
        "task-implementer",
      ),
    ).toThrow()
  })

  test("denies allowed lexical paths that traverse symlinks", () => {
    const root = mkdtempSync(join(tmpdir(), "plan-guard-"))
    mkdirSync(join(root, "orchestrator", "tests"), { recursive: true })
    writeFileSync(join(root, "target.py"), "protected")
    symlinkSync(join(root, "target.py"), join(root, "orchestrator", "tests", "test_perplexity_model.py"))

    expect(() =>
      assertEditAllowed(
        "orchestrator/tests/test_perplexity_model.py",
        ledger,
        root,
        "task-implementer",
      ),
    ).toThrow()
  })
})

describe("hook authorization", () => {
  test("clears cached controller authority when a later message omits the agent", async () => {
    const root = mkdtempSync(join(tmpdir(), "plan-guard-hook-"))
    mkdirSync(join(root, ".opencode", "state"), { recursive: true })
    writeFileSync(join(root, ".opencode", "state", "ledger.json"), JSON.stringify(ledger))
    const hooks = await PlanGuard({ directory: root })
    const session = { sessionID: "session-1" }

    await hooks["chat.message"]({ ...session, agent: "plan-controller" })
    await hooks["chat.message"](session)

    await expect(
      hooks["tool.execute.before"](
        { ...session, tool: "write" },
        { args: { filePath: ".opencode/state/ledger.json" } },
      ),
    ).rejects.toThrow()
  })
})

describe("command policy", () => {
  test("allows read-only git and task verification", () => {
    expect(() => assertCommandAllowed("git status --short", ledger)).not.toThrow()
    expect(() =>
      assertCommandAllowed(
        ".venv/bin/python -m pytest tests/test_perplexity_model.py -q",
        ledger,
      ),
    ).not.toThrow()
  })

  test("denies staging, commits, destructive git, shell writes, and broad tests", () => {
    for (const command of [
      "git add orchestrator/perplexity_model.py",
      'git commit -m "provider"',
      "git reset --hard",
      "git checkout -- orchestrator/perplexity_model.py",
      "rm -rf orchestrator",
      "printf x > orchestrator/perplexity_model.py",
      ".venv/bin/python -m pytest -q",
    ]) {
      expect(() => assertCommandAllowed(command, ledger)).toThrow()
    }
  })

  test("denies shell control syntax appended to allowlisted commands", () => {
    for (const command of [
      "git diff -- '*' ; touch /tmp/plan-guard-bypass",
      "git status --short && rm -rf orchestrator",
      "git diff -- $(touch /tmp/plan-guard-bypass)",
      "git diff -- `touch /tmp/plan-guard-bypass`",
      "git diff -- '*' | tee /tmp/plan-guard-bypass",
      "git diff -- '*'\ntouch /tmp/plan-guard-bypass",
    ]) {
      expect(() => assertCommandAllowed(command, ledger)).toThrow()
    }
  })
})
