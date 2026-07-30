---
source: Context7 API (official Temporal SDK and documentation indexes)
library: Temporal Python SDK
package: temporalio
topic: Production long-horizon chat lifecycle, messaging, reliability, streams, and fairness
tech_stack: Python Temporal workflows with Strands Agents
fetched: 2026-07-30T00:00:00Z
official_docs: https://docs.temporal.io/develop/python/integrations/strands-agents
---

# Relevant evidence

## Long sessions and Continue-As-New

- The official Strands chat example waits until either chat completion or `workflow.info().is_continue_as_new_suggested()`.
- Before rollover, it waits on `workflow.all_handlers_finished` and then invokes `workflow.continue_as_new(...)` from the main Workflow method, carrying `TemporalAgent.messages` into the new run.
- Continue-As-New is not supported from an Update handler. Signals and Updates should finish before the main Workflow method rolls over.
- Temporal documents hard Workflow history limits of 51,200 events or 50 MB, with warnings at 10,240 events or 10 MB. Prefer the server suggestion to an invented fixed turn threshold.

```python
await workflow.wait_condition(
    lambda: self._done or workflow.info().is_continue_as_new_suggested()
)
await workflow.wait_condition(workflow.all_handlers_finished)
if not self._done:
    workflow.continue_as_new(ChatInput(messages=self._agent.messages))
```

## Concurrent chat messages

- The official chat example implements a turn as `@workflow.update`, waits until the agent exists, and serializes agent invocation with `asyncio.Lock`.
- A Signal marks session termination; a Query returns a copy of current messages.
- `workflow.all_handlers_finished` guards against losing in-flight Signal or Update work at completion or rollover.

## Model and tool reliability

- `TemporalAgent` disables Strands `ModelRetryStrategy`; passing `retry_strategy` raises `ValueError`. Configure Temporal `retry_policy` on the agent and independently on activity tools, hooks, and MCP operations.
- Activity cancellation is delivered through heartbeats. A cancellable long-running activity needs a `heartbeat_timeout`, must heartbeat, and should handle `asyncio.CancelledError`.
- Define Heartbeat Timeout shorter than Start-To-Close. Without it, worker loss may remain undetected until Start-To-Close expires. On retry, heartbeat details can carry progress checkpoints.

## Deployment safety and tests

- Use `temporalio.worker.Replayer` with exported production histories to detect nondeterminism before deployment.
- `WorkflowEnvironment.start_local` plus a Worker supports end-to-end tests.
- Worker Versioning does not remove the need to preserve or test deterministic compatibility for histories handled by a deployment.

## Streaming

- Workflow Streams are Public Preview, not generally available. Treat them separately from the experimental Strands integration.
- `WorkflowStream.continue_as_new(...)` carries stream state into the next run and lets subscribers follow rollover without gaps.
- For manual rollover, detach stream pollers, wait for all handlers, then pass `stream.get_state()` into `workflow.continue_as_new(...)`.
- `TemporalAgent(streaming_topic=..., streaming_batch_interval=...)` exposes model-stream publication, but fetched excerpts do not establish the full client subscription protocol.

## Multi-tenant scheduling

- Python workflow start options accept `Priority(priority_key=..., fairness_key=..., fairness_weight=...)`.
- A tenant identifier can be the fairness key; fairness weight controls relative allocation. This is not application-level admission control or a complete noisy-neighbor strategy.
- Task queues remain the routing/isolation boundary. Fairness alone does not establish per-tenant quotas or concurrency limits.

## Status matrix

| Capability | Status |
|---|---|
| Temporal core workflows, Updates, Signals, Queries, Activities, Continue-As-New, replay | Supported |
| `temporalio.contrib.strands` | Experimental |
| Workflow Streams | Public Preview |
| Strands snapshots on `TemporalAgent` | Unsupported; methods raise `NotImplementedError` |
| Strands model retry strategy on `TemporalAgent` | Unsupported; use Temporal retries |
| Continue-As-New from an Update handler | Unsupported |
| Complete native Temporal approval protocol for Strands interrupts | Not established; application-owned |

# Primary sources

- https://docs.temporal.io/develop/python/integrations/strands-agents
- https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
- https://docs.temporal.io/develop/python/workflows/continue-as-new
- https://docs.temporal.io/develop/python/workflows/message-passing
- https://docs.temporal.io/develop/python/workflows/workflow-streams
- https://docs.temporal.io/develop/python/cancellation
- https://docs.temporal.io/develop/python/testing-suite
- https://docs.temporal.io/design-patterns/fairness
- https://python.temporal.io/temporalio.workflow.html
- https://python.temporal.io/temporalio.common.Priority.html
