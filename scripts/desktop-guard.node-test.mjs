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
class ChatWorkflow:
    def _think_agent_factory(self, **kwargs):
        return TemporalAgent(model=self._model_id)
    async def turn(self, prompt):
        return await self._agent.invoke_async(prompt, invocation_state={"think_agent_factory": self._think_agent_factory})
    async def run(self, input):
        workflow.continue_as_new(ChatInput(model_id=self._model_id, system_prompt=self._system_prompt, messages=list(self._agent.messages), stream_state=self._stream.get_state(), desktop=self._desktop))
`
const worker = `queue = await loop.run_in_executor(executor, wait_for_queue_assignment, stopped)\nWorker(client, task_queue=queue, activity_executor=ThreadPoolExecutor(max_workers=1))`
const fixture = {
  "orchestrator/config.py": config,
  "orchestrator/workflow.py": workflow,
  "orchestrator/run_worker.py": "WORKER_ACTIVITIES = [load_tool_activity]",
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
  // Dropping the factory from invocation_state, or the factory itself, loses on-demand Think.
  assert.ok(rules({ ...fixture, "orchestrator/workflow.py": workflow.replace(', invocation_state={"think_agent_factory": self._think_agent_factory}', "") }).includes("preserve-think"))
  assert.ok(rules({ ...fixture, "orchestrator/workflow.py": workflow.replace("self._think_agent_factory}", "None}") }).includes("preserve-think"))
  assert.ok(rules({ ...fixture, "orchestrator/workflow.py": workflow.replace("def _think_agent_factory(self, **kwargs):", "def _other_factory(self, **kwargs):") }).includes("preserve-think"))
  // Importing the stock tool into workflow code is the old binding; load_tool owns it now.
  for (const imported of ["from strands_tools.think import think\n", "import strands_tools.think\n", "from strands_tools import think\n"]) {
    assert.ok(rules({ ...fixture, "orchestrator/workflow.py": imported + workflow }).includes("preserve-think"), imported)
  }
  for (const loss of ["model_id=self._model_id", "system_prompt=self._system_prompt", "messages=list(self._agent.messages)", "stream_state=self._stream.get_state()", "desktop=self._desktop"]) {
    const changed = workflow.replace(loss, "discarded=None")
    assert.ok(rules({ ...fixture, "orchestrator/workflow.py": changed }).includes("continue-context"), loss)
  }
  assert.ok(rules({ ...fixture, "orchestrator/workflow.py": workflow.replace("desktop=self._desktop", "desktop=None") }).includes("continue-context"))
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
