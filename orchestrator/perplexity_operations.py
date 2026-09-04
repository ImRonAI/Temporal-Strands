"""Perplexity Agent API operation activities.

Ten async Temporal activities designed for Strands ``activity_as_tool``:

- Six preset create activities (``create_fast_agent_response`` ...
  ``create_wide_research_agent_response``), one per current dynamic preset.
  Each sends its fixed documented preset name so Perplexity can keep updating
  the dynamic preset internals; ``background=True`` and ``stream=True`` are
  implementation invariants, never model-authored arguments. The SDK stream
  is consumed to its authoritative terminal event; every event is published
  verbatim (as data) inside an envelope on the ``agent_runs`` workflow-stream
  topic, and bounded progress is heartbeated so Temporal knows the stream is
  alive between events.
- ``retrieve_agent_response`` / ``list_agent_response_files`` /
  ``download_agent_response_file`` implement the remaining supplied schemas.
- ``list_agent_models`` exposes the live ``GET /v1/models`` catalog so the
  orchestrator can discover current valid model ids before overriding a
  preset's model or building a ``models`` fallback list. The installed SDK
  has no models resource, so it issues the GET through the authenticated
  base-client request surface of the configured ``AsyncPerplexity`` client.
  Downloaded bytes are streamed to disk under ``config.AGENT_FILE_STORE_DIR``
  and never enter Temporal payloads: the result carries only bounded
  metadata, a SHA-256, the Agent API file path, and the browser-safe
  ``/api/orchestrator/file`` proxy URL.

Request validation mirrors the supplied OpenAPI constraints and runs before
any network I/O; violations raise non-retryable ``ApplicationError``.
SDK/HTTP failures are classified like ``perplexity_model.py``:
authentication / permission / bad-request / not-found / validation errors are
non-retryable, throttling / transport / 5xx stay retryable for Temporal's
activity retry policy. Note that the workflow wires the six create activities
with ``config.AGENT_CREATE_RETRY_POLICY`` (``maximum_attempts=1``): the Agent
API documents no idempotency key, so an ambiguous create failure is never
automatically re-issued — the retryable/non-retryable split still matters for
error semantics and for any caller that opts into retries deliberately.
Retrieve/list/download are read-only and retry normally. The worker installs
the ``AsyncPerplexity`` client via
:func:`configure`; the default client keeps ``max_retries=0`` so Temporal is
the only retry mechanism.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import perplexity
from perplexity import AsyncPerplexity
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

# agent_api_tools deliberately imports nothing from this module (or from
# run_worker/workflow), so defaulting the create activities' tools to the
# shared native array introduces no import cycle.
import agent_api_tools
import config

# Shared workflow/server contract: nested sub-agent run events ride this topic.
AGENT_RUNS_TOPIC = "agent_runs"

# --- OpenAPI schema constraints (contract constants, not deployment dials) ---
_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})
_BUILTIN_SKILLS = frozenset(
    {"office", "office/pdf", "office/docx", "office/pptx", "office/xlsx"}
)
_NATIVE_TOOL_TYPES = frozenset(
    {
        "web_search",
        "finance_search",
        "people_search",
        "fetch_url",
        "function",
        "sandbox",
        "mcp",
        "connector",
    }
)
_SEARCH_CONTEXT_SIZES = frozenset({"low", "medium", "high"})
_INLINE_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# OpenAPI: ^[a-zA-Z0-9_-]{1,64}$, and labels are unique per request.
_MCP_SERVER_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SKILLS_MAX_ITEMS = 16
_SKILL_NAME_MAX_CHARS = 64
# OpenAPI: "Short discovery description, limited to 1,024 UTF-8 bytes."
_SKILL_DESCRIPTION_MAX_BYTES = 1024
# additionalProperties: false on both skill schemas.
_BUILTIN_SKILL_KEYS = frozenset({"type", "name"})
_INLINE_SKILL_KEYS = frozenset({"type", "name", "description", "instructions"})
_SKILL_INSTRUCTIONS_MAX_BYTES = 65_536
_SKILL_INSTRUCTIONS_TOTAL_MAX_BYTES = 262_144
_RESPONSE_FORMAT_NAME_MAX_CHARS = 64
_MODELS_FALLBACK_MIN = 1
_MODELS_FALLBACK_MAX = 5
_MAX_STEPS_MIN = 1
_MAX_STEPS_MAX = 100
_WEB_SEARCH_MAX_RESULTS_RANGE = (1, 50)
_FETCH_URL_MAX_URLS_RANGE = (1, 10)
_TEMPERATURE_RANGE = (0.0, 2.0)
_TOP_P_RANGE = (0.0, 1.0)

# Optional ResponsesRequest fields forwarded verbatim to the SDK when present.
_PASSTHROUGH_FIELDS = (
    "instructions",
    "language_preference",
    "max_output_tokens",
    "max_steps",
    "model",
    "models",
    "previous_response_id",
    "reasoning",
    "response_format",
    "store",
    "tools",
    "skills",
)

# Non-retryable SDK failures: retrying cannot fix credentials, permissions,
# request shape, missing resources, or schema-invalid responses.
_NON_RETRYABLE_SDK_ERRORS = (
    perplexity.AuthenticationError,
    perplexity.PermissionDeniedError,
    perplexity.BadRequestError,
    perplexity.NotFoundError,
    perplexity.UnprocessableEntityError,
    perplexity.APIResponseValidationError,
)

# response.failed error codes containing these markers are permanent.
_NON_RETRYABLE_FAILURE_MARKERS = (
    "auth",
    "permission",
    "invalid",
    "validation",
    "unsupported",
    "not_found",
)

# --- worker client wiring ----------------------------------------------------

# Worker-set client factory installed by run_worker via configure(). The
# api-key-bearing client stays inside the factory closure and never enters
# workflow state or activity payloads.
_CLIENT_FACTORY: Callable[[], Any] | None = None


def configure(client_factory: Callable[[], Any] | None) -> None:
    """Install (or clear) the worker's ``AsyncPerplexity`` client factory."""
    global _CLIENT_FACTORY
    _CLIENT_FACTORY = client_factory


def _get_client() -> Any:
    """The configured client, or a default with SDK retries disabled.

    ``max_retries=0`` keeps Temporal's activity retry policy the only retry
    mechanism; SDK-internal retries would hide attempts from the workflow.
    """
    if _CLIENT_FACTORY is not None:
        return _CLIENT_FACTORY()
    return AsyncPerplexity(max_retries=0)


# --- errors -------------------------------------------------------------------


def _operation_error(message: str, *, non_retryable: bool) -> ApplicationError:
    return ApplicationError(
        message, type="PerplexityOperationError", non_retryable=non_retryable
    )


def _invalid(message: str) -> ApplicationError:
    return _operation_error(message, non_retryable=True)


def _raise_sdk_error(error: Exception) -> None:
    raise _operation_error(
        str(error), non_retryable=isinstance(error, _NON_RETRYABLE_SDK_ERRORS)
    ) from error


def _raise_failed(error: Any) -> None:
    code = str(
        getattr(error, "code", None) or getattr(error, "type", None) or ""
    ).lower()
    non_retryable = any(marker in code for marker in _NON_RETRYABLE_FAILURE_MARKERS)
    message = str(getattr(error, "message", None) or "Perplexity response failed")
    raise _operation_error(message, non_retryable=non_retryable)


# --- serialization -------------------------------------------------------------


def _to_data(value: Any) -> Any:
    """Project SDK objects to plain JSON-serializable data, verbatim."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _to_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_data(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return {str(key): _to_data(item) for key, item in vars(value).items()}
    return str(value)


def _project_response(response: Any) -> dict[str, Any]:
    """Project a terminal/retrieved response into the serializable toolResult.

    Unknown output item types are preserved as data (the schema requires
    clients to tolerate forward-compatible values). Output is not truncated;
    the model's ``max_output_tokens`` is the generation budget.
    """
    output_items = list(getattr(response, "output", None) or [])
    text_parts: list[str] = []
    for item in output_items:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "output_text":
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)
    output_text = "".join(text_parts)

    projected_output: list[dict[str, Any]] = []
    for item in output_items:
        data = _to_data(item)
        if not isinstance(data, dict):
            data = {"type": None, "value": data}
        projected_output.append(data)

    usage = getattr(response, "usage", None)
    error = getattr(response, "error", None)
    return {
        "response_id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "status": getattr(response, "status", None),
        "output_text": output_text,
        "output": projected_output,
        "usage": _to_data(usage) if usage is not None else None,
        "error": _to_data(error) if error is not None else None,
    }


# --- request validation ---------------------------------------------------------


def _validate_skills(skills: list[Any]) -> None:
    if len(skills) > _SKILLS_MAX_ITEMS:
        raise _invalid(f"skills accepts at most {_SKILLS_MAX_ITEMS} items")
    total_instruction_bytes = 0
    for skill in skills:
        if not isinstance(skill, Mapping):
            raise _invalid("each skill must be an object")
        skill_type = skill.get("type")
        if skill_type == "builtin":
            unknown_keys = set(skill) - _BUILTIN_SKILL_KEYS
            if unknown_keys:
                raise _invalid(
                    f"builtin skill has unknown properties {sorted(unknown_keys)}"
                )
            if skill.get("name") not in _BUILTIN_SKILLS:
                raise _invalid(f"unknown builtin skill {skill.get('name')!r}")
        elif skill_type == "inline":
            unknown_keys = set(skill) - _INLINE_SKILL_KEYS
            if unknown_keys:
                raise _invalid(
                    f"inline skill has unknown properties {sorted(unknown_keys)}"
                )
            name = skill.get("name")
            if (
                not isinstance(name, str)
                or len(name) > _SKILL_NAME_MAX_CHARS
                or not _INLINE_SKILL_NAME_RE.match(name)
            ):
                raise _invalid(f"invalid inline skill name {name!r}")
            description = skill.get("description")
            if (
                not isinstance(description, str)
                or not description
                or len(description.encode("utf-8")) > _SKILL_DESCRIPTION_MAX_BYTES
            ):
                raise _invalid(
                    "inline skill description must be 1-"
                    f"{_SKILL_DESCRIPTION_MAX_BYTES} UTF-8 bytes"
                )
            instructions = skill.get("instructions")
            if not isinstance(instructions, str) or not instructions:
                raise _invalid("inline skill instructions are required")
            instruction_bytes = len(instructions.encode("utf-8"))
            if instruction_bytes > _SKILL_INSTRUCTIONS_MAX_BYTES:
                raise _invalid(
                    "inline skill instructions exceed "
                    f"{_SKILL_INSTRUCTIONS_MAX_BYTES} bytes"
                )
            total_instruction_bytes += instruction_bytes
        else:
            raise _invalid(f"unknown skill type {skill_type!r}")
    if total_instruction_bytes > _SKILL_INSTRUCTIONS_TOTAL_MAX_BYTES:
        raise _invalid(
            "combined inline skill instructions exceed "
            f"{_SKILL_INSTRUCTIONS_TOTAL_MAX_BYTES} bytes"
        )


def _validate_tools(tools: list[Any]) -> None:
    # OpenAPI: server_label is ^[a-zA-Z0-9_-]{1,64}$ and unique per request,
    # across BOTH mcp and connector entries.
    seen_mcp_labels: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping):
            raise _invalid("each tool must be an object")
        tool_type = tool.get("type")
        if tool_type not in _NATIVE_TOOL_TYPES:
            raise _invalid(f"unknown tool type {tool_type!r}")
        if tool_type == "web_search":
            max_results = tool.get("max_results")
            low, high = _WEB_SEARCH_MAX_RESULTS_RANGE
            if max_results is not None and not low <= max_results <= high:
                raise _invalid(f"web_search max_results must be {low}-{high}")
            context_size = tool.get("search_context_size")
            if context_size is not None and context_size not in _SEARCH_CONTEXT_SIZES:
                raise _invalid(
                    f"web_search search_context_size must be one of "
                    f"{sorted(_SEARCH_CONTEXT_SIZES)}"
                )
        elif tool_type == "fetch_url":
            max_urls = tool.get("max_urls")
            low, high = _FETCH_URL_MAX_URLS_RANGE
            if max_urls is not None and not low <= max_urls <= high:
                raise _invalid(f"fetch_url max_urls must be {low}-{high}")
        elif tool_type == "function":
            if not tool.get("name"):
                raise _invalid("function tools require a name")
        elif tool_type == "mcp":
            label = tool.get("server_label")
            if not isinstance(label, str) or not _MCP_SERVER_LABEL_RE.match(label):
                raise _invalid(f"invalid mcp server_label {label!r}")
            if label in seen_mcp_labels:
                raise _invalid(
                    f"mcp server_label {label!r} is not unique within the request"
                )
            seen_mcp_labels.add(label)
            url = tool.get("server_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise _invalid("mcp server_url must use https")
        elif tool_type == "connector":
            connector_id = tool.get("id")
            if not isinstance(connector_id, str) or not connector_id:
                raise _invalid("connector tools require an id")
            label = tool.get("server_label")
            if not isinstance(label, str) or not _MCP_SERVER_LABEL_RE.match(label):
                raise _invalid(f"invalid connector server_label {label!r}")
            if label in seen_mcp_labels:
                raise _invalid(
                    f"connector server_label {label!r} is not unique within the request"
                )
            seen_mcp_labels.add(label)


def _validate_response_format(response_format: Any) -> None:
    if not isinstance(response_format, Mapping):
        raise _invalid("response_format must be an object")
    if response_format.get("type") != "json_schema":
        raise _invalid("response_format type must be 'json_schema'")
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, Mapping):
        raise _invalid("response_format.json_schema must be an object")
    name = json_schema.get("name")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > _RESPONSE_FORMAT_NAME_MAX_CHARS
    ):
        raise _invalid(
            "response_format.json_schema.name must be 1-"
            f"{_RESPONSE_FORMAT_NAME_MAX_CHARS} characters"
        )
    if not isinstance(json_schema.get("schema"), Mapping):
        raise _invalid("response_format.json_schema.schema must be an object")


def _decode_json_param(name: str, value: Any) -> Any:
    """Parse one JSON-string tool parameter into its structured form.

    The LLM-facing activity signatures carry ``response_format_json`` /
    ``tools_json`` / ``skills_json`` as plain JSON strings because
    ``activity_as_tool`` turns ``dict[str, Any]`` / ``list[dict[str, Any]]``
    annotations into JSON Schema with bare ``{}`` nodes, which the Agent API
    rejects for the WHOLE request ("invalid request" before inference —
    the same failure class as Data Commons' bare-object schema documented in
    run_worker.py). A ``string`` parameter keeps every tool schema valid.

    Models sometimes author the value as a nested object/array instead of a
    JSON-encoded string; both arrive here through Strands' parsed tool input,
    so structured values pass through as-is rather than failing the run.
    """
    if value is None:
        return None
    if isinstance(value, (Mapping, list)):
        return value
    # Models frequently author "" (or whitespace) for parameters they mean to
    # omit; an empty string is an omission, not malformed JSON.
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        # Non-retryable, but surfaced to the calling agent as a tool error it
        # can self-correct from: the instruction is part of the message.
        raise _invalid(
            f"{name} is not valid JSON ({error}). Re-issue the call with "
            f"{name} as one JSON-encoded string (escape inner quotes), or "
            "omit the parameter entirely if it is not strictly needed."
        ) from error


def _decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Map the schema-safe activity parameters onto the OpenAPI request fields."""
    decoded = dict(fields)
    effort = decoded.pop("reasoning_effort", None)
    if effort is not None:
        decoded["reasoning"] = {"effort": effort}
    decoded["response_format"] = _decode_json_param(
        "response_format_json", decoded.pop("response_format_json", None)
    )
    decoded["tools"] = _decode_json_param("tools_json", decoded.pop("tools_json", None))
    decoded["skills"] = _decode_json_param(
        "skills_json", decoded.pop("skills_json", None)
    )
    return decoded


def _build_request(preset: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Validate the supplied OpenAPI constraints and assemble the SDK request.

    All validation runs before network I/O; failures are non-retryable.
    Omitted optional fields are not sent at all so dynamic preset internals
    stay live. ``temperature``/``top_p`` travel through ``extra_body`` because
    the installed SDK signature does not carry them yet.
    """
    input_value = fields.get("input")
    if (
        input_value is None
        or (isinstance(input_value, str) and not input_value.strip())
        or (isinstance(input_value, list) and not input_value)
    ):
        raise _invalid("input is required and must be non-empty")

    models = fields.get("models")
    if models is not None and not (
        _MODELS_FALLBACK_MIN <= len(models) <= _MODELS_FALLBACK_MAX
    ):
        raise _invalid(
            f"models fallback must list {_MODELS_FALLBACK_MIN}-"
            f"{_MODELS_FALLBACK_MAX} models"
        )

    # The supplied OpenAPI documents model ids in provider/model format
    # (e.g. "openai/gpt-5", "anthropic/claude-sonnet-4-6"). A bare name like
    # "gpt-4o-mini" is a hallucinated id the API rejects with 400; catching it
    # pre-network returns an instructive tool error the agent can self-correct
    # from in the same turn.
    for candidate in [fields.get("model"), *(models or [])]:
        if candidate is not None and (
            not isinstance(candidate, str) or "/" not in candidate
        ):
            raise _invalid(
                f"model id {candidate!r} is not in provider/model format. "
                "Call list_agent_models and use one of the returned ids "
                "verbatim, or omit the override to use the preset's default."
            )

    max_steps = fields.get("max_steps")
    if max_steps is not None and not (_MAX_STEPS_MIN <= max_steps <= _MAX_STEPS_MAX):
        raise _invalid(f"max_steps must be {_MAX_STEPS_MIN}-{_MAX_STEPS_MAX}")

    max_output_tokens = fields.get("max_output_tokens")
    if max_output_tokens is not None and max_output_tokens < 1:
        raise _invalid("max_output_tokens must be a positive integer")

    model_candidates = [fields.get("model"), *(models or [])]
    if max_output_tokens is None and any(
        isinstance(candidate, str) and candidate.startswith("anthropic/")
        for candidate in model_candidates
    ):
        raise _invalid("anthropic/* models require max_output_tokens")

    reasoning = fields.get("reasoning")
    if reasoning is not None:
        if not isinstance(reasoning, Mapping):
            raise _invalid("reasoning must be an object")
        effort = reasoning.get("effort")
        if effort is not None and effort not in _REASONING_EFFORTS:
            raise _invalid(
                f"reasoning.effort must be one of {sorted(_REASONING_EFFORTS)}"
            )

    temperature = fields.get("temperature")
    if temperature is not None and not (
        _TEMPERATURE_RANGE[0] <= temperature <= _TEMPERATURE_RANGE[1]
    ):
        raise _invalid("temperature must be between 0 and 2")

    top_p = fields.get("top_p")
    if top_p is not None and not (_TOP_P_RANGE[0] <= top_p <= _TOP_P_RANGE[1]):
        raise _invalid("top_p must be between 0 and 1")

    response_format = fields.get("response_format")
    if response_format is not None:
        _validate_response_format(response_format)

    skills = fields.get("skills")
    if skills is not None:
        _validate_skills(skills)

    tools = fields.get("tools")
    if tools is not None:
        _validate_tools(tools)

    request: dict[str, Any] = {
        "preset": preset,
        "background": True,
        "stream": True,
        "input": input_value,
    }
    for key in _PASSTHROUGH_FIELDS:
        value = fields.get(key)
        if value is not None:
            request[key] = value
    extra_body: dict[str, Any] = {}
    if temperature is not None:
        extra_body["temperature"] = temperature
    if top_p is not None:
        extra_body["top_p"] = top_p
    if extra_body:
        request["extra_body"] = extra_body
    return request


# --- streaming create -------------------------------------------------------------


async def _run_create(
    preset: str, activity_name: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Shared body of the six preset create activities.

    Streams the background run to its authoritative terminal event, publishing
    each SDK event verbatim in an envelope on ``agent_runs`` and heartbeating
    bounded progress (response id + last sequence number).
    """
    decoded = _decode_fields(fields)
    if decoded.get("skills") is None:
        decoded["skills"] = [dict(skill) for skill in config.BUILTIN_SKILLS]
    request = _build_request(preset, decoded)
    client = _get_client()
    info = activity.info()

    stream_client = WorkflowStreamClient.from_within_activity(
        batch_interval=config.AGENT_RUNS_STREAM_BATCH_INTERVAL,
    )
    topic = stream_client.topic(AGENT_RUNS_TOPIC)

    response_id: str | None = None
    last_sequence: Any = None
    terminal: Any = None
    failure: Any = None

    # Pulse only if a heartbeat timeout is configured. With Temporal's default
    # (no heartbeat timeout) a silent stream is not killed for missing beats.
    pulse: asyncio.Future[None] | None = None
    if config.AGENT_OPERATION_HEARTBEAT is not None:
        async def _pulse() -> None:
            interval = config.AGENT_OPERATION_HEARTBEAT.total_seconds() / 3
            while True:
                activity.heartbeat(
                    {"response_id": response_id, "sequence_number": last_sequence}
                )
                await asyncio.sleep(interval)

        pulse = asyncio.ensure_future(_pulse())
    try:
        async with stream_client:
            try:
                events = await client.responses.create(**request)
            except ApplicationError:
                raise
            except perplexity.APIError as error:
                _raise_sdk_error(error)
            async for event in events:
                response = getattr(event, "response", None)
                event_response_id = getattr(response, "id", None)
                if isinstance(event_response_id, str):
                    response_id = event_response_id
                sequence_number = getattr(event, "sequence_number", None)
                last_sequence = sequence_number
                topic.publish(
                    {
                        "activity": activity_name,
                        "activity_id": info.activity_id,
                        "preset": preset,
                        "attempt": info.attempt,
                        "sequence_number": sequence_number,
                        "response_id": response_id,
                        "event": _to_data(event),
                    }
                )
                event_type = getattr(event, "type", None)
                if event_type == "response.completed":
                    terminal = response
                elif event_type == "response.failed":
                    failure = getattr(event, "error", None)
    finally:
        if pulse is not None:
            pulse.cancel()
            try:
                await pulse
            except asyncio.CancelledError:
                pass
        activity.heartbeat(
            {"response_id": response_id, "sequence_number": last_sequence}
        )

    if failure is not None:
        _raise_failed(failure)
    if terminal is None:
        # Classified retryable in principle (a broken stream is transient),
        # but the workflow runs creates under maximum_attempts=1 because the
        # API has no idempotency key — recovery goes through
        # retrieve_agent_response instead of a blind re-create.
        raise _operation_error(
            "stream ended without an authoritative terminal completion",
            non_retryable=False,
        )
    status = getattr(terminal, "status", None)
    if status != "completed":
        raise _operation_error(
            f"terminal response status {status!r} is not completed",
            non_retryable=False,
        )
    return _project_response(terminal)


_CREATE_DOC = """Run a Perplexity Agent API research sub-agent using the {preset!r} preset.

    Starts a background streaming Agent API run with the fixed {preset!r}
    dynamic preset and returns the bounded terminal response to the caller.

    Args:
        input: The task prompt for the sub-agent, as plain text.
        instructions: Optional system instructions for the run.
        language_preference: Optional preferred output language.
        max_output_tokens: Optional output token cap (required for
            anthropic/* model overrides).
        max_steps: Optional agent step budget (1-100).
        model: Optional model id override for the preset's default model.
            Discover valid ids with list_agent_models first; use them verbatim.
        models: Optional ordered fallback list of 1-5 model ids; takes
            precedence over model.
        previous_response_id: Optional prior response id to continue from.
        reasoning_effort: Optional reasoning effort, one of minimal, low,
            medium, high, xhigh, or max.
        response_format_json: Optional structured output, as a JSON string of
            a json_schema response_format object, e.g.
            '{{"type": "json_schema", "json_schema": {{"name": "answer",
            "schema": {{"type": "object", "properties": {{}}}}}}}}'.
        store: Optional response storage flag.
        temperature: Optional sampling temperature (0-2).
        top_p: Optional nucleus sampling value (0-1).
        tools_json: Optional native Agent API tools, as a JSON string of the
            tools array, e.g. '[{{"type": "web_search"}}, {{"type": "sandbox"}}]'.
            Valid types: web_search, finance_search, people_search, fetch_url,
            function, sandbox, mcp.
        skills_json: Optional Agent API skills, as a JSON string of the skills
            array, e.g. '[{{"type": "builtin", "name": "office/pdf"}}]' or
            inline skills with name/description/instructions.

    Returns:
        Bounded serializable result with response_id, model, status,
        output_text, output items, usage, and error.
    """


@activity.defn(name="create_fast_agent_response")
async def create_fast_agent_response(
    input: str,
    instructions: str | None = None,
    language_preference: str | None = None,
    max_output_tokens: int | None = None,
    max_steps: int | None = None,
    model: str | None = None,
    models: list[str] | None = None,
    previous_response_id: str | None = None,
    reasoning_effort: str | None = None,
    response_format_json: str | None = None,
    store: bool | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools_json: str | None = None,
    skills_json: str | None = None,
) -> dict[str, Any]:
    fields = dict(locals())
    return await _run_create("fast", "create_fast_agent_response", fields)


@activity.defn(name="create_low_agent_response")
async def create_low_agent_response(
    input: str,
    instructions: str | None = None,
    language_preference: str | None = None,
    max_output_tokens: int | None = None,
    max_steps: int | None = None,
    model: str | None = None,
    models: list[str] | None = None,
    previous_response_id: str | None = None,
    reasoning_effort: str | None = None,
    response_format_json: str | None = None,
    store: bool | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools_json: str | None = None,
    skills_json: str | None = None,
) -> dict[str, Any]:
    fields = dict(locals())
    return await _run_create("low", "create_low_agent_response", fields)


@activity.defn(name="create_medium_agent_response")
async def create_medium_agent_response(
    input: str,
    instructions: str | None = None,
    language_preference: str | None = None,
    max_output_tokens: int | None = None,
    max_steps: int | None = None,
    model: str | None = None,
    models: list[str] | None = None,
    previous_response_id: str | None = None,
    reasoning_effort: str | None = None,
    response_format_json: str | None = None,
    store: bool | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools_json: str | None = None,
    skills_json: str | None = None,
) -> dict[str, Any]:
    fields = dict(locals())
    return await _run_create("medium", "create_medium_agent_response", fields)


@activity.defn(name="create_high_agent_response")
async def create_high_agent_response(
    input: str,
    instructions: str | None = None,
    language_preference: str | None = None,
    max_output_tokens: int | None = None,
    max_steps: int | None = None,
    model: str | None = None,
    models: list[str] | None = None,
    previous_response_id: str | None = None,
    reasoning_effort: str | None = None,
    response_format_json: str | None = None,
    store: bool | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools_json: str | None = None,
    skills_json: str | None = None,
) -> dict[str, Any]:
    fields = dict(locals())
    return await _run_create("high", "create_high_agent_response", fields)


@activity.defn(name="create_xhigh_agent_response")
async def create_xhigh_agent_response(
    input: str,
    instructions: str | None = None,
    language_preference: str | None = None,
    max_output_tokens: int | None = None,
    max_steps: int | None = None,
    model: str | None = None,
    models: list[str] | None = None,
    previous_response_id: str | None = None,
    reasoning_effort: str | None = None,
    response_format_json: str | None = None,
    store: bool | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools_json: str | None = None,
    skills_json: str | None = None,
) -> dict[str, Any]:
    fields = dict(locals())
    return await _run_create("xhigh", "create_xhigh_agent_response", fields)


@activity.defn(name="create_wide_research_agent_response")
async def create_wide_research_agent_response(
    input: str,
    instructions: str | None = None,
    language_preference: str | None = None,
    max_output_tokens: int | None = None,
    max_steps: int | None = None,
    model: str | None = None,
    models: list[str] | None = None,
    previous_response_id: str | None = None,
    reasoning_effort: str | None = None,
    response_format_json: str | None = None,
    store: bool | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools_json: str | None = None,
    skills_json: str | None = None,
) -> dict[str, Any]:
    fields = dict(locals())
    return await _run_create(
        "wide-research", "create_wide_research_agent_response", fields
    )


create_fast_agent_response.__doc__ = _CREATE_DOC.format(preset="fast")
create_low_agent_response.__doc__ = _CREATE_DOC.format(preset="low")
create_medium_agent_response.__doc__ = _CREATE_DOC.format(preset="medium")
create_high_agent_response.__doc__ = _CREATE_DOC.format(preset="high")
create_xhigh_agent_response.__doc__ = _CREATE_DOC.format(preset="xhigh")
create_wide_research_agent_response.__doc__ = _CREATE_DOC.format(
    preset="wide-research"
)


# --- retrieve / files --------------------------------------------------------------


@activity.defn(name="retrieve_agent_response")
async def retrieve_agent_response(response_id: str) -> dict[str, Any]:
    """Retrieve a stored Agent API response by id.

    Args:
        response_id: The Agent API response id to retrieve.

    Returns:
        The bounded response projection (response_id, model, status,
        output_text, output items, usage, error). Unknown, cross-account, or
        store:false responses fail non-retryably (the API returns 404).
    """
    client = _get_client()
    try:
        response = await client.responses.retrieve(response_id)
    except perplexity.APIError as error:
        _raise_sdk_error(error)
    return _project_response(response)


@activity.defn(name="list_agent_response_files")
async def list_agent_response_files(response_id: str) -> dict[str, Any]:
    """List files produced by an Agent API response.

    Args:
        response_id: The Agent API response id whose files to list.

    Returns:
        The supplied object/data shape with bounded file metadata entries.
    """
    client = _get_client()
    try:
        files = await client.responses.files.list(response_id)
    except perplexity.APIError as error:
        _raise_sdk_error(error)
    data = _to_data(files)
    if not isinstance(data, dict):
        data = {}
    return {"object": data.get("object"), "data": data.get("data") or []}


@activity.defn(name="download_agent_response_file")
async def download_agent_response_file(response_id: str, file_id: str) -> dict[str, Any]:
    """Download one Agent API response file to the orchestrator file store.

    Bytes are streamed to disk under ``config.AGENT_FILE_STORE_DIR`` and never
    returned: the result carries only bounded metadata, the SHA-256, the Agent
    API file path, and the browser-safe ``/api/orchestrator/file`` proxy URL.

    Args:
        response_id: The Agent API response id owning the file.
        file_id: The file id to download.

    Returns:
        Metadata: response_id, file_id, filename, content_type, bytes,
        sha256, path, and proxy url.
    """
    for name, value in (("response_id", response_id), ("file_id", file_id)):
        if not isinstance(value, str) or not _SAFE_IDENTIFIER_RE.match(value):
            raise _invalid(f"unsafe {name} {value!r}")

    client = _get_client()
    try:
        binary = await client.responses.files.content(file_id, response_id=response_id)
    except perplexity.APIError as error:
        _raise_sdk_error(error)

    store_dir = Path(config.AGENT_FILE_STORE_DIR) / response_id
    store_dir.mkdir(parents=True, exist_ok=True)
    final_path = store_dir / file_id
    temp_path = store_dir / f".{file_id}.{uuid.uuid4().hex}.part"

    digest = hashlib.sha256()
    total_bytes = 0
    try:
        with temp_path.open("wb") as handle:
            async for chunk in binary.iter_bytes():
                total_bytes += len(chunk)
                digest.update(chunk)
                handle.write(chunk)
        temp_path.replace(final_path)
    finally:
        temp_path.unlink(missing_ok=True)

    headers = getattr(binary, "headers", None)
    content_type = headers.get("content-type") if headers is not None else None
    disposition = headers.get("content-disposition") if headers is not None else None
    filename = None
    if disposition:
        match = re.search(r'filename="([^"]+)"', disposition)
        if match:
            filename = match.group(1)

    # The documented Agent API file-content path (OpenAPI:
    # GET /v1/agent/{id}/files/{file_id}/content). The frontend proxy
    # (app/api/orchestrator/file/route.ts) honours exactly this shape.
    api_path = f"/v1/agent/{response_id}/files/{file_id}/content"
    return {
        "response_id": response_id,
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
        "path": api_path,
        "url": "/api/orchestrator/file?path=" + urllib.parse.quote(api_path, safe=""),
    }


# --- model catalog -------------------------------------------------------------

# The live models endpoint. The installed SDK exposes no models resource, so
# the activity issues this GET through the configured client's public async
# base-client request surface (AsyncPerplexity.get), which reuses the same
# authentication, base URL, and max_retries=0 contract as every other call.
_MODELS_PATH = "/v1/models"

# The four documented fields of one catalog entry, kept verbatim.
_MODEL_ENTRY_FIELDS = ("id", "object", "created", "owned_by")


@activity.defn(name="list_agent_models")
async def list_agent_models() -> dict[str, Any]:
    """List the live Perplexity Agent API model catalog (GET /v1/models).

    This is the authoritative way to discover the CURRENT valid model ids.
    Call it BEFORE passing a `model` override or constructing a `models`
    fallback list for any of the create_*_agent_response tools, then use the
    returned ids verbatim. Never guess, "correct", or hardcode model ids:
    the catalog changes continuously and contains ids newer than any
    training data. Not needed when a preset's default model is used.

    Returns:
        The catalog in its OpenAPI list shape: {"object": "list", "data":
        [{"id", "object", "created", "owned_by"}, ...]}. Records without a
        usable id are dropped; ids are never altered.
    """
    client = _get_client()
    try:
        payload = await client.get(_MODELS_PATH, cast_to=object)
    except perplexity.APIError as error:
        _raise_sdk_error(error)
    data = _to_data(payload)
    entries = data.get("data") if isinstance(data, Mapping) else None
    models: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        models.append({field: entry.get(field) for field in _MODEL_ENTRY_FIELDS})
    return {"object": "list", "data": models}
