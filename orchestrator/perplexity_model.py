from __future__ import annotations

import base64
import copy
import json
import mimetypes
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any, TypedDict, TypeVar, cast

import perplexity
from perplexity import AsyncPerplexity
from pydantic import BaseModel
from strands.models import Model
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec
from temporalio.exceptions import ApplicationError


class ModelConfig(TypedDict, total=False):
    model_id: str
    params: dict[str, Any]


T = TypeVar("T", bound=BaseModel)


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _application_error(message: str, *, non_retryable: bool) -> ApplicationError:
    return ApplicationError(message, type="PerplexityModelError", non_retryable=non_retryable)


def _ensure_object_properties(schema: Any) -> Any:
    if isinstance(schema, dict):
        normalized = {key: _ensure_object_properties(value) for key, value in schema.items()}
        if normalized.get("type") == "object" and "properties" not in normalized:
            normalized["properties"] = {}
        return normalized
    if isinstance(schema, list):
        return [_ensure_object_properties(item) for item in schema]
    return schema


class PerplexityModel(Model):
    """Strands model adapter for Perplexity's Agent Responses API."""

    def __init__(
        self,
        *,
        model_id: str,
        params: dict[str, Any] | None = None,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.config: ModelConfig = {"model_id": model_id, "params": copy.deepcopy(params or {})}
        self._validate_config(self.config)
        self.client = client or AsyncPerplexity(api_key=api_key, max_retries=0)

    def update_config(self, **model_config: Any) -> None:
        unknown = set(model_config) - {"model_id", "params"}
        if unknown:
            raise _application_error(
                f"Unsupported model configuration: {', '.join(sorted(unknown))}",
                non_retryable=True,
            )
        updated = cast(ModelConfig, {**self.config, **copy.deepcopy(model_config)})
        self._validate_config(updated)
        self.config = updated

    def get_config(self) -> ModelConfig:
        return cast(ModelConfig, copy.deepcopy(self.config))

    @staticmethod
    def _validate_config(config: ModelConfig) -> None:
        params = config.get("params") or {}
        forbidden = {
            "api_key",
            "extra_body",
            "extra_headers",
            "extra_query",
            "input",
            "model",
            "models",
            "previous_response_id",
            "stream",
            "timeout",
        } & params.keys()
        if forbidden:
            raise _application_error(
                f"Unsupported model parameters: {', '.join(sorted(forbidden))}",
                non_retryable=True,
            )

    @staticmethod
    def _image_part(image: dict[str, Any]) -> dict[str, str]:
        source = image.get("source", {})
        if "bytes" not in source:
            raise _application_error("Unsupported image source", non_retryable=True)
        mime = mimetypes.types_map.get(f".{image.get('format', '')}", "application/octet-stream")
        encoded = base64.b64encode(source["bytes"]).decode("ascii")
        return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"}

    @classmethod
    def _message_items(cls, messages: Messages) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        signatures: dict[str, str] = {}
        for message in messages:
            role = message["role"]
            message_parts: list[dict[str, str]] = []
            for block in message["content"]:
                if "text" in block:
                    message_parts.append({"type": "input_text", "text": block["text"]})
                    continue
                if "image" in block:
                    message_parts.append(cls._image_part(block["image"]))
                    continue
                if "toolUse" in block:
                    if message_parts:
                        result.append(cls._message_item(role, message_parts))
                        message_parts = []
                    tool = block["toolUse"]
                    call: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": tool["toolUseId"],
                        "name": tool["name"],
                        "arguments": json.dumps(tool["input"], ensure_ascii=False),
                    }
                    signature = tool.get("reasoningSignature")
                    if signature:
                        call["thought_signature"] = signature
                        signatures[tool["toolUseId"]] = signature
                    result.append(call)
                    continue
                if "toolResult" in block:
                    if message_parts:
                        result.append(cls._message_item(role, message_parts))
                        message_parts = []
                    tool_result = block["toolResult"]
                    contents = []
                    for content in tool_result.get("content", []):
                        if "json" in content:
                            contents.append(json.dumps(content["json"], ensure_ascii=False))
                        elif "text" in content:
                            contents.append(content["text"])
                        else:
                            raise _application_error("Unsupported function output content", non_retryable=True)
                    output: dict[str, Any] = {
                        "type": "function_call_output",
                        "call_id": tool_result["toolUseId"],
                        "output": "\n".join(contents),
                    }
                    result.append(output)
                    continue
                if "reasoningContent" in block:
                    continue
                raise _application_error("Unsupported Strands content block", non_retryable=True)
            if message_parts:
                result.append(cls._message_item(role, message_parts))
        return result

    @staticmethod
    def _message_item(role: str, parts: list[dict[str, str]]) -> dict[str, Any]:
        content: str | list[dict[str, str]]
        if len(parts) == 1 and parts[0]["type"] == "input_text":
            content = parts[0]["text"]
        else:
            content = parts
        return {"type": "message", "role": role, "content": content}

    def _format_request(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None,
        system_prompt: str | None,
    ) -> dict[str, Any]:
        params = copy.deepcopy(self.config.get("params") or {})
        native_tools = params.pop("tools", None) or []
        converted_tools = [
            {
                "type": "function",
                "name": spec["name"],
                "description": spec.get("description", ""),
                "parameters": _ensure_object_properties(spec["inputSchema"]["json"]),
            }
            for spec in tool_specs or []
        ]
        request = {
            "model": self.config["model_id"],
            "input": self._message_items(messages),
            "stream": True,
            **params,
        }
        request["tools"] = [*native_tools, *converted_tools]
        if system_prompt is not None:
            request["instructions"] = system_prompt
        return request

    @staticmethod
    def _raise_sdk_error(error: Exception) -> None:
        non_retryable = isinstance(
            error,
            (
                perplexity.AuthenticationError,
                perplexity.PermissionDeniedError,
                perplexity.BadRequestError,
                perplexity.NotFoundError,
                perplexity.UnprocessableEntityError,
                perplexity.APIResponseValidationError,
            ),
        )
        raise _application_error(str(error), non_retryable=non_retryable) from error

    @staticmethod
    def _raise_failed(error: Any) -> None:
        code = str(_value(error, "code", "") or _value(error, "type", "")).lower()
        non_retryable = any(
            marker in code for marker in ("auth", "permission", "invalid", "validation", "unsupported", "not_found")
        )
        message = str(_value(error, "message", "Perplexity response failed"))
        raise _application_error(message, non_retryable=non_retryable)

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        invocation_state: dict[str, Any] | None = None,
        model_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        del invocation_state, model_state
        unsupported = []
        if tool_choice is not None:
            unsupported.append("tool_choice")
        unsupported.extend(sorted(kwargs))
        if unsupported:
            raise _application_error(
                f"Unsupported Strands options: {', '.join(unsupported)}",
                non_retryable=True,
            )
        try:
            effective_system_prompt = system_prompt
            if system_prompt_content is not None:
                effective_system_prompt = "\n".join(
                    block["text"] for block in system_prompt_content if "text" in block
                ) or None
            request = self._format_request(messages, tool_specs, effective_system_prompt)
            provider_stream = await self.client.responses.create(**request)
        except ApplicationError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise _application_error(f"Unsupported Perplexity request: {error}", non_retryable=True) from error
        except perplexity.APIError as error:
            self._raise_sdk_error(error)

        yield {"messageStart": {"role": "assistant"}}
        next_index = 0
        next_output_index = 0
        output_blocks: dict[int, dict[str, Any]] = {}
        calls: dict[int, dict[str, Any]] = {}
        terminal = None

        def flush_ready() -> list[StreamEvent]:
            nonlocal next_index, next_output_index
            events: list[StreamEvent] = []
            while (state := output_blocks.get(next_output_index)) is not None:
                if state["type"] == "skip":
                    pass
                elif state["type"] == "text":
                    if not state["open"]:
                        state["open"] = True
                        state["content_index"] = next_index
                        next_index += 1
                        events.append(
                            {
                                "contentBlockStart": {
                                    "contentBlockIndex": state["content_index"],
                                    "start": {},
                                }
                            }
                        )
                    while state["emitted"] < len(state["fragments"]):
                        fragment = state["fragments"][state["emitted"]]
                        state["emitted"] += 1
                        events.append(
                            {
                                "contentBlockDelta": {
                                    "contentBlockIndex": state["content_index"],
                                    "delta": {"text": fragment},
                                }
                            }
                        )
                    if not state["done"]:
                        break
                    events.append({"contentBlockStop": {"contentBlockIndex": state["content_index"]}})
                else:
                    if not state["done"]:
                        break
                    content_index = next_index
                    next_index += 1
                    tool_use = {"toolUseId": state["call_id"], "name": state["name"]}
                    if state["signature"]:
                        tool_use["reasoningSignature"] = state["signature"]
                    events.append(
                        {
                            "contentBlockStart": {
                                "contentBlockIndex": content_index,
                                "start": {"toolUse": tool_use},
                            }
                        }
                    )
                    for argument_fragment in state["fragments"]:
                        events.append(
                            {
                                "contentBlockDelta": {
                                    "contentBlockIndex": content_index,
                                    "delta": {"toolUse": {"input": argument_fragment}},
                                }
                            }
                        )
                    events.append({"contentBlockStop": {"contentBlockIndex": content_index}})
                del output_blocks[next_output_index]
                next_output_index += 1
            return events

        try:
            async for provider_event in provider_stream:
                event_type = _value(provider_event, "type")
                if event_type == "response.output_text.delta":
                    output_index = _value(provider_event, "output_index", 0)
                    if not isinstance(output_index, int):
                        output_index = 0
                    state = output_blocks.setdefault(
                        output_index,
                        {"type": "text", "fragments": [], "emitted": 0, "done": False, "open": False},
                    )
                    state["fragments"].append(_value(provider_event, "delta", ""))
                elif event_type == "response.output_text.done":
                    output_index = _value(provider_event, "output_index", 0)
                    if not isinstance(output_index, int):
                        output_index = 0
                    state = output_blocks.setdefault(
                        output_index,
                        {"type": "text", "fragments": [], "emitted": 0, "done": False, "open": False},
                    )
                    completed_text = _value(provider_event, "text", "") or ""
                    streamed_text = "".join(state["fragments"])
                    if completed_text.startswith(streamed_text) and len(completed_text) > len(streamed_text):
                        state["fragments"].append(completed_text[len(streamed_text) :])
                    state["done"] = True
                elif event_type in ("response.output_item.added", "response.output_item.done"):
                    item = _value(provider_event, "item")
                    if _value(item, "type") != "function_call":
                        output_index = _value(provider_event, "output_index")
                        if event_type == "response.output_item.done" and isinstance(output_index, int):
                            output_blocks[output_index] = {"type": "skip", "done": True}
                            for stream_event in flush_ready():
                                yield stream_event
                        continue
                    output_index = _value(provider_event, "output_index")
                    if not isinstance(output_index, int):
                        raise _application_error("Function call missing output index", non_retryable=True)
                    state = calls.get(output_index)
                    if state is None:
                        state = {
                            "call_id": _value(item, "call_id"),
                            "name": _value(item, "name"),
                            "signature": _value(item, "thought_signature"),
                            "arguments": "",
                            "fragments": [],
                            "done": False,
                        }
                        calls[output_index] = state
                        state["type"] = "call"
                        output_blocks[output_index] = state
                    arguments = _value(item, "arguments", "") or ""
                    if event_type == "response.output_item.done":
                        status = _value(item, "status", "missing")
                        if status != "completed":
                            raise _application_error(
                                f"Non-completed function call: {status}",
                                non_retryable=False,
                            )
                        state.update(
                            call_id=_value(item, "call_id"),
                            name=_value(item, "name"),
                            signature=_value(item, "thought_signature"),
                        )
                    previous = state["arguments"]
                    fragment = arguments[len(previous) :] if arguments.startswith(previous) else arguments
                    if fragment:
                        state["fragments"].append(fragment)
                        state["arguments"] = arguments
                    if event_type == "response.output_item.done":
                        try:
                            json.loads(arguments)
                        except (TypeError, json.JSONDecodeError) as error:
                            raise _application_error("Malformed function arguments", non_retryable=True) from error
                        state["done"] = True
                elif event_type == "response.failed":
                    self._raise_failed(_value(provider_event, "error"))
                elif event_type == "response.completed":
                    response = _value(provider_event, "response")
                    if response is None or _value(response, "status") != "completed":
                        error = _value(response, "error")
                        if error:
                            self._raise_failed(error)
                        raise _application_error(
                            f"Non-completed terminal response: {_value(response, 'status', 'missing')}",
                            non_retryable=False,
                        )
                    terminal = response
                for stream_event in flush_ready():
                    yield stream_event
        except ApplicationError:
            raise
        except perplexity.APIError as error:
            self._raise_sdk_error(error)

        if terminal is None:
            raise _application_error("Stream ended without authoritative terminal completion", non_retryable=False)
        if any(not state["done"] for state in calls.values()):
            raise _application_error("Incomplete function call", non_retryable=False)
        for state in output_blocks.values():
            if state["type"] == "text":
                state["done"] = True
        for stream_event in flush_ready():
            yield stream_event

        output = _value(terminal, "output", []) or []
        has_calls = any(_value(item, "type") == "function_call" for item in output)
        yield {"messageStop": {"stopReason": "tool_use" if has_calls else "end_turn"}}
        usage = _value(terminal, "usage")
        if usage is not None:
            yield {
                "metadata": {
                    "usage": {
                        "inputTokens": _value(usage, "input_tokens", 0),
                        "outputTokens": _value(usage, "output_tokens", 0),
                        "totalTokens": _value(usage, "total_tokens", 0),
                    },
                }
            }

    async def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        text: list[str] = []
        async for event in self.stream(prompt, system_prompt=system_prompt, **kwargs):
            delta = event.get("contentBlockDelta", {}).get("delta", {})
            if "text" in delta:
                text.append(delta["text"])
        yield {"output": output_model.model_validate_json("".join(text))}
