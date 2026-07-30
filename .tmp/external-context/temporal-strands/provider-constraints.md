---
source: Official Temporal documentation and SDK source
library: Temporal Python SDK Strands integration
package: temporalio
topic: Custom model execution, retries, serialization, and replay
tech_stack: Strands Agents Python with Perplexity Agent API
fetched: 2026-07-30T00:00:00Z
official_docs: https://docs.temporal.io/develop/python/integrations/strands-agents
---

## Runtime placement

- Requires `temporalio` 1.28.0 or later and is currently Public Preview.
- Register providers as `StrandsPlugin(models={name: factory})` on the Worker.
- Pass the identical name to `TemporalAgent(model=name, ...)` in Workflow code.
- Factories execute lazily on first Activity use; the resulting model object is cached for the Worker lifetime.
- Never construct or pass a live provider client/model into Workflow state.

## Retries and failures

`TemporalAgent` disables Strands `ModelRetryStrategy`; passing a non-`None` `retry_strategy` raises `ValueError`. Configure model retries with Temporal `retry_policy` and Activity timeouts. Therefore, a Perplexity provider should classify/raise errors but should not independently retry requests, or retries can multiply and become nondeterministic operationally.

## Serialization and streaming

- `TemporalModel.stream()` executes the registered worker-side provider as an Activity and returns its native Strands `StreamEvent` dictionaries.
- The Activity input fields for messages/tools are intentionally typed `Any` because `NotRequired` TypedDict fields caused converter issues on Python <3.11; values pass through unchanged.
- `invocation_state` is filtered before Activity execution: entries that fail `json.dumps()` are silently dropped with a debug log.
- Non-streaming mode buffers all events into an Activity result list. Streaming mode still returns the full list but additionally publishes each event to a Workflow Stream topic.
- Workflow replay consumes recorded Activity results; it does not repeat a completed external model request. A failed Activity may be retried according to Temporal policy.
- `TemporalModel.structured_output()` is unsupported; use `TemporalAgent(structured_output_model=...)`, which routes through `stream()` and a structured-output tool.

## Operational consequence

All emitted events, message content, tool inputs/results, usage, signatures, and additional response fields must remain converter-safe. Prefer JSON-native strings/numbers/lists/dicts; do not place HTTP clients, exceptions, bytes (except where the configured converter explicitly supports the Strands type), or arbitrary provider objects into events.

Sources:
- https://docs.temporal.io/develop/python/integrations/strands-agents
- https://raw.githubusercontent.com/temporalio/sdk-python/main/temporalio/contrib/strands/_temporal_model.py
- https://raw.githubusercontent.com/temporalio/sdk-python/main/temporalio/contrib/strands/_model_activity.py
