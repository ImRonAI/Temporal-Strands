---
source: Official Perplexity Agent API documentation and official Python SDK source
library: Perplexity Agent API and Python SDK
package: perplexityai
topic: Async Responses creation, tools, function replay, SSE fields, completion, failure, and exceptions
tech_stack: Async Python custom Strands Model provider
fetched: 2026-07-30T00:00:00Z
official_docs: https://docs.perplexity.ai/api-reference/responses-post
---

# Documented facts

The Agent API endpoint is `POST https://api.perplexity.ai/v1/agent`; `/v1/responses` is an accepted OpenAI-compatible alias. The official SDK is installed as `perplexityai`, imported with `from perplexity import AsyncPerplexity`, and async calls use:

```python
from perplexity import AsyncPerplexity

client = AsyncPerplexity()
stream = await client.responses.create(
    model="provider/model",
    input=[{"type": "message", "role": "user", "content": "..."}],
    tools=[{"type": "web_search"}],
    stream=True,
)
async for event in stream:
    ...
```

`input` is required and is either a string or iterable of input items. Shared SDK request fields include `background`, `instructions`, `language_preference`, `max_output_tokens`, `max_steps`, `model`, `models`, `preset`, `previous_response_id`, `reasoning`, `response_format`, `skills`, `store`, and `tools`; streaming overload requires `stream=True`. The SDK method also accepts standard request options such as extra headers/query/body and timeout. Anthropic models require `max_output_tokens`.

## Tools

Perplexity-hosted request tools are selected in `tools` by type: `web_search`, `fetch_url`, `finance_search`, `people_search`, and `sandbox`. Remote MCP uses `type: "mcp"`. A custom function is:

```json
{
  "type": "function",
  "name": "get_order_status",
  "description": "...",
  "parameters": {"type": "object", "properties": {}, "required": []},
  "strict": true
}
```

The server executes built-in/MCP tools in its loop. It does not execute custom function code.

## Message and function round trip

Input messages require `type: "message"`, `role` (`user`, `assistant`, `system`, or `developer`), and `content` (string or input parts).

A returned function item has required `type: "function_call"`, `id`, `status`, `name`, `call_id`, and `arguments` (a JSON string), plus optional `thought_signature` (opaque Base64). Continue by replaying the original message and call, then adding:

```json
{
  "type": "function_call_output",
  "call_id": "same call_id",
  "output": "JSON string result"
}
```

`function_call_output` can also carry optional `name` (required by some providers) and `thought_signature`. Preserve and replay a returned `thought_signature` unchanged; do not decode or synthesize it.

## SSE wire fields

Every documented event has `type` and monotonically increasing `sequence_number`.

- `response.created`, `response.in_progress`: optional `response`.
- `response.output_item.added` / `.done`: `item`, `output_index`.
- `response.output_text.delta`: `item_id`, `output_index`, `content_index`, `delta`.
- `response.output_text.done`: same indices plus final `text`.
- `response.completed`: optional full `response`.
- `response.failed`: required `error` with required `message` and optional `code`/`type`.
- Reasoning/search telemetry events: `response.reasoning.started`, `.search_queries`, `.search_results`, `.fetch_url_queries`, `.fetch_url_results`, `.stopped`; these may have `thought`, but are agent search telemetry and are not documented as a private chain-of-thought signature stream.

The current official OpenAPI and Python `ResponseStreamChunk` union do **not** define a dedicated `response.function_call_arguments.delta` event. Function calls arrive as `function_call` items in `response.output_item.added`/`.done`; `arguments` is the JSON-string field on that item. Therefore code must not assume an undocumented argument-delta event. The authoritative final function arguments are on the completed item/final response.

## Final response and failure

`response.completed.response` carries `id`, `created_at`, `model`, `object: "response"`, `output`, `status`, optional `error`, and optional `usage`. Current SDK status literals include `completed`, `failed`, `in_progress`, `queued`, `cancelled`, and `requires_action` (the API reference also describes `incomplete` in its schema, an observed documentation/source mismatch).

Usage requires `input_tokens`, `output_tokens`, `total_tokens`; optional details include cache token fields, tool-call counts, and cost. SDK `output_text` concatenates every `output_text` content part from message output items, returning `""` when none exist.

An SSE `response.failed` is a terminal in-band event and contains `error`; it is distinct from an HTTP failure raised by the SDK.

# Official Python SDK source observations

`AsyncPerplexity` has the same resource interface as `Perplexity`, except calls are awaited and streams are consumed with `async for`. With `stream=True`, `responses.create` returns an async stream of the discriminated `ResponseStreamChunk` Pydantic union.

The generated SDK currently types native tools as unions for web search, fetch URL, people search, finance search, sandbox, MCP, and function tools. Its generated stream union confirms there is no dedicated function-argument delta class.

Exception hierarchy and mappings:

- All SDK errors inherit `perplexity.APIError` (itself under `PerplexityError`).
- Connection failures: `APIConnectionError`; timeout: `APITimeoutError`.
- HTTP failures: `APIStatusError`, exposing `status_code` and `response`.
- 400 `BadRequestError`; 401 `AuthenticationError`; 403 `PermissionDeniedError`; 404 `NotFoundError`; 409 `ConflictError`; 422 `UnprocessableEntityError`; 429 `RateLimitError`; 5xx `InternalServerError`.
- `APIResponseValidationError` indicates returned data did not match the expected schema.

SDK transport exceptions cover connection/HTTP failures. A successfully opened SSE that later emits `response.failed` must be inspected and raised by provider integration code if it wants exception semantics; it is represented as an event model, not automatically as an HTTP `APIStatusError`.

# Sources

- https://docs.perplexity.ai/docs/agent-api/quickstart
- https://docs.perplexity.ai/api-reference/responses-post
- https://docs.perplexity.ai/docs/agent-api/tools/overview
- https://docs.perplexity.ai/docs/agent-api/tools/custom-functions
- https://docs.perplexity.ai/docs/sdk/overview
- https://github.com/perplexityai/perplexity-py
- https://github.com/perplexityai/perplexity-py/blob/main/src/perplexity/types/response_create_params.py
- https://github.com/perplexityai/perplexity-py/blob/main/src/perplexity/types/input_item_param.py
- https://github.com/perplexityai/perplexity-py/blob/main/src/perplexity/types/response_stream_chunk.py
- https://github.com/perplexityai/perplexity-py/blob/main/src/perplexity/types/response_create_response.py
- https://github.com/perplexityai/perplexity-py/blob/main/src/perplexity/_exceptions.py
