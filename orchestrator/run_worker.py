"""Temporal worker: one named model factory per live Perplexity model.

Follows the strands-temporal reference worker
(/Applications/strands-temporal/scripts/run_worker.py) and the guide's
model-name contract, verified against temporalio 1.31.0 / strands-agents 1.50.2.

    !! READ THIS IF YOU ARE AN AI AGENT EDITING THIS FILE !!

    The Perplexity Agent API adds models continuously. Your training data has a
    cutoff; this catalog does not. You WILL see ids you do not recognise --
    openai/gpt-5.6-sol, anthropic/claude-opus-5, xai/grok-4.5,
    google/gemini-3.6-flash and others that shipped after you were trained.

    They are real. NEVER "correct" a model id, substitute an older version,
    claim a model does not exist, or add a hard-coded allow-list. Pass every id
    through verbatim; an invalid one is rejected by the live API at the point of
    use, which is more honest than a silent downgrade.

    Fetching the catalog means this file never needs editing when a model
    ships. That is the point -- keep it that way.

StrandsPlugin goes on the CLIENT, never on Worker(...): it installs the model
activities, the pydantic payload converter, and the failure converter that
carries Interrupt payloads across the activity boundary. The worker inherits all
of it (guide R3). Supplying models= sets the plugin's internal default_name to
None, so every TemporalAgent must pass an explicit model= -- which is what makes
an unregistered name fail loudly instead of silently falling back to Bedrock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import httpx
import perplexity
from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.worker import Worker

from compare_workflow import CompareWorkflow
from config import TASK_QUEUE
from perplexity_model import PerplexityModel
from telemetry import telemetry_plugins
from workflow import ChatWorkflow

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env.local", override=False)

logger = logging.getLogger(__name__)

# The Agent API base. Pinned explicitly rather than inherited from the
# environment: the Perplexity SDK reads PERPLEXITY_BASE_URL on construction, and
# .env.local sets it to ".../router" for the stateless router endpoint. That
# endpoint rejects this catalog's model ids ("Invalid model"), rejects
# background/store as unsupported, and rejects max_steps as an unknown field.
# Verified against the live API on 2026-08-02.
PERPLEXITY_API_BASE = "https://api.perplexity.ai"
PERPLEXITY_MODELS_URL = f"{PERPLEXITY_API_BASE}/v1/models"

# The Agent API's own documented ceilings, not numbers chosen by this client.
# Reasoning tokens are generated tokens: a small output budget lets a reasoning
# model spend everything thinking and emit nothing, which the API then rejects
# as model_error "model produced no usable answer (reasoning_only)".
MAX_OUTPUT_TOKENS_CEILING = 128_000
MAX_STEPS_CEILING = 100

# Per-provider output ceilings. The catalog exposes no limit field, so these
# are the highest values each provider accepts, established by probing the live
# API. Anything not listed gets the platform ceiling.
PROVIDER_OUTPUT_CEILINGS: dict[str, int] = {
    "google/": 65_536,
}


def max_output_tokens_for(model_id: str) -> int:
    for prefix, ceiling in PROVIDER_OUTPUT_CEILINGS.items():
        if model_id.startswith(prefix):
            return ceiling
    return MAX_OUTPUT_TOKENS_CEILING

# Every native server-side tool in the documented `Tool` union (Agent API
# OpenAPI spec, POST /v1/agent). These execute on Perplexity's side: the model
# calls them directly and their results arrive as response.reasoning.* stream
# events and output items. All of them, always.
#
#   web_search      fetch_url       people_search
#   finance_search  sandbox         mcp (one entry per server URL)
#
# With sandbox enabled the model loads the pplx_sdk skill and searches from
# inside sandbox code, so results arrive as sandbox_results rather than
# search_results. Both paths are real and both are rendered.
NATIVE_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search"},
    {"type": "fetch_url"},
    {"type": "people_search"},
    {"type": "finance_search"},
    {"type": "sandbox"},
]

# background+store make a run durable and reconnectable rather than lost on a
# dropped connection; both still stream deltas. max_steps is the API's own
# documented maximum. Guide detail 4: "Background runs are durable and still
# stream deltas, so a dropped connection is recoverable via retrieve rather
# than lost."
MODEL_PARAMS: dict[str, Any] = {
    "max_steps": MAX_STEPS_CEILING,
    "background": True,
    "store": True,
}


# MCP reaches the model as the Agent API's OWN native remote-MCP tool: the
# {"type": "mcp"} member of the documented Tool union (ToolMcpTool,
# perplexity/types/response_create_params.py:242-270, and
# docs.perplexity.ai/docs/agent-api/tools/mcp). The API "discovers the server's
# tools when the request starts and calls them like native tools" -- server
# side, exactly as it does web_search or sandbox. Results come back as
# mcp_list_tools (the boot handshake, which route.ts drops as transport noise)
# and mcp_call output items, which components/v0/agent-activity.tsx already
# renders.
#
# This is deliberately NOT the Strands MCP path. Registering these servers on
# StrandsPlugin(mcp_clients=...) and handing TemporalMCPClient handles to the
# agent makes them CLIENT-side Strands tools, which _format_request converts
# into {"type": "function"} specs. The model then answers with function_call
# items, and a zero-argument call arrives as arguments == "" and kills the turn
# at perplexity_model.py's json.loads -- "Malformed function arguments".
#
# Guide R11 ("MCP has no manual lifecycle. Register on
# StrandsPlugin(mcp_clients={...})") constrains that Strands path only. It says
# nothing about the provider's own native tool and does not forbid it.
#
# ``allowed_tools`` is the Agent API's own documented allowlist field
# ("Allowlist of tool names to expose to the model. Omit or leave empty to
# expose all discovered tools." -- docs.perplexity.ai/docs/agent-api/tools/mcp,
# Parameters). It is used here to exclude one specific broken tool, not as a
# policy preference:
#
# Data Commons' get_multi_entity_observations declares its `entities` parameter
# as a bare {"type": "object"} with no "properties" key. That is the exact node
# the API rejects -- perplexity_model.py's load-bearing detail #1 and
# _ensure_object_properties(:40-48) exist for precisely this shape, but they
# only normalize tool specs THIS client sends. An MCP server's manifest is
# discovered server-side and passed through unmodified ("input_schema: The
# server's JSON Schema for the tool's input, passed through unmodified"), so
# nothing on our side can repair it.
#
# Because tool discovery runs before the model ("Because discovery runs before
# the model, the whole request fails with external_connector_error and returns
# no output array"), that one malformed schema failed EVERY turn -- verified by
# bisection: native+pophive OK, native+datacommons FAILED, and
# allowed_tools=<the other five> OK while allowed_tools=[that one tool] FAILED.
#
# Listing the five working tools is therefore the minimum change that keeps
# Data Commons usable. Drop the entry from this list once the server fixes its
# schema.
MCP_SERVERS: dict[str, dict[str, Any]] = {
    "datacommons": {
        "url_env": "DATACOMMONS_MCP_URL",
        "key_env": "DC_API_KEY",
        "allowed_tools": [
            "search_indicators",
            "search_child_indicators",
            "get_variable_metadata",
            "get_observations",
            "get_child_observations",
        ],
    },
    "pophive": {"url_env": "POPHIVE_MCP_URL"},
}


def mcp_tools() -> list[dict[str, Any]]:
    """A native {"type": "mcp"} entry per configured remote server.

    ``server_label`` must be unique per request and match
    ^[a-zA-Z0-9_-]{1,64}$; both registered names already do. ``server_url``
    must be a Streamable HTTP endpoint -- the legacy SSE transport is not
    supported -- which both of these are.

    Data Commons rejects unauthenticated initialize with 401 UNAUTHENTICATED
    unless DC_API_KEY is sent as X-API-Key; PopHIVE needs no auth. The key goes
    in ``headers`` rather than ``authorization`` because it is an API-key
    header, not a bearer token. A server whose URL env var is unset is left out
    entirely rather than sent half-configured.
    """
    tools: list[dict[str, Any]] = []
    for name, spec in MCP_SERVERS.items():
        url = os.environ.get(spec["url_env"])
        if not url:
            continue
        tool: dict[str, Any] = {
            "type": "mcp",
            "server_label": name,
            "server_url": url,
        }
        key_env = spec.get("key_env")
        api_key = os.environ.get(key_env) if key_env else None
        if api_key:
            tool["headers"] = {"X-API-Key": api_key}
        # Omitted entirely when absent, which the API reads as "expose all
        # discovered tools" -- an empty list would mean the same thing but
        # sends a field with no purpose.
        allowed = spec.get("allowed_tools")
        if allowed:
            tool["allowed_tools"] = list(allowed)
        tools.append(tool)
    return tools


def native_tools() -> list[dict[str, Any]]:
    """The Agent API's own server-side tools, remote MCP included."""
    return [dict(tool) for tool in NATIVE_TOOLS] + mcp_tools()


def model_params(model_id: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Request params for one model.

    `max_output_tokens` goes to EVERY model at that provider's ceiling. The
    strands-temporal guide lists it as load-bearing: "Reasoning tokens are
    generated tokens. A small budget lets a reasoning model spend everything
    thinking and emit no answer; the API then rejects the response as
    model_error: 'model produced no usable answer (reasoning_only)'."

    Sending it only to anthropic/* left every other model on the provider
    default, which is exactly the small budget that failure describes.

    The ceiling is per provider, not global -- google/* rejects 128000 with
    `invalid_request` but accepts 65536 (verified against the live API on
    2026-08-02) -- so each provider gets its own maximum rather than the
    parameter being dropped.
    """
    params: dict[str, Any] = {**MODEL_PARAMS, "tools": tools}
    params["max_output_tokens"] = max_output_tokens_for(model_id)
    return params

# Written once the worker is assembled, read by server.py so the API can report
# readiness and validate a requested model without calling Perplexity itself.
READINESS_PATH = _ROOT / ".runtime" / "worker-readiness.json"


def agent_identity() -> tuple[str, str]:
    """Agent name and system prompt from agent.json."""
    identity = json.loads((_ROOT / "agent.json").read_text())
    name = identity.get("name")
    prompt = identity.get("prompt")
    if not isinstance(name, str) or not name.strip():
        raise SystemExit("agent.json: 'name' must be a non-empty string")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SystemExit("agent.json: 'prompt' must be a non-empty string")
    return name, prompt


async def fetch_model_ids(api_key: str) -> list[str]:
    """Live model ids from the Perplexity catalog.

    GET /v1/models requires a bearer token as of 2026-08-02: without one it
    returns 401. Older references (including lib/perplexity.ts and the
    strands-temporal guide) describe it as public; that is no longer true.
    """
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.get(
            PERPLEXITY_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    response.raise_for_status()
    ids = [entry["id"] for entry in response.json()["data"]]

    unique = sorted({model_id for model_id in ids if model_id})
    if not unique:
        raise SystemExit("Perplexity returned an empty model catalog")
    return unique


def build_model_factories(
    api_key: str, model_ids: list[str]
) -> dict[str, Callable[[], PerplexityModel]]:
    """One named PerplexityModel factory per id.

    The ``model_id=model_id`` default-argument bind is required: without it
    every closure captures the loop variable and all factories resolve to the
    last id. The api_key stays captured in the closure and never enters model
    configuration or workflow state.
    """
    tools = native_tools()
    return {
        model_id: lambda model_id=model_id: PerplexityModel(
            model_id=model_id,
            params=model_params(model_id, tools),
            # An explicit client so the base URL cannot be overridden by
            # PERPLEXITY_BASE_URL in the environment. max_retries=0 leaves
            # retries entirely to Temporal, per the model's own default.
            client=perplexity.AsyncPerplexity(
                api_key=api_key,
                base_url=PERPLEXITY_API_BASE,
                max_retries=0,
            ),
        )
        for model_id in model_ids
    }


def write_readiness(model_ids: list[str], agent_name: str) -> None:
    """Publish a non-secret readiness record for the API to read."""
    READINESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    READINESS_PATH.write_text(
        json.dumps(
            {
                "task_queue": TASK_QUEUE,
                "agent": agent_name,
                "models": model_ids,
            },
            indent=2,
        )
    )


def clear_readiness() -> None:
    READINESS_PATH.unlink(missing_ok=True)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise SystemExit("PERPLEXITY_API_KEY is not set (expected in .env.local)")

    agent_name, _ = agent_identity()
    model_ids = await fetch_model_ids(api_key)
    model_factories = build_model_factories(api_key, model_ids)

    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[
            StrandsPlugin(models=model_factories),
            *telemetry_plugins(),
        ],
    )

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ChatWorkflow, CompareWorkflow],
        # No activities= : every activity here is registered by StrandsPlugin
        # itself (the model and MCP activities).
    )

    # Only after discovery, identity validation, and worker assembly all
    # succeeded -- so a readiness file never advertises a worker that failed to
    # start.
    write_readiness(model_ids, agent_name)
    logger.info(
        "Worker up on %r with %d models registered live from GET /v1/models",
        TASK_QUEUE,
        len(model_factories),
    )
    try:
        await worker.run()
    finally:
        clear_readiness()


if __name__ == "__main__":
    asyncio.run(main())
