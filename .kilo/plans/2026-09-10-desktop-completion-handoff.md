# Desktop completion handoff

## Later Think execution decision and evidence (September 10)

The user subsequently rejected the separate shared-tools Think implementation
and completed a one-question-at-a-time interview. Implemented contract now:
async adaptation in `orchestrator/think_activity.py::think_async`, upstream
`ThoughtProcessor.create_thinking_prompt`, default parent tool inheritance
excluding recursive Think, always `openai/gpt-6-astra` through the existing
Perplexity factory (same native API tools/settings). The orchestrator makes
Think the first tool call per user turn and selects effort and 0-10 cycles.
Zero cycles returns immediately without an Astra request; one effort applies
per call, all cycle context is retained within that call, subsequent calls use
the current parent conversation, and conclusions plus tool evidence return to
the parent. Errors are ordinary tool results; no heuristic effort selection,
model fallback or automatic zero-cycle substitution. Accepted/echoed live API
efforts: minimal, low, medium, high, xhigh, max; distinct internal effects are
not proven by acceptance alone.

Removed `ChatWorkflow._run_think`, the rejected `_shared_think` path and
`tests/test_think_shared_tools.py`. Native tool/context binding and safety hooks
stay in the workflow. `think-model-chosen-astra-v1` gates new behavior; old Think
activity/hook remain only for replay of pre-migration histories, not new turns.
Do not mistake their `tools=[]` legacy code for the new implementation.

Real UI session `chat-c5077b4164fa7676`: "Hey there!" produced a model-chosen
Think call with minimal/0 and no thinking-model activity; a second prompt asked
Think to verify Astra via the inherited `list_agent_models` tool while choosing
its own effort/cycles. It selected low/1, executed that tool and returned live
evidence to the outer response. History confirms `model_name=openai/gpt-6-astra`,
`streaming_topic=thinking`, `reasoning_effort=low` around the actual tool activity.
Original Run ID `01a08cd7-5f0a-79cf-9a63-2b6f4864904e` continued as new to
`15cc49a2-18cf-4145-bf61-69276b2c5c22` with unchanged Workflow ID. This verifies
Think, NOT the full desktop/HITL acceptance sequence.

Targeted Think/provider/workflow/observation run: 223 passed, 1 Linux-only skip
before additional repeated-call/error tests. Full backend: 971 passed, 2 failed,
2 skipped; failures were adherence checks on `compare_workflow.py` timedelta
and `desktop_observation.py`'s existing empty except. Frontend: 435 passed, 2
adherence failures (protected compare route streaming primitives, preset ID in
a comment). TypeScript and scoped desktop contracts pass; full lint still sees
errors in the existing sibling Kilo worktrees. Recheck current state rather than
using these as blanket completion claims.

## Assignment and read order

The user requested a fresh instance to finish the desktop feature, not another broad plan or an architecture reset. This file is the continuation context. A subsequent urgent request to restore **all Perplexity Agent API models in the picker** was implemented and verified before this handoff; preserve that fix.

Repository: `/Users/tims-stuff/Desktop/v0-clone-blurple`.
Branch at handoff: `cursor/orchestrator-gemini-computer-use`.
HEAD at inspection: `9729d51` (`feat: implement desktop-native infrastructure and workspace management service with associated test suite`).
There is extensive uncommitted/concurrent work. This handoff is for a fresh **local session in the same directory**, not a new clean worktree that omits it. Do not stash/reset/revert, stage unrelated changes, or commit/push without authorization. No product files need modification just to receive the handoff.

Read before implementing:

1. Root `AGENTS.md` and `orchestrator/AGENTS.md`.
2. `orchestrator/desktop/AGENTS.md`: binding completion contract, known gaps, native APIs, required real UI test.
3. `components/v0/AGENTS.md`: exact AI Elements props/types and composition rules.
4. This handoff, then `.kilo/plans/1788845228632-gce-desktop-native-infrastructure-spec.md` and `.kilo/plans/2026-09-08-desktop-implementation-handoff.md` for older background only.
5. Relevant installed SDK source and current implementation, not old assistant summaries.

## The user's finish line

- BOTH computer-use and browser-use operate in a real **headed Linux virtual desktop**. No agent browser/window on the Mac; no headless substitute.
- Native **noVNC viewer in the WebPreview iframe**, showing the desktop/browser chrome and real work. A CDP screenshot player or iframe of the visited website does not satisfy this.
- Functional Take Control: stop/fence agent mutations, settle in-flight input, confirm native human control before enabling it.
- Functional Relinquish: revoke native human input and clean held keys/buttons/connections, then native PromptInput accepts feedback/updates/steering. Preserve text on failure.
- Resume through native Continue-As-New: same application session ID and Temporal Workflow ID, new Run ID, retained conversation/tool context AND usable browser state. Apply steering once and take a fresh screenshot. Rollover and worker/container loss are different cases.
- Actual image pixels reach Perplexity in supported image input, not filenames or textual base64. Prove this with an unpredictable visual-only task.
- Separate browser/computer-use Task/ChainOfThought component, with native AI Elements props/types and animations. Preserve every emitted update, Think result, and agent action/result/screenshot in order. Do not invent private model reasoning or fake human input as agent tool calls.
- **Zero redundant custom framework wrappers.** Use TemporalAgent, official activity_as_tool, typed activity definitions, native workers/executors, hooks, updates, cancellation, streams, and native browser tools directly. Necessary domain logic at documented extension points is allowed; replacement adapters, facades, browser subclasses duplicating stock platforms, custom RFB/control servers, and duplicate agent loops are not.
- Preserve Think for Perplexity, asynchronous streaming, provider declarations, all concurrent work, protected paths, and model switching. No GCE resources/deployment without explicit approval.

Previous turns made contradictory claims (headless vs headed; Perplexity omitted input vs wrapper failure; takeover verified vs unfinished). Treat those claims as unreliable. Diagnose from current source and actual payloads. No current end-to-end acceptance proof exists for the native replacement.

## Latest urgent fix: all Perplexity models restored

Root cause: `lib/perplexity.ts` expected string model IDs, but `/health` returns worker-declared `{id, provider, label}` objects. It rejected the full catalog and silently supplied nine hardcoded fallback entries. The browser hook then cached that incomplete list for the life of the tab.

Fixed in this worktree:

- `lib/perplexity.ts` validates/marshals every registered model object, preserves its declared provider in `owned_by`, and carries the worker's provider display label. No model ID/prefix inference and no static preset/Gemini fallback list. An unavailable/invalid catalog throws a useful error through the existing route. Default-model fallback remains `preset:high` as before. Health timeout is 15 seconds.
- `components/v0/use-models.ts` deduplicates only requests currently in flight, uses fetch `cache: no-store`, clears the promise on completion, refreshes on picker open/close, reports unavailable/empty catalogs and clears errors after recovery.
- `components/v0/model-picker.tsx` uses declared provider labels (`Perplexity Agent API`, `Google AI Studio`), displays catalog errors, retains native searchable items and nested reasoning effort.
- Tests: `lib/perplexity.test.ts`, `components/v0/model-picker.test.tsx`, new `components/v0/use-models.test.ts`; compare-view compatibility tested too.
- Protected `app/api/models/route.ts` was READ, NOT changed. Existing backend `/health` contract remained unchanged.

Fresh live evidence from this handoff turn:

- `/health`: 58 registered entries, including 55 Perplexity entries (49 model IDs + six presets) and three Google AI Studio direct models.
- `/api/models`: all 58 delivered; every Perplexity ID matched the health catalog, no missing IDs.
- Real UI via agent-browser session `model-catalog-check`: opened picker, read every rendered option and declared group, selected `openai/gpt-6-astra` and Default effort, reopened and compared the DOM with `/api/models`.
- UI comparison result: `apiTotal=58`, `uiTotal=58`, `perplexityTotal=55`, `missingInPicker=[]`, `extraInPicker=[]`.
- 34 scoped tests passed; changed files passed targeted ESLint and `pnpm check:contracts`.

Do not filter user-facing model lists to the subagent-policy allowlist. Those are separate rules. Discover current catalog again before choosing a test model; never hardcode this snapshot into product code.

## Native desktop source state

- `orchestrator/browser_activity.py`: module-level stock LocalChromiumBrowser, synchronous `@activity.defn(name="browser")`, typed BrowserInput forwarded unchanged. This minimal binding is legitimate required integration.
- `orchestrator/workflow.py`: `activity_as_tool(browser_activity, task_queue=DESKTOP_BROWSER_TASK_QUEUE, retry_policy=BROWSER_RETRY_POLICY, ...)`, queue resolves to `desktop-browser`.
- `orchestrator/desktop_worker.py`: plain Temporal client + Worker, `ThreadPoolExecutor(max_workers=1)`, only browser activity. Models and credentials stay on host. Verify native converter/serialization and cancellation semantics; merely having one executor thread is not ownership proof.
- Host `run_worker.py` no longer registers the exact `browser` activity, BUT still registers `computer_use_activity.COMPUTER_USE_ACTIVITIES`. That separate path retains host/CDP/headless defaults and is not converted by the browser routing change.
- Current `orchestrator/desktop/Dockerfile`: Python 3.13 slim base, apt Xvfb/openbox/x11vnc/noVNC/websockify, installs full host requirements and Playwright as root, `COPY . .`, then switches to `desktop`. It is unfinished/unsafe as a build context. Requirements include a relative local dependency missing during that install stage. Do not build a whole-repo/credential-bearing image as a workaround.
- `orchestrator/desktop/start-desktop.sh`: starts Xvfb, openbox, x11vnc view-only, websockify, then desktop_worker. No display readiness/service-exit supervision. Fix directly using normal runtime configuration; retain Chromium sandbox, non-root user and private listeners.
- `server.py` handoff: claim_control update followed by synchronous `_vnc_input` subprocess of configured native x11vnc command. Release/give revokes VNC then relinquishes. Verify timeout/output errors, lack of per-session ownership, global shared display races, held keys/stale connections, and blocking subprocess in async endpoint. Do not restore custom HTTP control server.
- `workflow.py` claim_control waits for the turn lock; relinquish records resume_prompt and triggers rollover. `_stream.continue_as_new` carries session_id/messages/stream state/loaded tools/MCP state/steering. This is groundwork, not proof that in-flight sync browser activity actually settled or the same desktop survives restart.
- `components/v0/computer-use.ts` currently points browser/computer actions at a configurable noVNC URL. That does not establish they act on that same display. UI tests still reference deleted screenshotUrl/devtoolsFrontendUrl fields.
- `computer-use-activity.tsx` is the standalone native Task/ChainOfThought composition. Preserve its event ordering, interleaved text/Think, failed/denied states and unknown-completion warning. Removing unsupported image plumbing did not complete native screenshot delivery.

Removed custom experiment files include `remote_browser.py`, `browser_observation.py`, the custom Node browser relay/control service, `computer_click`, custom desktop_handoff activity, model hydration helper, `/browser-observations` route and `public/computer-use-live.html`. Do not restore these facades to make a demo. Existing Gemini tool code and stale tests still reference parts of the old approach; fix the supported integration rather than hiding tests.

## Screenshot boundary: hard blocker

Verified SDK versions during this handoff: temporalio 1.31.0, strands-agents 1.50.2, strands-agents-tools 0.8.5, playwright 1.61.0, pydantic 2.13.4.

Installed source under `orchestrator/.venv/lib/python3.13/site-packages/`:

- `strands_tools/browser/browser.py::_async_screenshot` saves a PNG and returns a textual saved-file message.
- `temporalio/contrib/strands/_temporal_activity_tool.py::TemporalActivityTool.stream` JSON/text-serializes activity data.
- `temporalio/contrib/strands/workflow.py::activity_as_tool` accepts native options including task_queue and requires @activity.defn.

Their current direct composition does NOT deliver image pixels to Perplexity. Resolve using supported image/content/storage mechanisms and necessary direct integration at an existing boundary, with scoped/integrity-checked references and bounded history. Do not substitute filename/tool text, modify site-packages, create replacement adapter/facade layers, or remove vision. If the installed SDK makes requirements materially incompatible, present exact source evidence and ask one focused question before changing dependencies/behavior.

## Guard tooling completed, keep it

These are Kilo development hooks, not product runtime wrappers:

- `.kilo/plugins/desktop-guard.ts`: native tool.execute.before/after + compaction hook.
- `scripts/framework-contracts.mjs`: incremental TypeScript language service, actual installed types and vendored components, changed files and affected callers (including prior importers after deletion), source overlays, multiset before/after diagnostics ignoring moved line numbers.
- `scripts/check_desktop_python.py`: AST architecture checks; isolated `--contracts` mode lazily inspects installed public Temporal signatures and validates literal native BrowserInput with Pydantic. No application execution or secret reads. Dynamic/unpacked/ambiguous arguments defer instead of guessing. This is not universal runtime/OpenAPI validation.
- `scripts/desktop-guard.mjs`: direct standalone checks, existing failures remain visible.
- `scripts/desktop-guard.node-test.mjs`, `framework-contracts.node-test.mjs`, `test_desktop_python_contracts.py`, `desktop-guard-hooks.test.ts`: fixtures cover aliases, real component props, valid JSX spreads, invalid SDK arguments, importers, moved existing errors, unrelated code, repairs, concurrent writes, native/MCP outputs, no rollback.

Full writes/edits reject NEW attributable contract errors before execution. Partial patches check actual files after execution and return correction instructions; they are not transactional. Existing errors and ambiguous architecture hints do not broadly block repairs. Hooks do not scan ordinary shell commands, external editors or unknown tool schemas, do not rewrite final text, do not auto-revert, and do not install Git hooks. Unsupported/failed checking is explicit, never a pass.

Installed @kilocode/plugin 7.4.17 API and official Kilo source were inspected: project `.kilo/plugins/*.{ts,js}` auto-load at backend instance startup. A new chat/local Agent Manager session in the same backend does NOT guarantee plugin reload. Backend reload rejects active sessions; restarting the backend is safest for updated import cache but must not disrupt active work. Plugin init failures can be skipped. Activation in the running backend has NOT been verified. Native after hooks mutate `output.output`; MCP after hooks receive raw `content` despite the declared native type; implementation handles both. Thrown before errors reject a tool call, not guarantee an entire session stop.

Guard validation from the completed preceding increment: 21 Node architecture/compiler tests + 30 Python tests + 16 native/MCP hook tests = **67 passed**. Targeted new-file TypeScript/ESLint passed, and `check:contracts` on browser_activity/desktop_worker/computer-use-activity passed. During THIS handoff, a refresh command was user-aborted after the first 13 Node tests; do not call that a completed rerun or erase the prior completed evidence.

Fresh `pnpm check:desktop` result (expected nonzero, fixes remain):

```text
orchestrator/computer_use_activity.py:163 [novnc-viewer] removed CDP screenshot player reference
orchestrator/computer_use_activity.py:98 [headed-desktop] headless default
orchestrator/desktop/Dockerfile:16 [source-only-image] COPY . .
orchestrator/run_worker.py:404 [host-desktop-execution] computer activities registered on host
```

Keep checks useful, not restrictive busywork. Do not broaden the assignment into rebuilding hook infrastructure.

## Runtime at handoff (recheck before touching it)

- Homepage `http://127.0.0.1:3000` returned HTTP 200. `/health` at 8787 reported temporal=true, worker=true, pollers=true, full model catalog.
- Listener inspection: Next PID 82253 on 3000, API PID 81892 on 8787, Temporal PID 81839 on 7233/8233. PIDs are transient, not authority to kill them later.
- No listeners on 3001 or noVNC 6080. `docker --context colima ps` failed: cannot connect to `/Users/tims-stuff/.colima/default/docker.sock`. **Desktop unavailable now.** Do not infer image/container success from an old background build ID.
- background_process list for the source session is empty; visible app processes may be owned by another supervisor/session. Inspect process lineage and active workflows before restart.
- A previous startup outage was a stale same-repo Next on 3001 holding Next's shared dev lock. `pnpm dev:clean` frees 3000/7233/8233/8787, not arbitrary Next instances. `pnpm dev:all` uses concurrently --kill-others, so a child exit shuts down the whole stack. No need to restart currently healthy app just to read this handoff.
- Docker/Colima are installed. Verify daemon; use documented recovery for stale session-bound Colima. The previously working command was `colima start --foreground --cpu 2 --memory 4 --disk 20 --vm-type vz --mount-type virtiofs --mount /var/folders/c8/y41r0sp91172dqm_0jk3wcww0000gn/T/kilo:w` through tracked background_process; do not blindly change mounts/state or assume that temporary path exists in a new runtime. No user home/repo/credential mount into browser containers.
- On one previous Docker pull, credential helper discovery needed `/Applications/Docker.app/Contents/Resources/bin` on PATH. This is not permission to read Docker credentials.

## Validation commands and known failures

From repository root:

```bash
pnpm check:desktop
pnpm check:contracts orchestrator/browser_activity.py orchestrator/desktop_worker.py components/v0/computer-use-activity.tsx
pnpm test:desktop-guard
pnpm test:python-contracts
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' scripts/desktop-guard-hooks.test.ts
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' lib/perplexity.test.ts components/v0/model-picker.test.tsx components/v0/use-models.test.ts components/v0/compare-view.test.tsx
npx tsc --noEmit
pnpm lint
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
```

From `orchestrator/`:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest tests/test_browser_activity.py tests/test_computer_use_activity.py tests/test_perplexity_model.py tests/test_server.py tests/test_workflow.py -q
```

Last complete project-wide checks in preceding work were NOT green: stale devtoolsFrontendUrl/screenshotUrl test fields cause TypeScript and component-test failures; lint also collected `.kilo/worktrees/successful-jam` and `victorious-shrimp` with set-state-in-effect errors. Preserve those worktrees. Full backend suite was not rerun in the guard/model-list increments. Reproduce current failures and state their exact scope; don't hide them by weakening tests or claiming historical passes. Browser worker tests must route activities to their actually polled queue and use the required native executor/converter, or they can hang. `pnpm build` does not enforce types (ignoreBuildErrors is enabled).

## First implementation increment

1. Re-read scope and current state; run the guard and targeted contract checks, preserve the now-working full model picker. Verify hook activation or explicitly use commands; do not spend the task rebuilding hooks.
2. Fix container/source-only dependencies, Chromium runtime ownership and service startup/readiness. Verify the stock browser worker polls its dedicated queue and no agent browser starts on Mac. Integrate readiness without needlessly terminating active host turns.
3. Resolve image transport against installed native contracts and unify computer-use with the correct desktop session. Do not leave Perplexity vision absent or computer-use host-bound.
4. Complete server-confirmed exclusive takeover, release, steering and native same-ID continuation with actual browser context and cancellation semantics.
5. Complete fresh live UI acceptance below; retain code/transport/provider tests and update the handoff with observed failures rather than storytelling.

## Literal acceptance sequence

Use a local disposable visual fixture with random positions/code painted only into a canvas; don't use external login/purchase actions. Discover a live Perplexity model, choose one permitted by the project model policy (GPT6 was present in this handoff), and submit through the real composer:

"Open the visual test page at <actual fixture URL> using the browser tool. From the screenshot, click the blue rectangle and report the random code revealed in the image. Do not inspect DOM, source, files, or HTTP responses. Leave the desktop open for my takeover."

Watch the actual noVNC iframe. Take control during work, make a visible human change, relinquish, then submit through the steering PromptInput:

"I changed the visible note to HUMAN-UPDATED. Observe the new screenshot, retain the original task context, and continue using that note."

Prove unchanged application session/Workflow ID, changed Run ID, retained tabs/context, fresh screenshot, applied steering once, and complete ordered Task/ChainOfThought events. Repeat with computer-use including an action outside the browser page (e.g. a disposable desktop editor), plus stale connections, held inputs, reconnect and worker loss. Screenshots only in UI or successful navigation alone do not prove the agent sees pixels or takeover works. Do not disable takeover or remove noVNC/Think to claim completion.

Existing PNGs such as `desktop-ui-working.png` and `novnc-desktop-connected.png` document a REMOVED custom implementation. Keep them as history, never cite them as acceptance for this replacement. Until the full sequence passes on current code, desktop completion remains open.

## September 10 native desktop implementation update

Current source replaces the stale gaps above: source-only image allowlist, pinned desktop dependencies, Xauth-cookie display, sandboxed headed Chromium, supervised services, single-slot native desktop worker, physical PyAutoGUI inputs, trusted screenshot manifests and request-local provider image hydration, private image proxy, durable session-ID UI wiring, native Task screenshot evidence, and authoritative takeover/reconnect state.

Important live finding: Linux flock over Colima virtiofs did NOT exclude a host flock. Consequently all control mutations now execute in `browser_activity.desktop_control` via the native workflow update and desktop queue, not host locks. Host status and evidence reads are read-only. Native worker liveness is probed through a container-local lifetime lock, never through a host lock.

`pnpm build:desktop` explicitly builds the image. `pnpm dev:desktop` only starts a verified content-hash-labeled image; stale/missing images fail with rebuild instructions. It does not silently build every startup. The launcher excludes macOS resource-fork files from its allowlisted tar context.

Real diagnostic evidence, NOT complete acceptance:

- Earlier real Perplexity UI run on workflow `chat-6189f37c1b65e992` ran browser init, navigate and physical click on `desktop-browser`; canvas pixels revealed `VISION-842910`. Two 1440px screenshots decoded in the UI. Human VNC key events changed the note to `ORIGINAL-NOTEHUMAN`.
- That run found rollover ownership loss, incomplete terminal action delivery, and view-only reconnect denial. Source fixes include persisted desktop handoff fields, lifecycle release, native ownership transitions and a bounded native stream completion drain. Do not call the earlier run a successful full sequence.
- A later real Temporal-native probe `desktop-native-probe-20260910` executed take -> release -> prepare_resume -> resume, typed `DESKTOP-INPUT-VERIFIED` into Mousepad, captured PNG pixels, and released ownership. Native receipt at end was viewonly=1, deny=0, client_count=0, pointer_mask=0. This was a direct activity probe, NOT a model-driven UI editor test.
- Normal-configured UI requests on `http://127.0.0.1:3002` fail before desktop execution with `Managed connector "connector_googledrive" is not connected.` Workflow `chat-ddecf753a8239ac5` has no pending activities after the failure was made nonretryable. The UI now shows the actionable API Group setup URL instead of generic `Workflow update failed`. Configured connectors were NOT removed and no test-only connector override was used.
- https://docs.perplexity.ai/docs/agent-api/tools/connectors.md documents the standard IDs and API Group authorization at https://console.perplexity.ai/group/connectors. It says disconnected connectors should produce an empty catalog rather than fail; the observed provider failure conflicts with that behavior. No public connector-list/status API was found. The real console is behind an interactive Cloudflare check; do not bypass it or claim authorization.
- The persistent hero seen in the QA browser was caused by the QA tab being hidden with paused animations. `agent-browser ... tab t1` foregrounded it and rendered the actual conversation. No UI source bug was established for that symptom.

Latest passing checks: 631 scoped backend desktop/provider-boundary tests; 169 desktop UI tests; 120 workflow/control tests with one real-browser opt-in skipped; `pnpm exec tsc --noEmit`; `pnpm check:desktop`; `git diff --check`. Full gates remain NOT green: latest full backend stops at preexisting `compare_workflow.py:81` timedelta adherence failure after 559 passed/1 skipped; full frontend retains two adherence failures in the compare route and model-catalog comment; lint retains unrelated `.kilo/worktrees` failures. Do not suppress those gates.

Evidence is under the approved temp directory `.../T/kilo/`: `desktop-acceptance-observations.json`, `desktop-acceptance-final/acceptance-human-edit.png`, `desktop-native-control-final/`, `desktop-history-evidence.py`, `desktop-native-probe.py`. Final visible error screenshot: `/Users/tims-stuff/.agent-browser/tmp/screenshots/screenshot-1789080700084.png`. No final success evidence exists.

Check actual processes before continuing: isolated acceptance used Temporal 7235, API 8789, Next 3002 with `.next-desktop-acceptance`, fixture 8799. Primary 3000/8787 stack belongs to other work and was preserved. After session-bound removal, the final image `ab85eaf88849` was rebuilt and started as a tracked persistent process `bgp_08d874722001Z6vWrwEecGtdQe`; Docker reported running/healthy UID501 and logs confirmed `Desktop worker polling 'desktop-browser'`. Recheck runtime rather than relying on this snapshot. No cloud resources or commits were created. Think remains the separate agent's scope and was preserved.

Remaining finish gate: restore verified image/runtime, resolve the real API Group connector authorization without removing capabilities, then repeat the complete literal UI sequence including fresh post-human pixels, same-ID/new-run steering, all action/Think updates, and the non-browser editor task. Feature remains incomplete until this passes.
