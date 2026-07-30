---
source: Context7 API and official Strands Agents package source
library: Strands Agents SDK (Python)
package: strands-agents
topic: Custom Model provider signatures and StreamEvent wire dictionaries
tech_stack: Python custom Model provider for Perplexity Responses streaming
fetched: 2026-07-30T00:00:00Z
official_docs: https://strandsagents.com/latest/documentation/docs/user-guide/concepts/model-providers/custom_model_provider/
---

# Documented facts

The official custom-provider guide requires a `Model` subclass to implement configuration methods and an async `stream` that converts provider events into Strands `StreamEvent` dictionaries. Its examples use `messageStart`, `contentBlockStart`, `contentBlockDelta`, `contentBlockStop`, `messageStop`, and `metadata`.

Canonical tool lifecycle:

```python
{"messageStart": {"role": "assistant"}}
{"contentBlockStart": {"start": {"toolUse": {
    "name": "tool_name", "toolUseId": "call_id",
    "reasoningSignature": "optional opaque signature",
}}}}
{"contentBlockDelta": {"delta": {"toolUse": {"input": "partial JSON string"}}}}
{"contentBlockStop": {}}
{"messageStop": {"stopReason": "tool_use"}}
{"metadata": {
    "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
    "metrics": {"latencyMs": 0},
}}
```

Text uses `contentBlockDelta.delta.text`. Reasoning uses `contentBlockDelta.delta.reasoningContent`, whose optional members are `text: str | None`, `signature: str | None`, and `redactedContent: bytes | None`.

# Package-source observations (main branch fetched 2026-07-30)

The abstract class at `strands-py/src/strands/models/model.py` has these exact signatures:

```python
def update_config(self, **model_config: Any) -> None: ...
def get_config(self) -> Any: ...
def structured_output(
    self, output_model: type[T], prompt: Messages,
    system_prompt: str | None = None, **kwargs: Any,
) -> AsyncGenerator[dict[str, T | Any], None]: ...
def stream(
    self,
    messages: Messages,
    tool_specs: list[ToolSpec] | None = None,
    system_prompt: str | None = None,
    *,
    tool_choice: ToolChoice | None = None,
    system_prompt_content: list[SystemContentBlock] | None = None,
    invocation_state: dict[str, Any] | None = None,
    **kwargs: Any,
) -> AsyncIterable[StreamEvent]: ...
```

`structured_output` yields model events with the final event containing the validated structured value. An in-tree provider (`OpenAIModel`) emits only `{"output": parsed}` on success. `stateful` returns `False` by default. `count_tokens(...)` is concrete and uses a heuristic fallback, so it is not an abstract requirement.

`StreamEvent` is a `TypedDict(total=False)` with keys: `messageStart`, `contentBlockStart`, `contentBlockDelta`, `contentBlockStop`, `messageStop`, `metadata`, `redactContent`, `internalServerException`, `modelStreamErrorException`, `serviceUnavailableException`, `throttlingException`, and `validationException`.

Exact nested tool fields:

- `contentBlockStart`: optional `contentBlockIndex: int | None`; `start.toolUse` is `ContentBlockStartToolUse | None`.
- `ContentBlockStartToolUse`: required `name: str`, `toolUseId: str`; optional `reasoningSignature: str`.
- `contentBlockDelta`: optional `contentBlockIndex`; `delta.toolUse.input: str` is required when that shape is used, while `toolUseId` and `name` are optional on the delta.
- `contentBlockStop`: optional `contentBlockIndex`.
- Persisted `ToolUse` has `input`, `name`, `toolUseId`, and optional `reasoningSignature`.
- Persisted reasoning is `reasoningContent.reasoningText.{text, signature}` or `reasoningContent.redactedContent`.

`Usage` requires `inputTokens`, `outputTokens`, and `totalTokens`; optional cache fields are `cacheReadInputTokens` and `cacheWriteInputTokens`. `Metrics` requires `latencyMs` and optionally has `timeToFirstByteMs`.

Model stop reasons currently include `cancelled`, `checkpoint`, `content_filtered`, `end_turn`, `guardrail_intervened`, `interrupt`, `limit_output_tokens`, `limit_total_tokens`, `limit_turns`, `max_tokens`, `stop_sequence`, and `tool_use`.

Tool types: `ToolSpec` requires `name`, `description`, `inputSchema`, with optional `outputSchema`; `ToolChoice` is `{"auto": {}}`, `{"any": {}}`, or `{"tool": {"name": str}}`.

# Sources

- https://strandsagents.com/latest/documentation/docs/user-guide/concepts/model-providers/custom_model_provider/
- https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/models/model.py
- https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/types/streaming.py
- https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/types/content.py
- https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/types/tools.py
- https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/types/event_loop.py
- https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/models/openai.py
