---
source: Context7 API + official Temporal/Strands documentation and source
library: Temporal Python SDK Strands integration + Strands Agents Python SDK
package: temporalio[strands-agents]
topic: activity versus inline Strands tool classification
tech_stack: Temporal Python SDK 1.28.0+; TemporalAgent; Strands Agents Python
fetched: 2026-07-30T00:00:00Z
official_docs: https://docs.temporal.io/develop/python/integrations/strands-agents
---

# Decision framework: `@activity.defn` vs Strands `@tool`

## Bottom line

In `TemporalAgent`, the Strands agent loop runs in **Temporal Workflow context**. Therefore a plain Strands `@tool` also runs in Workflow context and may remain inline **only when its execution is replay-deterministic, local, non-blocking, and side-effect free**. Any tool that performs I/O, observes mutable external state, uses nondeterministic process state, or has side effects must be a Temporal Activity and exposed with the integration's native `temporalio.contrib.strands.workflow.activity_as_tool()` API.

The integration does **not** automatically convert arbitrary `@tool` functions into Activities. It does automatically activity-back:

1. **Model calls** made by `TemporalAgent`, through plugin-registered `ModelActivity.invoke_model` / `ModelActivity.invoke_model_streaming`.
2. **MCP listing and calls** when using `TemporalMCPClient`, through generated `{server}-list-tools` / `{server}-call-tool` Activities.
3. **User Activities explicitly exposed as tools**, through `activity_as_tool()`.
4. **User Activities explicitly exposed as hooks**, through `activity_as_hook()`.

Plain `@tool` and ordinary hook callbacks are not auto-converted. [Temporal integration guide](https://docs.temporal.io/develop/python/integrations/strands-agents) · [integration source: `workflow.py`](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/workflow.py) · [`TemporalActivityTool`](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_temporal_activity_tool.py) · [`TemporalModel`](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_temporal_model.py)

## Classification table

| Tool/operation | Classification in `TemporalAgent` | Exact native API / symbol | Exceptions and rationale | Confidence |
|---|---|---|---|---|
| Pure arithmetic, parsing, validation, deterministic transforms | **May remain Strands `@tool`** | `strands.tool` | Only if output depends solely on recorded inputs/Workflow state; no system clock, OS randomness, thread, environment, global mutation, I/O, or blocking. | High |
| Deterministic read/update of in-memory agent or invocation state | **May remain `@tool`** | `@tool(context=True)`, `ToolContext`; ordinary deterministic hooks | State changes must replay identically. `TemporalModel` drops non-JSON-serializable invocation-state entries before model Activities, so do not assume arbitrary objects reach the provider. | High |
| Network/HTTP/provider API call | **Must be Activity** | `@activity.defn` + `temporalio.contrib.strands.workflow.activity_as_tool()` | Network I/O is forbidden in Workflow logic. Model-provider calls are the special case already routed by `TemporalAgent`. | High |
| MCP discovery/call | **Use native activity-backed MCP path** | `StrandsPlugin(mcp_clients=...)`, `TemporalMCPClient(server=..., ...)` | Do not use a normal workflow-side `MCPClient`; current source generates list/call Activities and keeps transport worker-side. | High |
| Filesystem read/write, shell, subprocess, environment inspection | **Must be Activity** | `@activity.defn` + `activity_as_tool()` | Files and environment differ across replay/workers; subprocesses and external processes are forbidden in Workflow code. Even a read is nondeterministic unless the bytes are already recorded in Workflow input/history. | High |
| Database/cache/object-store query or mutation | **Must be Activity** | `@activity.defn` + `activity_as_tool()` | Queries observe mutable external state; writes are side effects. Writes must tolerate Activity re-execution. | High |
| Model invocation by the main `TemporalAgent` | **Automatically Activity-backed** | `TemporalAgent`, `StrandsPlugin(models={name: factory})`; internally `TemporalModel.stream`, `ModelActivity.invoke_model`, `ModelActivity.invoke_model_streaming` | Factory runs worker-side and model instance is cached for worker lifetime. Never pass a live provider as `TemporalAgent(model=...)`; `model` is a registered string name. | High |
| Nested local `Agent` passed directly/as `.as_tool()` | **Not automatically safe or Activity-backed** | Strands `Agent.as_tool()` is only a Strands tool | A normal nested `Agent` can make model/network calls inline and is therefore unsafe in Workflow context. The official Temporal integration documents `TemporalAgent`, not durable Strands `Graph`/`Swarm`/ordinary nested-Agent conversion. If the nested object is itself a `TemporalAgent`, its model calls are Activities, but its plain tools still follow this table; direct nested support/state semantics are not documented. | Medium |
| Remote A2A/nested-agent call | **Must be Activity** | No Strands-specific Temporal native symbol documented | It is network I/O. No official Temporal-Strands native A2A activity adapter was found. | High on classification; Low on integration support |
| Long-running computation with no I/O | **Prefer Activity; must be Activity if it blocks Workflow progress or needs cancellation/retry** | `@activity.defn`, `activity.heartbeat`, `heartbeat_timeout` via `activity_as_tool()` | Tiny bounded pure work may remain inline. CPU-heavy or long work should not occupy Workflow Tasks. Activities may be sync thread/process or async, as appropriate. | High |
| Long-running I/O/polling | **Must be Activity** | `@activity.defn`; `activity.heartbeat()`; `activity.info().heartbeat_details`; `heartbeat_timeout` | Heartbeat for liveness, retry progress, and cancellation delivery. For operations longer than a few minutes, Temporal says support heartbeats or polling. | High |
| Human approval gate | **Workflow-side deterministic interrupt/wait; external side effects as Activities** | `BeforeToolCallEvent.interrupt()`, `event.cancel_tool`; `AgentResult.stop_reason == "interrupt"`; `workflow.signal`, `workflow.wait_condition`; resume via `TemporalAgent.invoke_async(interruptResponses)` | A pure `@tool` may raise `InterruptException(Interrupt(...))`. An Activity tool may also interrupt; `StrandsPlugin` must be attached to the **client** so its failure converter preserves the payload. Human waiting belongs in Workflow state, not a long-held Activity. | High |
| Hook that only modifies deterministic local state/tool input/result | **May remain ordinary Strands hook** | `HookProvider`, `HookRegistry.add_callback`, typed hook events | Hooks fire in Workflow context. No clock, UUID, I/O, nondeterministic logging, or arbitrary external objects. | High |
| Hook that audits, emits alerts/metrics, or persists data | **Must dispatch Activity** | `@activity.defn` + `temporalio.contrib.strands.workflow.activity_as_hook(activity_fn, activity_input=..., ...)` | `activity_input` must extract serializable data because events contain `Agent`/`AgentTool` references. | High |
| Async-generator `@tool` yielding progress | **May remain inline only if entirely deterministic**; **not supported as an Activity-stream equivalent by `activity_as_tool()`** | Strands `@tool` async generator emits `ToolStreamEvent`; `TemporalActivityTool.stream()` emits one final `ToolResultEvent` | Current `activity_as_tool` awaits one Activity result; it does not forward generator yields. Model streaming is separate and natively supported through `streaming_topic`/Workflow Streams. | High |
| Model streaming | **Automatically inside Activity** | `TemporalAgent(streaming_topic=..., streaming_batch_interval=...)`, `WorkflowStream`, `WorkflowStreamClient`; internally `ModelActivity.invoke_model_streaming` | Activity accumulates and ultimately returns the complete `list[StreamEvent]` while publishing batches externally. Internal model Activities auto-heartbeat only when `heartbeat_timeout` is configured. | High |
| Dynamic runtime tools created/loaded from code/files | **Do not run loading/file/code-generation inline**; external portions must be Activities | No documented general native dynamic `activity_as_tool` registration API; MCP has native dynamic relisting through `TemporalMCPClient(cache_tools=False)` | Current source dynamically reconciles MCP tools before model calls. Arbitrary runtime tool code/schema mutation risks replay/version nondeterminism and has no documented Temporal-Strands durable mechanism. Predeclared deterministic in-memory registry changes could replay, but compatibility is unverified. | Medium |
| Tool-result persistence in agent conversation/workflow history | **Automatic after completed calls, subject to serialization/history limits** | Temporal Event History; `TemporalAgent.messages`; Pydantic data converter installed by `StrandsPlugin` | Activity inputs/results are history payloads. `TemporalActivityTool` converts a result to text (`str`, JSON, fallback `str`), then Strands appends the tool result message. Large results increase history and face payload limits (2 MB individual payload/default; 4 MB gRPC message). External blob persistence remains an Activity, with only a compact reference returned. | High |
| Strands session manager / snapshot persistence | **Do not treat as the Temporal integration persistence mechanism** | `TemporalAgent.take_snapshot()` and `.load_snapshot()` raise `NotImplementedError`; use event history and `workflow.continue_as_new()` | File/S3 session managers perform I/O and are not the native TemporalAgent durability path. Long chats should carry `agent.messages` through Continue-As-New when suggested. | High |

Temporal Workflow constraints supporting these classifications: no threads, randomness, external process calls, network I/O, global mutation, or system date/time. Replay-safe alternatives include `workflow.random()`, `workflow.uuid4()`, and `workflow.now()`. [Workflow logic requirements](https://docs.temporal.io/develop/python/workflows/basics#develop-workflow-logic)

## Exact native integration surface

Public exports from `temporalio.contrib.strands` are:

- `StrandsPlugin`
- `TemporalAgent`
- `TemporalMCPClient`
- module `workflow`, containing `activity_as_tool` and `activity_as_hook`

`activity_as_tool(activity_fn, *, task_queue, schedule_to_close_timeout, schedule_to_start_timeout, start_to_close_timeout, heartbeat_timeout, retry_policy, cancellation_type, activity_id, versioning_intent, summary, priority) -> AgentTool` requires a callable decorated with `@activity.defn`; it forwards options to `workflow.execute_activity`. `activity_as_hook()` exposes the same Activity options plus required `activity_input`. [API source](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/workflow.py)

The plugin itself registers model Activities and, for every configured MCP server, generated `{server}-call-tool` and `{server}-list-tools` Activities. It does **not** scan `TemporalAgent.tools` and convert arbitrary Strands tools. [`StrandsPlugin` source](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_plugin.py)

## Retries, idempotency, and the double-retry boundary

### Model calls

Ordinary Strands agents default to `ModelRetryStrategy`: six total attempts by default for throttling, exponential delay starting at four seconds. [`ModelRetryStrategy` docs](https://strandsagents.com/docs/user-guide/concepts/agents/retry-strategies/)

`TemporalAgent` explicitly rejects a non-`None` `retry_strategy`, forces `retry_strategy=None`, and directs model retries to Temporal `retry_policy`. This removes the **Strands model retry layer**. [`TemporalAgent` source](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_temporal_agent.py) · [Temporal integration retries](https://docs.temporal.io/develop/python/integrations/strands-agents#configure-retries)

Exception: a provider SDK or HTTP client hidden inside the model factory may still retry internally. The integration cannot disable provider-internal retries generically. Account for those attempts in each Activity's Start-To-Close/Schedule-To-Close bounds and provider configuration; otherwise effective attempts multiply (`provider attempts × Temporal Activity attempts`). Temporal warns that retries inside Activities lengthen timeout needs and obscure metrics/UI debugging. [Activity retry warning](https://docs.temporal.io/activity-definition#activity-retry-policy)

Also avoid an `AfterModelCallEvent` hook that sets `event.retry=True` under `TemporalAgent`: it creates a Strands-level retry loop around already retried model Activities, despite `retry_strategy=None`.

### Tool and MCP calls

Temporal Activities have a default Retry Policy if none is supplied: initial interval 1s, coefficient 2, maximum interval 100s, unlimited attempts. Set explicit `RetryPolicy` where unbounded retry is unsuitable. `maximum_attempts=1` disables Temporal retry. [Retry policy](https://docs.temporal.io/encyclopedia/retry-policies)

Do not also set `AfterToolCallEvent.retry=True` for an Activity-backed tool unless deliberately accepting a second retry layer. Strands re-executes the tool with the same `toolUseId`; discarded attempts' streaming events may already have escaped, while only the final result reaches conversation history. [Strands tool-call retry](https://strandsagents.com/docs/user-guide/concepts/agents/hooks/index.md#tool-call-retry)

Activity code must be idempotent because it can execute more than once, including the completion-acknowledgment race. Completed Activities are observed once by the Workflow, but the body is at-least-once. Use downstream idempotency support keyed by stable execution identity where effects require exactly-once business behavior. [Temporal idempotency](https://docs.temporal.io/activity-definition#idempotency)

### Important MCP exception

Current source catches exceptions from MCP `session.call_tool()` and converts them to Strands error `ToolResult`s instead of raising; those returned errors count as successful Activity completion and therefore do **not** trigger Temporal retries. MCP connection/list failures can raise and retry, but call-tool failures mapped to results require a new agent/tool decision (or a Strands hook retry, which is a separate layer). [`build_call_tool_activity`](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_temporal_mcp_client.py)

## Heartbeats and cancellation

- Configure `heartbeat_timeout` and call `activity.heartbeat(details...)` for long-running user Activities. Cancellation is delivered to a non-local Activity when it heartbeats; without heartbeat, cancellation is not received. [`activity.heartbeat`](https://docs.temporal.io/develop/python/activities/timeouts#heartbeat-an-activity) · [cancellation](https://docs.temporal.io/develop/python/workflows/cancellation#cancel-an-activity-from-a-workflow)
- `activity_as_tool` exposes `cancellation_type`, defaulting to `ActivityCancellationType.TRY_CANCEL`.
- Integration model Activities use internal `@auto_heartbeater`, heartbeating at half the configured heartbeat timeout. No configured heartbeat timeout means no heartbeat task. [`auto_heartbeater`](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_heartbeat_decorator.py)
- Generated MCP Activities are not decorated with that auto-heartbeater in current source. Setting `heartbeat_timeout` alone does not cause them to heartbeat, so long/stuck MCP calls do not gain timely cancellation merely from the option. This is a source-observed limitation.

## Streaming and persistence details

Strands `@tool` mechanics: the decorator extracts schema from type hints/docstrings, validates with Pydantic, injects optional `ToolContext`, invokes sync functions using `asyncio.to_thread`, awaits coroutine functions, and consumes async generators into `ToolStreamEvent`s; its final yielded value becomes the result. Exceptions normally become error `ToolResultEvent`s rather than escaping. [`@tool` source](https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/tools/decorator.py) · [custom tools](https://strandsagents.com/docs/user-guide/concepts/tools/custom-tools/)

This matters in Workflows: a sync plain `@tool` uses a thread, while Temporal Workflow logic forbids threading. Thus even a mathematically pure synchronous `@tool` may conflict with the sandbox through Strands' invocation mechanism; prefer an async deterministic `@tool` for inline Workflow execution. This conclusion follows directly from the current Strands decorator and Temporal constraint, but the Temporal integration guide's pure `letter_counter` sample does not discuss the thread path—see contradiction C2 below.

For Activity tools, `TemporalActivityTool.stream()` awaits one recorded Activity result and emits one final success `ToolResultEvent`. It does not preserve native JSON content blocks: non-string results are JSON-encoded to text when possible. [`TemporalActivityTool` source](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_temporal_activity_tool.py)

## Unresolved contradictions and version-sensitive findings

| ID | Evidence conflict | Reconciliation / status |
|---|---|---|
| C1 | The current Temporal guide intro says the plugin routes “tool calls” through Activities, but its tools section says nondeterministic tools require `@activity.defn` + `activity_as_tool`; source shows plain tools execute directly. | Treat the intro as shorthand for supported activity-backed tools, not automatic conversion. Source and concrete API are decisive. Confidence: High. |
| C2 | Temporal guide sample includes a plain `letter_counter`, implying pure tools may run inline. Current Strands `DecoratedFunctionTool.stream()` invokes synchronous functions with `asyncio.to_thread`, while Temporal says Workflow logic allows no threading and the integration warns synchronous `agent(message)` threads are sandbox-blocked. | Pure **async** inline tools are the defensible case. Whether current sandbox/integration special-cases sync decorated tools was not established from official material. Test/replay verification remains unresolved. Confidence: Medium. |
| C3 | Rendered Temporal integration docs fetched on 2026-07-30 say MCP connects at Worker startup, freezes schema for Worker lifetime, and fails startup if unavailable. Current SDK main source and README say lazy worker-process connection plus list-tools Activity, default re-list every turn, optional `cache_tools=True`, idle eviction. | Clear documentation/source drift. For an installed version, inspect its API/source. This report targets current `main` source plus docs requiring `temporalio>=1.28.0`; exact released patch behavior is unresolved. Confidence: High that drift exists. |
| C4 | Integration docs say hooks are routed through Activities in the broad intro, while detailed docs/source say ordinary callbacks execute in Workflow context and only `activity_as_hook` dispatches an Activity. | Detailed docs/source win: no automatic conversion of arbitrary hooks. Confidence: High. |
| C5 | Strands docs promote arbitrary objects (DB connections/loggers) in invocation state; `TemporalModel` filters state through `json.dumps` and drops nonserializable entries before the model Activity. | Such objects can exist only in deterministic Workflow-side callbacks/tools—which themselves cannot safely perform DB/I/O—and do not cross into the model Activity. TemporalAgent narrows ordinary Strands behavior. Confidence: High. |
| C6 | Strands supports dynamic meta-tools/hot reload, but Temporal replay requires identical tool registry/schema decisions. No general Temporal durable dynamic-tool API is documented. | Only the integration's `TemporalMCPClient` dynamic relisting has an explicit recorded Activity path. General runtime-loaded tools remain unsupported/unsafe unless behavior is deterministically versioned; no official guarantee found. Confidence: Medium. |

## Evidence index

- Temporal Strands integration (Public Preview, requires `temporalio` 1.28.0+): https://docs.temporal.io/develop/python/integrations/strands-agents
- Temporal integration source/README: https://github.com/temporalio/sdk-python/tree/main/temporalio/contrib/strands
- Temporal Workflow determinism: https://docs.temporal.io/develop/python/workflows/basics#develop-workflow-logic
- Temporal Activity definition/idempotency: https://docs.temporal.io/activity-definition
- Python Activity basics/execution/timeouts: https://docs.temporal.io/develop/python/activities/basics · https://docs.temporal.io/develop/python/activities/execution · https://docs.temporal.io/develop/python/activities/timeouts
- Temporal retries: https://docs.temporal.io/encyclopedia/retry-policies
- Temporal cancellation: https://docs.temporal.io/develop/python/workflows/cancellation
- Strands custom tools/tool streaming: https://strandsagents.com/docs/user-guide/concepts/tools/custom-tools/
- Strands hooks/tool retries: https://strandsagents.com/docs/user-guide/concepts/agents/hooks/
- Strands model retries: https://strandsagents.com/docs/user-guide/concepts/agents/retry-strategies/
- Strands interrupts: https://strandsagents.com/docs/user-guide/concepts/interrupts/
- Strands agents-as-tools: https://strandsagents.com/docs/user-guide/concepts/multi-agent/agents-as-tools/
- Strands dynamic meta-tooling: https://strandsagents.com/docs/examples/python/meta_tooling/
- Temporal llms.txt: https://docs.temporal.io/llms.txt
- Strands llms.txt: https://strandsagents.com/llms.txt
