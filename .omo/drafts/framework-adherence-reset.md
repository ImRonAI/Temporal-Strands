---
slug: framework-adherence-reset
status: drafting
intent: clear
review_required: false
pending-action: write .omo/plans/framework-adherence-reset.md
approach: Two-part plan. (1) Rewrite root AGENTS.md + orchestrator/AGENTS.md from verified repo state (multi-provider gateway: Perplexity Agent API primary, Google AI Studio Gemini second, think tool LIVE, providers roadmap). (2) Framework-adherence fixes scoped to CONFIRMED violations only, each replaced by the framework primitive; keep justified custom Model subclasses; expose provider metadata to the picker via the readiness lease.
---

# Draft: framework-adherence-reset

## Components (topology ledger)
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
- C1 | Documentation reset: root `AGENTS.md` and `orchestrator/AGENTS.md` regenerated from verified state; every stale claim removed (think discontinued, graph_activity absent, StrandsPlugin on worker, test suites missing, Gemini-only) | active | AGENTS.md, orchestrator/AGENTS.md, explore lane bg_4783d9b6
- C2 | Orchestrator framework adherence: duplicate `_closable`/`closable_activity_options` collapsed to one config.py helper; `_ThinkFirstHook` re-expressed on `activity_as_hook`; think activity envelope moved to config.py; no other Python changes unless CONFIRMED | active | orchestrator/workflow.py:117-136,178-189,680-752; orchestrator/config.py:87-106; SITE/temporalio/contrib/strands/workflow.py:61-103
- C3 | Provider declaration in the model picker: readiness lease + /health + lib/perplexity.ts carry an explicit provider per model id (perplexity-agent-api / google-ai-studio) instead of prefix-guessing; picker groups by declared provider; static fallback lists removed from lib/perplexity.ts | active | orchestrator/run_worker.py:325-347; orchestrator/server.py:284-311; lib/perplexity.ts:15-68; components/v0/model-picker.tsx:37-48,95-105
- C4 | Provider roadmap contract: a documented, single-file extension point (run_worker.py factory assembly + config.py) for OpenAI / Anthropic / Mistral / xAI using native Strands classes (OpenAIModel/OpenAIResponsesModel, AnthropicModel, MistralModel, OpenAIModel base_url for xAI) — documented in AGENTS.md; no provider code written now | active | run_worker.py:278-322; librarian lane bg_39a07ea3 §4
- C5 | Frontend hygiene: dirty `components/v0/model-picker.tsx` (`actionsRef` imperative close) verified against @base-ui Popover contract; either kept (framework-native `actionsRef` prop) or dropped | active | components/v0/model-picker.tsx:87-93,114-119; components/ui/popover.tsx (dirty)
- C6 | Workspace hygiene: untracked GCP service-account key + `compute.json` + GCE scripts, stale `.kilo/worktrees/*`, `.omo/run-continuation/*` — record as dirty_worktree risk, NOT in scope for edits except .gitignore guard | active (guard only) | git status output in bg_8e4479cf

## Open assumptions (announced defaults)
<!-- assumption | adopted default | rationale | reversible? -->
- SUPERSEDED (user pushback 2026-09-08): `PerplexityModel` is NOT kept as-is; it is rebased onto `strands.models.openai_responses.OpenAIResponsesModel` (V5). `GeminiModel` subclass keeps additive overrides only; copied stream loop is minimized (V6).
- Subagent dispatch: NONE for the rest of this session (user model directive; planner cannot control harness model routing). All further verification is first-hand.
- 2s nested-stream batch interval in dirty config.py is CORRECT, not a regression | keep | config.py:107-160 documents 1,769 Signals/15.5 MB and 20 MB/52 MB runs at 200ms; Temporal history cap is 50 MB/51,200 events; coarser batching lowers Signal count. The failure-diagnosis lane's "revert" advice is rejected. | yes
- Test strategy | TDD for every code change (failing test first) — pytest from `orchestrator/`, scoped vitest | matches existing suite discipline (333 pytest, 22 route tests passing) | yes

## Findings (cited - path:lines)
### Confirmed violations (framework primitive exists; custom code duplicates/bypasses it)
- V1 Duplicate helper: `orchestrator/config.py:97-106 closable_activity_options` and `orchestrator/workflow.py:124-136 _closable` are byte-identical logic with two constants (`UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE` / `_UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE`); workflow.py never imports the config one. Violates repo rule "constants live in config.py".
- V2 Think envelope inlined: `workflow.py:183-187 _THINK_ACTIVITY_OPTIONS` hardcodes 10min/2min/1-attempt with comment "No THINK_* timeout constants exist in config.py yet". Violates config.py rule.
- V3 `_ThinkFirstHook` (`workflow.py:680-752`) hand-rolls `workflow.execute_activity` inside a Strands hook, with an injectable `executor` seam for tests. The integration ships `activity_as_hook(activity_fn, activity_input=..., **options)` for exactly this (SITE/temporalio/contrib/strands/workflow.py:61-103; README "Hooks"). NOTE: `activity_as_hook` discards the activity return value (line 101) — the hook needs the notes to mutate `event.messages[-1]`, so a pure `activity_as_hook` swap loses the notes fold-in. DECISION NEEDED (see Open questions Q2).
### Additional confirmed violations (re-assessed after user pushback; subagent verdicts overruled)
- V4 `think_activity.py` (394 lines) is a FORK of `strands_tools.think`, not the README-sanctioned thin `@activity.defn` wrapper around the installed tool ("if you're importing tools from strands_tools, wrap them in a thin async function"). `run_worker.py:208` excludes the real `think.py` from tools/ so the fork wins. Fix: thin wrapper around installed `strands_tools.think`; streaming via the nested Agent's `stream_async` published on THINKING_TOPIC is the ONLY permitted delta, expressed as a wrapper not a copy.
- V5 `perplexity_model.py:69-611` from-scratch `Model` with hand-rolled ordering machine (`flush_ready`). Perplexity Agent API is OpenAI-Responses-wire-compatible (docs.perplexity.ai openai-compatibility; SDK event names identical). Fix: `PerplexityModel(OpenAIResponsesModel)` — reuse the SDK parser; extend only for `preset`, `background`, `sequence_number`, `response.reasoning.search_*`/`fetch_url_*`, `share_file`/`sandbox_results` output items. UNVERIFIED: whether Perplexity actually interleaves output_index (would justify a minimal reorder shim). Locked by tests/test_perplexity_model.py (33 KB) — behavior must be preserved.
- V6 `gemini_model.py:252-343` 85-line copy of base `stream()` to read `grounding_metadata`. Additive overrides (video Blob, Computer Use FunctionResponse, tool filtering, reasoning_effort) are legitimate. Fix: shrink the override to the smallest interception the base allows; file/record upstream gap.
- V7 `workflow.py:113-115` `_load_tool_workflow_exec` accesses private `load_tool._tool_func`.
- V8 `run_worker.py:207-236` `ensure_strands_tools_dir` symlinks 64 strands_tools modules into `orchestrator/tools/` (gitignored) to satisfy `load_tool`'s cwd()/tools lookup — hand re-vendoring.
- V10 `app/api/compare/route.ts:52-208` hand-encoded SSE + bespoke envelope vs `createUIMessageStream`/`createUIMessageStreamResponse`. PROTECTED route: plan a failing compatibility test first (compare-view.tsx consumes `{model,type}` envelope).
### Confirmed NOT violations (defended)
- `gemini_model.py` additive overrides (video, Computer Use, tool filtering) — capability the 1.50.2 base lacks.
- `server.py` FastAPI bridge uses `WorkflowStreamClient.create(...).subscribe(...)` (server.py:371,405) — the sanctioned Workflow Streams primitive; the consumer-task + `start_update(wait_for_stage=ACCEPTED)` shape is required because ChatWorkflow stays Running between turns (server.py:387-399).
- `app/api/orchestrator/route.ts` uses `createUIMessageStream`/`createUIMessageStreamResponse`/`parseJsonEventStream` (ai 7.0.41 exports verified) — the AI SDK's intended bridge for a foreign SSE source.
- `app/api/compare/route.ts:54-208` hand-built `ReadableStream` + `TextEncoder` SSE — SUSPECTED redundancy vs `createUIMessageStream` + `createUIMessageStreamResponse`; but compare-view consumes a bespoke `{model,type}` envelope, not UI message chunks, and `app/api/compare/route.ts` is a PROTECTED route (change only on failing compatibility test). Recorded as out of scope.
- Frontend (components/v0, components/ai-elements, components/ui): 0 confirmed violations — native `Conversation`/`MessageResponse`/`PromptInput*`/`ChainOfThought*`, `useChat`+`DefaultChatTransport`, `@base-ui/react` (no `asChild`), no hardcoded catalog lists, `think` tool cards routed to ChainOfThought (agent-activity.tsx:594-646,1869-1870,2010).
### Stale documentation (AGENTS.md claims falsified)
- "think tool discontinued" — FALSE: `THINK_TOOL` built once (workflow.py:189), on tools (981), `_ThinkFirstHook` on hooks (988), activity registered (run_worker.py:524) and configured (543).
- "graph_activity.py planned/absent" — FALSE: present, wired (workflow.py:173).
- "StrandsPlugin on the Worker" (root) vs "on the Client" (orchestrator) — Client is correct (run_worker.py:506).
- "compare_workflow/run_worker/server have no tests" — FALSE: 21 suites incl. test_server.py, test_run_worker.py.
- "gemini-flash-latest default / Gemini-only" — FALSE: default is `preset:high`; 62 registered ids (6 presets + 53 Perplexity catalog + 3 Gemini) per worker-readiness.json.
- "2 expected failing route tests" — FALSE: 22/22 pass on main.
### Provider declaration gap (user vision)
- Provider is GUESSED client-side from id shape: `lib/perplexity.ts:48-54 ownerOf()` (`preset:`→perplexity, `<p>/`→p, `gemini*`→google). Readiness lease (`run_worker.py:337-346`) and `/health` (`server.py:293-310`) carry only id strings. Picker groups by `owned_by` (`model-picker.tsx:97-105`) so Gemini shows under "Google", but nothing DECLARES "Google AI Studio (direct)" vs "google/* via Perplexity gateway" — both collapse into the same "Google" group. That is the gap the user named.
- `lib/perplexity.ts:21-36` static fallback lists (PERPLEXITY_PRESETS, GEMINI_MODEL_IDS) mirror config.py — a hardcoded list the repo rule forbids ("pickers use /api/models").
### Run-failure diagnosis
- No on-disk failure artifacts; all gates green (pytest 333, vitest 22/22 + others, tsc 0, lint 0). Historical failure modes documented in code: payload/history overflow from stream Signals (config.py:107-160; workflow.py:1036-1045; learnings TMPRL1103), unknown-model 400s from `ownerOf`/id mismatch, activity timeout validation (workflow.py:117-123). The dirty config.py 200ms→2s change is the live mitigation, not the cause.
### Dirty worktree (risk, out of scope)
- Untracked: `fair-expanse-493212-h8-138622c839d2.json` (appears to be a GCP service-account key — must never be committed; not in .gitignore), `compute.json`, `scripts/gce-startup.sh`, `scripts/start-all.sh`, `.kilo/plans/*`, `components/v0/model-picker.test.tsx`. Modified: `components/ui/popover.tsx`, `components/v0/model-picker.tsx`, `orchestrator/config.py`, `orchestrator/tests/test_config.py`, `package.json`, `pnpm-lock.yaml`, `orchestrator/.runtime/worker-readiness.json`.

## Decisions (with rationale)
- D1 Keep both custom Model classes (see assumptions).
- D2 Consolidate `_closable` into `config.closable_activity_options`; delete workflow.py copy + private constant.
- D3 Add `THINK_START_TO_CLOSE`, `THINK_HEARTBEAT_TIMEOUT`, `THINK_RETRY_POLICY` to config.py; workflow.py imports them.
- D4 Provider declaration travels with the lease: readiness record gains `providers: {<id>: {"provider": "perplexity-agent-api"|"google-ai-studio", "label": ...}}` (or per-model objects); `/health` forwards; `lib/perplexity.ts` stops guessing and stops shipping static lists; picker groups by declared provider label.
- D5 No new provider code now; AGENTS.md documents the exact Strands class + extra per planned provider and the single registration point.

## Scope IN
- C1–C5 above.

## Scope OUT (Must NOT have)
- No rewrite/removal of `perplexity_model.py` or `gemini_model.py`.
- No changes to the six protected `app/api/**/route.ts` files (incl. compare) without a failing compatibility test.
- No `orchestrator/graph_tool.py` creation.
- No new providers implemented (OpenAI/Anthropic/Mistral/xAI) — roadmap only.
- No revert of the 2s nested batch interval.
- No commits of credential files; no reading `.env*`.
- No MVP/phase reduction.

## Open questions (ANSWERED 2026-09-08)
- Q1 Provider declaration → lease carries {id, provider, label} per model; /health forwards; lib/perplexity.ts drops guessing + static lists.
- Q2 `_ThinkFirstHook` → restructure onto `activity_as_hook`: think activity writes notes via workflow-visible path (publish on THINKING_TOPIC / workflow signal), hook becomes pure `activity_as_hook`.
- Q3 Picker → keep `actionsRef` close + adopt untracked model-picker.test.tsx.

## Scope IN (revised)
- C1 docs reset; C2 orchestrator adherence now covers V1–V8; C3 provider declaration (V9); C4 provider roadmap docs; C5 picker; C6 .gitignore guard; C7 compare route V10 behind failing compatibility test.

## Approval gate
status: approved
approved-at: 2026-09-08 (user: "approved")
plan-written: .omo/plans/framework-adherence-reset.md (22 impl todos, 4 final; structural self-check passed)
metis: performed first-hand (no subagent dispatch per user model directive); gaps fixed inline — todo 5 native-frame recovery path, todo 10 catalog transport, todo 16 provider heading source.
revision-2 (user: no caveats): every executor fork resolved by first-hand source reads — todo 5 native tap = httpx event_hooks on the openai client (parent loop verified unhookable, openai_responses.py:321,334-449); todo 6 = _get_client proxy + _format_request_config override (gemini.py:126,291,540); todo 4 = notes frame on THINKING_TOPIC + BeforeModelCallEvent reader; todo 17 = compare route/workflow DELETED (verified dead: compare-view uses AgentChat → /api/orchestrator); todo 14 = MODEL_STREAM_BATCH_INTERVAL constant; popover actionsRef passthrough verified (popover.tsx:7-8). Hedge-word sweep: 0 remaining.
pending-action: none — hand off to `$start-work framework-adherence-reset`
