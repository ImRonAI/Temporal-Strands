## [2026-08-10] Task: context-gathering (Atlas)
- route.ts currently IGNORES messageStart/messageStop (they fall through if-chain, lines 415-487).
- textId is `const "response"` (line 228) — rotation requires making it mutable.
- Per-topic state needed: "events" and "thinking" topics both carry StreamEvent shapes incl. messageStart/messageStop.
- reasoningSeq/nativeSeq are monotonic counters; native-* data parts use `native-${type}-${nativeSeq++}` ids.
- Existing reconciled data parts pattern: data-session (id "session"), data-approval (id "approval") — stable id = update-in-place.
- route.test.ts has 2 INTENTIONALLY FAILING graph tests (lines 5-127) — NEVER touch/fix/delete them. Expected vitest result: exactly those 2 failures.
- Vitest scoping: pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts
- Test pattern: vi.stubGlobal("fetch", ...) returning SSE Response; assert on raw response body text.
- perplexity_model.py:299 yields {"messageStart": {"role": "assistant"}} after provider stream opens; messageStop only on terminal success (:499).
- pytest: run from orchestrator/, .venv/bin/python -m pytest tests -q
- ai package is v7 (^7.0.41), pnpm layout — node_modules/ai/dist/index.mjs does NOT exist at that exact path; must locate actual dist file to verify reconciliation semantics.

## [2026-08-10] Task: vendor terminal/jsx-preview/file-tree (Atlas direct)
- Source: raw.githubusercontent.com/vercel/ai-elements/main/packages/elements/src/{terminal,jsx-preview,file-tree}.tsx
- Import adaptation: @repo/shadcn-ui/components/ui/* → @/components/ui/*, @repo/shadcn-ui/lib/utils → @/lib/utils (sed).
- base-ui adaptation: file-tree.tsx CollapsibleTrigger used Radix `asChild` → converted to base-ui `render` prop (precedent: web-preview.tsx:224).
- Lint fixes: (1) jsx-preview unused `_lastGoodJsx` state in provider → ref-writer via useCallback; (2) lastGoodJsxRef read-during-render in JSXPreviewContent → useState mirror; (3) setState-in-effect reset → eslint-disable with vendored-behavior justification; (4) file-tree treeitem aria-selected added (2 sites).
- Deps added: ansi-to-react ^6.2.6, react-jsx-parser ^2.4.1.
- Gates: tsc 0, lint 0 (no errors/warnings), vitest = exactly 2 expected graph failures.
- Exports: Terminal{,Header,Title,Status,Actions,CopyButton,ClearButton,Content} (output/isStreaming/autoScroll/onClear); JSXPreview{,Content,Error}+useJSXPreview (jsx/isStreaming/components/bindings/onError); FileTree{,Folder,File,Icon,Name,Actions}.
- NOTE: visual-engineering task category model is broken in ~/.config/opencode/opencode.jsonc — errors as "perplexity-agent/google/gemini-3.1-pro-preview" not found (agent config at :208 says kimi-k3; the failing model id comes from elsewhere in the omo pipeline). Route around it or fix config.

## [2026-08-10] Task: think as activity_as_tool (Atlas direct, per user correction)
- USER DIRECTIVE: faithful copy of strands_tools/think.py, ONLY changes = async streaming + activity_as_tool. Nested agent is plain strands.Agent, NOT TemporalAgent. No extra invention.
- orchestrator/think_activity.py: ThoughtProcessor copied (minus rich Console); process_cycle async, consumes agent.stream_async(prompt), publishes event["event"] (raw StreamEvent from ModelStreamChunkEvent, strands/types/_events.py:104-118) to THINKING_TOPIC via WorkflowStreamClient.from_within_activity(200ms).
- LLM-facing signature: thought, cycle_count, system_prompt, thinking_system_prompt (tools/model_provider/model_settings/agent dropped — no parent registry across activity boundary; session model resolved via activity.client().get_workflow_handle(info.workflow_id).query("model_id") → worker factory registry installed by think_activity.configure()).
- Empty system_prompt falls back to workflow.THINK_SYSTEM_PROMPT (repo's handoff contract), not the original generic default.
- workflow.py: _build_thinker() deleted; tools=[activity_as_tool(think, MODEL_* timeouts, MODEL_RETRY_POLICY)]; think imported under workflow.unsafe.imports_passed_through().
- run_worker.py: think_activity.configure(model_factories); Worker(activities=[think_activity.think]).
- config.py: THINK_STREAM_BATCH_INTERVAL = 200ms.
- Tests: tests/test_think_activity.py (5) via ActivityEnvironment + patched activity.client/from_within_activity. GOTCHA: env.run(async fn) must be awaited INSIDE the patch context. Full suite: 61 passed.
- Return shape preserved: {"status": "success|error", "content": [{"text": ...}]} — activity_as_tool wraps via _to_text (json.dumps of the dict) into the ToolResult text.

## 2026-08-11 Phase 1+2 shipped (think activity + agent-activity restructure)
- think_activity.py: faithful strands_tools.think copy, plain strands.Agent (NOT TemporalAgent), stream_async publishing raw chunks to THINKING_TOPIC, registered via activity_as_tool in workflow.py. NO heartbeat_timeout on activity_as_tool: our activity has no @auto_heartbeater (only the SDK's _model_activity.py does) — declaring the timeout without heartbeating caused Temporal to cancel mid-stream every attempt.
- TMPRL1103 wedge: continue-as-new input carries agent.messages; a >2MB toolResult (think transcript) exceeded Temporal's 2MB per-payload cap and the rollover retried forever. Fix: _clamp_tool_results() at the rollover boundary, ROLLOVER_TOOL_RESULT_MAX_CHARS=20_000 in config.py, marker-inclusive clamp. TDD test: test_rollover_clamps_oversized_tool_results (note: >2MB fixtures cannot even start_workflow — "Blob data size exceeds limit").
- agent-activity.tsx: searches (native + pplx CLI in sandbox bash, PPLX_SEARCH regex) → Task>ChainOfThoughtSearchResults only; python → Sandbox Code/Output tabs; real bash → full Terminal composition (Header/Title/Status/Actions/CopyButton/Content, ANSI $ prompt) per upstream terminal.tsx example; reasoning text → ChainOfThoughtStep children (NOT description — description slot is text-xs muted) with MessageResponse/Streamdown; share_file images → ChainOfThoughtImage immediately; code files → Artifact+FileTree+CodeBlock (+WebPreview for html/pdf); GenUI → JSXPreview Content+Error; data-retry part → "Retrying · attempt N" step.
- globals.css was MISSING the required @source "../node_modules/streamdown/dist/*.js" (message component docs mark it required) — markdown classes were never compiled. Biggest visual defect of the session.
- Live QA green: searches as Tasks with link badges, Sandbox·python Completed with tabs, Terminal runs, full markdown final answer, 0 activity failures, 0 TMPRL1103.
- Gates: tsc 0, lint 0, vitest 2 expected graph failures only, pytest 62 passed.
