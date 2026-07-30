---
source: Official Strands Agents source
library: Strands Agents SDK (Python)
package: strands-agents
topic: Custom Model provider interface and native stream events
tech_stack: Temporal Python SDK Strands integration
fetched: 2026-07-30T00:00:00Z
official_docs: https://github.com/strands-agents/harness-sdk/tree/main/strands-py
---

## Required `Model` interface

Current source defines these abstract methods:

```python
def update_config(self, **model_config: Any) -> None: ...
def get_config(self) -> Any: ...
def structured_output(
    self, output_model: type[T], prompt: Messages,
    system_prompt: str | None = None, **kwargs: Any
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

`stateful` defaults to `False`. A provider that keeps conversation state server-side may override it. `count_tokens()` has a built-in heuristic and need not be overridden.

## Native event lifecycle

Emit Strands `StreamEvent` dictionaries, not provider-native SSE objects:

1. `{"messageStart": {"role": "assistant"}}`
2. For text, emit `contentBlockDelta.delta.text` events.
3. For a tool call, emit `contentBlockStart.start.toolUse` with `name`, `toolUseId`, and optional `reasoningSignature`; stream JSON argument fragments via `contentBlockDelta.delta.toolUse.input`; then emit `contentBlockStop`.
4. Emit `messageStop.stopReason` (`end_turn`, `tool_use`, `max_tokens`, etc.).
5. Emit `metadata` with `usage` and `metrics`.

Reasoning deltas use `contentBlockDelta.delta.reasoningContent` with optional `text`, `signature`, and `redactedContent`. Persisted reasoning uses `ContentBlock.reasoningContent.reasoningText.{text,signature}`.

## Content and tools

- Messages contain `role` (`user` or `assistant`) and a list of content blocks.
- Relevant blocks: `text`, `toolUse`, `toolResult`, and `reasoningContent`.
- `ToolSpec` maps `name`, `description`, and `inputSchema`; `outputSchema` is optional and should be removed for providers that do not support it.
- `ToolUse` requires `toolUseId`, `name`, and JSON-serializable `input`; optional `reasoningSignature` ties thinking to the call.
- `ToolResult` requires matching `toolUseId`, status `success` or `error`, and content items containing text or JSON.
- Tool choices are Strands shapes `{"auto": {}}`, `{"any": {}}`, or `{"tool": {"name": "..."}}`; a provider must reject or explicitly map unsupported choices.

## Usage and stop reasons

`metadata.usage` requires `inputTokens`, `outputTokens`, and `totalTokens`; cache token fields are optional. Common provider mapping is normal completion → `end_turn`, function call → `tool_use`, and output token exhaustion → `max_tokens`.

Sources:
- https://raw.githubusercontent.com/strands-agents/harness-sdk/main/strands-py/src/strands/models/model.py
- https://raw.githubusercontent.com/strands-agents/harness-sdk/main/strands-py/src/strands/types/streaming.py
- https://raw.githubusercontent.com/strands-agents/harness-sdk/main/strands-py/src/strands/types/content.py
- https://raw.githubusercontent.com/strands-agents/harness-sdk/main/strands-py/src/strands/types/tools.py
- https://raw.githubusercontent.com/strands-agents/harness-sdk/main/strands-py/src/strands/types/event_loop.py
