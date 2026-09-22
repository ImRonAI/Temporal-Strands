# Agent response investigation — September 16, 2026

## Reproduction and cause

The local UI, API and Temporal server were stopped at inspection. The existing
Linux desktop container was healthy and was preserved. Starting the four host
services restored a healthy catalog of 57 worker-declared models.

A real Playwright UI turn using `preset:high` returned “Yes, I can respond.” in
21.9 seconds. The captured `/api/orchestrator` response contained a zero-cycle
Think call, its result, text deltas, text-end and `[DONE]`; the answer appeared
in the native MessageResponse UI. General text delivery was working.

Selecting `anthropic/claude-fable-5-1` with High effort reproduced the stall in
session `chat-745341b5acd6ff14`, run
`01a0a926-5dee-7493-9944-729afb74466e`. Prompt:

> Remember the phrase amber kite for this conversation. Use Think for one
> low-effort cycle, then confirm the phrase briefly. No research or other tools
> are needed.

The worker repeatedly retried `invoke_model_streaming` activity 3. The gateway
returned HTTP 200, followed by a stream error:

```json
{"code":"invalid_request","message":"invalid request","type":"invalid_request"}
```

Two defects combined:

1. `PerplexityModel.stream` forces named `think` selection whenever the native
   workflow hook sets `require_think`. Fable 5.1 does not support forced tool
   selection. Replaying the actual failing model-activity input through the
   production provider reproduced the error; changing only selection to native
   `auto` produced a real Think tool call and `stopReason: tool_use`.
2. `_is_permanent` recognized HTTP status and numeric error codes, but not this
   symbolic stream error. `MODEL_RETRY_POLICY = RetryPolicy()` therefore retried
   the same invalid request indefinitely within its activity lifetime. The UI
   remained on Thinking because the workflow update had not finished.

## Changes

- `orchestrator/perplexity_model.py` / `config.py`: classify permanent symbolic
  request errors as native non-retryable Temporal ApplicationErrors. Adapt named
  tool choice for the documented Fable 5.1 capability restriction to native auto
  plus an explicit instruction. Preserve the workflow's Think-first validation,
  user-selected effort, and model-selected Think arguments. No provider fallback.
- `orchestrator/agent.json`: remove the obsolete claim that Think automatically
  runs before model invocation and returns a `think_notes` block. Describe the
  current model-chosen Think contract.
- `components/v0/agent-activity.tsx`: render Think tokens directly as reasoning
  in native ChainOfThoughtContent / ChainOfThoughtStep with MessageResponse.
  The initially added generic Think Tool card was incorrect and has been removed:
  Think arguments must not appear as a JSON Parameters panel. Keep pending/error
  status and unstreamed conclusions in native reasoning steps, deduplicate streamed
  conclusions, and keep the chain expanded while streaming using native controlled
  props. Completed chains remain manually collapsible. Replace the nested component
  definition with direct keyed rendering to preserve component identity.

The protected Next.js routes and vendored AI Elements primitives are unchanged.

## Documentation checked

- [Temporal Strands integration](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md),
  also fetched via Context7; compared against installed temporalio 1.31.0 source.
- [Perplexity documentation index](https://docs.perplexity.ai/llms.txt) and
  [OpenAI compatibility](https://docs.perplexity.ai/docs/agent-api/openai-compatibility).
- [Claude forced-tool restriction](https://platform.claude.com/docs/en/api/errors#forced-tool-use-not-supported).
- [AI Elements Tool](https://elements.ai-sdk.dev/components/tool) and
  [Chain of Thought](https://elements.ai-sdk.dev/components/chain-of-thought),
  compared against the installed primitives; AI Elements also queried via Context7.
- [React state preservation](https://react.dev/learn/preserving-and-resetting-state),
  queried via Context7 for the nested-component remount defect.

## Baseline gates

- `npx tsc --noEmit`: passed before edits.
- `pnpm lint`: 5 errors and 22 warnings. All errors are existing
  `computer-use-preview.tsx` effect violations under `.kilo/worktrees/`:
  actually-soup, ethereal-asterisk, successful-jam, victorious-shrimp, wax-tern.
- Scoped Vitest: 532 passed, 2 failed. Existing framework-adherence failures:
  protected compare route streaming implementation and a model ID in a comment
  in `lib/perplexity.ts`.
- From `orchestrator/`, `.venv/bin/python -B -m pytest tests -q`:
  1270 passed, 2 failed, 2 skipped. Existing failures: timedelta outside config in
  `compare_workflow.py:81`, empty except in `graph_activity.py:579`.
  Skips: `test_browser_activity.py` requires opt-in `GWEN_DESKTOP_LIVE_TEST=1`;
  `test_workflow.py::test_browser_survives_handoff_rollover` requires Linux and
  DISPLAY. This run does not establish desktop acceptance.

## Post-change verification

- Fresh Playwright UI session `chat-7c3fcc30c4919406`, Fable 5.1 / High: the same
  amber-kite prompt completed in 18.4 seconds. Native Tool displayed the real
  low-effort, one-cycle Think invocation and its completed status, streamed
  reasoning text, and the final response.
- A second UI turn, “What phrase did I ask you to remember? Answer with only the
  phrase.” returned `amber kite` in 18.7 seconds under the same session. The
  user-collapsed first Think panel remained collapsed through composer changes
  and the second response, confirming native component state retention.
- Replaying actual model-activity input with unsupported forced `any` selection
  against the real gateway produced the documented `invalid_request` SSE error.
  The production adapter now raised `PerplexityModelError` with
  `non_retryable=True`, preserving the real APIError as its cause.
- Final TypeScript and changed-file ESLint passed. Full lint, scoped Vitest and
  full orchestrator pytest were rerun and retained exactly the baseline counts
  and failures listed above. No failing checks were excluded or weakened.
- Existing unrelated root `AGENTS.md:96` trailing whitespace fails full
  `git diff --check`; checks limited to this change pass.

During verification the Mac load average exceeded 500. Worker startup and
browser commands stalled, and Turbopack's CSS subprocess timed out. A normal
Next config reload recovered the dev server without changing the configuration
or restarting Temporal. A separate older session also logged Temporal workflow
deadlock/time-limit diagnostics during host contention. Neither is counted as a
green runtime check. The fresh successful sessions above completed afterward.

The first live pass of the new Think presentation exposed ToolInput receiving
undefined during `input-streaming`; its CodeBlock cannot render that value.
That intermediate composition waited for actual input before mounting ToolInput,
and both fresh UI turns above completed without that crash. The later correction
removes Think's ToolInput entirely. A screenshot assertion was
also rerun with a unique final-paragraph locator after a strict-locator error
matched both reasoning and final text.

No mocks or new synthetic tests were added as evidence of production behavior.
Runtime logs, captured response stream, UI assertions and screenshots are under
`output/playwright/agent-response/`. The corrected identity prompt loads on API
startup; already-running API processes and durable sessions retain their prior
prompt, while the provider capability fix applies to their subsequent calls.

## Think presentation correction

The Tool card described in the historical UI checks above was the wrong
presentation. The final UI composes ChainOfThoughtContent, ChainOfThoughtStep,
and MessageResponse directly for the existing native reasoning deltas. Think's
arguments are no longer displayed as JSON. No route, event protocol, or vendored
primitive changed. Pending status disappears once reasoning text is visible;
failed/interrupted Think and conclusions missing from the stream remain visible.
The chain uses native controlled open props to remain expanded during the turn,
with manual collapse available after completion.

Real Playwright session `chat-7a2ab346565d499e` used Fable 5.1 / High and
requested two low-effort Think cycles comparing queues and stacks. DOM observation
captured text growing from "A queue" to "A queue follows FIFO (first in," while
the Thinking header remained expanded, without any preformatted JSON panel.
Both cycles and the final answer completed in the actual UI. Evidence:
`output/playwright/agent-response/think-live.txt` and `think-streaming.png`.

After removing the redundant pending placeholder, a fresh final UI session
`chat-b64c769cc44ad45c` repeated the two-cycle request successfully. Incremental
DOM text remained expanded with no JSON panel. Attempting to activate the header
during streaming did not collapse it. Completed-chain collapse/reopen also passed.
Final artifacts: `think-live-final.txt` and `think-streaming-final.png` in the
same directory. The final snapshot showed both reasoning cycles and the final
answer; the browser console contained no errors.

Correction verification:

- TypeScript and changed-file lint passed.
- Full lint: 5 existing errors in `.kilo/worktrees/*/components/v0/computer-use-preview.tsx:74`
  (actually-soup, ethereal-asterisk, successful-jam, victorious-shrimp, wax-tern),
  plus 22 warnings.
- Full scoped Vitest: 530 passed, 4 failed. The two existing adherence failures
  remain (compare route streaming primitives and the model-id comment in
  `lib/perplexity.ts`). Two desktop-guard-hooks tests exceeded their 5-second
  timeout: duplicate violations/line shifts, and installed Python SDK signatures.
  Rerunning that entire guard suite alongside the Think presentation suite
  passed all 35 tests without changing timeout limits or test implementation.
- Full orchestrator pytest: 1270 passed, 2 failed, 2 skipped. Existing failures:
  `test_f_no_timedelta_outside_config` and `test_h_no_empty_except_handler`.
  Skips remain the opt-in live browser test and Linux DISPLAY workflow handoff
  test. These do not establish desktop acceptance.

Logs use the `think-*` prefix under `output/playwright/agent-response/`.
