# framework-adherence-reset - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** Every hand-written reimplementation of something Temporal, Strands, or the AI SDK already provides is deleted and replaced by the framework's own primitive. The model picker declares each model's real provider (Perplexity gateway vs. Google AI Studio direct) instead of guessing from the id. Both contributor guides are rewritten from verified state, including your model allowlist. Two new adherence test suites fail the build if custom code ever creeps back.

**Why this approach:** The frameworks already parse Perplexity's OpenAI-compatible stream, already run hooks as activities, already ship the `think` tool, already know when a worker is alive, and already turn a stream into an SSE response. Every custom copy of those was a place for bugs to hide; inheriting instead of copying removes the copies and leaves only the genuine extensions (Perplexity presets/native tools, Gemini video/computer-use/grounding).

**What it will NOT do:** Add any new provider (OpenAI, Anthropic, Mistral, xAI stay roadmap-only). Touch the credential file sitting untracked in the repo (it only gets ignored). Revert the coarser streaming batch interval — that setting is what keeps long runs under Temporal's history limit.

**Effort:** Large
**Risk:** Medium - the Perplexity model rebase must keep the existing behavior tests green while the parser underneath is replaced by the framework's.
**Decisions to sanity-check:** the four kinds of custom code that remain (workflow definitions, activity wrappers, provider subclasses, hook providers) are each an extension point the framework documents; anything beyond that is forbidden by test. The compare comparison feature keeps working because its page never used the route being deleted.

Your next move: run `$start-work framework-adherence-reset`, or ask for a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Large / Medium risk. 22 impl todos + 4 final. Deletes 10 confirmed framework bypasses (V1-V10) plus the readiness lease, server pump, and dead compare pipeline; worker declares provider per model; regenerates both AGENTS.md; adds two adherence test gates. No open decisions remain for the executor.

## Scope
### Must have
- V1 `_closable` duplicate collapsed into `config.closable_activity_options`.
- V2 Think activity envelope in `config.py` (`THINK_START_TO_CLOSE`, `THINK_HEARTBEAT_TIMEOUT`, `THINK_RETRY_POLICY`).
- V3 `_ThinkFirstHook` re-expressed on `temporalio.contrib.strands.workflow.activity_as_hook`; notes reach the model through workflow state, not a returned value.
- V4 `think_activity.py` reduced to a thin `@activity.defn(name="think")` wrapper around installed `strands_tools.think`; the 394-line fork deleted.
- V5 `PerplexityModel` rebased onto `strands.models.openai_responses.OpenAIResponsesModel`; only Perplexity extensions remain (`preset`, `background`, `response.reasoning.*` / `skill.loaded` events, `share_file`/`sandbox_results` output items) emitted as the existing `{"perplexity": ...}` frame.
- V6 `gemini_model.py` `stream` override reduced to a wrapper around `super().stream()` that appends `{"gemini": ...}` grounding frames; additive overrides kept.
- V7 `_load_tool_workflow_exec` private-attribute access removed; `load_tool` wired via `activity_as_tool`.
- V8 `ensure_strands_tools_dir` symlink farm + `_SKIP_COMMUNITY_FILES` removed; `orchestrator/tools/` directory and its `.gitignore` lines removed; `strands_tools` consumed as the installed package.
- V9 Provider declared per model id by the worker (`{id, provider, label}`), forwarded by `/health`, consumed by `lib/perplexity.ts` and the picker; static lists and `ownerOf` deleted.
- V10 Dead compare pipeline deleted: `app/api/compare/route.ts` (hand-encoded SSE nothing calls), `orchestrator/compare_workflow.py`, `/compare/stream`. `compare-view.tsx` already runs one `AgentChat` per pane through `/api/orchestrator`.
- Outer `streaming_batch_interval` literal moved to `config.MODEL_STREAM_BATCH_INTERVAL`.
- Readiness: custom JSON lease replaced by Temporal `DescribeTaskQueue` pollers as the liveness source; model catalog + provider metadata served by the worker through a Temporal-native path (see todo 15).
- `server.py` turn pump: `WorkflowStreamClient.subscribe` iterated directly with the update handle as the turn boundary; the hand-rolled `asyncio.Queue` + drain sleep removed (todo 16).
- `orchestrator/tests/test_framework_adherence.py` and `app/framework-adherence.test.ts` gates.
- Root `AGENTS.md` + `orchestrator/AGENTS.md` regenerated from verified state, including the model allowlist directive.
- `.gitignore` guard for `*.json` GCP service-account key pattern and `compute.json`.
- Untracked `components/v0/model-picker.test.tsx` adopted; dirty `model-picker.tsx` `actionsRef` change kept.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No new provider implementations (OpenAI, Anthropic, Mistral, xAI) — documentation only.
- No revert of `THINK_STREAM_BATCH_INTERVAL` / `AGENT_RUNS_STREAM_BATCH_INTERVAL = 2s`.
- No creation of `orchestrator/graph_tool.py`.
- No reading, editing, committing, or moving `.env*` or `fair-expanse-*.json`.
- No new `class X(strands.models.Model)` direct subclasses; providers subclass a concrete Strands provider.
- No `workflow.execute_activity` calls inside any `HookProvider`; hooks use `activity_as_hook`.
- No `timedelta(` literals outside `config.py` in orchestrator source (tests excepted).
- No `new ReadableStream(`, `TextEncoder`, `.getReader(` in `app/**` or `components/v0/**`.
- No string-literal model ids in `lib/**`, `app/**`, `components/**` outside tests.
- No wrapper functions that only forward arguments; no `try/except Exception: pass`.
- No "MVP" / phased reduction; every Must-have ships.
- No subagent dispatch by the worker to models outside the user's allowlist (Fable 5.1, GPT 6, DeepSeek V4 Flash/Pro, GLM 5.3/5.3 Flash, Gemini 3.8 Flash, Grok 4.6, Kimi K3).

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD (failing test first, every todo) + pytest 9.1.1 (from `orchestrator/`) and vitest 4.1.10 (scoped).
- Gates (every todo): `cd orchestrator && .venv/bin/python -m pytest tests -q` (currently 333 passed), `pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'`, `npx tsc --noEmit` (0), `pnpm lint` (0).
- Evidence: `.omo/evidence/framework-adherence-reset/task-<N>.<ext>` (outside ulw-loop) — command transcripts saved verbatim.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave.
- Wave 1 (foundations, parallel): 1, 2, 3, 20, 21, 22
- Wave 2 (orchestrator rewrites, parallel): 4, 5, 6, 7, 8, 9
- Wave 3 (readiness + server + provider surface): 10, 11, 12, 13, 14
- Wave 4 (frontend): 15, 16, 17, 18, 19
- Final: F1-F4

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | - | 4,5,6 | 2,3,20,21,22 |
| 2 | - | 5,6 | 1,3,20,21,22 |
| 3 | - | 4 | 1,2,20,21,22 |
| 4 | 1,3 | 6 | 5,7,8,9 |
| 5 | 1,2 | - | 4,7,8,9 |
| 6 | 1,2,4 | - | 5,7,8,9 |
| 7 | - | - | 4,5,6,8,9 |
| 8 | - | - | 4,5,6,7,9 |
| 9 | 8 | 10 | 4,5,6,7 |
| 10 | 9 | 11,12,15 | 13,14 |
| 11 | 10 | 15 | 12,13,14 |
| 12 | - | - | 10,11,13,14 |
| 13 | - | - | 10,11,12,14 |
| 14 | - | - | 10,11,12,13 |
| 15 | 10,11 | 16,17 | 18,19 |
| 16 | 15 | - | 17,18,19 |
| 17 | 15 | - | 16,18,19 |
| 18 | - | - | 15,16,17,19 |
| 19 | - | - | 15,16,17,18 |
| 20 | - | 4-19 | 1,2,3,21,22 |
| 21 | - | 15-19 | 1,2,3,20,22 |
| 22 | - | - | 1,2,3,20,21 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. orchestrator/config.py + workflow.py: collapse the duplicate `_closable` helper into `closable_activity_options` (V1)
  What to do / Must NOT do: Delete `workflow.py:117-136` (`_UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE`, `_closable`). Import `closable_activity_options` from `config` and replace the 5 `_closable(` call sites (`workflow.py:139,197,920,941` and any grep hit). Do NOT change the fallback value (1 day). Do NOT touch the docstring semantics of `config.py:87-106`.
  Parallelization: Wave 1 | Blocked by: - | Blocks: 4,5,6
  References: `orchestrator/config.py:87-106`; `orchestrator/workflow.py:117-146,197-203,916-946`; `orchestrator/tests/test_config.py` (dirty, has 31 new lines — read first); `orchestrator/tests/test_workflow.py`.
  Acceptance criteria: `grep -n "_closable\|_UNCAPPED_FALLBACK" orchestrator/workflow.py` returns 0 lines; `grep -c "closable_activity_options" orchestrator/workflow.py` >= 5; `cd orchestrator && .venv/bin/python -m pytest tests -q` all pass.
  QA scenarios: happy — new test `test_workflow_uses_config_closable` asserts `workflow._MCP_ACTIVITY_OPTIONS["schedule_to_close_timeout"] == config.UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE`; failure — temporarily set `MODEL_START_TO_CLOSE = None` and `MODEL_SCHEDULE_TO_CLOSE = None`, assert fallback applied (this is the default today). Evidence `.omo/evidence/framework-adherence-reset/task-1.txt`.
  Commit: Y | Collapse duplicate closable helper into config

- [ ] 2. orchestrator/config.py: add THINK_* envelope constants (V2)
  What to do / Must NOT do: Add `THINK_START_TO_CLOSE = timedelta(minutes=10)`, `THINK_HEARTBEAT_TIMEOUT = timedelta(minutes=2)`, `THINK_RETRY_POLICY = RetryPolicy(maximum_attempts=1)` next to `THINK_STREAM_BATCH_INTERVAL` (`config.py:116`). Preserve the existing values from `workflow.py:183-187` exactly. Do NOT change `THINK_STREAM_BATCH_INTERVAL`.
  Parallelization: Wave 1 | Blocked by: - | Blocks: 5,6
  References: `orchestrator/config.py:107-116`; `orchestrator/workflow.py:178-189`; `orchestrator/tests/test_config.py`.
  Acceptance criteria: `test_config.py::test_think_envelope` asserts the three constants and their values; pytest green.
  QA scenarios: happy — constants importable and typed `timedelta`/`RetryPolicy`; failure — `RetryPolicy.maximum_attempts != 1` fails the test. Evidence `.omo/evidence/framework-adherence-reset/task-2.txt`.
  Commit: Y | Move think activity envelope into config

- [x] 3. orchestrator/think_activity.py: replace the fork with a thin wrapper around installed strands_tools.think (V4)
  What to do / Must NOT do: Delete `ThoughtProcessor` copy, `DEFAULT_THINK_SYSTEM_PROMPT`, `DEFAULT_THINKING_SYSTEM_PROMPT`, `configure()` factory registry, and every helper the fork carried. New module: `@activity.defn(name="think") async def think(thought: str, cycle_count: int, system_prompt: str, thinking_system_prompt: str | None = None) -> dict` that (a) resolves the session model by `activity.client().get_workflow_handle(activity.info().workflow_id).query("model_id")` and the worker's `StrandsPlugin` model factories (pass the mapping in via a module-level `set_model_factories()` called once from `run_worker.py` — this is the ONE permitted registry because `strands_tools.think`'s `model_provider` cannot name a Temporal factory), (b) builds a plain `strands.Agent(model=factory(), system_prompt=system_prompt, tools=[])`, (c) publishes each `stream_async` chunk on `THINKING_TOPIC` via `WorkflowStreamClient.from_within_activity(batch_interval=THINK_STREAM_BATCH_INTERVAL)` with `activity.heartbeat()` per chunk, and (d) delegates prompt construction to `strands_tools.think.create_thinking_prompt` / the installed module's public functions — do NOT copy them. Return shape `{"status": "success"|"error", "content": [{"text": ...}]}` unchanged. The two persona/methodology prompts move to `agent.json` under a `think` key (identity lives there) and are passed in as `system_prompt`/`thinking_system_prompt` by the caller.
  Parallelization: Wave 1 | Blocked by: - | Blocks: 4
  References: `orchestrator/think_activity.py:1-394`; `orchestrator/.venv/lib/python3.13/site-packages/strands_tools/think.py:186-397`; `orchestrator/.venv/lib/python3.13/site-packages/temporalio/contrib/strands/README.md` "Tools" section; `orchestrator/.venv/lib/python3.13/site-packages/temporalio/contrib/workflow_streams/_client.py` (`from_within_activity`); `orchestrator/tests/test_think_activity.py` (5 tests, `ActivityEnvironment`); `orchestrator/run_worker.py:44,524,543`; `orchestrator/agent.json`.
  Acceptance criteria: `wc -l orchestrator/think_activity.py` < 120; `grep -n "from strands_tools.think import\|import strands_tools.think" orchestrator/think_activity.py` >= 1; `grep -n "class ThoughtProcessor\|DEFAULT_THINK_SYSTEM_PROMPT" orchestrator/think_activity.py` = 0; `tests/test_think_activity.py` green (adapt fixtures to call `set_model_factories`).
  QA scenarios: happy — `ActivityEnvironment` run with a scripted fake model yields >= 1 frame on `THINKING_TOPIC` and `status == "success"`; failure — factory raises → `status == "error"` with message, no exception escapes. Evidence `.omo/evidence/framework-adherence-reset/task-3.txt`.
  Commit: Y | Wrap installed strands_tools.think as a thin streaming activity

- [ ] 4. orchestrator/workflow.py: re-express _ThinkFirstHook on activity_as_hook (V3)
  What to do / Must NOT do: Delete `_ThinkFirstHook.__init__(executor=...)`, `_think_first`, `mark_turn_start`, and the direct `workflow.execute_activity` call (`workflow.py:702-752`). New design: `_ThinkFirstHook(system_prompt, stream: WorkflowStream)`; `register_hooks` adds two callbacks. (1) `BeforeInvocationEvent` → `activity_as_hook(think_activity.think, activity_input=self._input, start_to_close_timeout=THINK_START_TO_CLOSE, heartbeat_timeout=THINK_HEARTBEAT_TIMEOUT, retry_policy=THINK_RETRY_POLICY)`; `_input(event)` returns `ThinkInput(thought=<text of event.messages[-1]>, cycle_count=1, system_prompt=self._system_prompt)` (frozen dataclass in `think_activity.py`). Because `activity_as_hook` discards the return value (`temporalio/contrib/strands/workflow.py:101`), the think activity publishes its final notes as one frame `{"think_notes": <str>}` on `THINKING_TOPIC` (same `WorkflowStreamClient.from_within_activity` it already uses) before returning. (2) `BeforeModelCallEvent` deterministic callback: read `self._stream.get_state()`; take the LAST frame on `THINKING_TOPIC` with key `think_notes` whose offset is >= this turn's `turn_start_offset`; if present and not yet applied this turn, append `{"text": "\n\n<think_notes>\n" + notes + "\n</think_notes>"}` to the last user message in `event.messages`; mark applied. Once-per-turn guard: `self._applied_offset: int | None`, reset by `turn()` where `mark_turn_start()` was called (`:1051`). Skip when `messages[-1]` is not a text user message (interruptResponse resumes). Failure: `activity_as_hook` raises `ActivityError` on a failed think → catch in a thin `HookProvider` wrapper is NOT allowed (hook must stay `activity_as_hook`); instead `THINK_RETRY_POLICY(maximum_attempts=1)` plus the activity itself returning `status: "error"` (todo 3) means the activity never raises for model errors — only infra errors propagate, and those fail the turn like any other activity (`test_failed_model_activity_surfaces_to_the_caller` pattern). Zero `execute_activity` calls remain in any hook.
  Parallelization: Wave 2 | Blocked by: 1,3 | Blocks: 6
  References: `orchestrator/workflow.py:645-752,891,940,988,1049-1052`; `orchestrator/.venv/lib/python3.13/site-packages/temporalio/contrib/strands/workflow.py:61-103`; README "Hooks" section; `orchestrator/tests/test_think_hook.py` (rewrite: drive `HookRegistry` with a fake `activity_as_hook` via `unittest.mock.patch`); `strands/hooks/events.py` (`BeforeInvocationEvent`, `BeforeModelCallEvent`).
  Acceptance criteria: `grep -n "execute_activity" orchestrator/workflow.py` returns only non-hook sites (or 0); `grep -c "activity_as_hook" orchestrator/workflow.py` >= 1; `tests/test_think_hook.py` + `tests/test_workflow.py` green (needs `temporal` CLI on PATH).
  QA scenarios: happy — `test_workflow.py` turn with a scripted think activity that publishes `{"think_notes": "N"}` shows `<think_notes>\nN\n</think_notes>` appended to the model's first user message and NOT re-appended on the same turn's second model call; failure — think activity returns `status: "error"` and publishes no notes → model receives the bare prompt, turn completes normally. Evidence `.omo/evidence/framework-adherence-reset/task-4.txt`.
  Commit: Y | Run think-first hook through activity_as_hook

- [ ] 5. orchestrator/perplexity_model.py: rebase PerplexityModel onto OpenAIResponsesModel (V5)
  What to do / Must NOT do: `class PerplexityModel(OpenAIResponsesModel)`. Delete `_message_items`, `_message_item`, `_image_part`, custom `_format_request`, `flush_ready`, `_raise_sdk_error`, `_raise_failed`, `_value`, `_application_error`, `_native_event`, and the 280-line `stream` body. Keep: `_preset_name`, `_ensure_object_properties`, `_validate_config`.
  Overrides (exactly three):
  (1) `_resolve_client_args()` → `{"base_url": f"{PERPLEXITY_API_BASE}/v1", "api_key": <from factory>, "max_retries": 0}`.
  (2) `_format_request(...)`: `request = super()._format_request(...)`; if `_preset_name(model_id)` → `request["preset"] = name; del request["model"]`; run `_ensure_object_properties` over `request["tools"]` function entries. `background`/`store`/`max_steps`/`skills`/native `tools` already arrive through `params` (parent merges `**params` at `openai_responses.py:562`). Note parent sets `"store": self.stateful` AFTER params — construct with `stateful=True` so `store=True` is preserved.
  (3) Native-event tap at the HTTP layer — VERIFIED against `openai_responses.py:321,334-449`: the parent builds its own `openai.AsyncOpenAI(**self._resolve_client_args())`, its loop never yields or stores the `response.completed` object (`:445-449` reads only `usage`), and it silently skips every event type it does not name. Therefore the ONLY place Perplexity-only events are observable without copying the parser is the HTTP response body. Implementation, no alternatives: `_resolve_client_args()` returns `{"base_url": f"{PERPLEXITY_API_BASE}/v1", "api_key": ..., "max_retries": 0, "http_client": httpx.AsyncClient(event_hooks={"response": [self._tap_sse]})}` — `http_client` is a documented `openai.AsyncOpenAI` constructor argument; `event_hooks` is documented `httpx` API. `_tap_sse(response)`: if `response.headers.get("content-type","").startswith("text/event-stream")`, replace `response.aiter_bytes`/`aiter_lines` with a wrapper that forwards every byte unchanged to the SDK AND parses each `data:` line with `json.loads`; when `type` starts with `response.reasoning.` (Perplexity `search_queries`/`search_results`/`fetch_url_queries`/`fetch_url_results`), equals `response.skill.loaded`, or is `response.output_item.done` with `item.type` in `NATIVE_OUTPUT_ITEM_TYPES = ("search_results","fetch_url_results","sandbox_results","sandbox_write_file","share_file","mcp_call","skill_loaded","advisor_result")`, append the parsed dict to `self._native_queue: asyncio.Queue`. `stream(...)`: `agen = super().stream(...)`; `async for chunk in agen: drain self._native_queue → yield {"perplexity": item}; yield chunk`; after the parent finishes, drain once more. Mid-stream ordering is preserved (native frames are yielded at the next parent chunk boundary), so `route.ts:745` and its tests are unchanged. `NATIVE_OUTPUT_ITEM_TYPES` and `PERPLEXITY_API_BASE` live in `config.py`. `perplexity_model.py` imports `httpx` (already pinned 0.28.1) and `openai` (already installed via the `strands-agents[openai]` requirement — add the extra to `requirements.txt` line 1: `strands-agents[gemini,openai]==1.50.2`).
  `openai.AsyncOpenAI` replaces `perplexity.AsyncPerplexity` in this module (Perplexity is OpenAI-compatible: docs.perplexity.ai/docs/agent-api/openai-compatibility). `perplexityai` stays in requirements for `perplexity_operations.py` (files/retrieve/cancel). `route.ts` is not touched.
  Parallelization: Wave 2 | Blocked by: 1,2 | Blocks: -
  References: `orchestrator/perplexity_model.py:1-611`; `orchestrator/.venv/lib/python3.13/site-packages/strands/models/openai_responses.py:137-202,288-449,533-593,817+`; `orchestrator/.venv/lib/python3.13/site-packages/perplexity/types/response_stream_chunk.py`; `orchestrator/.venv/lib/python3.13/site-packages/perplexity/types/output_item.py`; `orchestrator/run_worker.py:102-179` (factory + `MODEL_PARAMS`); `orchestrator/tests/test_perplexity_model.py` (33 KB — every test must stay green or be rewritten to the same behavior with the new client seam); `app/api/orchestrator/route.ts:163-200,733-776`; `orchestrator/agent_api_tools.py`.
  Acceptance criteria: `cd orchestrator && .venv/bin/python -c "from perplexity_model import PerplexityModel; from strands.models.openai_responses import OpenAIResponsesModel; assert issubclass(PerplexityModel, OpenAIResponsesModel)"`; `wc -l orchestrator/perplexity_model.py` < 200; `grep -n "flush_ready\|_message_items\|AsyncPerplexity\|_format_chunk({" orchestrator/perplexity_model.py` = 0; `tests/test_perplexity_model.py` green (fixtures rewritten to serve a canned SSE body through `httpx.MockTransport` — the tap and the parent both read that one body); `tests/test_run_worker.py` green.
  QA scenarios: happy — canned SSE body containing `response.created`, `output_text.delta` ×3, `output_item.added(function_call)`, `function_call_arguments.done`, `response.reasoning.search_results`, `output_item.done(share_file)`, `response.completed` yields: messageStart, text block, toolUse block, exactly two `{"perplexity": ...}` frames positioned before messageStop, messageStop, metadata; failure — body with `response.failed` raises through the parent's `classify_openai_error` mapping (`ModelThrottledException` for throttling, `ContextWindowOverflowException` for context, else re-raise) — no bare `Exception`. Evidence `.omo/evidence/framework-adherence-reset/task-5.txt`.
  Commit: Y | Rebase PerplexityModel onto OpenAIResponsesModel

- [ ] 6. orchestrator/gemini_model.py: shrink the stream override to a wrapper (V6)
  What to do / Must NOT do: Replace `stream()` body (`gemini_model.py:252-343`). VERIFIED: base `stream` (`gemini.py:516-605`) reads `self.config.get("params")` at `:540` and obtains the client via `self._get_client()` (`:126`). Implementation: (a) `reasoning_effort` → override `_format_request_config(...)` (`gemini.py:291`) — it receives the params dict; merge `{"thinking_config": {**existing, "thinking_level": effort}}` there when `self._reasoning_effort` is set; `stream()` sets `self._reasoning_effort = (kwargs.get("invocation_state") or {}).get("reasoning_effort")` before delegating and clears it in `finally`. (b) grounding → override `_get_client()` to return `_GroundingTapClient(super()._get_client(), sink=self._grounding)` — a proxy whose `.aio.models.generate_content_stream(**kw)` awaits the real call and returns an async generator that calls `maps_from_grounding(event)` / `search_from_grounding(event)` on each raw event, stores the latest non-None result on `sink`, then yields the event unchanged. (c) `stream()` body: set effort; `self._grounding = {}`; `async for chunk in super().stream(...): yield chunk`; then `if s := self._grounding.get("search"): yield {"gemini": s}`; same for `"maps"`; `finally` clear effort. Keep `_format_request_content`, `_computer_use_function_response`, `_format_request_content_part` (video + CU), `_format_request_tools`. Zero lines of the parent loop are copied.
  Parallelization: Wave 2 | Blocked by: 1,2,4 | Blocks: -
  References: `orchestrator/gemini_model.py:1-346`; `orchestrator/.venv/lib/python3.13/site-packages/strands/models/gemini.py:126-140,291-340,516-605`; `orchestrator/tests/test_gemini_multimodal.py`; `orchestrator/tests/test_computer_use_activity.py`; `app/api/orchestrator/route.ts` Gemini grounding tests ("POST Gemini Google Maps grounding").
  Acceptance criteria: `grep -c "_format_chunk({\"chunk_type\"" orchestrator/gemini_model.py` = 0; `grep -c "super().stream(" orchestrator/gemini_model.py` = 1; `tests/test_gemini_multimodal.py` green; route grounding tests green.
  QA scenarios: happy — fake `genai` stream with a grounding_metadata chunk + text yields parent frames unchanged plus one `{"gemini": {"type": "google_search", ...}}`; failure — `genai.errors.ClientError(RESOURCE_EXHAUSTED)` surfaces as `ModelThrottledException` via the parent (no local `match` block). Evidence `.omo/evidence/framework-adherence-reset/task-6.txt`.
  Commit: Y | Reduce GeminiModel stream override to a grounding wrapper

- [ ] 7. orchestrator/workflow.py: remove private load_tool access (V7)
  What to do / Must NOT do: Delete `_load_tool_workflow_exec` (`workflow.py:111-115`). Wrap `load_tool` as `@activity.defn(name="load_tool") async def load_tool_activity(path: str, name: str) -> dict` in `load_tool.py` (existing module — extend, don't duplicate) calling the PUBLIC `strands_tools.load_tool.load_tool(path=..., name=...)`; register it in `run_worker.py` activities and add `activity_as_tool(load_tool_activity, **_MCP_ACTIVITY_OPTIONS)` to `PERMANENT_COMMUNITY_TOOLS` in place of bare `load_tool`. Remove `tool_file_path` if it only served the private call.
  Parallelization: Wave 2 | Blocked by: - | Blocks: -
  References: `orchestrator/workflow.py:92-115,166-176`; `orchestrator/load_tool.py`; `orchestrator/.venv/lib/python3.13/site-packages/strands_tools/load_tool.py`; `orchestrator/tests/test_hot_load.py`; `orchestrator/run_worker.py:516-530`.
  Acceptance criteria: `grep -n "_tool_func" orchestrator/` = 0; `tests/test_hot_load.py` green.
  QA scenarios: happy — hot-load `calculator` via the activity tool in `test_hot_load.py` and invoke it; failure — nonexistent path → `ApplicationError` with the strands_tools message. Evidence `.omo/evidence/framework-adherence-reset/task-7.txt`.
  Commit: Y | Load community tools through the public load_tool activity

- [ ] 8. orchestrator/run_worker.py: delete the strands_tools symlink farm (V8)
  What to do / Must NOT do: Delete `_SKIP_COMMUNITY_FILES` and `ensure_strands_tools_dir` (`run_worker.py:207-236`) and its call site. Delete `orchestrator/tools/` (all 64 symlinks). Remove `.gitignore:19-21` (`orchestrator/strands-tools`, `orchestrator/tools/*.py`). `load_tool` path resolution: inside `load_tool_activity` (todo 7) resolve a bare `name` to `Path(strands_tools.__file__).parent / f"{name}.py"` and pass that absolute path to `strands_tools.load_tool.load_tool` — no on-disk copy. `agent.json` line 3 references `load_tool`: rewrite that sentence to name tools by bare module name (e.g. `load_tool(name="calculator")`), removing any `tools/<name>.py` path text.
  Parallelization: Wave 2 | Blocked by: - | Blocks: 9
  References: `orchestrator/run_worker.py:207-236,496-560`; `.gitignore:17-21`; `orchestrator/tests/test_run_worker.py`; `orchestrator/tests/test_hot_load.py`; `orchestrator/AGENTS.md:32-38` (skills_loader references `load_tool(..., name="list_skills"|"skill")` — ensure paths resolve to the installed `strands_agentskills`/skills dir, not `tools/`).
  Acceptance criteria: `test -d orchestrator/tools` fails; `grep -n "ensure_strands_tools_dir\|_SKIP_COMMUNITY_FILES" orchestrator/` = 0; pytest green.
  QA scenarios: happy — worker boot in `test_run_worker.py` without `tools/`; failure — `load_tool_activity("does/not/exist.py")` → ApplicationError. Evidence `.omo/evidence/framework-adherence-reset/task-8.txt`.
  Commit: Y | Consume strands_tools as an installed package

- [ ] 9. orchestrator/run_worker.py: provider-declaring model catalog (V9 backend)
  What to do / Must NOT do: Introduce `@dataclass(frozen=True) class RegisteredModel: id: str; provider: str; label: str` in `config.py` with provider constants `PROVIDER_PERPLEXITY_AGENT_API = "perplexity-agent-api"`, `PROVIDER_GOOGLE_AI_STUDIO = "google-ai-studio"`. `assemble_model_factories()` returns `(factories, catalog: list[RegisteredModel], default_model)`; `build_perplexity_factories` tags presets and catalog ids `perplexity-agent-api` with label = id (presets label `"<Name> (preset)"`); `build_model_factory` tags Gemini ids `google-ai-studio`. Do NOT rename any model id.
  Parallelization: Wave 2 | Blocked by: 8 | Blocks: 10
  References: `orchestrator/run_worker.py:151-179,239-322`; `orchestrator/config.py:17,69`; `orchestrator/tests/test_run_worker.py`.
  Acceptance criteria: `test_run_worker.py::test_catalog_declares_provider` asserts every registered id has a provider in the two constants and the three Gemini ids are `google-ai-studio`.
  QA scenarios: happy — both keys present → 6+N+3 entries with correct providers; failure — no keys → `SystemExit` unchanged. Evidence `.omo/evidence/framework-adherence-reset/task-9.txt`.
  Commit: Y | Declare a provider for every registered model

- [ ] 10. Readiness: replace the custom JSON lease with Temporal-native liveness + a catalog query
  What to do / Must NOT do: Delete `write_readiness`, `refresh_readiness_lease`, `clear_readiness`, `READINESS_PATH`, `READINESS_HEARTBEAT_INTERVAL`, `READINESS_LEASE_TTL`, `validate_readiness_record`, `readiness()` (`run_worker.py:325-394`, `server.py:120-170`), `tests/test_readiness_lease.py`, and `orchestrator/.runtime/`. Liveness = `task_queue_has_pollers()` only (Temporal `DescribeTaskQueue`, `server.py:175-209`). Catalog: add `@workflow.defn class ModelCatalogWorkflow` with `@workflow.run async def run(self) -> list[RegisteredModel]: return await workflow.execute_activity(model_catalog, start_to_close_timeout=CATALOG_ACTIVITY_TIMEOUT)` and `@activity.defn(name="model_catalog") async def model_catalog() -> list[RegisteredModel]` returning the todo-9 catalog captured at worker start. Register both on the worker. `server.py` calls `client.execute_workflow(ModelCatalogWorkflow.run, id=f"model-catalog-{uuid4()}", task_queue=TASK_QUEUE)` and caches the result for `CATALOG_CACHE_TTL` (config.py). A `@workflow.defn` + `@activity.defn` pair is the framework's own unit of work — this is not custom infrastructure. `/health` returns `{"status", "temporal", "worker": pollers_ok, "pollers", "models": [{"id","provider","label"}], "providers": {"perplexity-agent-api": "Perplexity Agent API", "google-ai-studio": "Google AI Studio"}, "default_model"}` — `model_ids` and the count field removed. Provider display names live in `config.py` next to the provider constants (server-side, single source). `/sessions` and `/turns/stream` validate `model_id` against the cached catalog ids; on cache miss they refresh once.
  Parallelization: Wave 3 | Blocked by: 9 | Blocks: 11,12,15
  References: `orchestrator/run_worker.py:325-394,553,565`; `orchestrator/server.py:68,120-215,284-367`; `orchestrator/config.py` readiness constants (~:174-194); `orchestrator/tests/test_server.py`; `orchestrator/tests/test_readiness_lease.py` (delete); `scripts/worker_supervisor.py` (check for readiness-file coupling); `package.json` `dev:web` waits on `/health` — keep 200 semantics.
  Acceptance criteria: `grep -rn "READINESS_PATH\|worker-readiness" orchestrator/ scripts/ app/ lib/` = 0; `test_server.py::test_health_lists_provider_catalog` asserts each model object has `provider`; pytest green.
  QA scenarios: happy — `/health` with pollers → `status: ok` + catalog; failure — no pollers → `status: degraded`, `/sessions` → 503. Evidence `.omo/evidence/framework-adherence-reset/task-10.txt`.
  Commit: Y | Source worker liveness and catalog from Temporal

- [ ] 11. orchestrator/server.py: simplify the turn stream pump
  What to do / Must NOT do: Replace `body_iter` (`server.py:387-470`) with: start the update (`wait_for_stage=ACCEPTED`) FIRST is wrong (README/comment: subscribe before accept or lose early frames) — keep order: create `subscribe(CHAT_TOPICS, from_offset=start_offset)` async iterator, start the update, then `async for item in subscription:` yield `sse(...)`; stop when `update_handle.result()` completes — implement with `asyncio.wait({anext_task, result_task}, FIRST_COMPLETED)` on the ITERATOR directly (no intermediate `asyncio.Queue`, no `consume()` task, no `asyncio.sleep(0.2)` drain). After result: yield `{"done", "reply"}` or `{"error"}`; `finally: await subscription.aclose()`. Document in a one-line comment the SDK fact that makes the boundary necessary (`workflow_streams/_client.py` subscribe returns only on workflow close).
  Parallelization: Wave 3 | Blocked by: 10 | Blocks: 15
  References: `orchestrator/server.py:344-475`; `orchestrator/.venv/lib/python3.13/site-packages/temporalio/contrib/workflow_streams/_client.py:469-494`; `orchestrator/tests/test_server.py`.
  Acceptance criteria: `grep -n "asyncio.Queue\|asyncio.sleep(0.2)\|def consume" orchestrator/server.py` = 0; `test_server.py` turn-stream tests green (frames before `done`, no lost first frame).
  QA scenarios: happy — scripted workflow emits 3 frames then returns → client sees 3 frames + done; failure — update raises → `{"error"}` frame, subscription closed, workflow NOT cancelled. Evidence `.omo/evidence/framework-adherence-reset/task-11.txt`.
  Commit: Y | Iterate the workflow stream directly in the turn endpoint

- [ ] 12. orchestrator/tests/test_framework_adherence.py: static adherence gate
  What to do / Must NOT do: New pytest module asserting, via `ast` over every `orchestrator/*.py` (excluding `tests/`, `.venv/`): (a) no `ClassDef` whose bases include bare `Model` imported from `strands.models`/`strands.models.model`; (b) `PerplexityModel` MRO includes `OpenAIResponsesModel`; (c) `GeminiModel` source has no `_format_chunk({"chunk_type"` literal; (d) `think_activity` imports `strands_tools.think`; (e) no `Call` to `execute_activity` inside any class whose bases include `HookProvider`; (f) no `timedelta(` call outside `config.py`; (g) no attribute access `._tool_func`; (h) no `ExceptHandler` with body `[Pass]`. Failures print file:line.
  Parallelization: Wave 3 | Blocked by: - | Blocks: -
  References: all todos 1-11; `orchestrator/AGENTS.md` (add gate to DoD in todo 20).
  Acceptance criteria: gate passes after waves 1-3; deliberately re-adding `timedelta(minutes=1)` to `workflow.py` fails it.
  QA scenarios: happy — clean tree passes; failure — mutation test above fails with file:line. Evidence `.omo/evidence/framework-adherence-reset/task-12.txt`.
  Commit: Y | Add orchestrator framework-adherence test gate

- [ ] 13. .gitignore: credential + GCE artifact guard
  What to do / Must NOT do: Append `# GCP service-account keys / GCE metadata — never commit` and patterns `fair-expanse-*.json`, `*-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f].json`, `compute.json`, `orchestrator/.runtime/`. Do NOT open, move, or delete the key file.
  Parallelization: Wave 3 | Blocked by: - | Blocks: -
  References: `.gitignore:1-21`; `git status --porcelain` (untracked list).
  Acceptance criteria: `git check-ignore fair-expanse-493212-h8-138622c839d2.json compute.json` prints both paths.
  QA scenarios: happy — check-ignore succeeds; failure — `.env.local` still ignored (`git check-ignore .env.local`). Evidence `.omo/evidence/framework-adherence-reset/task-13.txt`.
  Commit: Y | Ignore GCP credentials and GCE metadata

- [ ] 14. orchestrator/config.py + workflow.py: move the outer model batch interval into config
  What to do / Must NOT do: Add `MODEL_STREAM_BATCH_INTERVAL = timedelta(milliseconds=200)` to `config.py` carrying the rationale comment from `workflow.py:955-960` (history-pressure dial; 25ms produced 5,158 Signals). Replace the literal at `workflow.py:961` with the constant. (`compare_workflow.py:81` is deleted by todo 17.) Test: `test_config.py::test_model_stream_batch_interval` asserts the value; `test_workflow.py` asserts `TemporalAgent` is constructed with `streaming_batch_interval == config.MODEL_STREAM_BATCH_INTERVAL` (inspect via the existing fake-model harness or `unittest.mock.patch.object(workflow, "TemporalAgent")`).
  Parallelization: Wave 3 | Blocked by: - | Blocks: -
  References: `orchestrator/workflow.py:947-964`; `orchestrator/config.py:107-160`; `orchestrator/tests/test_config.py`; `orchestrator/tests/test_workflow.py`.
  Acceptance criteria: `grep -rn "timedelta(" orchestrator/*.py | grep -v config.py` = 0 (this is also adherence gate check (f)); pytest green.
  QA scenarios: happy — constant imported and used; failure — mutating the constant to 25ms fails `test_config`. Evidence `.omo/evidence/framework-adherence-reset/task-14.txt`.
  Commit: Y | Move model stream batch interval into config

- [ ] 15. lib/perplexity.ts + app/api/models/route.ts: consume the declared catalog (V9 frontend)
  What to do / Must NOT do: Delete `PERPLEXITY_PRESETS`, `GEMINI_MODEL_IDS`, `ownerOf`, `fallbackModels`, `toModel`, `modelIdsOf`, `listPerplexityModels` alias. `Model` type becomes `{ id: string; provider: string; label: string }`. `listModels()` returns `payload.models` from `/health`; on unreachable orchestrator return `[]` (the picker shows its existing "Loading models…"/empty state — no invented list). `defaultModel()` unchanged in spirit. Update `app/api/models/route.ts` (PROTECTED: change only via failing `lib/perplexity.test.ts` first proving the old shape cannot carry `provider`). `components/v0/use-models.ts` types updated.
  Parallelization: Wave 4 | Blocked by: 10,11 | Blocks: 16,17
  References: `lib/perplexity.ts:1-126`; `lib/perplexity.test.ts`; `app/api/models/route.ts`; `components/v0/use-models.ts`; `orchestrator/server.py` `/health` (todo 10).
  Acceptance criteria: `grep -n "preset:\|gemini-" lib/perplexity.ts` = 0 except `DEFAULT_MODEL`; `lib/perplexity.test.ts` green; `npx tsc --noEmit` 0.
  QA scenarios: happy — mocked `/health` with 3 providers → 3 provider values preserved; failure — fetch rejects → `[]`, no throw. Evidence `.omo/evidence/framework-adherence-reset/task-15.txt`.
  Commit: Y | Read declared providers from the orchestrator catalog

- [ ] 16. components/v0/model-picker.tsx: group by declared provider; keep actionsRef close
  What to do / Must NOT do: Delete `PROVIDER_LABELS`, `providerLabel`, `PRESET_LABELS`, `modelLabel`. Group by `model.provider`; heading text = `providers[model.provider]` from the `/health` payload (todo 10) exposed by `useModels()` as `{ models, providers, status }`; fall back to the raw provider id if missing. Trigger shows `model.label`. Keep `actionsRef` (`:87-93,114`) and adopt untracked `model-picker.test.tsx` (read it; extend for provider grouping). `reasoningLevels()` / `lib/reasoning-levels.json` untouched (server validates effort at `server.py:364-367`).
  Parallelization: Wave 4 | Blocked by: 15 | Blocks: -
  References: `components/v0/model-picker.tsx:1-225`; `components/v0/model-picker.test.tsx` (untracked); `components/ui/popover.tsx:7-8` (`Popover` spreads `PopoverPrimitive.Root.Props`, so `actionsRef` reaches `@base-ui/react/popover` unchanged — keep the dirty edit as-is).
  Acceptance criteria: `grep -n "PROVIDER_LABELS\|PRESET_LABELS" components/v0/model-picker.tsx` = 0; picker tests green; tsc 0; lint 0.
  QA scenarios: happy — models from two providers render two `PromptInputCommandGroup` headings from server-provided text; failure — model with unknown provider renders under its raw provider id (no crash). Evidence `.omo/evidence/framework-adherence-reset/task-16.txt`.
  Commit: Y | Group the model picker by declared provider

- [ ] 17. Delete the dead compare pipeline: app/api/compare/route.ts, CompareWorkflow, /compare/stream (V10)
  What to do / Must NOT do: VERIFIED dead: `components/v0/compare-view.tsx:107-113` renders one `<AgentChat model={pane.model}>` per pane and `AgentChat` posts to `/api/orchestrator` (`agent-chat.tsx:168`); `grep -rn "/api/compare" app components lib` returns only `app/api/compare/route.ts` itself; no test references `CompareWorkflow`. Delete `app/api/compare/route.ts`, `orchestrator/compare_workflow.py`, the `/compare/stream` endpoint (`server.py:584-636`) and its `CompareWorkflow` import (`server.py:58`), the `CompareWorkflow` registration (`run_worker.py:55,515`), and the `POST /compare/stream` line in `server.py`'s module docstring (`:9`). Protected-route rule satisfied: the failing compatibility test is `app/api/compare/route.test.ts` asserting the route module does not exist and that `app/compare/page.tsx` renders `CompareView` whose panes each issue a `POST /api/orchestrator` (mock fetch, two panes → two orchestrator POSTs, zero `/api/compare` POSTs) — write it first, watch it fail on the existing tree, then delete. `compare-view.tsx` and `compare-view.test.tsx` are unchanged.
  Parallelization: Wave 4 | Blocked by: 15 | Blocks: -
  References: `app/api/compare/route.ts:1-217`; `components/v0/compare-view.tsx:1-135`; `components/v0/agent-chat.tsx:167-175`; `orchestrator/compare_workflow.py`; `orchestrator/server.py:9,58,584-636`; `orchestrator/run_worker.py:55,515`; `components/v0/compare-view.test.tsx`.
  Acceptance criteria: `test -e app/api/compare/route.ts` fails; `test -e orchestrator/compare_workflow.py` fails; `grep -rn "compare/stream\|CompareWorkflow" orchestrator app lib components --include=*.py --include=*.ts --include=*.tsx` = 0; `app/api/compare/route.test.ts` + `compare-view.test.tsx` green; pytest green; tsc 0.
  QA scenarios: happy — compare page with two panes → two `/api/orchestrator` sessions stream independently; failure — orchestrator down → each `AgentChat` shows its existing `useChat` error state. Evidence `.omo/evidence/framework-adherence-reset/task-17.txt`.
  Commit: Y | Delete the unused compare SSE route and workflow

- [ ] 18. app/framework-adherence.test.ts: frontend adherence gate
  What to do / Must NOT do: Vitest suite reading `app/**/*.ts(x)`, `components/v0/**/*.ts(x)`, `lib/**/*.ts` (excluding `*.test.*`, `.worktrees`, `.kilo`) and asserting: no `new ReadableStream(`, `new TextEncoder(`, `.getReader(`, `new EventSource(`; no regex match `/["'](preset:[a-z-]+|gemini-[0-9.]+-[a-z]+|(anthropic|openai|google|xai|perplexity)\/[a-z0-9.-]+)["']/` outside `lib/perplexity.ts` `DEFAULT_MODEL`; no `asChild=` in `components/**`; no local file named `reasoning.tsx`.
  Parallelization: Wave 4 | Blocked by: - | Blocks: -
  References: todos 15-17; `vitest.config.ts`.
  Acceptance criteria: passes on the finished tree; adding `new TextEncoder()` to any `app/api/**/route.ts` fails it.
  QA scenarios: happy — clean pass; failure — mutation fails with path. Evidence `.omo/evidence/framework-adherence-reset/task-18.txt`.
  Commit: Y | Add frontend framework-adherence test gate

- [ ] 19. components/v0/agent-activity.tsx: lock think + native-tool rendering against the rebased frames
  What to do / Must NOT do: No component edits. Extend `app/api/orchestrator/route.test.ts` with two fixtures generated from the todo-5 and todo-6 test suites' actual emitted frames (copy the recorded StreamEvent JSON from `.omo/evidence/framework-adherence-reset/task-5.txt` and `task-6.txt`): one `{"perplexity": <share_file item>}` frame → `data-native-tool` part; one `{"gemini": <google_search>}` frame → `data-native-tool` part. Extend `components/v0/agent-chat.test.tsx` with a `think` dynamic-tool part whose output is the installed `strands_tools.think` return shape `{"status":"success","content":[{"text":"..."}]}` → renders as a `ChainOfThoughtStep`.
  Parallelization: Wave 4 | Blocked by: - | Blocks: -
  References: `components/v0/agent-activity.tsx:594-646,1869-1870,2010`; `app/api/orchestrator/route.test.ts`.
  Acceptance criteria: both suites green without component edits.
  QA scenarios: happy — `think` dynamic-tool part renders as ChainOfThought step; failure — malformed think payload falls back to GenericTool card (existing behavior `:753`). Evidence `.omo/evidence/framework-adherence-reset/task-19.txt`.
  Commit: N

- [x] 20. orchestrator/AGENTS.md: regenerate from verified state
  What to do / Must NOT do: Rewrite (200-400 words) with: pytest from `orchestrator/`; conventions (config.py constants; `StrandsPlugin` on the Client `run_worker.py:506`; providers subclass concrete Strands providers; hooks via `activity_as_hook`; community tools via thin `@activity.defn` wrappers; `strands_tools` imported, never vendored); providers: Perplexity Agent API (primary, `PerplexityModel(OpenAIResponsesModel)`, presets + live catalog) and Google AI Studio (`GeminiModel` subclass, 3 ids), roadmap table (OpenAI→`OpenAIResponsesModel`, Anthropic→`AnthropicModel`, Mistral→`MistralModel`, xAI→`OpenAIModel(base_url=https://api.x.ai/v1)`, extras from `strands_agents-1.50.2.dist-info/METADATA`), registration point `assemble_model_factories`; `think` is LIVE (tool + think-first hook); stream topics (`events`,`thinking`,`approval`,`handoff`,`tool_results`,`agent_runs`); batch intervals and WHY (history cap 50 MB/51,200 events); adherence gate command; graph tool section preserved from current file `:27-38` only where still true. Remove every falsified claim listed in `.omo/drafts/framework-adherence-reset.md` "Stale documentation".
  Parallelization: Wave 1 | Blocked by: - | Blocks: 4-19 (executors read it)
  References: `orchestrator/AGENTS.md`; `.omo/drafts/framework-adherence-reset.md`; `orchestrator/run_worker.py`; `orchestrator/config.py`; `orchestrator/tests/` listing.
  Acceptance criteria: `grep -n "discontinued\|graph_tool.py is absent\|no test suites" orchestrator/AGENTS.md` = 0; every command in the file executes (`.venv/bin/python -m pytest tests -q`).
  QA scenarios: happy — every cited path exists (`for p in $(grep -o 'orchestrator/[a-z_/.]*\.py' orchestrator/AGENTS.md); do test -f $p; done`); failure — a cited missing path fails the loop. Evidence `.omo/evidence/framework-adherence-reset/task-20.txt`.
  Commit: Y | Regenerate orchestrator agent notes from verified state

- [x] 21. AGENTS.md (root): regenerate from verified state with the model allowlist
  What to do / Must NOT do: Rewrite (300-500 words): product = multi-provider agent gateway (Perplexity Agent API primary; Google AI Studio direct; roadmap providers); request path; `components/*` rules (unchanged hard rule on AI Elements natives, no `reasoning.tsx`, `@base-ui` not Radix); providers declared by the worker, never inferred client-side; protected paths (`.env*`, six `app/api/**/route.ts`, `orchestrator/graph_tool.py`); commands from `package.json:5-18` verbatim; gates incl. both adherence suites; commit style ("Capitalized imperative subject, no trailing period; `feat:` prefix appears in history but is not required"); **Agent model policy** section: "Delegated/subagent work may run ONLY on: Fable 5.1, GPT 6, DeepSeek V4 Flash, DeepSeek V4 Pro, GLM 5.3, GLM 5.3 Flash, Gemini 3.8 Flash, Grok 4.6, Kimi K3. Haiku, Sonnet, and any other model are prohibited." Remove the "Current state" tables entirely (falsified: think discontinued; planned modules that exist; 2 expected failing tests; Gemini-only). Keep the "Work tracking" and "Jira documentation rules (project GWEN)" sections verbatim — `docs/jira-backlog.md` still references project GWEN (3 hits) — but replace the `docs/jira-backlog.md is the migration source` sentence's stale "orchestrator first, product reset second" ordering note with a single line: "Sequencing: this plan (`.omo/plans/framework-adherence-reset.md`) precedes the coding-agent product reset."
  Parallelization: Wave 1 | Blocked by: - | Blocks: 15-19
  References: `AGENTS.md`; `package.json`; `docs/jira-backlog.md`; `.omo/drafts/framework-adherence-reset.md`; `git log -25 --format=%s`.
  Acceptance criteria: `grep -n "Fable 5.1" AGENTS.md` >= 1; `grep -n "discontinued\|expected failures\|gemini-flash-latest\`)" AGENTS.md` = 0; word count 300-500 for body; every command present in `package.json`.
  QA scenarios: happy — path-existence loop as in todo 20; failure — same. Evidence `.omo/evidence/framework-adherence-reset/task-21.txt`.
  Commit: Y | Regenerate repository guidelines from verified state

- [x] 22. Housekeeping: prune stale worktrees and record dirty-tree disposition
  What to do / Must NOT do: `git worktree list`; for `.kilo/worktrees/{nickel-yellowhorn,nonstop-utensil,oasis-streetcar}` run `git worktree remove --force` ONLY if `git -C <path> status --porcelain` is empty; otherwise leave and record. Do NOT touch `.worktrees/coding-agent-product-reset`. Do NOT stage `fair-expanse-*.json`, `compute.json`, `scripts/gce-startup.sh`, `scripts/start-all.sh`, `.kilo/plans/*` — list them in the final report as user-owned untracked files.
  Parallelization: Wave 1 | Blocked by: - | Blocks: -
  References: `git worktree list` output; `.omo/drafts/framework-adherence-reset.md` "Dirty worktree".
  Acceptance criteria: `pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'` still the documented invocation; report lists each untracked file and its disposition.
  QA scenarios: happy — clean worktrees removed; failure — dirty worktree left with a note. Evidence `.omo/evidence/framework-adherence-reset/task-22.txt`.
  Commit: N

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — every Must-have present, every Must-NOT absent (grep list in Scope), both adherence gates green.
- [ ] F2. Code quality review — `npx tsc --noEmit`, `pnpm lint`, scoped vitest, `cd orchestrator && .venv/bin/python -m pytest tests -q`; zero new `# noqa`/`eslint-disable` beyond pre-existing.
- [ ] F3. Real manual QA — `pnpm dev:all`; one turn on `preset:high` and one on `gemini-3.8-flash`; picker shows two provider headings; think step streams in Chain of Thought; `/health` returns provider objects; compare with two models renders both panes. Capture terminal + `curl /health` output to `.omo/evidence/framework-adherence-reset/f3.txt`.
- [ ] F4. Scope fidelity — no new provider code, no revert of 2s batch intervals, credential file untouched and ignored, protected routes changed only with their failing test committed first.

## Commit strategy
One commit per todo marked `Commit: Y`, subject as listed (capitalized imperative, no trailing period). Never stage untracked credential/GCE files. Run all four gates before each commit.

## Success criteria
- `orchestrator/tests/test_framework_adherence.py` and `app/framework-adherence.test.ts` exist and pass.
- `PerplexityModel` subclasses `OpenAIResponsesModel`; `think_activity.py` imports `strands_tools.think`; no `execute_activity` in hooks; no `_closable` in workflow.py; no `orchestrator/tools/`; no readiness JSON lease.
- `/health` returns `[{id, provider, label}]`; picker groups by server-declared provider; `lib/perplexity.ts` has no static model lists.
- `app/api/compare/route.ts`, `compare_workflow.py`, and `/compare/stream` no longer exist; compare page still works through `/api/orchestrator`.
- Both AGENTS.md regenerated; root carries the model allowlist.
- All existing suites green (≥333 pytest, all vitest), tsc 0, lint 0.
