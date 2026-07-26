"""The full Perplexity Agent API surface, made available to the orchestrator's
agent as two distinct kinds of tools:

1. Native tools (web_search, fetch_url, people_search, finance_search,
   sandbox, mcp) — passed via PerplexityModel's params={"tools": [...]}
   (perplexity_model.py) and executed entirely server-side by Perplexity on
   every turn. Confirmed each tool's exact accepted fields against the
   OpenAPI spec from Agents API.pdf — NOT the unverified fields in an
   earlier pasted spec, which included max_results on web_search and
   max_tokens/max_tokens_per_page on people_search; neither field exists on
   those tools per the OpenAPI schema, so they're omitted here rather than
   silently sent and silently ignored. run_worker.py's _default_native_tools()
   is what actually selects which of these get attached to every turn —
   see its docstring for why that's sandbox + finance_search, not all five.

2. Function tools (create/retrieve/cancel a response, list/download its
   files) — the rest of the Agent API (background jobs, skills, wide-
   research, presets, model fallback chains) exposed as Strands tools the
   agent can call *on itself*, so it can spawn and manage sub-tasks rather
   than being limited to one native tool call per turn. These do real HTTP
   I/O, so per the strands-temporal integration's determinism rules they are
   Temporal activities (activity_as_tool), never plain @tool functions.
"""

import json
import os
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Optional

from temporalio import activity
from temporalio.common import RetryPolicy
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.exceptions import ApplicationError

if TYPE_CHECKING:
    import httpx

# httpx is deliberately NOT imported at module level: this module is pulled
# into workflow.py, which Temporal loads through its workflow sandbox, and
# httpx's own internals (a Cookies class inheriting from urllib.request.
# Request) trip the sandbox's restrictions during that import — confirmed
# live (RestrictedWorkflowAccessError on urllib.request.Request.__mro_entries__).
# The activity functions below run outside the sandbox, so importing httpx
# locally inside each of them sidesteps the problem entirely instead of
# needing to extend StrandsPlugin's sandbox passthrough list.

PERPLEXITY_BASE_URL = "https://api.perplexity.ai/v1"


def _raise_for_status(r: "httpx.Response") -> None:
    """Like r.raise_for_status(), but fixes two real problems found by
    direct testing:

    1. r.raise_for_status() drops the response body — the exact reason
       Perplexity rejected a request (e.g. an invalid field combination)
       was completely invisible in every activity failure log, making a
       genuine 400 look like an opaque, unexplained error.
    2. Client errors (4xx — a malformed request the agent constructed)
       were being retried by MODEL_RETRY_POLICY up to its full attempt
       count before failing, exactly like a transient 5xx or network
       blip. A 400 will never succeed on retry; wasting the retry budget
       on it means slower, noisier failures for something a
       non_retryable=True ApplicationError resolves on the first attempt.
    """
    if r.is_success:
        return
    detail = r.text[:2000]
    if 400 <= r.status_code < 500:
        raise ApplicationError(
            f"Perplexity API {r.status_code} for {r.request.method} {r.request.url}: {detail}",
            non_retryable=True,
        )
    r.raise_for_status()


# Well under Temporal's default 2MB payload limit (2,097,152 bytes) — a real
# incident, not a defensive guess: a wide-research + office/pdf skill
# response returned a payload just over that limit, which doesn't fail
# cleanly. It crashes the WORKFLOW TASK itself (gRPC/history layer, below
# the application layer), which Temporal then retries by replaying the
# workflow from its last checkpoint — reissuing the same tool calls that
# led to the oversized result, hitting the same limit again, forever.
# Neither RetryPolicy nor non_retryable ApplicationErrors catch this,
# because the activity itself "succeeds" — only returning it to the
# workflow fails. Truncating here, in the one place all of these tools
# return their result, closes the whole class of failure rather than
# special-casing whichever endpoint happens to return a big payload today.
MAX_RESULT_CHARS = 200_000


def _cap_result(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return (
        text[:MAX_RESULT_CHARS]
        + f"\n\n[...truncated: {len(text):,} chars total, "
        f"showing the first {MAX_RESULT_CHARS:,}. Use a narrower request "
        "(e.g. background polling status only, or a specific file id via "
        "list_agent_response_files / download_agent_response_file) to see "
        "more of a specific part.]"
    )


# --- Native tools (server-side, always available) -------------------------


def web_search_tool() -> dict[str, Any]:
    """Verified fields: type, max_tokens, max_tokens_per_page, filters,
    user_location (OpenAPI WebSearchTool schema)."""
    return {
        "type": "web_search",
        "max_tokens": 6000,
        "max_tokens_per_page": 1200,
    }


def fetch_url_tool() -> dict[str, Any]:
    """Verified fields: type, max_urls (OpenAPI FetchUrlTool schema)."""
    return {"type": "fetch_url", "max_urls": 10}


def people_search_tool() -> dict[str, Any]:
    """Verified fields: type only (OpenAPI PeopleSearchTool schema)."""
    return {"type": "people_search"}


def finance_search_tool() -> dict[str, Any]:
    """Verified fields: type only (OpenAPI FinanceSearchTool schema)."""
    return {"type": "finance_search"}


def sandbox_tool() -> dict[str, Any]:
    """Verified fields: type only (OpenAPI SandboxTool schema)."""
    return {"type": "sandbox"}


def mcp_tool() -> Optional[dict[str, Any]]:
    """Only included when MCP_SERVER_URL is actually configured — a
    placeholder server would just fail every call, not degrade gracefully."""
    server_url = os.getenv("MCP_SERVER_URL")
    if not server_url:
        return None
    tool: dict[str, Any] = {
        "type": "mcp",
        "server_label": os.getenv("MCP_SERVER_LABEL", "default"),
        "server_url": server_url,
    }
    if os.getenv("MCP_AUTHORIZATION"):
        tool["authorization"] = os.environ["MCP_AUTHORIZATION"]
    return tool


# Every native tool, by the name create_agent_response's tool_names_json
# accepts — this dict is what that parameter builds tool configs from, so a
# one-off call can enable any combination the API allows (minus the
# sandbox + web_search pairing that call rejects; see its docstring).
#
# This is separate from the per-turn set: run_worker.py's
# _default_native_tools() picks which of these ride along on EVERY model
# call, and it deliberately picks sandbox + finance_search — a pair with no
# conflict, since the sandbox's preinstalled SDK already covers web search
# internally and finance_search is the one capability it doesn't.
_NATIVE_TOOL_BUILDERS = {
    "web_search": web_search_tool,
    "fetch_url": fetch_url_tool,
    "people_search": people_search_tool,
    "finance_search": finance_search_tool,
    "sandbox": sandbox_tool,
}


# --- Function tools: the rest of the Agent API, as activities -------------


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


@activity.defn
async def create_agent_response(
    task: str,
    preset: Optional[str] = None,
    model: Optional[str] = None,
    models: Optional[list[str]] = None,
    instructions: Optional[str] = None,
    background: Optional[bool] = None,
    max_steps: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    previous_response_id: Optional[str] = None,
    skills_json: Optional[str] = None,
    tool_names_json: Optional[str] = None,
    stream_topic_name: Optional[str] = None,
) -> str:
    """Submit a new Perplexity Agent API request — the full surface, not
    just one turn's native tools. Use this to spawn a sub-task with a
    different preset (fast/low/medium/high/xhigh/wide-research), with
    skills (office/pdf, office/docx, office/pptx, office/xlsx, or an inline
    skill), with its own native tools (including sandbox, which is
    unavailable in the default per-turn tool set — see below), or as a
    background job for anything long-running. background must be true for
    skills or wide-research.

    This runs to completion and returns the finished response JSON — its
    text, output items, usage and cost — so in the normal case you do NOT
    need to poll retrieve_agent_response afterwards. The sub-agent's output
    streams to the user live while it works. Only fall back to
    retrieve_agent_response (with the "id" from the returned JSON) if this
    call reports that the run outlived it.

    Args:
        task: The prompt or task for this request. (Named "task", not
            "input" — Perplexity's own request body field is called
            "input"; naming the tool parameter the same made the model
            consistently omit it when calling this tool, verified by
            direct testing: repeated live calls all failed the same way,
            "missing a required argument: 'input'", because the model
            never supplied a value for it.)
        preset: fast, low, medium, high, xhigh, or wide-research.
        model: A specific model id, e.g. "anthropic/claude-opus-4-8". Overrides the preset's model.
        models: A fallback chain of model ids (max 5), tried in order. Takes precedence over model.
        instructions: System instructions for this request.
        background: Run asynchronously; required for skills and wide-research.
        max_steps: Max research loop steps, 1-100. Overrides the preset default.
        max_output_tokens: Max tokens to generate. Omit it — it already
            defaults to the platform maximum (128,000). Only pass a value to
            deliberately cap a sub-request's length.
        reasoning_effort: minimal, low, medium, high, xhigh, or max.
        previous_response_id: Continue a prior completed response's conversation.
        skills_json: JSON-encoded array of skills, e.g.
            '[{"type": "builtin", "name": "office/pdf"}]' or an inline skill:
            '[{"type": "inline", "name": "house-style", "description": "...", "instructions": "..."}]'.
            Must be a JSON string, not a nested object — Perplexity rejects
            function-tool parameters typed as a bare object/array-of-object
            (confirmed by direct testing: even an empty {"type": "object"}
            schema with no declared properties is rejected outright).
        tool_names_json: JSON-encoded array of native tool names to enable
            for this specific request, chosen from "web_search", "fetch_url",
            "people_search", "finance_search", "sandbox", e.g. '["sandbox"]'.
            Omit to use no native tools for this call. "sandbox" and
            "web_search" may NOT both appear in the same list. Perplexity's
            own sandbox docs confirm why: the sandbox container ships with a
            preinstalled Perplexity SDK that can call web search internally,
            and declaring both tools together makes the model reach for that
            internal path (visible as a "skill_loaded": "pplx_sdk" output
            item) instead of the outer web_search tool; when that internal
            path doesn't pan out the model just refuses, and no instructions
            or tool_choice setting reliably prevents it — confirmed by
            repeated direct testing, including the exact instructions
            Perplexity itself suggests for forcing the outer tool. If you
            need both search and sandbox execution for one task, issue two
            separate create_agent_response calls instead.
    """
    import asyncio

    import perplexity as perplexity_sdk

    # Imported locally, like the SDK, because this module is pulled into the
    # workflow sandbox and perplexity_model imports the Perplexity SDK at
    # module scope.
    from perplexity_model import MAX_OUTPUT_TOKENS_CEILING, MAX_STEPS_CEILING

    api_key = os.environ["PERPLEXITY_API_KEY"]

    def _valid_model_id(m: str) -> bool:
        # "provider/model", both sides non-empty. Verified necessary by
        # direct testing: the model repeatedly sent model="/" — the docstring
        # already showed a correct example ("anthropic/claude-opus-4-8") and
        # that alone didn't stop it, so this is caught here instead of
        # letting every malformed value hit the API and fail with a 400.
        provider, _, name = m.partition("/")
        return bool(provider and name)

    model = model if model and _valid_model_id(model) else None
    models = [m for m in (models or []) if _valid_model_id(m)] or None

    # Same defensive normalization as _valid_model_id, for the same reason:
    # the model fills in optional parameters with invented values. Observed
    # live in a single call: model="My husband", models=["Hi I am lonely"],
    # previous_response_id="resp_123", and a builtin skill it was never asked
    # for. model/models were already stripped; the rest went through and the
    # Agent API rejected the whole request as invalid_request — which the agent
    # then retried until the turn limit, because nothing told it which field
    # was wrong.
    #
    # A real response id is "resp_" plus a UUID. "resp_123" is a fabrication,
    # and continuing a conversation that does not exist can only 400.
    if previous_response_id and not (
        previous_response_id.startswith("resp_") and len(previous_response_id) >= 21
    ):
        previous_response_id = None

    skills = json.loads(skills_json) if skills_json else None
    # Perplexity requires background for skills and for wide-research; this is
    # stated in this tool's own docstring, and the model still passed
    # background=false alongside a skill. Honor the documented requirement
    # rather than sending a request that cannot succeed.
    if (skills or preset == "wide-research") and background is False:
        background = None

    body: dict[str, Any] = {"input": task, "store": True}
    if preset:
        body["preset"] = preset
    if models:
        body["models"] = models
    elif model:
        body["model"] = model
    if not (preset or model or models):
        raise ValueError("One of preset, model, or models is required.")
    if instructions:
        body["instructions"] = instructions
    # Async by default, matching PerplexityModel's own baseline for every model
    # call. A background run is durable and recoverable — a dropped connection
    # or a retried activity can pick it back up via retrieve_agent_response
    # instead of losing the work — and it returns immediately rather than
    # holding an activity open for the whole sub-task. Left unset, this was the
    # one Agent API path in the codebase still making a blocking, synchronous
    # call. Passing background=false explicitly still forces a sync run.
    body["background"] = True if background is None else background
    # Same reasoning as MAX_OUTPUT_TOKENS_CEILING: give the sub-request the
    # platform maximum unless the caller deliberately narrows it, rather than
    # letting it fall back to whatever the preset happens to allow.
    body["max_steps"] = MAX_STEPS_CEILING if max_steps is None else max_steps
    if tool_names_json:
        names = json.loads(tool_names_json)
        unknown = set(names) - _NATIVE_TOOL_BUILDERS.keys()
        if unknown:
            raise ValueError(f"Unknown tool name(s) {sorted(unknown)}; choose from {sorted(_NATIVE_TOOL_BUILDERS)}.")
        if "sandbox" in names and "web_search" in names:
            raise ValueError(
                "sandbox and web_search cannot be requested together in one "
                "create_agent_response call — verified to make the model "
                "refuse to search. Issue two separate calls instead."
            )
        body["tools"] = [_NATIVE_TOOL_BUILDERS[name]() for name in names]
    # Always sent, at the platform maximum unless the caller deliberately
    # narrowed it. Anthropic models 400 without this field, and a small value
    # starves reasoning models into a model_error — see
    # MAX_OUTPUT_TOKENS_CEILING in perplexity_model.py.
    body["max_output_tokens"] = (
        max_output_tokens if max_output_tokens is not None else MAX_OUTPUT_TOKENS_CEILING
    )
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort}
    if previous_response_id:
        body["previous_response_id"] = previous_response_id
    if skills:
        body["skills"] = skills

    # Streamed, not fire-and-poll. `background=True` keeps the run durable
    # server-side; `stream=True` on the SAME call is what Perplexity documents
    # for watching it live ("Background runs are durable, so you can also
    # stream them and reconnect after a drop"), and `retrieve` has no stream
    # option, so create is the only place a sub-agent can be attached to at
    # all. Every event is republished to the workflow's stream topic, which is
    # how it reaches the sub-agent card in the UI instead of appearing only
    # after the job finished.
    #
    # Uses Perplexity's own SDK rather than the raw httpx call the other tools
    # in this module make, because responses.create IS covered by the SDK
    # (typed streaming included) — the httpx path exists for the /files
    # endpoints the SDK doesn't cover.
    body["stream"] = True
    client = perplexity_sdk.AsyncPerplexity(api_key=api_key)
    stream = await client.responses.create(**body)

    topic = None
    stack = None
    if stream_topic_name:
        from temporalio.contrib.workflow_streams import WorkflowStreamClient

        stack = WorkflowStreamClient.from_within_activity()
        await stack.__aenter__()
        topic = stack.topic(stream_topic_name)

    def publish(payload: dict[str, Any]) -> None:
        if topic is not None:
            topic.publish(payload)

    # A sub-agent run is a full agent turn; one cycle routinely exceeds the
    # heartbeat timeout, so heartbeat on a timer rather than per event.
    async def heartbeat_loop() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(10)

    pinger = asyncio.ensure_future(heartbeat_loop())
    response_id: Optional[str] = None
    text_parts: list[str] = []
    final: Optional[dict[str, Any]] = None
    try:
        async for event in stream:
            etype = event.type
            if etype == "response.created":
                response_id = getattr(getattr(event, "response", None), "id", None)
                # Emitted first so the UI can open the sub-agent card — and so
                # the id is recoverable via retrieve_agent_response even if
                # this activity later dies.
                publish({"subagent_id": response_id, "status": "created"})
            elif etype == "response.output_text.delta":
                text_parts.append(event.delta)
                publish({"subagent_id": response_id, "delta": event.delta})
            elif etype.startswith("response.reasoning.") or etype == "response.skill.loaded":
                from perplexity_model import PerplexityModel

                described = PerplexityModel._describe_reasoning_event(event)
                if described:
                    publish({"subagent_id": response_id, "reasoning": described})
            elif etype == "response.output_item.done":
                # search_results / sandbox_results / share_file etc. — the
                # structured items the sub-agent card renders.
                publish(
                    {
                        "subagent_id": response_id,
                        "output_item": event.item.model_dump(mode="json"),
                    }
                )
            elif etype == "response.failed":
                publish({"subagent_id": response_id, "error": event.error.message})
                # The API's own message for a rejected request is just
                # "invalid request" with no indication of which field is at
                # fault, so the agent retried the identical call until the turn
                # limit stopped it. Echoing back the parameters actually sent
                # (minus the prompt, which is the agent's own text) is what
                # lets it fix the offending one instead of guessing.
                sent = {k: v for k, v in body.items() if k != "input"}
                raise ApplicationError(
                    f"Sub-agent response failed: {event.error}. "
                    f"Parameters sent: {json.dumps(sent, default=str)}. "
                    "Do not retry with the same parameters — omit the optional "
                    "ones you are unsure about and send only task plus preset.",
                    non_retryable=getattr(event.error, "code", None)
                    in ("invalid_request", "model_error"),
                    type=str(getattr(event.error, "code", None) or "subagent_failed"),
                )
            elif etype == "response.completed":
                final = event.response.model_dump(mode="json") if event.response else None
                break
        # Published INSIDE the try, before the stream client is closed below.
        # Publishing after __aexit__ silently dropped it, so the sub-agent card
        # never left "running" and spun forever even though the run finished.
        publish({"subagent_id": response_id, "status": "completed"})
    finally:
        pinger.cancel()
        if stack is not None:
            await stack.__aexit__(None, None, None)

    # Prefer the API's own terminal response object; fall back to the pieces
    # actually observed if the stream ended without a completed event.
    result = final or {
        "id": response_id,
        "status": "incomplete",
        "output_text": "".join(text_parts),
        "note": (
            "Stream ended without a completed event. The run is durable — "
            "poll retrieve_agent_response with this id for the final state."
        ),
    }
    return _cap_result(json.dumps(result))


#  A background job takes real time; polling it faster than this wastes
# calls without new information. This is a floor built into the activity
# itself — not a docstring request for the model to pace itself — because
# a live test showed the model polling 17 times back-to-back with no gap
# at all. Asking nicely in the docstring doesn't reliably work (the same
# lesson learned earlier with argument names and tool combinations in this
# file); a hard floor here does, regardless of what the model does.
RETRIEVE_POLL_MIN_INTERVAL = timedelta(seconds=4)


@activity.defn
async def retrieve_agent_response(response_id: str) -> str:
    """Retrieve a stored Agent API response by id — its full JSON snapshot,
    including status, output, usage, and cost. Use to poll a background job
    (from create_agent_response) until status is completed, failed,
    cancelled, or incomplete. Returns 404-derived errors for unknown ids or
    responses created with store=false.

    This call always takes at least a few seconds — it waits before
    checking, since the job needs real time to progress and checking more
    often than that just burns calls for no new information. Use other
    tools, or reply with an interim status update, while a background job
    is still running instead of polling this in a tight loop.

    Args:
        response_id: The response id to retrieve, format resp_<...>.
    """
    import asyncio
    import httpx

    await asyncio.sleep(RETRIEVE_POLL_MIN_INTERVAL.total_seconds())

    api_key = os.environ["PERPLEXITY_API_KEY"]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{PERPLEXITY_BASE_URL}/agent/{response_id}", headers=_headers(api_key)
        )
        _raise_for_status(r)
        return _cap_result(json.dumps(r.json()))


@activity.defn
async def cancel_agent_response(response_id: str) -> str:
    """Cancel an in-flight background Agent API response. Returns
    status=cancelling immediately — poll retrieve_agent_response until the
    terminal cancelled status. Errors if the response is already terminal
    or unknown.

    Args:
        response_id: The response id to cancel, format resp_<...>.
    """
    import httpx

    api_key = os.environ["PERPLEXITY_API_KEY"]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{PERPLEXITY_BASE_URL}/agent/{response_id}/cancel",
            headers=_headers(api_key),
        )
        _raise_for_status(r)
        return _cap_result(json.dumps(r.json()))


@activity.defn
async def list_agent_response_files(response_id: str) -> str:
    """List files produced by a response — sandbox-generated artifacts
    (CSVs, JSON, documents from skills) that were shared. Returns metadata
    only (id, filename, bytes, created_at); use download_agent_response_file
    to fetch actual content. For a background job, only call this after it
    reaches a terminal status.

    Args:
        response_id: The response id whose files to list, format resp_<...>.
    """
    import httpx

    api_key = os.environ["PERPLEXITY_API_KEY"]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{PERPLEXITY_BASE_URL}/agent/{response_id}/files",
            headers=_headers(api_key),
        )
        _raise_for_status(r)
        return _cap_result(json.dumps(r.json()))


@activity.defn
async def download_agent_response_file(response_id: str, file_id: str) -> str:
    """Download a file produced by a response. file_id comes from
    list_agent_response_files — it is not the same as response_id. Returns
    the raw bytes decoded as UTF-8 text when possible (CSV/JSON/text
    artifacts). Binary files under 1.5MB are returned as
    '{"binary_content_base64": ..., "content_type": ...}' — real,
    decodable image bytes for e.g. sandbox-generated charts, not just a
    byte count — so the UI can actually render them. Larger binary files
    return only size and content type; download and inspect via other
    means for those.

    Args:
        response_id: The response id, format resp_<...>.
        file_id: The file id from list_agent_response_files.
    """
    import base64
    import httpx

    api_key = os.environ["PERPLEXITY_API_KEY"]
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(
            f"{PERPLEXITY_BASE_URL}/agent/{response_id}/files/{file_id}/content",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        _raise_for_status(r)
        try:
            return _cap_result(r.content.decode("utf-8"))
        except UnicodeDecodeError:
            content_type = r.headers.get("content-type", "")
            if len(r.content) <= 1_500_000:
                return json.dumps(
                    {
                        "binary_content_base64": base64.b64encode(r.content).decode("ascii"),
                        "content_type": content_type,
                        "binary_content_bytes": len(r.content),
                    }
                )
            return json.dumps({"binary_content_bytes": len(r.content), "content_type": content_type})


@activity.defn
async def create_contextualized_embedding(
    documents_json: str,
    model: str = "pplx-embed-context-v1-0.6b",
    dimensions: Optional[int] = None,
    encoding_format: Optional[str] = None,
) -> str:
    """Embed text for later semantic-similarity retrieval — Perplexity's
    contextualized embeddings, POST /v1/contextualizedembeddings. Chunks
    within the same document are encoded with awareness of each other, so
    grouping related chunks under one document (rather than embedding each
    in isolation) gives better retrieval quality. Use this to build memory:
    embed notes/facts/prior turns worth recalling later, store the
    response's embeddings (e.g. via list_agent_response_files-style
    external storage, or the FastAPI bridge's /embeddings/contextualized
    endpoint from outside the agent loop), and compare against a query
    embedding with cosine similarity to retrieve relevant context.

    Args:
        documents_json: JSON-encoded array of arrays of strings — the
            nested structure Perplexity requires. Each inner array is the
            chunks of ONE document, e.g.
            '[["User prefers dark mode.", "User is on the Pro plan."], ["Second document, one chunk."]]'.
            Must be a JSON string, not a nested array parameter — Perplexity
            function-tool schemas reject bare object/array-of-object
            parameters (same constraint as skills_json above). Max 512
            documents; max 16,000 chunks total; max 32K tokens per document.
        model: pplx-embed-context-v1-0.6b (1024 dims, cheaper/faster) or
            pplx-embed-context-v1-4b (2560 dims, higher quality).
        dimensions: Optional Matryoshka truncation. 128-1024 for the 0.6b
            model, 128-2560 for the 4b model. Omit for full dimensions.
        encoding_format: Optional: "base64_int8" or "base64_binary". Omit
            for the default. Embeddings are always returned base64-encoded
            (int8 array or packed bits, not raw floats) — decode
            accordingly.
    """
    import perplexity as perplexity_sdk

    api_key = os.environ["PERPLEXITY_API_KEY"]
    documents = json.loads(documents_json)
    client = perplexity_sdk.AsyncPerplexity(api_key=api_key)
    kwargs: dict[str, Any] = {"input": documents, "model": model}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    if encoding_format is not None:
        kwargs["encoding_format"] = encoding_format
    response = await client.contextualized_embeddings.create(**kwargs)
    return response.model_dump_json()


# Same bounded-retry shape workflow.py applies to model activities, applied
# here to the tool activities. The Strands plugin README's "Retries" section
# names activity_as_tool as one of the places retry config belongs; without
# it these tools inherit Temporal's default policy, which retries an
# unlimited number of times. Combined with no schedule_to_close ceiling, a
# persistently-failing tool would retry forever and the turn would hang —
# exactly what workflow.py's module docstring promises it doesn't do.
#
# Defined here rather than imported from workflow.py because workflow.py
# imports this module; importing back would be circular.
TOOL_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
)

# Short, bounded timeouts — these are thin HTTP calls, not the long-running
# work itself (that happens inside Perplexity's own background job, polled
# via retrieve_agent_response). Each start_to_close bounds ONE attempt; the
# schedule_to_close below bounds the whole retried lifetime.
def make_create_agent_response_tool(stream_topic_name: str) -> Any:
    """`create_agent_response` bound to one workflow's sub-agent stream topic.

    A factory rather than a plain `activity_as_tool` entry for the same reason
    `make_think_tool` is one: the topic name is workflow state, not something
    the model should see or choose. Binding it in this workflow-side wrapper
    keeps it out of the tool schema entirely — the model's parameter list is
    unchanged — while the activity still receives it. CompareWorkflow gives
    each pane its own topic this way, so sub-agent output stays attributable
    per model.

    The docstring the model reads is the activity's own, reused verbatim so
    there is exactly one description of this tool in the codebase.

    Timeouts are generous because this now runs the sub-task to completion
    rather than returning a queued id: a wide-research or skills run is real
    work, and cutting it short is what would force the agent back into
    polling. The run is durable regardless (background=True), so a genuine
    overrun is still recoverable by id via retrieve_agent_response.
    """
    from strands import tool
    from temporalio import workflow as _workflow

    async def create_agent_response_tool(
        task: str,
        preset: Optional[str] = None,
        model: Optional[str] = None,
        models: Optional[list[str]] = None,
        instructions: Optional[str] = None,
        background: Optional[bool] = None,
        max_steps: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        previous_response_id: Optional[str] = None,
        skills_json: Optional[str] = None,
        tool_names_json: Optional[str] = None,
    ) -> str:
        return await _workflow.execute_activity(
            create_agent_response,
            args=[
                task,
                preset,
                model,
                models,
                instructions,
                background,
                max_steps,
                max_output_tokens,
                reasoning_effort,
                previous_response_id,
                skills_json,
                tool_names_json,
                stream_topic_name,
            ],
            start_to_close_timeout=timedelta(minutes=30),
            schedule_to_close_timeout=timedelta(minutes=60),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=TOOL_RETRY_POLICY,
        )

    create_agent_response_tool.__name__ = "create_agent_response"
    create_agent_response_tool.__doc__ = create_agent_response.__doc__
    return tool(create_agent_response_tool)


# create_agent_response is NOT here — it needs a stream topic bound per
# workflow, so it comes from make_create_agent_response_tool() instead.
AGENT_API_FUNCTION_TOOLS = [
    activity_as_tool(
        retrieve_agent_response,
        start_to_close_timeout=timedelta(seconds=30),
        schedule_to_close_timeout=timedelta(minutes=5),
        retry_policy=TOOL_RETRY_POLICY,
    ),
    activity_as_tool(
        cancel_agent_response,
        start_to_close_timeout=timedelta(seconds=30),
        schedule_to_close_timeout=timedelta(minutes=5),
        retry_policy=TOOL_RETRY_POLICY,
    ),
    activity_as_tool(
        list_agent_response_files,
        start_to_close_timeout=timedelta(seconds=30),
        schedule_to_close_timeout=timedelta(minutes=5),
        retry_policy=TOOL_RETRY_POLICY,
    ),
    activity_as_tool(
        download_agent_response_file,
        start_to_close_timeout=timedelta(minutes=2),
        schedule_to_close_timeout=timedelta(minutes=10),
        retry_policy=TOOL_RETRY_POLICY,
    ),
    activity_as_tool(
        create_contextualized_embedding,
        start_to_close_timeout=timedelta(minutes=2),
        schedule_to_close_timeout=timedelta(minutes=10),
        retry_policy=TOOL_RETRY_POLICY,
    ),
]

AGENT_API_ACTIVITIES = [
    create_agent_response,
    retrieve_agent_response,
    cancel_agent_response,
    list_agent_response_files,
    download_agent_response_file,
    create_contextualized_embedding,
]
