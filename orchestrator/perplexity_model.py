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
import base64
import binascii
import copy
import inspect
import io
import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai.resources.responses import AsyncResponses
from openai import APIError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.exceptions import ContextWindowOverflowException, ModelThrottledException
from strands.types.streaming import StreamEvent
from temporalio import activity
from temporalio.exceptions import ApplicationError

from config import (
    DOCUMENT_INLINE_MAX_CHARS, NATIVE_OUTPUT_ITEM_TYPES, PERMANENT_API_ERROR_TYPES,
    PERMANENT_HTTP_STATUSES, PERPLEXITY_API_BASE, REASONING_EFFORTS,
    VISION_INCAPABLE_MODELS, VISION_PROXY_EFFORT, VISION_PROXY_MAX_OUTPUT_TOKENS, VISION_PROXY_MODEL,
)
from desktop_observation import latest_observation, resolve_observation
from desktop_provision_activity import container_artifact_dir
from config import desktop_container_name
from workspace_state import StateConflict

logger = logging.getLogger(__name__)

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
# HTTP client errors the SDK raises as ``openai.APIError`` subclasses that no
# Temporal retry can fix; the set is shared with perplexity_operations.
_PERMANENT_HTTP_STATUSES = PERMANENT_HTTP_STATUSES


@dataclass
class _Invocation:
    """Per-turn tap state: native-event queue, recorded call arguments, effort."""

    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    arguments: dict[str, str] = field(default_factory=dict)
    effort: str | None = None
    progressed: bool = False
    omitted_connectors: set[str] = field(default_factory=set)


def _error(message: str, *, non_retryable: bool) -> ApplicationError:
    return ApplicationError(message, type="PerplexityModelError", non_retryable=non_retryable)


def _is_permanent(error: APIError) -> bool:
    """Whether an SDK error is a client error no retry can fix.

    ``APIStatusError`` carries ``status_code``. A bare ``APIError`` raised from
    the SSE loop (``openai/_streaming.py``) has none, but keeps the API's own
    ``code``/``type`` from the error body; the Agent API puts the numeric HTTP
    status in ``code`` (``{"type": "external_connector_error", "code": 424}``).
    """
    if getattr(error, "status_code", None) in _PERMANENT_HTTP_STATUSES:
        return True
    code = getattr(error, "code", None)
    if code in PERMANENT_API_ERROR_TYPES or getattr(error, "type", None) in PERMANENT_API_ERROR_TYPES:
        return True
    if isinstance(code, int) or (isinstance(code, str) and code.isdigit()):
        return int(code) in _PERMANENT_HTTP_STATUSES
    return False


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


# JSON Schema keywords whose values are name -> subschema maps. A key inside
# one of these is a property name, never a schema keyword, so ``pattern`` as
# a property name must survive the strip below.
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions"})
# Validation keywords the Agent API's request validator rejects for the WHOLE
# request (400 ``invalid request`` before inference) when their value uses a
# regex feature outside its dialect -- lookahead/lookbehind, ``(?<name>``,
# ``\p{..}`` (bisected live against POST /v1/agent, 2026-10-15). The dialect is
# undocumented, MCP servers commonly emit such patterns (every Telnyx MCP tool
# ends its patterns in ``(?![\s\S])``), and the keyword only constrains
# argument *validation*, which the tool server performs itself on call. The
# ``description`` the model actually reads is untouched.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({"pattern"})


def _strip_unsupported_keywords(schema: Any) -> Any:
    """Drop ``_UNSUPPORTED_SCHEMA_KEYWORDS`` from every schema node."""
    if isinstance(schema, dict):
        stripped: dict[str, Any] = {}
        for key, value in schema.items():
            if key in _UNSUPPORTED_SCHEMA_KEYWORDS and isinstance(value, str):
                continue
            if key in _SCHEMA_MAPS and isinstance(value, dict):
                stripped[key] = {name: _strip_unsupported_keywords(sub) for name, sub in value.items()}
            else:
                stripped[key] = _strip_unsupported_keywords(value)
        return stripped
    if isinstance(schema, list):
        return [_strip_unsupported_keywords(item) for item in schema]
    return schema


def function_parameters(schema: Any) -> Any:
    """The exact ``parameters`` object sent for one function tool.

    Shared by ``_outbound_tools`` and ``run_worker.validate_outbound_tools``
    so startup validation replays the wire conversion byte for byte.
    """
    return _strip_unsupported_keywords(_ensure_object_properties(schema))


def _outbound_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every function tool a parameters schema the Agent API accepts.

    Native tool objects (``web_search``, ``mcp``, ``connector``, and the rest
    of the API's ``Tool`` union) pass through unchanged.
    """
    return [
        {**entry, "parameters": function_parameters(entry.get("parameters"))}
        if entry.get("type") == "function" else entry
        for entry in tools
    ]


def source_bytes(data: Any) -> bytes:
    """Raw bytes for a media block's ``source.bytes`` after a Temporal hop.

    The workflow carries attachments as base64 text (JSON has no bytes type);
    in-activity blocks such as the desktop screenshot are already ``bytes``.
    A non-base64 ``str`` is a legacy history whose UTF-8 bytes the pydantic
    converter decoded to text, so it is re-encoded rather than rejected.
    """
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=True)
        except binascii.Error:
            return data.encode("utf-8")
    raise TypeError("media source bytes must be bytes or base64 text")


def _with_media_bytes(content: Any) -> Any:
    """``content`` with every image/document/video ``source.bytes`` as bytes.

    Recurses into ``toolResult.content``, which the parent formats the same way.
    """
    if not isinstance(content, dict):
        return content
    for kind in ("image", "document", "video"):
        block = content.get(kind)
        if isinstance(block, dict) and isinstance(source := block.get("source"), dict) and "bytes" in source:
            return {**content, kind: {**block, "source": {**source, "bytes": source_bytes(source["bytes"])}}}
    if isinstance(result := content.get("toolResult"), dict) and isinstance(items := result.get("content"), list):
        return {**content, "toolResult": {**result, "content": [_with_media_bytes(c) for c in items]}}
    return content


# The vision proxy answers for a session model that cannot see. It returns
# only what that model needs to act: no prose, no guesses, 0-999 coordinates in
# the same space the computer-use actions take.
VISION_PROXY_INSTRUCTIONS = """You are the eyes for a desktop agent that cannot see images.
Read the screenshot and report only the facts the agent needs to act. No
greeting, no advice, no prose paragraphs.

Coordinates are image pixels, x then y, measured from the top-left corner.
Give the centre of each target.

Report, in this order:
SCREEN: active app or page title, and URL if a browser address bar is visible.
STATE: modals, dialogs, errors, loading spinners, or focused fields.
TARGETS: one line per actionable element as `label | type | x,y`
  (types: button, link, input, checkbox, tab, menu, icon, dropdown).
  Include every element a next step could need. Quote visible text exactly.
TEXT: important on-screen text the agent may need verbatim (values, errors,
  headings), one item per line.
If something is unreadable or ambiguous, say so instead of guessing."""


def _messages_with_media_bytes(messages: Any) -> Any:
    """A copy of ``messages`` whose media blocks carry raw bytes (input untouched)."""
    if not isinstance(messages, list):
        return messages
    return [
        {**m, "content": [_with_media_bytes(c) for c in m["content"]]}
        if isinstance(m, dict) and isinstance(m.get("content"), list) else m
        for m in messages
    ]


# Document formats that are plain text under the hood. Together with ``pdf``
# (parsed to its text layer below) this covers every member of workflow.py's
# _SUPPORTED_DOCUMENT_FORMATS.
_TEXT_DOCUMENT_FORMATS = frozenset({"txt", "html", "csv", "md", "json"})


def _extract_pdf_text(raw: bytes) -> str:
    """All page text of a PDF; empty when it has no text layer (scans)."""
    return "\n\n".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages
    ).strip()


async def _pdf_text(raw: bytes) -> str:
    """A PDF's text layer, or a non-retryable error naming why there is none."""
    try:
        text = await asyncio.to_thread(_extract_pdf_text, raw)
    except (PdfReadError, ValueError, OSError) as error:
        raise _error(
            f"The attached PDF is not readable ({error}). Attach a valid PDF.",
            non_retryable=True,
        ) from error
    if not text:
        raise _error(
            "The attached PDF has no extractable text — it looks like a scan. "
            "Attach a text format (txt, md, csv, json, html) instead, or switch to a "
            "Google AI Studio model, which reads scanned documents natively.",
            non_retryable=True,
        )
    return text


async def _document_as_text(content: Any) -> Any:
    """A ``document`` block replaced by its text, inlined for the Agent API.

    The API's request validator accepts a fixed content-part union that
    excludes OpenAI's ``input_file`` (live 400: ``invalid type "input_file"``),
    so documents go in as plain text: text formats decode as UTF-8, PDFs are
    parsed to their text layer. Any other format has no valid representation
    and fails here, before the request, with the reason.
    """
    if not isinstance(content, dict):
        return content
    block = content.get("document")
    if isinstance(block, dict) and isinstance(source := block.get("source"), dict) and "bytes" in source:
        fmt = str(block.get("format", "")).lower()
        raw = source_bytes(source["bytes"])
        if fmt == "pdf":
            text = await _pdf_text(raw)
        elif fmt in _TEXT_DOCUMENT_FORMATS:
            text = raw.decode("utf-8", errors="replace")
        else:
            raise _error(
                f"The Perplexity Agent API cannot read {fmt or 'this'} document attachments. "
                "Attach a text format (txt, md, csv, json, html) or PDF instead, or switch to "
                "a Google AI Studio model, which reads documents natively.",
                non_retryable=True,
            )
        if (total := len(text)) > DOCUMENT_INLINE_MAX_CHARS:
            text = (f"{text[:DOCUMENT_INLINE_MAX_CHARS]}\n\n"
                    f"[truncated: kept the first {DOCUMENT_INLINE_MAX_CHARS} of {total} characters]")
        return {"text": f"Attached document (format: {fmt}):\n\n{text}"}
    if isinstance(result := content.get("toolResult"), dict) and isinstance(items := result.get("content"), list):
        return {**content, "toolResult": {**result, "content": [await _document_as_text(c) for c in items]}}
    return content


async def _messages_with_document_text(messages: Any) -> Any:
    """A copy of ``messages`` whose document blocks are inlined text (input untouched)."""
    if not isinstance(messages, list):
        return messages
    return [
        {**m, "content": [await _document_as_text(c) for c in m["content"]]}
        if isinstance(m, dict) and isinstance(m.get("content"), list) else m
        for m in messages
    ]


def _native_payload(payload: Any) -> dict[str, Any] | None:
    """The Perplexity-only server-side tool event in an SSE payload, else None.

    Typed reasoning events (``response.reasoning.*``, ``response.skill.loaded``)
    are forwarded as they are. A completed output item is forwarded as the
    bare item: the ``response.output_item.done`` envelope adds only
    ``sequence_number``/``output_index``, and the UI keys its cards on the
    item's own ``type``/``call_id``/``id`` (``sandbox-{call_id}``,
    ``mcp-{id}``, ``file-{call_id}``). ``.added`` is skipped so an item
    renders once, on completion.
    """
    if not isinstance(payload, dict) or not isinstance(kind := payload.get("type"), str):
        return None
    if kind.startswith("response.reasoning.") or kind == "response.skill.loaded":
        return payload
    item = payload.get("item") if kind == "response.output_item.done" else None
    if isinstance(item, dict) and item.get("type") in NATIVE_OUTPUT_ITEM_TYPES:
        return item
    return None


def _is_native(payload: Any) -> bool:
    """Whether an SSE payload is a Perplexity-only server-side tool event."""
    return _native_payload(payload) is not None


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
    state: _Invocation | None = None,
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
        if state is not None:
            state.progressed = True
        return
    if state is not None and not (
        isinstance(payload, dict) and (
            payload.get("error")
            or (payload.get("type") in {"response.created", "response.in_progress", "response.failed"}
                and isinstance(payload.get("response"), dict)
                and not payload["response"].get("output"))
        )
    ):
        # The parent buffers function calls and skips native tool events. Fence
        # retries at the wire boundary, including progress it never yields.
        state.progressed = True
    if (native := _native_payload(payload)) is not None:
        queue.put_nowait(native)
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
        transport: httpx.AsyncBaseTransport | None = None,
        stream_callback: Callable[[StreamEvent], None] | None = None, **model_config: Any,
    ) -> None:
        params = copy.deepcopy(params or {})
        self._validate_config(model_id, params)
        self._api_key, self._transport = api_key, transport
        self._stream_callback = stream_callback
        # Temporal caches model instances. Request recovery and stream state
        # must not leak across concurrent sessions using the same model.
        self._invocation: ContextVar[_Invocation | None] = ContextVar("perplexity_invocation", default=None)
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

    @staticmethod
    def _normalise_targets(readout: str, width: int, height: int) -> str:
        """Pixel ``label | type | x,y`` lines rewritten into 0-999 click space.

        The proxy reports pixels (models measure those reliably); the
        computer-use actions take 0-999 across the full screenshot, so the
        conversion happens here, deterministically, not in the model.
        """
        def scale(match: re.Match[str]) -> str:
            x = min(999, max(0, round(int(match.group(2)) * 999 / max(1, width - 1))))
            y = min(999, max(0, round(int(match.group(3)) * 999 / max(1, height - 1))))
            return f"{match.group(1)}{x},{y}"
        return re.sub(r"^(.*\|\s*)(\d+)\s*,\s*(\d+)\s*$", scale, readout, flags=re.MULTILINE)

    async def _describe_screenshot(self, png: bytes) -> str:
        """The vision proxy's read-out of one desktop screenshot.

        A nested call on ``VISION_PROXY_MODEL`` at high reasoning effort, made
        inside the same model activity, so it is retried and timed out with the
        turn. No tools: it only reads the image.
        """
        proxy = PerplexityModel(
            model_id=VISION_PROXY_MODEL,
            params={"max_output_tokens": VISION_PROXY_MAX_OUTPUT_TOKENS},
            api_key=self._api_key,
            transport=self._transport,
        )
        text = ""
        try:
            async for event in proxy.stream(
                [{"role": "user", "content": [
                    {"text": "Read this desktop screenshot."},
                    {"image": {"format": "png", "source": {"bytes": png}}},
                ]}],
                None,
                VISION_PROXY_INSTRUCTIONS,
                invocation_state={"reasoning_effort": VISION_PROXY_EFFORT},
            ):
                text += event.get("contentBlockDelta", {}).get("delta", {}).get("text", "") or ""
        except ApplicationError:
            raise
        except Exception as error:
            raise _error(f"Vision proxy failed: {error}", non_retryable=False) from error
        if not text.strip():
            raise _error("Vision proxy returned no description", non_retryable=False)
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
        return (
            "Target coordinates are already in click space (0-999 across the full screenshot).\n"
            + self._normalise_targets(text.strip(), width, height)
        )

    async def _tap_sse(self, response: httpx.Response) -> None:
        """Observe Perplexity-only events, forwarding every byte unchanged."""
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            return
        state = self._invocation.get()
        if state is None:
            return
        original, queue, arguments = response.aiter_bytes, state.queue, state.arguments

        async def tapped(chunk_size: int | None = None) -> AsyncIterator[bytes]:
            buffer = b""
            async for chunk in original() if chunk_size is None else original(chunk_size):
                # SSE permits CR, LF and CRLF. A chunk-split CRLF becomes an
                # extra empty line here, harmless to this line observer; the
                # SDK still receives the original bytes for decoding.
                *lines, buffer = re.split(b"\r\n|\r|\n", buffer + chunk)
                for line in lines:
                    _enqueue_native(queue, line, arguments, state)
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
        request["tools"] = _outbound_tools(request.get("tools", []))
        state = self._invocation.get()
        if state and state.omitted_connectors:
            request["tools"] = [tool for tool in request["tools"] if not (
                tool.get("type") == "connector" and tool.get("id") in state.omitted_connectors)]
        if state and (effort := state.effort) is not None:
            if effort not in REASONING_EFFORTS:
                # Fail before the API call: an effort outside the Agent API's
                # enum would be a permanent 400, never worth a Temporal retry.
                raise _error(
                    f"reasoning effort must be one of {sorted(REASONING_EFFORTS)}",
                    non_retryable=True,
                )
            request["reasoning"] = {**(request.get("reasoning") or {}), "effort": effort}
        if extra := {k: request.pop(k) for k in list(request) if k not in _OPENAI_FIELDS}:
            request["extra_body"] = extra
        return request

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[StreamEvent, None]:
        """Parent frames, with native server-side tool events interleaved."""
        # A fresh queue and argument record per turn: a replayed activity
        # attempt must not surface the previous attempt's native frames or
        # tool arguments.
        state = _Invocation(effort=(kwargs.get("invocation_state") or {}).get("reasoning_effort"))
        token = self._invocation.set(state)
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
                        root=container_artifact_dir(desktop_container_name(info.workflow_id)),
                    )
            except (OSError, ValueError, StateConflict, RuntimeError) as error:
                raise _error("Desktop observation unavailable; obtain a fresh screenshot", non_retryable=True) from error
            # Documents cannot ride as ``input_file`` parts (the API 400s on
            # them), so they inline as text first: text formats decode as
            # UTF-8, PDFs are parsed to their text layer.
            messages = await _messages_with_document_text(messages)
            # Temporal's pydantic JSON converter carries attachment bytes as
            # base64 text (see workflow.turn_content_blocks); the parent's
            # data-URL encoder requires real bytes.
            messages = _messages_with_media_bytes(messages)
            if observation and str(self.config.get("model_id", "")) in VISION_INCAPABLE_MODELS:
                # This model rejects image input outright. Its vision proxy reads
                # the screenshot and only the text read-out reaches the request.
                readout = await self._describe_screenshot(png)
                messages = [*messages, {"role": "user", "content": [
                    {"text": f"Desktop screenshot from tool call {call_id}, read by the vision agent "
                             f"({VISION_PROXY_MODEL}) because this model cannot view images:\n\n{readout}"},
                ]}]
            elif observation:
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
            configured_connectors = {tool.get("id") for tool in (self.config.get("params") or {}).get("tools", [])
                                     if tool.get("type") == "connector"}
            model_state = kwargs.get("model_state")
            original_model_state = copy.deepcopy(model_state)
            while True:
                start = None
                emitted = False
                try:
                    async for chunk in super().stream(*args, **kwargs):
                        # The SDK emits messageStart before reading the response.
                        # Hold it so a rejected request cannot create a duplicate.
                        if "messageStart" in chunk:
                            start = chunk
                            continue
                        for frame in ([start] if start else []) + self._drain() + [chunk]:
                            frame, current_tool_use = _fill_tool_arguments(frame, state.arguments, current_tool_use)
                            emitted = True
                            if self._stream_callback:
                                self._stream_callback(frame)
                            yield frame
                        start = None
                    break
                except (ContextWindowOverflowException, ModelThrottledException):
                    raise
                except Exception as error:
                    disconnected = re.search(r'Managed connector "([^"\r\n]+)" is not connected\.', str(error))
                    connector_id = disconnected.group(1) if disconnected else None
                    if (getattr(error, "status_code", None) in {None, 400, 424}
                            and not state.progressed and not emitted and connector_id in configured_connectors
                            and connector_id not in state.omitted_connectors):
                        # This is request validation, not an inference retry.
                        # Each attempt removes one named connector, bounding it
                        # by the configured set; never replay partial tool work.
                        logger.warning("Optional connector %s is disconnected; continuing without it", connector_id)
                        state.omitted_connectors.add(connector_id)
                        state.arguments.clear()
                        self._drain()
                        if model_state is not None:
                            model_state.clear()
                            model_state.update(original_model_state)
                        continue
                    retryable = (isinstance(error, APIError) and not state.progressed
                                 and not _is_permanent(error)
                                 and error.message == "upstream model is overloaded, please try again later")
                    raise _error(str(error), non_retryable=not retryable) from error
            for native in self._drain():
                if self._stream_callback:
                    self._stream_callback(native)
                yield native
        finally:
            self._invocation.reset(token)

    def _drain(self) -> list[StreamEvent]:
        """Every native event observed since the last parent chunk."""
        state = self._invocation.get()
        if state is None:
            return []
        queue, events = state.queue, []
        while not queue.empty():
            events.append({"perplexity": queue.get_nowait()})
        return events  # type: ignore[return-value]
