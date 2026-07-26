"""A native Strands Model provider for Perplexity's Agent API, built on
Perplexity's own official SDK (`perplexityai` on PyPI, `import perplexity`)
rather than the generic `openai` package with a baseURL override.

Perplexity's own guidance is explicit about this: "We recommend using the
Perplexity SDK for the best experience with full type safety, enhanced
features, and preset support... Use OpenAI SDKs if you're already
integrated and need drop-in compatibility." We weren't already integrated
with the OpenAI SDK for any other reason, so there's no reason to stay on
the fallback path.

This follows Strands' own documented pattern for a custom model provider
(https://strandsagents.com/ -> Custom Model Providers): subclass
strands.models.Model, implement stream()/update_config()/get_config().
Request-building (message/tool-spec formatting) is adapted from Strands'
own OpenAIResponsesModel, since Perplexity's Agent API is wire-compatible
with that request shape by design. The response side is NOT adapted from
it — it uses Perplexity's own richer, typed event stream
(perplexity.types.ResponseStreamChunk), which exposes real events the
OpenAI-compatible endpoint doesn't: response.skill.loaded (this is what
"skill_loaded": "pplx_sdk" actually is — a first-class, documented event,
not an undocumented quirk) and response.reasoning.* (search_queries,
search_results, fetch_url_queries, fetch_url_results) for real chain-of-
thought instead of the simulated one this app used before.

background=True is the baseline for every request: confirmed via the Agent
API reference that background runs are durable and, per `stream: true`,
still deliver real-time deltas ("Background runs are durable, so you can
also stream them and reconnect after a drop") — a strict resilience
upgrade over a plain synchronous call. store=True alongside it so a
dropped connection can be recovered via retrieve_agent_response rather
than losing the run.

max_steps is set to MAX_STEPS_CEILING (100, the Agent API's own documented
maximum — not a number chosen for this app) rather than left unset.
Tried unset first, with background=True: confirmed by direct, repeated
testing that it still fails a task needing the sandbox's internal search
path exactly as it did before background was added — background changes
durability, not the step budget. max_steps=100 completes the same task
correctly every time it was tested. The agent is not being held to some
conservative client-chosen number; it gets the maximum the platform allows.
"""

import base64
import json
import logging
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Optional, TypedDict, cast

import perplexity
from strands.models.model import Model
from temporalio.exceptions import ApplicationError
from strands.types.content import ContentBlock, Messages, Role
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolResult, ToolSpec, ToolUse
from typing_extensions import Unpack

# The Agent API's own documented ceiling (1-100) — the least restrictive
# value that can be set at all, used here because leaving max_steps unset
# reproduces the original bug (verified directly; see module docstring).
MAX_STEPS_CEILING = 100

# The Agent API's output ceiling. Anthropic models require max_output_tokens to
# be present at all, so it is always sent — but it is sent at the platform
# maximum, never at a number chosen by this client.
#
# This is the single source of truth for every path that talks to the Agent
# API: the per-turn model factories (run_worker.py), the agent's own
# create_agent_response tool (perplexity_tools.py), and the `think` tool's
# nested agent (thinking_activity.py). It was 2048 in one of those and absent
# from the other two, which starved reasoning models into
# "model produced no usable answer (reasoning_only)".
MAX_OUTPUT_TOKENS_CEILING = 128_000

logger = logging.getLogger(__name__)


def _ensure_object_properties(schema: Any) -> Any:
    """Give every JSON-Schema `object` node an explicit `properties` key.

    Perplexity's Agent API rejects the whole request — `invalid_request`,
    with no indication of which tool is at fault — if any tool parameter is
    typed `object` but carries no `properties` key. Verified by bisection
    against the live API: a bare `{"type": "object"}` fails, the identical
    schema with `"properties": {}` succeeds. Notably `additionalProperties:
    true` does NOT satisfy it; the `properties` key itself must be present.

    This bites any tool with a free-form mapping parameter. Strands' @tool
    decorator renders `dict[str, Any]` as exactly that bare object node, so
    the agent's `think` tool (whose `model_settings` param is deliberately
    free-form, matching the upstream strands-agents-tools signature) made
    every single turn fail before this normalization existed.

    Adding an empty `properties` is purely a wire-format fix and does not
    narrow anything: JSON Schema permits unlisted keys unless
    `additionalProperties: false` says otherwise, so the agent can still
    pass whatever the underlying tool accepts. Applied to every tool schema
    rather than special-casing one, so the next free-form parameter doesn't
    reintroduce the same silent failure.
    """
    if isinstance(schema, dict):
        normalized = {key: _ensure_object_properties(value) for key, value in schema.items()}
        if normalized.get("type") == "object" and "properties" not in normalized:
            normalized["properties"] = {}
        return normalized
    if isinstance(schema, list):
        return [_ensure_object_properties(item) for item in schema]
    return schema


class PerplexityModel(Model):
    """Strands Model provider backed by Perplexity's official `perplexity` SDK."""

    class ModelConfig(TypedDict, total=False):
        model_id: str
        params: Optional[dict[str, Any]]

    def __init__(self, api_key: str, **model_config: Unpack["PerplexityModel.ModelConfig"]) -> None:
        self.config: "PerplexityModel.ModelConfig" = dict(model_config)  # type: ignore[assignment]
        self._client = perplexity.AsyncPerplexity(api_key=api_key)

    def update_config(self, **model_config: Unpack["PerplexityModel.ModelConfig"]) -> None:
        self.config.update(model_config)

    def get_config(self) -> "PerplexityModel.ModelConfig":
        return self.config

    # --- Request building ---------------------------------------------------
    # Adapted from strands.models.openai_responses.OpenAIResponsesModel: the
    # wire format for messages/tool-specs is the same Responses-API shape
    # regardless of which client library sends it.

    def _format_request(
        self,
        messages: Messages,
        tool_specs: Optional[list[ToolSpec]] = None,
        system_prompt: Optional[str] = None,
        tool_choice: Optional[ToolChoice] = None,
    ) -> dict[str, Any]:
        params = cast(dict[str, Any], self.config.get("params", {}) or {})
        request: dict[str, Any] = {
            "model": self.config["model_id"],
            "input": self._format_request_messages(messages),
            "stream": True,
            "background": True,
            "store": True,
            "max_steps": MAX_STEPS_CEILING,
            **params,
        }

        if system_prompt:
            request["instructions"] = system_prompt

        if tool_specs:
            request["tools"] = [
                *request.get("tools", []),
                *(
                    {
                        "type": "function",
                        "name": tool_spec["name"],
                        "description": tool_spec.get("description", ""),
                        "parameters": _ensure_object_properties(
                            tool_spec["inputSchema"]["json"]
                        ),
                    }
                    for tool_spec in tool_specs
                ),
            ]
            request.update(self._format_request_tool_choice(tool_choice))

        return request

    @classmethod
    def _format_request_tool_choice(cls, tool_choice: Optional[ToolChoice]) -> dict[str, Any]:
        if not tool_choice:
            return {}
        match tool_choice:
            case {"auto": _}:
                return {"tool_choice": "auto"}
            case {"any": _}:
                return {"tool_choice": "required"}
            case {"tool": {"name": tool_name}}:
                return {"tool_choice": {"type": "function", "name": tool_name}}
            case _:
                return {"tool_choice": "auto"}

    @classmethod
    def _format_request_messages(cls, messages: Messages) -> list[dict[str, Any]]:
        formatted_messages: list[dict[str, Any]] = []

        for message in messages:
            role = message["role"]
            contents = message["content"]

            formatted_contents = [
                cls._format_request_message_content(content, role=role)
                for content in contents
                if not any(block_type in content for block_type in ["toolResult", "toolUse", "reasoningContent"])
            ]
            formatted_tool_calls = [
                cls._format_request_message_tool_call(content["toolUse"])
                for content in contents
                if "toolUse" in content
            ]
            formatted_tool_messages = [
                cls._format_request_tool_message(content["toolResult"])
                for content in contents
                if "toolResult" in content
            ]

            if formatted_contents:
                # `type` is a required field on Perplexity's InputMessage
                # (input_item_param.py), alongside role and content.
                formatted_messages.append(
                    {"type": "message", "role": role, "content": formatted_contents}
                )
            formatted_messages.extend(formatted_tool_calls)
            formatted_messages.extend(formatted_tool_messages)

        return [
            m
            for m in formatted_messages
            if m.get("content") or m.get("type") in ["function_call", "function_call_output"]
        ]

    @classmethod
    def _format_request_message_content(cls, content: ContentBlock, *, role: Role = "user") -> dict[str, Any]:
        if "text" in content:
            text_type = "output_text" if role == "assistant" else "input_text"
            return {"type": text_type, "text": content["text"]}
        if "citationsContent" in content:
            text = "".join(c["text"] for c in content["citationsContent"].get("content", []) if "text" in c)
            text_type = "output_text" if role == "assistant" else "input_text"
            return {"type": text_type, "text": text}
        if "image" in content:
            # Perplexity's InputMessageContentContentPartArray accepts
            # {"type": "input_image", "image_url": ...} (see the installed
            # perplexity SDK's input_item_param.py).
            #
            # Both ImageSource variants are read. `location` is the one this
            # app's own chat path uses — Strands documents Location as
            # provider-interpreted, and it is the only variant that survives
            # the workflow/activity payload boundary (see workflow.py's
            # _turn_content). `bytes` is still handled because a tool or
            # another caller may supply an image that way, and encoding it
            # here is what image_url expects. Without this branch either form
            # raised TypeError and took the whole turn down.
            image = content["image"]
            source = image.get("source", {})
            raw = source.get("bytes")
            if raw is not None:
                encoded = base64.b64encode(raw).decode("ascii")
                return {
                    "type": "input_image",
                    "image_url": f"data:image/{image['format']};base64,{encoded}",
                }
            location = source.get("location") or {}
            return {"type": "input_image", "image_url": location.get("uri", "")}
        raise TypeError(f"content_type=<{next(iter(content))}> | unsupported type")

    @classmethod
    def _format_request_message_tool_call(cls, tool_use: ToolUse) -> dict[str, Any]:
        return {
            "type": "function_call",
            "call_id": tool_use["toolUseId"],
            "name": tool_use["name"],
            "arguments": json.dumps(tool_use["input"], ensure_ascii=False),
        }

    @classmethod
    def _format_request_tool_message(cls, tool_result: ToolResult) -> dict[str, Any]:
        parts = []
        for content in tool_result["content"]:
            if "json" in content:
                parts.append(json.dumps(content["json"], ensure_ascii=False))
            elif "text" in content:
                parts.append(content["text"])
        return {
            "type": "function_call_output",
            "call_id": tool_result["toolUseId"],
            "output": "\n".join(parts),
        }

    # --- Response streaming ---------------------------------------------------

    async def stream(
        self,
        messages: Messages,
        tool_specs: Optional[list[ToolSpec]] = None,
        system_prompt: Optional[str] = None,
        *,
        tool_choice: Optional[ToolChoice] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        request = self._format_request(messages, tool_specs, system_prompt, tool_choice)
        logger.debug("formatted request=<%s>", request)

        response = await self._client.responses.create(**request)

        yield self._format_chunk({"chunk_type": "message_start"})

        data_type: Optional[str] = None
        tool_calls: list[SimpleNamespace] = []
        finish_reason = "stop"
        final_usage = None

        async for event in response:
            match event.type:
                case "response.output_text.delta":
                    chunks, data_type = self._stream_switch_content("text", data_type)
                    for chunk in chunks:
                        yield chunk
                    yield self._format_chunk({"chunk_type": "content_delta", "data_type": "text", "data": event.delta})

                # Every reasoning event the SDK's ResponseStreamChunk union
                # defines, plus response.skill.loaded — the first-class event
                # for a resolved load_skill call. All of them are real chain of
                # thought, so all of them are surfaced.
                case (
                    "response.reasoning.started"
                    | "response.reasoning.stopped"
                    | "response.reasoning.search_queries"
                    | "response.reasoning.search_results"
                    | "response.reasoning.fetch_url_queries"
                    | "response.reasoning.fetch_url_results"
                    | "response.skill.loaded"
                ):
                    text = self._describe_reasoning_event(event)
                    if text:
                        chunks, data_type = self._stream_switch_content("reasoning_content", data_type)
                        for chunk in chunks:
                            yield chunk
                        yield self._format_chunk(
                            {"chunk_type": "content_delta", "data_type": "reasoning_content", "data": text}
                        )

                case "response.output_item.done":
                    item = event.item
                    if item.type == "function_call":
                        tool_calls.append(
                            SimpleNamespace(
                                function=SimpleNamespace(name=item.name, arguments=item.arguments),
                                id=item.call_id,
                            )
                        )

                case "response.failed":
                    # Must raise, not break. Swallowing this yielded a
                    # perfectly well-formed EMPTY completion, so the agent
                    # saw a successful turn, the user got a blank reply, and
                    # — worst of all — the model activity returned "success",
                    # so none of workflow.py's resilience applied: no
                    # MODEL_RETRY_POLICY attempt, no schedule_to_close
                    # ceiling, nothing. A failed API call has to surface as a
                    # failed activity for Temporal to be able to do its job.
                    #
                    # Two codes are terminal verdicts on THIS request rather
                    # than transient conditions, so retrying them only delays
                    # the real error by the full schedule_to_close:
                    #
                    # - invalid_request: a malformed schema or parameter. It
                    #   will be malformed on attempt six too.
                    # - model_error: the API already ran the request to
                    #   completion and judged the output unusable (e.g.
                    #   "model produced no usable answer (reasoning_only)").
                    #   Observed live costing 15.2 minutes of dead air — six
                    #   attempts of a multi-minute call — before the user saw
                    #   any error at all. That is the "no hanging" guarantee in
                    #   workflow.py's docstring being broken by the retry
                    #   policy meant to protect it.
                    #
                    # Everything else (rate limits, upstream blips) stays
                    # retryable and rides the normal backoff.
                    error = event.error
                    code = getattr(error, "code", None) or getattr(error, "type", None)
                    raise ApplicationError(
                        f"Perplexity response failed: {error}",
                        non_retryable=code in ("invalid_request", "model_error"),
                        type=str(code or "perplexity_response_failed"),
                    )

                case "response.completed":
                    if event.response and event.response.usage:
                        final_usage = event.response.usage
                    break

        if data_type:
            yield self._format_chunk({"chunk_type": "content_stop", "data_type": data_type})

        for tool_call in tool_calls:
            yield self._format_chunk({"chunk_type": "content_start", "data_type": "tool", "data": tool_call})
            yield self._format_chunk({"chunk_type": "content_delta", "data_type": "tool", "data": tool_call})
            yield self._format_chunk({"chunk_type": "content_stop", "data_type": "tool"})

        finish_reason = "tool_calls" if tool_calls else finish_reason
        yield self._format_chunk({"chunk_type": "message_stop", "data": finish_reason})

        if final_usage:
            yield self._format_chunk({"chunk_type": "metadata", "data": final_usage})

    @staticmethod
    def _describe_reasoning_event(event: Any) -> Optional[str]:
        """One line of chain of thought per reasoning event.

        Field names come straight from the installed SDK's
        response_stream_chunk.py: SearchQueriesEvent.queries,
        SearchResultsEvent.results (SearchResult.title/url),
        FetchURLQueriesEvent.urls, FetchURLResultsEvent.contents
        (title/url/snippet), ResponseSkillLoadedEvent.name, and `thought`
        on every reasoning event.
        """
        match event.type:
            case "response.reasoning.search_queries":
                return f"[searching: {', '.join(event.queries)}]"
            case "response.reasoning.search_results":
                titles = [r.title or r.url for r in event.results]
                return f"[found: {', '.join(titles)}]" if titles else None
            case "response.reasoning.fetch_url_queries":
                return f"[fetching: {', '.join(event.urls)}]"
            case "response.reasoning.fetch_url_results":
                titles = [c.title or c.url for c in event.contents]
                return f"[fetched: {', '.join(titles)}]" if titles else None
            case "response.skill.loaded":
                return f"[loaded skill: {event.name}]"
            case "response.reasoning.started" | "response.reasoning.stopped":
                return event.thought or None
        return None

    def _stream_switch_content(self, data_type: str, prev_data_type: Optional[str]) -> tuple[list[StreamEvent], str]:
        chunks: list[StreamEvent] = []
        if data_type != prev_data_type:
            if prev_data_type is not None:
                chunks.append(self._format_chunk({"chunk_type": "content_stop", "data_type": prev_data_type}))
            chunks.append(self._format_chunk({"chunk_type": "content_start", "data_type": data_type}))
        return chunks, data_type

    def _format_chunk(self, event: dict[str, Any]) -> StreamEvent:
        match event["chunk_type"]:
            case "message_start":
                return {"messageStart": {"role": "assistant"}}

            case "content_start":
                if event["data_type"] == "tool":
                    return {
                        "contentBlockStart": {
                            "start": {
                                "toolUse": {
                                    "name": event["data"].function.name,
                                    "toolUseId": event["data"].id,
                                }
                            }
                        }
                    }
                return {"contentBlockStart": {"start": {}}}

            case "content_delta":
                if event["data_type"] == "tool":
                    return {
                        "contentBlockDelta": {"delta": {"toolUse": {"input": event["data"].function.arguments or ""}}}
                    }
                if event["data_type"] == "reasoning_content":
                    return {"contentBlockDelta": {"delta": {"reasoningContent": {"text": event["data"]}}}}
                return {"contentBlockDelta": {"delta": {"text": event["data"]}}}

            case "content_stop":
                return {"contentBlockStop": {}}

            case "message_stop":
                match event["data"]:
                    case "tool_calls":
                        return {"messageStop": {"stopReason": "tool_use"}}
                    case _:
                        return {"messageStop": {"stopReason": "end_turn"}}

            case "metadata":
                usage = event["data"]
                usage_data = {
                    "inputTokens": getattr(usage, "input_tokens", 0),
                    "outputTokens": getattr(usage, "output_tokens", 0),
                    "totalTokens": getattr(usage, "total_tokens", 0),
                }
                return {"metadata": {"usage": usage_data, "metrics": {"latencyMs": 0}}}

        raise RuntimeError(f"chunk_type=<{event['chunk_type']}> | unrecognized chunk type")

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # noqa: ANN001, ANN201
        raise NotImplementedError("structured_output is not implemented for PerplexityModel")
