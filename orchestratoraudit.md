# Orchestrator Audit — findings ordered by impact

Every claim below is grounded in source I read. Versions in play: temporalio 1.31.0, strands-agents 1.50.2, fastapi 0.141.1, starlette 1.3.1, perplexityai 0.42.0.

## TIER 1 — Time-to-first-byte

### F1. poll_cooldown is left at its 100 ms default on both subscribers — the single largest unset-default latency in the codebase

File / lines: server.py:210-212 (chat), server.py:338 (compare)

Current behavior. Both call sites pass only topics and from_offset:

```python
subscription = stream_client.subscribe(CHAT_TOPICS, from_offset=start_offset)   # 210-212
async for item in stream_client.subscribe(topics, from_offset=0):               # 338
```

Library contract. _client.py:487-493 declares:

```python
async def subscribe(self, topics=None, from_offset=0, *,
                    result_type=None,
                    poll_cooldown: timedelta = timedelta(milliseconds=100))
```

and _client.py:610-612 is where it bites:

```python
cooldown_secs = poll_cooldown.total_seconds()
if not result.more_ready and cooldown_secs > 0:
    await asyncio.sleep(cooldown_secs)
```

The workflow-side long-poll (_stream.py:395-409) blocks in workflow.wait_condition until items exist, so a poll that returns is a poll that had data — and more_ready is only True when the ~1 MB cap (_stream.py:57, _stream.py:445) forced a split, which streaming text deltas never hit. Therefore the 100 ms sleep executes after essentially every batch, on the caught-up path, which is *the streaming path*.

Fix. poll_cooldown=timedelta(milliseconds=10) (or lower) on both calls. The guide's own Pattern 8 example sets it explicitly — strands_temporal_agent_guide.md:704 uses poll_cooldown=timedelta(milliseconds=50). The docstring warns only against timedelta(0) ("an idle subscriber busy-loops", _client.py:517-519), which does *not* apply here because both subscriptions terminate when the workflow does.

Observable change. Removes up to 100 ms of dead time between every batch of deltas. Cost: more poll updates in workflow history when idle. Note the compare stream has no idle period at all (it starts, streams, ends), so it can go lower than the chat stream safely.

### F2. batch_interval=50ms on the client is set on a code path that never batches — the real publish-side delay is streaming_batch_interval, which is never set

File / lines: server.py:198-200, server.py:333-335. Related: workflow.py:199-216, workflow.py:225-233, compare_workflow.py:74-82.

Current behavior. The code sets batch_interval=timedelta(milliseconds=50) on WorkflowStreamClient.create, with a three-line comment (server.py:195-197) claiming the 2 s default "holds every frame for up to two seconds before the client sees it."

That comment is wrong. batch_interval is read in exactly two places in _client.py: stored at line 121, and consumed at line 461 inside _run_flusher:

```python
await asyncio.wait_for(self._flush_event.wait(),
                       timeout=self._batch_interval.total_seconds())
```

_run_flusher is started only by __aenter__ (_client.py:221-224), and it drains self._buffer, which is only filled by _publish_to_topic (_client.py:243-256). server.py never publishes and never enters the client as a context manager. The flusher task is never created. batch_interval on these two clients is *inert* — dead configuration with a comment asserting the opposite.

Where the delay actually lives. The publisher is ModelActivity.invoke_model_streaming (_model_activity.py:81-95):

```python
stream = WorkflowStreamClient.from_within_activity(
    batch_interval=timedelta(seconds=input.streaming_batch_interval_seconds),
)
```

fed from _temporal_model.py:129: streaming_batch_interval_seconds=self._streaming_batch_interval.total_seconds(), whose default is timedelta(milliseconds=100) (_temporal_agent.py:50 and _temporal_model.py:69). None of the four TemporalAgent(...) constructions set *it*.

Fix.

1. Delete batch_interval=timedelta(milliseconds=50) from server.py:199 and :334, and delete the incorrect comment at server.py:195-197. No library equivalent needed — the parameter is simply not on this path.
2. Add streaming_batch_interval=timedelta(milliseconds=25) to all four TemporalAgent(...) calls: workflow.py:199-216, workflow.py:225-233, compare_workflow.py:74-82. That is the parameter whose default actually inserts publish-side delay.

Observable change. (1) is pure dead-code removal — zero behavior change, which is itself the point: the current code creates a false impression that read latency has been tuned. (2) genuinely cuts up to 75 ms per publish batch, at the cost of more signal round-trips to the server per turn (each flush is one __temporal_workflow_stream_publish signal, _client.py:438) and correspondingly more workflow history events.

Combined F1+F2 note: together these are the entire streaming latency budget. Current worst case per delta ≈ 100 ms publish + 100 ms cooldown ≈ 200 ms. The README's stated "~100ms per roundtrip" (workflow_streams/README.md:11-12) is the floor these two defaults sit on top of.

### F3. The sequential "thinker" agent runs a complete second model turn before the primary agent starts — the dominant TTFB cost, by orders of magnitude

File / lines: workflow.py:218-233 (_build_thinker), workflow.py:276-285 (the await), workflow.py:59-67 (THINK_SYSTEM_PROMPT).

Current behavior.

```python
analysis = str(await self._build_thinker().invoke_async(blocks)).strip()   # 280
if analysis:
    blocks = [{"text": f"My prior analysis:\n{analysis}"}, *blocks]        # 282-285
result = await agent.invoke_async(blocks)                                   # 287
```

invoke_async here is fully blocking: TemporalModel.stream (_temporal_model.py:118) does await workflow.execute_activity_method(...) and only then yields the collected list (_temporal_model.py:147-148). The activity returns the complete event list. So the primary agent's first token cannot be produced until an entire independent model run — with max_steps: 100 and all five native tools plus MCP (run_worker.py:84-90, 147-151) — has finished.

Is this a reimplementation? Partly. Strands ships first-class multi-agent primitives that do the same composition natively: strands/multiagent/ and Agent.as_tool() (agent/agent.py:958-964), plus agent/_agent_as_tool.py. Wiring the reasoning stage as a tool on the primary agent (rather than a mandatory serial prelude) means the primary agent starts streaming immediately and only pays the reasoning cost when it decides it needs it. agent/_concurrency.py exists for the concurrent case.

But — and stated plainly — the user-visible mitigation does not require restructuring: the thinker already publishes its own StreamEvents to THINKING_TOPIC from inside its activity (workflow.py:231 → _temporal_model.py:117-132 → _model_activity.py:87-94), and server.py:77 subscribes to it. So bytes do reach the client during the thinking phase. The TTFB problem is real for *answer text*, not for *the connection*.

Recommendation. No library API is a drop-in replacement for the product decision here. If the sequential prelude is intentional product design, it is justified and I will not invent a replacement. If it is not, Agent.as_tool() / strands.multiagent is the native path. What is not justified is that this is undocumented as the primary TTFB cost while server.py:195-197 fusses over 50 ms.

### F4. agent_identity() re-reads and re-parses agent.json from disk on every worker start and every API start, and server.py imports run_worker to get it

File / lines: run_worker.py:176-185, called at run_worker.py:263 and server.py:98; import at server.py:59.

Current behavior. from run_worker import READINESS_PATH, agent_identity pulls the entire worker module into the API process. Importing run_worker executes its module body: load_dotenv (:53), and transitively imports perplexity_model, compare_workflow, workflow, and telemetry (:46-50). The API process needs exactly two things from it: a Path constant and a 10-line JSON reader.

Impact. This is startup-time, not per-request, so it does not affect TTFB after boot. It does inflate API cold-start and couples the two processes at import level. There is no library API at issue — this is a structural finding.

Fix. Move READINESS_PATH and agent_identity() into config.py (which both already import) and drop server.py:59. config.py is described in AGENTS.md as the home for module-level constants, so this matches the stated project convention.

Observable change. None functionally. Removes an import cycle risk and shortens API startup.

## TIER 2 — Non-compliance and correctness

### F5. compare_workflow.py calls detach_pollers() after all_handlers_finished — the documented order is reversed, and this can hang the workflow

File / lines: compare_workflow.py:102-103

```python
await workflow.wait_condition(workflow.all_handlers_finished)
self._stream.detach_pollers()
```

Library contract. _stream.py:282-284, in the detach_pollers docstring:

Call this *before* await workflow.wait_condition(workflow.all_handlers_finished) and workflow.continue_as_new().

And WorkflowStream.continue_as_new (_stream.py:331-332) hard-codes the correct order:

```python
self.detach_pollers()
await workflow.wait_condition(workflow.all_handlers_finished)
```

Why it hangs. all_handlers_finished is not self._in_progress_updates and not self._in_progress_signals (_workflow_instance.py:1199). The stream's poll is registered as an update handler (_stream.py:158-160), and a subscriber parked in _on_poll's wait_condition (_stream.py:403-409) is an in-progress update. It only releases when self._detaching flips true. So wait_condition(all_handlers_finished) waits for the poller, and the poller waits for detach_pollers(), which is on the next line and will never run.

server.py:338 subscribes with from_offset=0 and iterates to exhaustion, so a live poller at this moment is the normal case, not an edge case.

Note this is correct in workflow.py. workflow.py:358-360 gets the order right, and the comment at :351-357 explains exactly this deadlock. compare_workflow.py has the same two calls in the opposite order with no comment. This is an inconsistency between two files in the same codebase, and compare_workflow.py is the wrong one.

Fix. Swap lines 102 and 103.

Observable change. Fixes a hang. CompareWorkflow currently relies on the subscriber's own WorkflowUpdateRPCTimeoutOrCancelledError path (_client.py:580-583) or gRPC deadline to break the deadlock incidentally.

### F6. import base64 is executed inside a loop body, inside a workflow method

File / lines: workflow.py:244-257, specifically line 248.

```python
for image in turn.images:
    if image.format not in _SUPPORTED_IMAGE_FORMATS:
        continue
    # base64 decoding is *pure computation*, deterministic under replay.
    import base64
```

The comment defends the decoding (correctly), but says nothing about why the import is inside a loop. base64 is stdlib, deterministic, and safe at module scope. Placing an import inside a per-iteration branch in workflow code is needless complexity and, under the sandboxed workflow runner, an import statement is a more expensive operation than in plain Python.

Fix. Move import base64 to the module header alongside import asyncio (workflow.py:34). No library API involved.

Observable change. None.

### F7. Unused imports and unread state

Verified by AST analysis (names imported, never referenced):

| File:line | Symbol | Status |
| --- | --- | --- |
| server.py:45 | `from strands.types.streaming import StreamEvent` | Never referenced. Confirmed: the only occurrence in the file is the import itself. |
| workflow.py:180 | `self._events = self._stream.topic(EVENTS_TOPIC)` | Handle created, .publish never called. Publishing is done by the SDK inside the activity (_model_activity.py:90-94), not by the workflow. |
| workflow.py:184 | `self._thinking = self._stream.topic(THINKING_TOPIC)` | Same — never published to from the workflow. |

On _events / _thinking: these are not simply dead. WorkflowStream.topic() records a name→type binding in self._topic_types (_stream.py:234) and returns a handle. Since both are bound to the default Any (_stream.py:218), and the activity-side publisher is a different WorkflowStreamClient instance whose type map does not coordinate across processes (_stream.py:195-197 states this explicitly), the binding has no enforcement effect. The handles are genuinely unused. The comment at workflow.py:183 — "Hosted here so the think activity's client can reach it" — is incorrect: the activity reaches the topic by name through its own client (_model_activity.py:90), not *through this handle*.

Fix. Delete server.py:45; delete workflow.py:180 and :184 and the misleading comment at :183. Keep the EVENTS_TOPIC / THINKING_TOPIC string constants — those are load-bearing (workflow.py:209, :231, server.py:77).

Observable change. None.

### F8. _ToolResultHook and _ApprovalHook are wired to a tool set that is *provably empty*

File / lines: workflow.py:76, :123-168, :212-215, and the HITL resume loop :293-315.

APPROVAL_REQUIRED_TOOLS = frozenset() (:76). Neither _build_agent (:199-216) nor _build_thinker (:225-233) nor CompareWorkflow's agent (compare_workflow.py:74-82) passes tools=. Confirmed by grep: the string *tools* appears nowhere in workflow.py or compare_workflow.py outside the approval-set name.

Consequences, all verified:

- _ApprovalHook._gate (:136-142) returns at line 139 on every invocation — name not in frozenset() is always true.
- _ToolResultHook._record (:158-168) never fires: AfterToolCallEvent requires a tool call.
- The while result.stop_reason == "interrupt" loop (:293-315) is unreachable — the only interrupt source is _gate.
- approve signal (:319-321), self._approval (:195), self._pending_reason (:196), pending_approval query (:332-334), and TOOL_RESULTS_TOPIC (:69) publishing are all consequently *dead* at runtime.
- Route-side, app/api/orchestrator/route.ts:362-384 and :397-404 handle tool_results and approval frames that cannot currently arrive.

Verdict — this is justified, not a defect. workflow.py:71-76 states the rationale explicitly: keeping the machinery wired makes adding a gated tool a one-line change rather than a workflow rewrite. Both hooks are correctly implemented against the real API — registry.add_callback(EventType, cb) per strands/hooks/registry.py, event.tool_use["name"] and event.interrupt(name, reason=...) per types/interrupt.py:82 and the SKILL.md correctness table (SKILL.md:118-121), and the resume loop answers every interrupt per guide R9 (strands_temporal_agent_guide.md:844-846). I am reporting the dead-at-runtime status as fact, not recommending removal.

One genuine nit inside it: _ToolResultHook._record at :164 does result.get("toolUseId", event.tool_use["toolUseId"]). Per strands/types/tools.py:90-101, ToolResult is a total TypedDict — toolUseId, status, and content are all required. The three .get(..., default) calls at :164-166 defend against a shape the type system guarantees. Harmless, but it is defensive code with no failure mode to defend against.

### F9. _content_blocks hand-builds ContentBlock dicts *including* a manual format allow-list

File / lines: workflow.py:116-120, :235-258

The _SUPPORTED_IMAGE_FORMATS frozenset duplicates IMAGE_FORMATS in app/api/orchestrator/route.ts:31-37. The route already filters (route.ts:82-86: if (!format) continue), so by the time an image reaches workflow.py:245 its format is *necessarily* one of the four. The check is a second gate on a value the first gate guaranteed.

Is there a library equivalent for the block construction? No, and I will say so plainly. AgentInput is str | list[ContentBlock] | list[InterruptResponseContent] | Messages | None (strands/types/agent.py:14), and ContentBlock is a plain TypedDict from strands/types/content.py. Strands ships no builder or from-base64 helper. Constructing {"image": {"format": ..., "source": {"bytes": ...}}} by hand is the intended usage.

Fix. Keep _content_blocks. The duplicated allow-list is defensible defense-in-depth given route.ts is a protected file that could change independently — but the comment at :116-119 should say that rather than implying the check is *load-bearing*.

### F10. readiness() reads and JSON-parses a file from disk on every /health and every /sessions request

File / lines: server.py:82-93, called from health() (:150) and start_session() (:164), and compare_stream() (:304).

Each call does READINESS_PATH.read_text() + json.loads synchronously on the event loop. FastAPI runs async def endpoints directly on the loop (no threadpool), so this is blocking I/O in an async handler. supported_models() (:90-93) calls readiness() again, so health() performs two full read+parse cycles per request (:150 and :158).

Library equivalent. For the file-read pattern: none — this is an application-invented IPC channel. Temporal does expose worker discovery (WorkflowService.describe_task_queue, list_workers, count_workers — confirmed present on the installed service class), which would replace the file entirely with an authoritative source. That is a larger change.

Minimum fix within the current design. Read once per request instead of twice (health() should call readiness() once and derive models from that record), and if the file must be read per-request, use starlette.concurrency.run_in_threadpool or accept a short TTL cache.

Observable change. Caching would make model-list changes visible only after the TTL. De-duplicating the double read in health() is behavior-neutral.

### F11. response_file_content constructs a fresh httpx.AsyncClient per request

File / lines: server.py:290-293

```python
async with httpx.AsyncClient(timeout=60) as http:
    upstream = await http.get(url, headers=...)
```

A new client means a new connection pool and a fresh TLS handshake to api.perplexity.ai for every file fetch — no keep-alive reuse. Same pattern at run_worker.py:195-199, though that runs once at startup and is fine there.

Fix. Create one httpx.AsyncClient in the lifespan context manager (server.py:96-108) — which already exists for exactly this kind of resource — store it in _state, and close it after yield. This is *the* documented FastAPI lifespan pattern.

Second issue on the same endpoint: the response is fully buffered. upstream.content (:297) materializes the entire file in memory before Response(content=...) sends it. httpx provides client.stream(...) and Starlette provides StreamingResponse (starlette/responses.py:222) for the incremental path. Note the caller, app/api/orchestrator/file/route.ts:39, already streams (new Response(upstream.body)) — so the buffering is introduced solely by this hop.

Observable change. Streaming changes error semantics: the current code can return a clean HTTPException(upstream.status_code) before sending any bytes (:294-295); a streaming version must decide status from response headers before the body arrives. httpx.AsyncClient.stream gives you response.status_code before body iteration, so this is preservable.

### F12. Dead configuration: EMBEDDING_GENERATIONS

File / lines: config.py:15-21

Repo-wide grep (excluding .venv, node_modules, .next, .git, .worktrees) finds references only in: config.py itself, tests/test_config.py:4,15, AGENTS.md:29, and the plan document docs/superpowers/plans/2026-07-30-durable-strands-temporal-orchestrator.md:57,62,114. No orchestrator or frontend code reads it. LanceDB is pinned in requirements.txt:5 but imported nowhere.

Per AGENTS.md, memory.py (Task 4) is planned but not on disk. This is forward-declared configuration for unwritten code, currently held in place by a test that asserts its literal value. Not a defect; noting it as dead-today so it is not mistaken for live config.

### F13. telemetry.py — three findings

File / lines: telemetry.py:41-49, :9+:32+:57, :59

(a) OTEL_SERVICE_NAME is read manually and passed into Resource.create. Lines 44-46 do os.getenv("OTEL_SERVICE_NAME", "perplexity-orchestrator"). Resource.create already consults that variable: OTELResourceDetector.detect (opentelemetry/sdk/resources/__init__.py:332-334) reads OTEL_SERVICE_NAME and maps it to SERVICE_NAME, and Resource.create runs the detectors at line 185. Passing it explicitly is redundant and subtly wrong: the explicit attributes argument is merged last (line 186: get_aggregated_resources(...).merge(Resource(attributes, schema_url))), so when the env var is set, this code passes the same value it would have gotten anyway — but the fallback "perplexity-orchestrator" also wins over OTEL_RESOURCE_ATTRIBUTES=service.name=..., which the SDK docs (resources/__init__.py:549) state should take highest merge precedence. Correct form: Resource.create({SERVICE_NAME: "perplexity-orchestrator"}) only as a default when the env var is absent, or simply Resource.create() and let the detector do it.

(b) _configured module global is a hand-rolled idempotence guard. Lines 9, 32, 57. opentelemetry.trace.set_tracer_provider already warns and no-ops on a second call. In practice telemetry_plugins() is called once, from run_worker.py:269. This is a guard for a scenario that does not occur.

(c) return [OpenTelemetryPlugin()] at line 59 is outside the if not _configured block but inside the function — correct, but it references a name bound in the try at lines 21-24. If that import succeeded and the inner import at 34-39 failed, line 59 still returns a plugin with no exporter configured, silently. The except ImportError at 53-56 logs "spans will not be exported" and then the function returns a live plugin anyway. That is arguably intended (graceful degradation, which AGENTS.md names as this file's design goal) but the logged message and the return value disagree about what happened.

Overall verdict on telemetry.py: the graceful-degradation structure matches the guide's StrandsPlugin + OpenTelemetryPlugin composition (strands/README.md:432-450) and is correct in placing the plugin *on the client* (run_worker.py:269) as that README requires. Only (a) has a concrete library-provided replacement.

## TIER 3 — Compliance confirmations (no action)

These were checked against the contracts and are correct. Listing them so the audit is not read as silence-implies-defect.

| Item | Verified against |
| --- | --- |
| model= is a registered string name everywhere | workflow.py:202, :226, compare_workflow.py:75 vs SKILL.md:56-63 (R1), _temporal_agent.py:38 |
| StrandsPlugin on the client, never on Worker(...) | run_worker.py:269, server.py:103 vs SKILL.md:72-78 (R3), strands/README.md:333-338. Worker(...) at run_worker.py:272-278 correctly omits plugins= and activities= — the comment at :276-277 is accurate; _plugin.py:60-63 registers invoke_model and invoke_model_streaming itself. |
| Agent rebuilt in run, not __init__, for continue-as-new | workflow.py:344 vs R7 exception, strands_temporal_agent_guide.md:790-791 |
| WorkflowStream constructed directly in a method named __init__ | workflow.py:179, compare_workflow.py:65 vs the sys._getframe(1) check at _stream.py:127-133 |
| stream_state typed `WorkflowStreamState \ None, not Any` | workflow.py:113 vs the explicit warning at _stream.py:120-125 |
| WorkflowStream.continue_as_new(build_args) helper used *instead of* the 3-line recipe | workflow.py:368-377 vs _stream.py:288-335 — this is the correct use of the library helper |
| detach_pollers() *before* all_handlers_finished in ChatWorkflow | workflow.py:358-360 vs _stream.py:282-284 (contrast F5) |
| Turn is @workflow.update, subscriber created *before* start_update | workflow.py:260, server.py:210-223 vs Pattern 9, strands_temporal_agent_guide.md:760-765 |
| wait_for_stage=ACCEPTED *on* start_update | server.py:222 — matches the library's own pattern at _client.py:545-552 and its rationale comment |
| update_handle.result() not cancelled on disconnect | server.py:233-238 — deliberate, documented at :22-23, correct for durability |
| No tools= passed, so no activity_as_tool timeouts to check | R8 vacuously satisfied |
| retry_strategy never passed | _temporal_agent.py:54-62 would raise ValueError; it does not |
| Topic names match between publisher and subscriber | EVENTS_TOPIC/THINKING_TOPIC/APPROVAL_TOPIC/TOOL_RESULTS_TOPIC in server.py:77; model_topic() shared via import at server.py:51-57. Satisfies R10. |
| WorkflowStreamClient.create(client, id) not the bare constructor | server.py:198, :333 vs SKILL.md:123, _client.py:139-175 |
| No I/O / clock / randomness in workflow methods | R5 — verified; os.urandom calls are in server.py (:171, :321), correctly outside workflow context |
| No asyncio.run() in workflow code | R6 — asyncio used only for Lock (workflow.py:192) and gather (compare_workflow.py:100) |
| HookProvider.register_hooks(self, registry, **kwargs) signature | workflow.py:133, :155 — matches SKILL.md:91-92, no phantom get_hooks() |
| Model factory closure binds model_id=model_id | run_worker.py:221 — the late-binding fix is real and necessary; comment at :214-218 is accurate |
| No hard-coded model allow-list; catalog fetched live | run_worker.py:188-206 — Rule 0 compliant, and the agent-directed warning block at :7-20 is warranted |
| agent.json KaTeX escaping | Verified by decoding: JSON \\\\[ yields Python \\[, which is literal-backslash + [ — the correct thing to show a model when telling it not to emit \[. Not a double-escape bug. |
| compare_workflow.py:98-99 comment on return_exceptions | Accurate — run_one (:83-87) catches Exception and records to result.errors, so gather cannot raise. Correct reasoning, correctly documented. |

## Summary Table

| # | File:lines | Impact | Library fix exists |
| --- | --- | --- | --- |
| F1 | server.py:210-212, :338 | TTFB: +100 ms/batch | Yes — poll_cooldown= (_client.py:493) |
| F2 | server.py:198-200,:333-335; 4× TemporalAgent | TTFB: +100 ms/batch; dead config w/ false comment | Yes — drop batch_interval, set streaming_batch_interval= (_temporal_agent.py:50) |
| F3 | workflow.py:280 | TTFB: one full extra model turn | Partial — Agent.as_tool(); may be intentional design |
| F4 | server.py:59; run_worker.py:176-185 | Startup coupling | No library API; move to config.py |
| F5 | compare_workflow.py:102-103 | Correctness: deadlock | Yes — order per _stream.py:282-284 |
| F6 | workflow.py:248 | Complexity | Stdlib; move to module scope |
| F7 | server.py:45; workflow.py:180,:184 | Dead code + wrong comment | Delete |
| F8 | workflow.py:76 + hook machinery | Dead at runtime — justified | N/A |
| F9 | workflow.py:116-120,:235-258 | Duplicated allow-list — justified | No builder exists in Strands |
| F10 | server.py:82-93,:150,:158 | Blocking I/O on event loop, ×2 per /health | Partial — describe_task_queue exists |
| F11 | server.py:290-299 | Per-request TLS handshake; full buffering | Yes — lifespan client + StreamingResponse |
| F12 | config.py:15-21 | Dead config for unwritten memory.py | N/A |
| F13 | telemetry.py:41-49, :9/32/57 | Redundant env read; unnecessary guard | Yes — Resource.create() detector (resources:332-334) |

If only three changes are made: F5 (hang), F1 (100 ms), F2 (100 ms + removes a comment that actively misleads the next reader about where streaming latency lives).

No files were modified.