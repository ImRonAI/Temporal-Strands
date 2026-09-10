"""Perplexity Agent API provider, rebased onto Strands' OpenAI Responses provider.

The Agent API is OpenAI-compatible (docs.perplexity.ai/docs/agent-api/
openai-compatibility), so message formatting, block sequencing, tool-call
accumulation, and error classification all come from ``OpenAIResponsesModel``;
three methods are overridden. The native-event tap sits at the HTTP layer
because that is the only observation point -- the parent builds its own
``openai.AsyncOpenAI`` (openai_responses.py:321) and its loop neither yields
nor stores events it has no branch for (:334-449).

The same tap records custom-function ``arguments``. The Agent API carries them
only on the ``function_call`` item of ``response.output_item.added``/``.done``
and emits no ``response.function_call_arguments.delta``/``.done`` (see
``.tmp/external-context/perplexity-agent-api/async-responses-streaming-2026-07-30.md``);
the parent reads arguments only from those OpenAI events (:403-417), so its
tool-use delta would otherwise carry ``input: ""`` and every tool call would
reach the agent as ``{}`` (``missing a required argument: 'path'``).
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from typing import Any

import httpx
from openai.resources.responses import AsyncResponses
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.streaming import StreamEvent
from temporalio import activity
from temporalio.exceptions import ApplicationError

from config import NATIVE_OUTPUT_ITEM_TYPES, PERPLEXITY_API_BASE
from desktop_observation import latest_observation, resolve_observation
from workspace_state import StateConflict

PRESET_PREFIX = "preset:"
PRESETS = frozenset({"fast", "low", "medium", "high", "xhigh", "wide-research"})
# Request fields this adapter owns; a caller-supplied value would be silently
# overwritten or would corrupt the request envelope.
_RESERVED_PARAMS = frozenset(
    "api_key extra_body extra_headers extra_query input model preset "
    "previous_response_id stream timeout".split())
# Named kwargs of ``AsyncOpenAI.responses.create``; Agent-API-only fields
# (``preset``, ``max_steps``, ``skills``, ``models``, ``language_preference``)
# ride in ``extra_body``, the SDK's documented verbatim JSON passthrough.
_OPENAI_FIELDS = frozenset(inspect.signature(AsyncResponses.create).parameters) - {"self"}


def _error(message: str, *, non_retryable: bool) -> ApplicationError:
    return ApplicationError(message, type="PerplexityModelError", non_retryable=non_retryable)


def _ensure_object_properties(schema: Any) -> Any:
    """Add ``properties: {}`` to every ``object`` node lacking one.

    The Agent API rejects such a schema (verified live: 400 ``invalid
    request``) and tools like ``mcp_client`` declare free-form ``dict`` args
    that serialize exactly that way -- the provider-adapter role Strands'
    ``ensure_strict_json_schema`` plays for Bedrock.
    """
    if isinstance(schema, dict):
        normalized = {key: _ensure_object_properties(value) for key, value in schema.items()}
        if normalized.get("type") == "object" and "properties" not in normalized:
            normalized["properties"] = {}
        return normalized
    if isinstance(schema, list):
        return [_ensure_object_properties(item) for item in schema]
    return schema


def _is_native(payload: Any) -> bool:
    """Whether an SSE payload is a Perplexity-only server-side tool event."""
    if not isinstance(payload, dict) or not isinstance(kind := payload.get("type"), str):
        return False
    if kind.startswith("response.reasoning.") or kind == "response.skill.loaded":
        return True
    item = payload.get("item") if kind == "response.output_item.done" else None
    return isinstance(item, dict) and item.get("type") in NATIVE_OUTPUT_ITEM_TYPES


def _function_call_item(payload: Any) -> dict[str, Any] | None:
    """The ``function_call`` item of an ``output_item.added``/``.done`` event, else None.

    The Agent API delivers custom-function arguments only as the JSON-string
    ``arguments`` field of that item; it defines no
    ``response.function_call_arguments.*`` events (OpenAPI + SDK
    ``ResponseStreamChunk`` union). The parent parser fills arguments from
    those OpenAI-only events, so without this record every tool call would
    reach the agent with an empty input.
    """
    if not isinstance(payload, dict) or payload.get("type") not in (
        "response.output_item.added",
        "response.output_item.done",
    ):
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return None
    return item if isinstance(item.get("call_id"), str) else None


def _enqueue_native(
    queue: asyncio.Queue[dict[str, Any]],
    raw: bytes,
    arguments: dict[str, str] | None = None,
) -> None:
    """Queue one ``data:`` line iff it carries a Perplexity-only event.

    ``arguments`` (``call_id`` -> JSON-string arguments) is updated from every
    ``function_call`` item seen; a later item for the same call wins, so the
    ``.done`` record is authoritative when the server sends both.

    Total by construction: a truncated or non-JSON line is dropped and left to
    the SDK's own decoder, so the tap can never interrupt the stream.
    """
    if not (line := raw.strip()).startswith(b"data:"):
        return
    try:
        payload = json.loads(line[len(b"data:") :])
    except (ValueError, UnicodeDecodeError):
        return
    if _is_native(payload):
        queue.put_nowait(payload)
    if arguments is not None and (item := _function_call_item(payload)) is not None:
        if isinstance(args := item.get("arguments"), str) and args:
            arguments[item["call_id"]] = args


def _fill_tool_arguments(
    chunk: StreamEvent, arguments: Mapping[str, str], current: str | None
) -> tuple[StreamEvent, str | None]:
    """Put the recorded ``function_call`` arguments into the parent's tool-use delta.

    The parent emits one ``contentBlockStart`` (``toolUseId``) then one
    ``contentBlockDelta`` per tool call; against the Agent API that delta's
    ``input`` is ``""`` because no argument-delta event ever arrived, and
    Strands would then parse the tool input as ``{}``. Returns the (possibly
    replaced) chunk plus the tool-use id the block sequence is currently in.
    """
    if not isinstance(chunk, dict):
        return chunk, current
    if (start := chunk.get("contentBlockStart")) is not None:
        tool_use = (start.get("start") or {}).get("toolUse") or {}
        return chunk, tool_use.get("toolUseId")
    if "contentBlockStop" in chunk:
        return chunk, None
    delta = ((chunk.get("contentBlockDelta") or {}).get("delta") or {}).get("toolUse")
    if delta is None or current is None or delta.get("input") or current not in arguments:
        return chunk, current
    return {"contentBlockDelta": {"delta": {"toolUse": {"input": arguments[current]}}}}, current


class PerplexityModel(OpenAIResponsesModel):
    """Strands model adapter for Perplexity's Agent Responses API."""

    def __init__(
        self, *, model_id: str, params: dict[str, Any] | None = None, api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None, **model_config: Any,
    ) -> None:
        params = copy.deepcopy(params or {})
        self._validate_config(model_id, params)
        self._api_key, self._transport = api_key, transport
        self._native_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # call_id -> JSON-string arguments, from the turn's function_call items.
        self._call_arguments: dict[str, str] = {}
        self._reasoning_effort: str | None = None
        # ``store`` in params only survives if the model reports itself
        # stateful: the parent writes ``"store": self.stateful`` AFTER merging
        # **params (openai_responses.py:558-564).
        model_config.setdefault("stateful", bool(params.get("store")))
        super().__init__(model_id=model_id, params=params, **model_config)

    @staticmethod
    def _preset_name(model_id: str) -> str | None:
        """Validated preset name for ``preset:<name>`` ids, else None."""
        if not model_id.startswith(PRESET_PREFIX):
            return None
        if (name := model_id[len(PRESET_PREFIX) :]) not in PRESETS:
            valid = ", ".join(sorted(PRESETS))
            raise _error(f"Unknown Perplexity preset: {name!r}. Valid presets: {valid}", non_retryable=True)
        return name

    @classmethod
    def _validate_config(cls, model_id: Any, params: dict[str, Any]) -> str | None:
        """Reject adapter-owned params; return the preset name, if any."""
        if forbidden := _RESERVED_PARAMS & params.keys():
            names = ", ".join(sorted(forbidden))
            raise _error(f"Unsupported model parameters: {names}", non_retryable=True)
        return cls._preset_name(model_id) if isinstance(model_id, str) else None

    def _resolve_client_args(self) -> dict[str, Any]:
        # Agent API endpoint, no SDK retries (Temporal owns them), plus the
        # tap. A fresh httpx client per call: the parent closes the SDK client,
        # and with it this transport, at the end of every ``async with`` block.
        return {
            "base_url": f"{PERPLEXITY_API_BASE}/v1",
            "api_key": self._api_key,
            "max_retries": 0,
            "http_client": httpx.AsyncClient(
                transport=self._transport, event_hooks={"response": [self._tap_sse]}),
        }

    async def _tap_sse(self, response: httpx.Response) -> None:
        """Observe Perplexity-only events, forwarding every byte unchanged."""
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            return
        original, queue, arguments = response.aiter_bytes, self._native_queue, self._call_arguments

        async def tapped(chunk_size: int | None = None) -> AsyncIterator[bytes]:
            buffer = b""
            async for chunk in original() if chunk_size is None else original(chunk_size):
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    _enqueue_native(queue, line, arguments)
                yield chunk

        response.aiter_bytes = tapped  # type: ignore[method-assign]

    def _format_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Parent request, retargeted at the Agent API's own envelope."""
        request = super()._format_request(*args, **kwargs)
        # Exactly one of `model` or `preset` goes out.
        params = dict(self.config.get("params") or {})
        if preset := self._validate_config(str(self.config.get("model_id", "")), params):
            request["preset"] = preset
            request.pop("model", None)
        request["tools"] = [
            _ensure_object_properties(t) if t.get("type") == "function" else t
            for t in request.get("tools", [])]
        if (effort := self._reasoning_effort) is not None:
            request["reasoning"] = {**(request.get("reasoning") or {}), "effort": effort}
        if extra := {k: request.pop(k) for k in list(request) if k not in _OPENAI_FIELDS}:
            request["extra_body"] = extra
        return request

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[StreamEvent, None]:
        """Parent frames, with native server-side tool events interleaved."""
        # A fresh queue and argument record per turn: a replayed activity
        # attempt must not surface the previous attempt's native frames or
        # tool arguments.
        self._native_queue = asyncio.Queue()
        self._call_arguments = {}
        self._reasoning_effort = (kwargs.get("invocation_state") or {}).get("reasoning_effort")
        current_tool_use: str | None = None
        try:
            messages = args[0] if args else kwargs.get("messages", [])
            try:
                observation = latest_observation(messages)
                if observation:
                    call_id, _, ref = observation
                    info = activity.info()
                    if not info.workflow_id:
                        raise ValueError("Desktop observation requires a workflow identity")
                    png = await asyncio.to_thread(
                        resolve_observation, ref, namespace=info.namespace, workflow_id=info.workflow_id,
                    )
            except (OSError, ValueError, StateConflict, RuntimeError) as error:
                raise _error("Desktop observation unavailable; obtain a fresh screenshot", non_retryable=True) from error
            if observation:
                # Image bytes exist only in this activity's outgoing request, not
                # in Temporal messages. Perplexity function outputs stay strings.
                messages = [*messages, {"role": "user", "content": [
                    {"text": f"Desktop screenshot from tool call {call_id}"},
                    {"image": {"format": "png", "source": {"bytes": png}}},
                ]}]
                if args:
                    args = (messages, *args[1:])
                else:
                    kwargs = {**kwargs, "messages": messages}
            async for chunk in super().stream(*args, **kwargs):
                for native in self._drain():
                    yield native
                chunk, current_tool_use = _fill_tool_arguments(
                    chunk, self._call_arguments, current_tool_use
                )
                yield chunk
            for native in self._drain():
                yield native
        finally:
            self._reasoning_effort = None

    def _drain(self) -> list[StreamEvent]:
        """Every native event observed since the last parent chunk."""
        queue, events = self._native_queue, []
        while not queue.empty():
            events.append({"perplexity": queue.get_nowait()})
        return events  # type: ignore[return-value]
