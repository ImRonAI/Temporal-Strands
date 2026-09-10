# Repository Guidelines

## Authority and scope

This is the browser/computer-use completion contract from the user's September 10, 2026 requirements. Read root `AGENTS.md`, `orchestrator/AGENTS.md`, and `components/v0/AGENTS.md`. Root and parent instructions require this document for sibling backend files and related routes/startup scripts. Paths below are repository-relative unless stated otherwise.

Read `.kilo/plans/1788845228632-gce-desktop-native-infrastructure-spec.md` and `.kilo/plans/2026-09-08-desktop-implementation-handoff.md` for context, not completion evidence. Later user corrections override their obsolete direct-RFB mounting suggestions: **noVNC must be inside the WebPreview iframe; Think must remain enabled.** Do not broaden this task into workspace redesign, graph changes, or GCE provisioning. Preserve concurrent changes and protected paths. No cloud resources without explicit approval.

## Non-negotiable implementation

- The browser is **headed inside isolated Linux**, never on the Mac and never made headless as a substitute. Both browser-use and computer-use actions target the same authorized desktop/session shown to the user. Browser-only actions do not prove full desktop input support.
- Embed native noVNC in `WebPreviewBody`'s iframe. Show the live desktop, browser chrome, and current page. CDP screencasts, screenshot players, and iframing the visited website are not substitutes. Agent screenshot evidence is a separate path.
- Zero redundant custom framework wrappers. Use `TemporalAgent`, worker model factories, `@activity.defn`, `activity_as_tool(task_queue=...)`, native workers/executors, hooks, updates, interrupts, streams and Continue-As-New. One typed activity binding around the stock tool is required integration, not a replacement framework.
- Current browser direction: stock `LocalChromiumBrowser` via `orchestrator/browser_activity.py`, registered by `orchestrator/desktop_worker.py` on `desktop-browser`. The host worker must not execute desktop tools. Inspect `orchestrator/computer_use_activity.py` separately; do not assume browser routing fixes it.
- Implement necessary domain behavior directly at documented extension points. Do not add facade classes, browser subclasses to duplicate stock functionality, replacement tool adapters/envelopes, event buses, custom RFB parsers/control servers, or duplicate agent loops. Do not delete legitimate domain logic merely because it is application-written.
- Preserve Perplexity Think and asynchronous streaming throughout execution. Resolve models from the live catalog and keep provider contracts intact. Show every emitted progress/Think event and agent action honestly, not private reasoning the provider never returns.

## Screenshot compatibility gate

The checked-in thin browser binding is NOT proof of image delivery. Installed source under `orchestrator/.venv/lib/python3.13/site-packages/` establishes:

- `strands_tools/browser/browser.py`, `_async_screenshot`: saves a PNG and returns a filename in text.
- `temporalio/contrib/strands/_temporal_activity_tool.py`, `TemporalActivityTool.stream`: converts returned activity data to JSON/text.
- `temporalio/contrib/strands/workflow.py`, `activity_as_tool`: requires `@activity.defn` and forwards native task-queue, timeout, retry, and cancellation options.

Recheck installed versions before coding. Preserve the native adapter; prove supported image content reaches the model request. A filename, textual base64, or UI-only screenshot fails acceptance. Keep large image bytes out of unbounded Temporal history; use the framework's supported storage/payload mechanisms or direct integration at the existing model boundary with scoped, integrity-checked references. Do not resurrect a standalone observation facade or silently patch site-packages. If the installed contract cannot satisfy this without a requirement change, present the precise limitation and ask one focused question before changing architecture/dependencies. Do not remove vision to achieve nominal framework compliance.

## Handoff and continuity

Required order: **agent -> stopping -> human -> relinquishing -> steering input -> resuming -> agent**.

1. Take Control fences new agent mutations and waits for actual in-flight action completion/cancellation cleanup before granting native VNC input. `Agent.cancel()`, a released turn lock, or a cancelled HTTP request alone is not cleanup proof. Use supported sequential execution and cancellation semantics with finite bounds.
2. During human control, no agent/session may concurrently mutate that desktop. Bind ownership on the server. Client `view_only`, button state, and pointer CSS are not security boundaries.
3. Relinquish revokes native input, handles existing connections, and clears held keys/buttons before acknowledging release. Native VNC controls must be verified for the deployed version, including clipboard/resize and stale clients. Keep steering text on failure; never leave agent and human input enabled together.
4. Resume consumes user feedback once through the existing chat path. Native Continue-As-New retains the application session ID and Temporal Workflow ID; only Run ID changes. Carry messages, tool context, browser/project binding and steering explicitly. Preserve the live browser session/tabs across rollover and obtain a fresh screenshot after human changes.
5. Worker/container loss is different from rollover. Detect lost browser state, stop uncertain mutations, and report recovery needs. Do not claim exactly-once GUI effects or blindly retry clicks, typing, or submission after ambiguous completion. Stream reconnect resumes event delivery, not action execution.

## Known gaps to verify first

September 10 inspection found an unfinished desktop Dockerfile/start script, a full-host requirements dependency outside the build context, Chromium installed before switching users, no orchestrator-context `.dockerignore`, and no desktop readiness gate in `pnpm dev:all`. Verify these before building; do not copy `.venv`, runtime data, credentials, or `.env*` into images. Pin actual resolved artifacts, never invented digests. Keep browser sandboxing, non-root execution, and private listeners.

The computer-use path still contains separate host/CDP behavior. Handoff currently uses API-side VNC commands and workflow updates, not proven exclusive ownership/cleanup. Continue-As-New carries messages but does not restore a crashed browser. Tests still reference removed image/DevTools fields. These are inspection findings, not authorization to weaken requirements. Previously reported pass counts and screenshots from removed code do not validate the replacement.

## Execution and evidence gates

Finish in order: runtime/readiness; screenshot compatibility and both tool paths; native handoff/continuation; UI acceptance. For each boundary, cite the installed API, add/run a targeted failing test, implement the smallest direct fix, then verify before moving on. No unrelated refactors or test exclusions to manufacture a pass. Current startup is `pnpm dev:all`; it stops project services. Inspect existing sessions before restarting. A stale same-repo Next instance on port 3001 previously blocked port 3000 through the shared Next development lock; do not kill unrelated servers.

This document is a contract, not executable enforcement. Extend the existing framework-adherence and boundary tests during implementation to check native routing, absence of host desktop execution, preserved tool schemas, image delivery, control exclusivity, continuity and actual component props. Do not assert that those gates already pass simply because they are specified here. Review the diff against this contract before announcing each increment; stop at a failed requirement rather than substitute another feature.

Project tooling: `.kilo/plugins/desktop-guard.ts` uses native Kilo before/after edit hooks. Full writes/edits are preflighted in memory; patches are diagnosed after execution without rollback. Only new attributed errors block full edits; existing diagnostics and ambiguous component moves do not. TypeScript uses installed declarations and affected callers, not a copied prop list. Ordinary shell commands, external writers, and unknown tool schemas are not intercepted. Run `pnpm check:desktop` for all current structural findings, `pnpm check:contracts components/v0/computer-use-activity.tsx` for compiler diagnostics in that source and its callers, and `pnpm test:desktop-guard` for guard fixtures. These supplement, not replace, the gates below. Kilo discovers `.kilo/plugins/*.ts` at backend instance startup; a new conversation alone does not load changes. Restart the backend when safe and verify plugin loading; initialization failures can leave hooks inactive. Never claim this running session has the hooks merely because files exist. Plugin declarations are checked against installed `@kilocode/plugin`; native and MCP tool results have different shapes and both must receive diagnostics.

The compiler hook covers TypeScript/JavaScript under `app/`, `components/`, `lib/`, and `hooks/`, including affected importers and previous import edges after deletion. The small desktop architecture rules apply only to their scoped source boundaries. Python signature validation uses the installed public Temporal APIs, and literal `BrowserInput` payloads use the installed Strands Pydantic model. Dynamic payloads, unpacking, unknown APIs and ambiguous aliases are deferred rather than guessed; this is not universal runtime/OpenAPI validation. Run `pnpm check:contracts orchestrator/browser_activity.py orchestrator/desktop_worker.py` for the Python boundary and `pnpm test:python-contracts` for its tests. The guard never executes application source, rewrites final answers, automatically reverts files, or installs Git hooks. Failed contract tools exit nonzero (2 means incomplete/unavailable checking); warnings about uncertain architectural patterns remain advisory. Live testing below remains mandatory even if every static check passes.

Run these gates from the repository root:

```bash
npx tsc --noEmit
pnpm lint
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
```

From `orchestrator/`:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest tests/test_browser_activity.py tests/test_computer_use_activity.py tests/test_perplexity_model.py tests/test_server.py tests/test_workflow.py -q
```

Test workers must poll the actual desktop queue with the required native executor/converter; a wrong test queue can hang without testing anything. `pnpm build` is not a type gate. Report every failing/timed-out/skipped test and its scope; a known unrelated failure is not a green gate.

**Completion requires fresh real UI evidence for BOTH tool paths:** submit a literal Perplexity task; show actual movement in the noVNC iframe; solve an unpredictable screenshot-only challenge without DOM/source substitutes; take control during work; make a visible change with human input; relinquish; send steering; prove unchanged session/Workflow IDs and a changed Run ID; observe the agent use the change and finish. Confirm every emitted action/result appears in the native timeline. Exercise repeated handoffs, stale clients, held keys, reconnect, and worker loss. Capture model/provider, IDs, action evidence, native control acknowledgments, request errors and screenshots without credentials. Mocked tests cannot replace this sequence. Until it passes on the current code, the feature is unfinished.

Prepare a nonsecret, controlled visual fixture during implementation, not an external purchase/login workflow. Use this literal browser task with its real fixture URL substituted: "Open the visual test page at <fixture URL> using the browser tool. From the screenshot, click the blue rectangle and report the random code revealed in the image. Do not inspect DOM, source, files, or HTTP responses. Leave the desktop open for my takeover." While work is active, take control and change a visible field; relinquish with: "I changed the visible note to HUMAN-UPDATED. Observe the new screenshot, retain the original task context, and continue using that note." Verify actual actions, not a canned final reply. Repeat through the computer-use tools, including an interaction outside the web page (for example typing in a disposable desktop editor) to prove desktop control rather than browser-only automation. Prompts, fixture data and completion claims must match what was really executed.
