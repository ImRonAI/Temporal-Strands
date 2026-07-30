---
source: Context7 API and official documentation
library: Strands Agents + Temporal Python SDK
package: strands-agents; temporalio[strands-agents]
topic: Native multi-agent features and Temporal compatibility
tech_stack: Python, Temporal Workflows, Strands Agents
fetched: 2026-07-30T00:00:00Z
official_docs: https://docs.temporal.io/develop/python/integrations/strands-agents
---

# Evidence matrix

| Feature | Native Strands API/evidence | Official Temporal integration evidence | Compatibility conclusion |
|---|---|---|---|
| Single agent | `Agent`; async call is `await agent.invoke_async(...)`. | `TemporalAgent(Agent)` and `StrandsPlugin`; inside a Workflow use `await agent.invoke_async(...)`, not synchronous `agent(...)`. Model invocations run as Temporal Activities. | **Explicitly supported.** |
| Agent as tool / nested agent | Pass an `Agent` directly in `tools`, or call `agent.as_tool(name=..., description=..., preserve_context=False)`. Context resets by default; `preserve_context=True` retains it. | No agent-as-tool or nested-agent example/support statement appears in the official Temporal integration guide or plugin README. The plugin explicitly routes model and registered tool calls, but that does not establish that a nested ordinary `Agent` is sandbox-safe or independently durable. | **Strands-native; Temporal compatibility undocumented. Do not treat the child as a native Temporal Child Workflow.** |
| Swarm | `from strands.multiagent import Swarm`; autonomous `handoff_to_agent(...)`, shared context, and `stream_async(...)`. Events include node start/stream, handoff, and result. | No `Swarm` mention or sample in the official Temporal integration guide/README. | **Strands-native; Temporal support not established.** No evidence that handoffs become Child Workflows or separately durable orchestration nodes. |
| Graph | `from strands.multiagent import GraphBuilder`; nodes can be agents or nested multi-agent systems; supports DAG/cyclic execution, conditional edges, parallel-ready nodes, timeout, sync call, and `invoke_async`. | No `GraphBuilder`, graph, or multi-agent sample in the official Temporal integration guide/README. | **Strands-native; Temporal support not established.** A graph running in one Workflow would not thereby make graph nodes Temporal Child Workflows/Activities. |
| Handoffs | Swarm exposes model-driven `handoff_to_agent(agent_name, message, context)` and emits `multiagent_handoff`. | No dedicated handoff API in the Temporal plugin docs. | **Only evidenced as a Swarm primitive; no native Temporal mapping documented.** |
| Parallel tool calls | Default executor is `ConcurrentToolExecutor`; `SequentialToolExecutor` is opt-in. Concurrent execution covers multiple tool calls returned in one model turn. | The plugin routes tool calls through Activities and exposes Activity options, but docs do not specify the scheduling/determinism semantics of Strands concurrent tool execution in a Workflow. | **Native Strands behavior; exact Temporal compatibility is undocumented.** Prefer only documented `activity_as_tool` boundaries when durability is required. |
| `batch` | Community package tool `strands_tools.batch`; invokes multiple named tools simultaneously. It is not a core `strands.multiagent` primitive. | Official Temporal docs say imported `strands_tools` must be wrapped in a thin async `@activity.defn` and supplied through `activity_as_tool`. No direct `batch` support is documented. | **Not directly supported/documented.** Wrapping `batch` as one Activity makes the batch one Activity boundary, not each inner operation a native Temporal Activity. |
| Streaming | `Agent.stream_async(...)`; multi-agent systems expose stream events such as node stream/handoff/result. Nested custom async-generator tools can surface `tool_stream_event`. | `TemporalAgent(streaming_topic="...")` publishes model `StreamEvent` chunks through `WorkflowStream`; default batching interval is 100 ms. | **Model streaming explicitly supported. Multi-agent events and nested tool-stream forwarding are not documented as supported.** |
| Skills | `from strands import AgentSkills, Skill`; install with `Agent(plugins=[AgentSkills(...)])`. Activation tool is named **`skills`**, called as `skills(skill_name="...")`. Activated names are tracked in `agent.state` under default key `agent_skills`. | No `AgentSkills`, `Skill`, or skills example/support statement in Temporal’s official integration docs. Plugin hooks execute in Workflow context and must be deterministic; filesystem/HTTPS skill loading may involve nondeterminism not covered by the integration. | **Temporal compatibility undocumented.** Exact documented names are `AgentSkills`, `Skill`, and tool `skills`; **`use_skill` is not the documented API**. |
| `user_agent` | No `user_agent` API appears in the current official documentation index or retrieved relevant API sections. | No `user_agent` symbol appears in official Temporal integration docs. | **Absent/undocumented under that exact spelling.** |
| State and sessions | Agents have messages/state; Strands session managers and snapshots provide persistence. Multi-agent persistence is documented for Graph/Swarm in current SDK material. | Workflow history durably persists in-Workflow state. `TemporalAgent.take_snapshot()` and `.load_snapshot()` raise `NotImplementedError`. Long chats should carry `agent.messages` through Continue-as-New. | **Temporal history replaces snapshots for `TemporalAgent`; do not combine by assuming Strands snapshots work. Multi-agent session-manager compatibility is undocumented.** |
| Interrupts / HITL | Agent and nested graph interrupts return interrupted results and resume with `interruptResponse` content. | Hook- and tool-body interrupts are explicitly supported. An invocation returns `AgentResult(stop_reason="interrupt", interrupts=[...])`; resume with `invoke_async(responses)`. Activity-tool interrupts require `StrandsPlugin` on the **client** so its failure converter is installed. | **Explicitly supported for `TemporalAgent`; multi-agent interrupt propagation is undocumented.** |
| Cancellation | Strands documents cancellation in tool executors and cooperative invocation cancellation in current SDK material. | Activity options include Temporal cancellation behavior, but the integration guide does not map Strands multi-agent/node cancellation events to Temporal cancellation. | **Activity-level Temporal cancellation exists; multi-agent cancellation mapping is undocumented.** |
| Failures and retries | Multi-agent status includes `PENDING`, `EXECUTING`, `COMPLETED`, `FAILED`, `INTERRUPTED`; results/events report node outcomes. | `TemporalAgent` disables Strands `ModelRetryStrategy`. Passing `retry_strategy` raises `ValueError`; configure Temporal `retry_policy` on model/tool/hook/MCP Activity options. | **Retries are explicitly Temporal-managed for supported boundaries. Multi-agent failure semantics are undocumented.** |
| MCP | Native Strands `MCPClient`. | `TemporalMCPClient(server=...)` plus `StrandsPlugin(mcp_clients={name: factory})`; calls and tool listing run through Activities. | **Explicitly supported.** Current SDK README says tools are re-listed each turn by default and `cache_tools=True` lists once. See contradiction below. |

## Durable-boundary distinction

- The supported unit is a **Temporal Workflow containing `TemporalAgent`**.
- The plugin records/runs model calls, `activity_as_tool` tools, `activity_as_hook` hooks, and `TemporalMCPClient` operations through Temporal Activities.
- Strands `Swarm`, Graph nodes, handoffs, nested agents, `batch`, and skills are not documented as Temporal Child Workflows or as independently durable orchestration units.
- Merely invoking a Strands-native orchestrator inside Workflow code does **not** prove that each Strands node/handoff/tool is a native Temporal Child Workflow or Activity.

## Confirmed limitations and contradictions

1. **Release label:** Temporal’s guide labels the integration **Public Preview**; the SDK README labels the package **experimental**. Treat both as non-GA signals.
2. **Version floor:** official Temporal guide requires `temporalio` **1.28.0 or later**.
3. **Snapshots:** explicitly unsupported on `TemporalAgent`; both snapshot methods raise `NotImplementedError`.
4. **Synchronous invocation:** unsupported in a Workflow sandbox because it starts a worker thread; use `invoke_async`.
5. **Retry strategy:** Strands `retry_strategy` is prohibited on `TemporalAgent`; use Temporal `retry_policy`.
6. **MCP schema behavior conflict:** the current SDK README says MCP tools are re-listed each turn unless `cache_tools=True`; the official Temporal web guide says tools are enumerated at Worker startup and frozen for the Worker lifetime. Verify behavior against the installed SDK version; the SDK README/source is the more implementation-proximate evidence.

## Open questions requiring source/tests or an explicit vendor statement

- Whether `Swarm` and `GraphBuilder` are Workflow-sandbox deterministic with `TemporalAgent` nodes.
- Whether nested ordinary `Agent` instances created by `Agent.as_tool()` inherit Temporal model routing or instead attempt direct model I/O.
- Whether default `ConcurrentToolExecutor` schedules Activity-backed tools safely and deterministically under replay.
- Whether Swarm/Graph stream and cancellation events can be forwarded through `WorkflowStream`.
- Whether `AgentSkills` initialization and runtime refresh are deterministic for filesystem or HTTPS sources.
- Whether any official compatibility tests cover Swarm, Graph, agents-as-tools, `batch`, or skills. None were cited by the retrieved official integration documentation.

## Primary sources

- Strands docs index: https://strandsagents.com/llms.txt
- Strands multi-agent overview: https://strandsagents.com/docs/user-guide/concepts/multi-agent/multi-agent-patterns/index.md
- Agents as tools: https://strandsagents.com/docs/user-guide/concepts/multi-agent/agents-as-tools/index.md
- Swarm: https://strandsagents.com/docs/user-guide/concepts/multi-agent/swarm/index.md
- Graph: https://strandsagents.com/docs/user-guide/concepts/multi-agent/graph/index.md
- Skills: https://strandsagents.com/docs/user-guide/concepts/plugins/skills/index.md
- Tool executors: https://strandsagents.com/docs/user-guide/concepts/tools/executors/index.md
- Temporal official guide: https://docs.temporal.io/develop/python/integrations/strands-agents
- Temporal plugin README/source: https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
