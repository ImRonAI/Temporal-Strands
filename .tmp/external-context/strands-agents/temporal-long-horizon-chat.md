---
source: Context7 API and official Temporal Python API
library: Strands Agents SDK + Temporal Python SDK
package: strands-agents, temporalio
topic: Temporal long-horizon chat APIs and constraints
tech_stack: Python Temporal workflows with Strands Agents
fetched: 2026-07-30T00:00:00Z
official_docs: https://python.temporal.io/temporalio.contrib.strands.html
---

# Relevant API matrix

| Concern | Exact API / behavior | Temporal boundary or constraint | Evidence maturity |
|---|---|---|---|
| Worker integration | `StrandsPlugin(models: dict[str, Callable[[], Model]] | None = None, mcp_clients: dict[str, Callable[[], MCPClient]] | None = None, mcp_connection_idle_timeout: timedelta | None = None)` | Model and MCP factories are registered on the worker, created lazily, and cached for the worker lifetime. If `models` is omitted, `BedrockModel()` is registered as `"bedrock"`. | Public official API |
| Workflow agent | `TemporalAgent(*, model=None, task_queue=None, schedule_to_close_timeout=None, schedule_to_start_timeout=None, start_to_close_timeout=None, heartbeat_timeout=None, retry_policy=None, cancellation_type=TRY_CANCEL, versioning_intent=None, summary=None, priority=Priority.default, streaming_topic=None, streaming_batch_interval=timedelta(milliseconds=100), **agent_kwargs)` | `model` is a registered factory name. Model calls execute as Temporal activities. Other Strands `Agent` options are forwarded through `agent_kwargs`. | Public official API |
| Retries | `TemporalAgent(..., retry_policy=RetryPolicy(...))` | Strands `retry_strategy` is disabled. Configure Temporal activity retry policies on model calls and separately on activity-backed tools/hooks/MCP calls. | Public official API |
| Activity tool | `activity_as_tool(activity_fn, *, task_queue=None, ... retry_policy=None, ... priority=Priority.default) -> AgentTool` | `activity_fn` must use `@activity.defn`. The returned workflow-side tool calls `workflow.execute_activity`, so I/O runs outside the deterministic workflow. | Public official API |
| Activity hook | `activity_as_hook(activity_fn, *, activity_input: Callable[[TEvent], Any], task_queue=None, ... retry_policy=None, ... priority=Priority.default) -> HookCallback[TEvent]` | Hook events are not serializable. `activity_input(event)` must extract serializable input. The callback dispatches an activity for each matching event. | Public official API |
| Activity-tool streaming surface | `TemporalActivityTool.stream(tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs) -> ToolGenerator` | Despite the Strands streaming protocol, the documented implementation dispatches the bound Temporal activity. No evidence here establishes incremental activity-result chunks; do not assume them. | Public class; implementation detail exposed in API docs |
| Model streaming | `TemporalAgent(..., streaming_topic: str | None, streaming_batch_interval=100ms)` | Temporal integration provides explicit workflow-stream configuration for model output. Exact consumer protocol is outside the filtered Strands integration pages fetched here. | Public official API, incomplete consumer detail |
| Native Strands hooks | `agent.add_hook(callback)` or `agent.hooks.add_callback(EventType, callback)`; examples include `BeforeInvocationEvent`, `AfterInvocationEvent`, `BeforeToolCallEvent`, and `BeforeModelCallEvent` | In-workflow callbacks must remain deterministic. Use `activity_as_hook` when the callback needs external I/O. `AfterInvocationEvent.resume` can re-invoke the agent, so enforce a termination condition. | Documented Strands API |
| Invocation state | Arbitrary keyword args passed to `agent(...)` are available through `event.invocation_state` | May contain non-JSON-serializable process objects in ordinary Strands. Such objects cannot be passed as Temporal activity input; extract serializable values in `activity_input`. | Documented Strands behavior plus Temporal serialization constraint |
| Human approval / interrupts | In `BeforeToolCallEvent`, call `event.interrupt(name, reason=...)`; inspect the returned response and optionally set `event.cancel_tool` | Ordinary Strands docs pair interrupts with a session manager to resume later. The Temporal package includes Strands-specific failure conversion, but the fetched public integration docs do not specify a complete signal/update-based approval protocol. Treat end-to-end Temporal approval wiring as application-owned until separately verified. | Strands documented; Temporal behavior partially documented |
| Sessions | Example: `FileSessionManager(session_id=..., storage_dir=...)` passed as `session_manager=` | Useful outside Temporal for interrupt resumption. In `TemporalAgent`, Temporal event history is explicitly the source of truth, so adding an external session manager requires careful consistency design and is not established as required by the integration docs. | Strands documented; integration guidance inferred conservatively |
| Snapshots | Ordinary `Agent.take_snapshot(preset="session")`, `Agent.load_snapshot(snapshot)`, `Snapshot.to_dict()`, `Snapshot.from_dict(...)` | Both `TemporalAgent.take_snapshot()` and `load_snapshot()` are disabled because Temporal event history is the source of truth. Do not build Temporal durability around Strands snapshots. | Explicit public official API |
| Checkpoint examples | Strands docs describe taking snapshots after workflow steps and rolling back with `load_snapshot()` | This pattern does not apply to `TemporalAgent`, where snapshots are disabled. No separately supported TemporalAgent checkpoint API was established by the fetched docs. | Documented ordinary Agent pattern; unsupported on TemporalAgent |
| MCP | `TemporalMCPClient`; worker registration through `StrandsPlugin(mcp_clients=...)` | Plugin registers `{server}-call-tool` and `{server}-list-tools` activities. Tool discovery caching is controlled by the client's `cache_tools`; worker MCP connections default to a five-minute idle timeout. | Public official API |

# Key conclusions

1. Native integration exists in `temporalio.contrib.strands`; it is not merely an application wrapper.
2. Instantiate `TemporalAgent` in workflow code using a model factory **name** registered by `StrandsPlugin` on the worker. Do not pass a live model instance into workflow construction.
3. Model calls, activity-backed tools, activity-backed hooks, and Temporal MCP operations are the supported off-workflow execution boundaries.
4. Use Temporal retry policies, history, and workflow lifecycle as the durability authority. Strands retry strategy and snapshots are disabled on `TemporalAgent`.
5. A plain Strands `@tool` documents schema/tool adaptation but does not by itself prove Temporal activity execution. Use `activity_as_tool` for side-effecting work that must execute as a Temporal activity.
6. Hooks that only mutate deterministic in-memory workflow state may remain callbacks; hooks requiring I/O should be converted with `activity_as_hook` and a serializable `activity_input` projection.

# Remaining evidence gaps

- Full public inventory and exact fields of every hook event.
- Exact workflow-stream client consumption protocol for `streaming_topic`.
- A documented native Temporal signal/update recipe for Strands interrupts and approvals.
- Whether experimental Strands `Checkpoint` APIs have any supported role beyond ordinary-Agent snapshots; no supported `TemporalAgent` checkpoint surface was verified.
- Exact `GoalLoop` signatures and compatibility with `TemporalAgent` were not present in the relevant fetched excerpts.

# Sources

- Context7 Strands library: https://context7.com/api/v2/context?libraryId=%2Fstrands-agents%2Fharness-sdk
- Temporal Strands package: https://python.temporal.io/temporalio.contrib.strands.html
- `TemporalAgent`: https://python.temporal.io/temporalio.contrib.strands.TemporalAgent.html
- `StrandsPlugin`: https://python.temporal.io/temporalio.contrib.strands.StrandsPlugin.html
- Workflow helpers: https://python.temporal.io/temporalio.contrib.strands.workflow.html
- Activity tool: https://python.temporal.io/temporalio.contrib.strands._temporal_activity_tool.TemporalActivityTool.html
- Strands documentation: https://strandsagents.com/latest/
