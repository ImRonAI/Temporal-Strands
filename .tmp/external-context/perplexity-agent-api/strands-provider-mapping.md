---
source: Official Perplexity Agent API documentation
library: Perplexity Agent API
package: perplexity-agent-api
topic: Direct POST /v1/agent mapping to Strands Model
tech_stack: Strands Agents Python with Temporal activities
fetched: 2026-07-30T00:00:00Z
official_docs: https://docs.perplexity.ai/api-reference/agent-post
---

## Request mapping

- Endpoint: `POST https://api.perplexity.ai/v1/agent`; set `stream: true` for SSE.
- Model IDs use `provider/model`. Discover live IDs with `GET /v1/models`; do not maintain a hardcoded list.
- Strands system prompt → `instructions`.
- Strands message text → Agent API message items.
- Strands `ToolSpec` → `{type:"function", name, description, parameters: inputSchema}`.
- `max_output_tokens` is optional generally but required if any Anthropic model appears in the selected model or fallback chain.
- The API exposes no documented equivalent for all Strands tool choices; unsupported `any`/forced-tool behavior must not be silently claimed.

## Tool round trip

Agent output represents a custom call as `function_call` with `id`, `call_id`, `name`, JSON-string `arguments`, and optional `thought_signature`.

Map it to a Strands tool block as follows:

- `call_id` → `toolUseId`
- `name` → `name`
- parsed `arguments` → `input`
- `thought_signature` → `reasoningSignature`

On the following request, replay the `function_call` and send a `function_call_output` using the same `call_id`. The OpenAPI permits `thought_signature` on both records; preserve the opaque Base64 string unchanged when returned, rather than decoding or synthesizing it. Some providers also require the function `name` on output.

## SSE mapping

- `response.created` / `response.in_progress`: lifecycle only; initialize Strands message state once.
- `response.output_text.delta.delta` → `contentBlockDelta.delta.text`.
- `response.output_item.added` or `.done` containing `function_call` → Strands tool-use start/arguments/stop events.
- `response.completed.response` contains authoritative final output, model, status, and usage. Emit Strands `messageStop`, then `metadata`.
- `response.failed.error` is a terminal provider error; do not emit a successful `messageStop`.
- Reasoning search/fetch SSE events are not equivalent to private chain-of-thought text. Do not fabricate Strands reasoning blocks from search telemetry alone.

## Usage, completion, and errors

Map final usage:

- `input_tokens` → `inputTokens`
- `output_tokens` → `outputTokens`
- `total_tokens` → `totalTokens`
- cache read/creation fields may map to Strands optional cache token fields when semantically appropriate.

Status `completed` with a function call maps to `tool_use`; normal completed text maps to `end_turn`; incomplete because of output limit maps to `max_tokens` only when the response identifies that cause. HTTP errors and `response.failed` should become provider exceptions so Temporal can apply its Activity retry policy.

## Conversation handling

Manual replay is the safest match for Strands `Messages` and durable Temporal history. `previous_response_id` is server-side state and requires a completed response from the same account; unresolved/running/failed IDs produce HTTP 400. `store:false` hides retrieval but still permits continuation, so it is not a no-persistence switch.

Sources:
- https://docs.perplexity.ai/api-reference/agent-post
- https://docs.perplexity.ai/docs/agent-api/tools/custom-functions
- https://docs.perplexity.ai/docs/agent-api/conversation-state
- https://docs.perplexity.ai/docs/agent-api/models
