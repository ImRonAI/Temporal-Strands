import assert from "node:assert/strict"
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { test } from "node:test"
import { checkSources, collectSources, scanSource } from "./desktop-guard.mjs"

const python = "python3"
const config = `
DESKTOP_BROWSER_TASK_QUEUE = "desktop-browser"
BROWSER_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
`
const workflow = `
browser = activity_as_tool(browser_activity, **{"task_queue": DESKTOP_BROWSER_TASK_QUEUE, "retry_policy": BROWSER_RETRY_POLICY})
THINK_TOOL = activity_as_tool(think_activity.think)
tools = [THINK_TOOL]
stream.continue_as_new(lambda state: [ChatInput(session_id=self._session_id, messages=messages, stream_state=state, resume_prompt=self._resume_prompt)])
`
const worker = `Worker(client, task_queue=DESKTOP_BROWSER_TASK_QUEUE, activity_executor=ThreadPoolExecutor(max_workers=1))`
const fixture = {
  "orchestrator/config.py": config,
  "orchestrator/workflow.py": workflow,
  "orchestrator/run_worker.py": "WORKER_ACTIVITIES = [think_activity.think]",
  "orchestrator/desktop_worker.py": worker,
}
const rules = files => checkSources(files, python, []).map(item => item.rule)

test("clean direct framework wiring passes without importing product code", () => {
  assert.deepEqual(rules(fixture), [])
})

test("stock browser binding must forward its native typed input", () => {
  const source = `from strands_tools.browser import LocalChromiumBrowser\nfrom strands_tools.browser.models import BrowserInput\nfrom temporalio import activity\nbrowser = LocalChromiumBrowser()\n@activity.defn(name="browser")\ndef browser_activity(browser_input: BrowserInput):\n    return browser.browser(browser_input=browser_input)`
  assert.deepEqual(rules({ "orchestrator/browser_activity.py": source }), [])
  assert.ok(rules({ "orchestrator/browser_activity.py": source.replace("browser.browser(browser_input=browser_input)", "browser.browser()") }).includes("stock-browser-activity"))
  assert.ok(rules({ "orchestrator/browser_activity.py": "def browser_activity(data): return 'fake'" }).includes("stock-browser-activity"))
})

test("browser routing removal and host computer-use registration fail", () => {
  assert.ok(rules({ ...fixture, "orchestrator/workflow.py": workflow.replace('"task_queue": DESKTOP_BROWSER_TASK_QUEUE, ', "") }).includes("native-browser-routing"))
  assert.ok(rules({ ...fixture, "orchestrator/run_worker.py": "WORKER_ACTIVITIES = [browser_activity, *computer_use_activity.COMPUTER_USE_ACTIVITIES]" }).includes("host-desktop-execution"))
})

test("automatic mutation retries and missing native executor fail", () => {
  assert.ok(rules({ ...fixture, "orchestrator/config.py": config.replace("maximum_attempts=1", "maximum_attempts=3") }).includes("mutation-retry"))
  assert.ok(rules({ ...fixture, "orchestrator/desktop_worker.py": worker.replace(", activity_executor=ThreadPoolExecutor(max_workers=1)", "") }).includes("desktop-worker"))
})

test("Think removal and lost continuation context fail", () => {
  assert.ok(rules({ ...fixture, "orchestrator/workflow.py": workflow.replace("tools = [THINK_TOOL]", "tools = []") }).includes("preserve-think"))
  for (const loss of ["session_id=self._session_id", "messages=messages", "stream_state=state", "resume_prompt=self._resume_prompt"]) {
    const changed = workflow.replace(loss, "discarded=None")
    assert.ok(rules({ ...fixture, "orchestrator/workflow.py": changed }).includes("continue-context"), loss)
  }
})

test("aliased stock-browser and Temporal facade subclasses fail, native hooks remain valid", () => {
  const bad = `from strands_tools.browser import LocalChromiumBrowser as Stock\nclass Remote(Stock): pass`
  assert.ok(rules({ "orchestrator/test_runtime.py": bad }).includes("framework-facade"))
  assert.ok(rules({ "orchestrator/test_runtime.py": "from temporalio.contrib.strands._temporal_activity_tool import TemporalActivityTool as Tool\nclass Rewrapped(Tool): pass" }).includes("framework-facade"))
  assert.deepEqual(rules({ "orchestrator/test_runtime.py": "from strands.hooks import HookProvider\nclass Policy(HookProvider): pass" }), [])
})

test("headless options, default environment fallback, and sandbox disabling fail", () => {
  for (const source of ["browser.launch(headless=True)", 'options = {"headless": True}', 'headless = os.getenv("STRANDS_BROWSER_HEADLESS", "true")']) {
    assert.ok(rules({ "orchestrator/test_runtime.py": source }).includes("headed-desktop"))
  }
  assert.ok(rules({ "orchestrator/test_runtime.py": 'args = ["--no-sandbox"]' }).includes("browser-sandbox"))
  assert.deepEqual(rules({ "orchestrator/test_runtime.py": '# headless=True\nvalue = "ordinary text"' }), [])
})

test("syntax errors and required file deletion fail closed", () => {
  assert.ok(rules({ "orchestrator/test_runtime.py": "def broken(" }).includes("python-syntax"))
  assert.ok(checkSources({}, python, ["orchestrator/browser_activity.py"]).some(item => item.rule === "required-boundary"))
})

test("structural scan defers prop contracts to TypeScript, including JSX spreads", () => {
  const source = `import { Task, TaskTrigger } from "@/components/ai-elements/task";
const props = { title: "valid" }; const element = <Task><TaskTrigger {...props} /></Task>`
  assert.deepEqual(scanSource("components/v0/fixture.tsx", source), [])
})

test("native composition accepts documented props and preserves unmodified types", () => {
  const source = `import { Task, TaskTrigger } from "@/components/ai-elements/task";
const element = <Task defaultOpen onOpenChange={setOpen}><TaskTrigger title="Computer use" /></Task>`
  assert.deepEqual(scanSource("components/v0/fixture.tsx", source), [])
  assert.ok(scanSource("components/v0/fixture.tsx", `${source}\nconst props = {} as any`).some(item => item.rule === "native-types"))
  assert.ok(scanSource("components/v0/computer-use-activity.tsx", "export const Empty = () => null").some(item => item.rule === "native-composition"))
})

test("CDP viewer and website iframe cannot substitute for noVNC", () => {
  const src = 'import { WebPreviewBody } from "@/components/ai-elements/web-preview"; const element = <WebPreviewBody src={preview.url} />'
  assert.ok(scanSource("components/v0/fixture.tsx", src).some(item => item.rule === "novnc-viewer"))
  assert.ok(scanSource("components/v0/fixture.ts", 'const url = "/computer-use-live.html"').some(item => item.rule === "novnc-viewer"))
  assert.deepEqual(scanSource("components/v0/fixture.ts", '// computer-use-live.html was removed\nconst url = "http://localhost:6080/vnc.html"'), [])
})

test("unsafe container copy and headless environment are detected", () => {
  const result = scanSource("orchestrator/desktop/Dockerfile", "FROM python:3.13\nCOPY . .\nENV STRANDS_BROWSER_HEADLESS=true")
  assert.deepEqual(result.map(item => item.rule), ["source-only-image", "headed-sandbox"])
})

test("collector never loads env, runtime, dependencies, tests or symlink targets", () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "desktop-guard-"))
  try {
    for (const dir of ["orchestrator/.runtime", "orchestrator/.venv", "orchestrator/tests", "components/v0"]) mkdirSync(path.join(root, dir), { recursive: true })
    for (const name of [".env", ".runtime/secret.py", ".venv/secret.py", "tests/test_secret.py"]) writeFileSync(path.join(root, "orchestrator", name), "DO NOT LOAD")
    writeFileSync(path.join(root, "orchestrator/config.py"), "SAFE = True")
    symlinkSync(path.join(root, "orchestrator/.venv"), path.join(root, "orchestrator/strands-tools"))
    assert.deepEqual(collectSources(root), { "orchestrator/config.py": "SAFE = True" })
    symlinkSync(path.join(root, "orchestrator/.env"), path.join(root, "orchestrator/linked.py"))
    assert.throws(() => collectSources(root), /symlink source/)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
