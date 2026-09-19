# Obsidian & Indigo — implementation and validation status

User-approved appearance-only redesign. **Implemented first pass; final acceptance is blocked, not complete.**

## Latest continuation — native streaming

The historical results below are records of prior attempts, not the current
gate verdict. Fresh visible output in this continuation confirms **540 Vitest
tests passed**, including streaming adherence. The unused Next compare route
was retired after the red-first test required by the framework-reset plan;
the compare UI keeps its existing independent native chat transports. Research,
compatibility coverage and limitations are recorded in
`/Users/tims-stuff/Desktop/v0-clone-blurple/docs/native-compare-streaming.md`.

TypeScript passed after regenerating stale Next route types. Lint passed with
one existing warning. All 15 saved TSX presentation baselines and the CSS motion
audit passed. Desktop structural checks, 21 guard fixtures and 30 Python contract
fixtures passed. Full Python initially had three `SessionRollingOver` failures;
the three cases passed on focused rerun (12 tests including adherence/history).
These results do not establish live desktop or final visual acceptance.

Final full Python rerun: **1,292 passed, two live-Linux-environment skips, 30
warnings**, exit 0. This records the current backend working tree, including
outside-task edits, not backend changes in this frontend commit. Final focused
compare/adherence check: 22 passed. Full logs: `/tmp/blurple-native-*.log`.

This publication includes prior native activity UI work as well as the styling
pass; it is not an appearance-only diff against HEAD. Backend and unrelated
working-tree changes remain outside the new frontend commit.

## Changes

- `/Users/tims-stuff/Desktop/v0-clone-blurple/app/globals.css`: near-black/indigo tokens, quieter static decorative surfaces, prose typography and panel utilities.
- `/Users/tims-stuff/Desktop/v0-clone-blurple/app/layout.tsx`: browser theme color.
- Presentation changes at existing native composition sites under `/Users/tims-stuff/Desktop/v0-clone-blurple/components/v0/`: `site-header.tsx`, `blurple-background.tsx`, `composer.tsx`, `agent-chat.tsx`, `model-picker.tsx`, `compare-view.tsx`, `agent-activity.tsx`, `computer-use-activity.tsx`, `computer-use-preview.tsx`, `skill-agent.tsx`, `graph-artifacts.tsx`, `graph-node.tsx`, `project-ide-panel.tsx`, `data-observation-panel.tsx`.
- Existing dirty activity/backend changes were not replaced. No intended dependency, API, backend, vendored AI Elements or shared primitive edits.
- Audit caught added decorative wrappers and modified pulse-soft keyframe colors; these were restored. Desktop composer now explicitly requests `md:text-base`. Generated inspection-server additions to tsconfig and next-env were removed.

## Evidence directory

`/var/folders/c8/y41r0sp91172dqm_0jk3wcww0000gn/T/obsidian-indigo-VqDIwBlE`

Contains brief, original presentation snapshots (`baseline.tar`), test logs, baseline and interim screenshots, and an AST/motion audit script. Temporary files may not survive OS cleanup. Interim screenshots are **not final visual acceptance**. Synthetic populated conversation screenshots are browser-only fixtures, not live agent/desktop evidence.

## Observed validation

- TypeScript: baseline and interim checks passed. Final post-correction rerun blocked by terminal execution failure.
- Scoped app lint: passed with two existing warnings before edits; implementer also reported changed-file lint passed before final corrections.
- Full baseline lint: 5 errors, 22 warnings, errors in nested worktree copies. Not a green gate.
- Baseline Vitest: 532 passed, 2 pre-existing framework-adherence failures.
- Post-styling Vitest: 531 passed, 3 failed. Same two adherence failures plus `scripts/desktop-guard-hooks.test.ts` Python-contract hook timeout at 5000ms. Isolated rerun: 15 passed, same 1 timeout. Do not classify timeout as passed.
- Baseline orchestrator pytest: 1,284 passed, 2 failed, 2 skipped, 30 warnings. Failures: `compare_workflow.py:81` timeout constant outside config; `graph_activity.py:605` empty exception handler. Final rerun not performed; no backend edits intended.
- Desktop structural guard passed before styling; real desktop acceptance was not run.
- Live Next home compiled and served updated theme before environment degradation. Isolated acceptance server later returned HTTP 200 but `/api/models` returned 502.
- Last executable AST audit passed presentation-only semantics for most changed files, flagged site-header/compare wrappers and pulse-soft keyframe changes. Corrections were applied; final audit could not execute.
- Independent external design evaluation was attempted twice but could not launch reliably. No evaluation verdict exists.

## Remaining checks / known limitations

1. Restore reliable terminal/browser execution, stop only task-owned stale processes, then rerun final AST audit and all repository gates.
2. Inspect full diff against baseline snapshots (not only HEAD, which includes substantial pre-existing work).
3. Complete fresh desktop/mobile/320px/768px/200%-zoom review, focus/portals/attachments/disclosures/scrolling and normal/reduced motion. Verify actual composited contrast. No certification of these final states is claimed.
4. Native InputGroup dims the entire empty composer when Submit is disabled; unsupported internal styling was not overridden. Native tool-status icon colors and Shiki backgrounds are unchanged.
5. Complete required live desktop acceptance separately if asserting browser/computer-use feature readiness.
6. Inspect generated `/Users/tims-stuff/Desktop/v0-clone-blurple/.next-obsidian-inspect/` from the implementer's inspection attempt; remove only that task-generated build output once its server is confirmed stopped. It is not intended source and may pollute broad lint/type discovery.

## September 19 failure-fix continuation

This continuation separately authorizes test/lint fixes; it does not change the
appearance-only scope of the earlier redesign. Existing dirty work was preserved.

Changes under `/Users/tims-stuff/Desktop/v0-clone-blurple/`:

- `orchestrator/compare_workflow.py` reuses the existing 200ms configured stream interval.
- Empty exception handlers now log optional-infrastructure/cleanup failures or explicitly preserve malformed tool text. Cancellation still propagates from graph execution. A full AST scan was necessary because the adherence assertion stops at its first violation.
- `orchestrator/tests/test_framework_adherence.py` temporarily registers its isolated provider import in `sys.modules`, fixing dataclass annotation resolution without depending on test collection order.
- Graph cancellation regression coverage now includes publication failure; `orchestrator/tests/test_workflow_history.py` verifies malformed JSON is retained once and screenshot sanitization does not mutate input history.
- `lib/perplexity.ts` references `DEFAULT_MODEL` in its comment instead of repeating a quoted model ID. The model constant and scanner assertions are unchanged.
- `eslint.config.mjs` excludes nested worktrees, inspection build output, and the installed tools symlink rather than modifying copied applications.
- `scripts/desktop-guard-hooks.test.ts` splits native SDK signature and BrowserInput checks into separate cases with 20-second budgets. Measured cold subprocess checks took 1.63s and 2.23s independently; the combined 5-second test passed in isolation but failed under full-suite contention. Runtime checker deadlines and diagnostic assertions are unchanged.

Fresh evidence (logs at `/tmp/blurple-resume-*.log`, with matching `.exit` files):

- `npx tsc --noEmit`: passed, including the final test changes.
- `pnpm lint`: passed, 0 errors and 2 existing unused-variable warnings.
- Scoped Vitest: **534 passed, 1 failed**. All 17 desktop hook cases passed.
- Focused Python adherence, graph, and new history regressions: **36 passed**.
- Desktop structural guard: passed, explicitly not feature acceptance.
- Node guard suites: **21 passed**; Python contract checker suite: **30 passed**.
- Presentation audit: all 15 TSX snapshots passed semantic comparison; CSS keyframes, custom properties, and reduced-motion blocks matched the baseline.
- Full Python rerun: **1,287 passed, 2 skipped, 30 deprecation warnings** in 95.29s. The newly added history test was created after collection and passed separately in the 36-test focused run. Skips require an explicitly enabled, unowned Linux desktop worker or a real headed Linux browser. An earlier run failed the then-unfixed empty-handler gate and timed out in `test_queued_native_action_times_out_before_execution[take_screenshot]`; the unchanged timeout test passed in the final full rerun, so intermittent timing remains worth monitoring.

**Remaining protected-route blocker:** `/Users/tims-stuff/Desktop/v0-clone-blurple/app/api/compare/route.ts` still uses raw stream/encoder construction and fails the frontend adherence check. It was not edited: repository policy requires a failing compatibility test proving the backend cannot satisfy an existing contract, not merely a structural assertion. No test was disabled or exempted to conceal this failure.

Documentation consulted: https://elements.ai-sdk.dev/docs/usage and
https://ai-sdk.dev/docs/ai-sdk-ui/streaming-data, plus the local Strands/Temporal guide.
AI SDK UI-message streaming is documented, but adopting its envelope is not automatically compatible with the existing compare event protocol.

No new browser or live Linux desktop acceptance was performed. Redesign acceptance remains incomplete. Old implementation PID 58009 was absent; port 3111 had no listener, and port 3100 still had a listener. No unrelated processes were stopped and no commit was made.

## Historical process cleanup caution

Initial implementation process PID was 58009 (OpenCode session `ses_f48729630ffez7JxsOar4k0p6y`). Stop requests could not be verified after terminal integration stopped executing commands reliably. Verify current process identity before stopping anything; do not trust an old PID blindly.

Task inspection servers were started on ports 3100 (`.next-desktop-acceptance`) and 3111 (`.next-obsidian-inspect`). Their final running state is unverified. Port 3000 became owned by a separate Electron process during this task; do not kill it. Browser sessions: `obsidian-review`, `obsidian-fixture`; close only task-owned sessions if still present. No commit was made.