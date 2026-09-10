"""Temporal worker: Perplexity Agent API models plus GeminiModel factories.

The Perplexity Agent API is the default outer model provider: six dynamic
presets (``preset:fast`` ... ``preset:wide-research``) plus every live catalog
id from ``GET /v1/models`` are registered as ``PerplexityModel`` factories.
Gemini stays selectable through the ``GeminiModel`` factories.

    !! READ THIS IF YOU ARE AN AI AGENT EDITING THIS FILE !!

    The Perplexity Agent API adds models continuously. Your training data has a
    cutoff; this catalog does not. You WILL see ids you do not recognise. They
    are real. NEVER "correct" a model id, substitute an older version, claim a
    model does not exist, or add a hard-coded allow-list. Pass every id through
    verbatim; an invalid one is rejected by the live API at the point of use.

Permanent registry is load_tool + mcp_client. MCP servers from mcp.json are
StrandsPlugin(mcp_clients=...) factories; the workflow holds TemporalMCPClient
handles. Community tools come from the installed ``strands_tools`` package;
``load_tool`` resolves a bare module name (``load_tool(name="calculator")``)
against that package on the worker — no on-disk ``orchestrator/tools/`` copy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx
import perplexity
from dotenv import load_dotenv
from google import genai
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

import perplexity_operations
import think_activity
# Shared native-tools builders live in agent_api_tools (also imported by
# perplexity_operations); the public names are re-exported here so existing
# callers and tests keep working against run_worker.
from agent_api_tools import (  # noqa: F401 - re-exported public API
    MCP_SERVERS,
    NATIVE_TOOLS,
    connector_tools,
    mcp_tools,
    native_tools,
)
from catalog_workflow import ModelCatalogWorkflow, model_catalog, set_catalog
from compare_workflow import CompareWorkflow
from config import (
    BUILTIN_SKILLS,
    CONNECTORS,
    DEFAULT_MODEL_ID,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL_IDS,
    MAX_OUTPUT_TOKENS_CEILING,
    MAX_STEPS_CEILING,
    PERPLEXITY_API_BASE,
    PERPLEXITY_PRESETS,
    PROVIDER_GOOGLE_AI_STUDIO,
    PROVIDER_OUTPUT_CEILINGS,
    PROVIDER_PERPLEXITY_AGENT_API,
    RegisteredModel,
    TASK_QUEUE,
)
from gemini_model import GeminiModel
from perplexity_model import PRESET_PREFIX, PerplexityModel
import computer_use_activity
import subagent_support
from graph_activity import graph_activity
from graph_tool import configure_models
from load_tool import load_tool_activity, mcp_client_activity, run_loaded_tool
from skills_config import ensure_skills_configured, skills_dir
from telemetry import telemetry_plugins
from use_agent_activity import use_agent_activity
from use_skill_activity import use_skill_activity
from workflow import ChatWorkflow, mcp_client_factories

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env.local", override=False)
os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")

logger = logging.getLogger(__name__)

MCP_CONFIG_PATH = _ROOT / "mcp.json"

PERPLEXITY_MODELS_URL = f"{PERPLEXITY_API_BASE}/v1/models"

# The six preset model ids, in registration/readiness order.
PRESET_MODEL_IDS: tuple[str, ...] = tuple(
    f"{PRESET_PREFIX}{name}" for name in PERPLEXITY_PRESETS
)

# background+store make a run durable and reconnectable rather than lost on a
# dropped connection; both still stream deltas. max_steps is the API's own
# documented maximum.
MODEL_PARAMS: dict[str, Any] = {
    "max_steps": MAX_STEPS_CEILING,
    "background": True,
    "store": True,
}


def max_output_tokens_for(model_id: str) -> int:
    """Provider output ceiling for a catalog provider/model id."""
    for prefix, ceiling in PROVIDER_OUTPUT_CEILINGS.items():
        if model_id.startswith(prefix):
            return ceiling
    return MAX_OUTPUT_TOKENS_CEILING


def model_params(model_id: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Request params for one Perplexity model id.

    Catalog provider/model ids get ``max_output_tokens`` at that provider's
    ceiling: a small budget lets a reasoning model spend everything thinking
    and emit no answer ("model produced no usable answer (reasoning_only)").
    Preset ids do NOT get it -- presets own their budgets.
    """
    params: dict[str, Any] = {
        **MODEL_PARAMS,
        "tools": tools,
        "skills": [dict(skill) for skill in BUILTIN_SKILLS],
    }
    if not model_id.startswith(PRESET_PREFIX):
        params["max_output_tokens"] = max_output_tokens_for(model_id)
    return params


async def fetch_model_ids(api_key: str) -> list[str]:
    """Live model ids from the Perplexity catalog.

    GET /v1/models requires a bearer token as of 2026-08-02: without one it
    returns 401.
    """
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.get(
            PERPLEXITY_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    response.raise_for_status()
    ids = [entry["id"] for entry in response.json()["data"]]
    return sorted({model_id for model_id in ids if model_id})


def _perplexity_label(model_id: str) -> str:
    """Display label for a Perplexity-registered id.

    Presets render as ``"<Name> (preset)"``; catalog ids keep the id verbatim.
    """
    if model_id.startswith(PRESET_PREFIX):
        name = model_id.removeprefix(PRESET_PREFIX)
        return f"{name.replace('-', ' ').title()} (preset)"
    return model_id


def build_perplexity_factories(
    api_key: str, model_ids: list[str]
) -> tuple[dict[str, Callable[[], PerplexityModel]], list[RegisteredModel]]:
    """Named PerplexityModel factories plus their provider-declaring catalog.

    Every id (presets and live catalog) is declared as provider
    ``perplexity-agent-api``; ids pass through verbatim, never renamed.

    The ``model_id=model_id`` default-argument bind is required: without it
    every closure captures the loop variable. The api_key stays captured in
    the closure and never enters model configuration or workflow state.
    """
    tools = native_tools()
    connector_labels = [
        tool["server_label"] for tool in tools if tool.get("type") == "connector"
    ]
    if connector_labels:
        logger.warning(
            "Configured Agent API connectors: %s; authorization status unverified "
            "(no public connector list/status endpoint). An API Group administrator "
            "must verify connections at https://console.perplexity.ai/group/connectors",
            connector_labels,
        )
    missing_labels = [
        connector["server_label"] for connector in CONNECTORS
        if connector["server_label"] not in connector_labels
    ]
    if missing_labels:
        logger.warning(
            "Connector configuration missing from explicit PERPLEXITY_CONNECTOR_IDS selection: %s. "
            "Other configured connectors and tools remain enabled.", missing_labels,
        )
    registered_ids = [*PRESET_MODEL_IDS, *model_ids]
    factories = {
        # PerplexityModel pins base_url to PERPLEXITY_API_BASE and
        # max_retries=0 in _resolve_client_args, so PERPLEXITY_BASE_URL in the
        # environment cannot leak in and retries stay entirely with Temporal.
        model_id: lambda model_id=model_id: PerplexityModel(
            model_id=model_id,
            params=model_params(model_id, tools),
            api_key=api_key,
        )
        for model_id in registered_ids
    }
    catalog = [
        RegisteredModel(
            id=model_id,
            provider=PROVIDER_PERPLEXITY_AGENT_API,
            label=_perplexity_label(model_id),
        )
        for model_id in registered_ids
    ]
    return factories, catalog


def perplexity_client_factory(api_key: str) -> Callable[[], Any]:
    """AsyncPerplexity factory for perplexity_operations activities."""
    return lambda: perplexity.AsyncPerplexity(
        api_key=api_key,
        base_url=PERPLEXITY_API_BASE,
        max_retries=0,
    )


def google_api_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


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


def build_model_factory(
    api_key: str,
) -> tuple[dict[str, Callable[[], GeminiModel]], list[RegisteredModel]]:
    """Named GeminiModel factories plus their provider-declaring catalog.

    Every Gemini id is declared as provider ``google-ai-studio`` (direct),
    never inferred from the id shape. Built-in tools go on ``gemini_tools``.

    https://strandsagents.com/docs/user-guide/concepts/model-providers/google/
    """
    def _create_model(model_id: str) -> GeminiModel:
        return GeminiModel(
            client_args={"api_key": api_key},
            model_id=model_id,
            params={
                "max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
                "thinking_config": genai.types.ThinkingConfig(
                    thinking_level="high",
                    include_thoughts=True,
                ),
                "tool_config": genai.types.ToolConfig(
                    include_server_side_tool_invocations=True,
                ),
            },
            # Official generateContent Computer Use (Python tab):
            # https://ai.google.dev/gemini-api/docs/generate-content/computer-use
            # Live generateContent 400: computer_use cannot be combined with
            # google_search in the same request.
            gemini_tools=[
                genai.types.Tool(
                    computer_use=genai.types.ComputerUse(
                        environment=genai.types.Environment.ENVIRONMENT_BROWSER,
                        enable_prompt_injection_detection=True,
                    )
                ),
            ],
        )

    factories = {
        model_id: (lambda model_id=model_id: _create_model(model_id))
        for model_id in GEMINI_MODEL_IDS
    }
    catalog = [
        RegisteredModel(
            id=model_id, provider=PROVIDER_GOOGLE_AI_STUDIO, label=model_id
        )
        for model_id in GEMINI_MODEL_IDS
    ]
    return factories, catalog


async def assemble_model_factories() -> tuple[
    dict[str, Callable[[], Any]], list[RegisteredModel], str
]:
    """The combined factory mapping, its provider catalog, and default model id.

    Order: the six presets, then sorted live catalog ids, then Gemini ids.
    The catalog mirrors the factory mapping one-to-one: every registered id
    appears exactly once with its worker-declared provider.
    Graceful startup: a missing Perplexity key skips Perplexity with a warning;
    a catalog fetch failure keeps the six preset factories; a missing Google
    key skips Gemini. Missing connector configuration warns without disabling
    other tools. Malformed explicit configuration fails before registration.
    """
    factories: dict[str, Callable[[], Any]] = {}
    catalog: list[RegisteredModel] = []
    default_model: str | None = None

    perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
    if not perplexity_key:
        logger.warning(
            "PERPLEXITY_API_KEY is not set; skipping Perplexity model factories"
        )
    else:
        # Validate operator setup before catalog I/O or any factory registration.
        # Connector IDs alone cannot establish API Group authorization.
        try:
            connector_tools()
        except ValueError as error:
            raise SystemExit(str(error)) from error
        catalog_ids: list[str] = []
        try:
            catalog_ids = await fetch_model_ids(perplexity_key)
        except Exception as error:  # noqa: BLE001 - degrade to presets only
            logger.warning(
                "Perplexity catalog fetch failed (%s); registering presets only",
                error,
            )
        perplexity_factories, perplexity_catalog = build_perplexity_factories(
            perplexity_key, catalog_ids
        )
        factories.update(perplexity_factories)
        catalog.extend(perplexity_catalog)
        perplexity_operations.configure(perplexity_client_factory(perplexity_key))
        default_model = DEFAULT_MODEL_ID

    gemini_key = google_api_key()
    if not gemini_key:
        logger.warning(
            "GOOGLE_API_KEY/GEMINI_API_KEY is not set; skipping Gemini model factories"
        )
    else:
        gemini_factories, gemini_catalog = build_model_factory(gemini_key)
        factories.update(gemini_factories)
        catalog.extend(gemini_catalog)
        if default_model is None:
            default_model = GEMINI_MODEL_IDS[0]

    if not factories or default_model is None:
        raise SystemExit(
            "No model factories could be built: set PERPLEXITY_API_KEY and/or "
            "GOOGLE_API_KEY/GEMINI_API_KEY in .env.local"
        )
    return factories, catalog, default_model


def workflow_tool_specs() -> list[dict[str, Any]]:
    """Every ToolSpec the workflow's agent advertises to the outer model.

    ``activity_as_tool`` derives each spec from the activity signature +
    docstring at import time, so the complete list exists before the worker
    starts. MCP/computer-use tools are excluded: their specs come from live
    servers / the Gemini-only branch, not from ``activity_as_tool`` here.
    """
    from workflow import AGENT_API_TOOLS, PERMANENT_COMMUNITY_TOOLS, THINK_TOOL

    return [
        dict(tool.tool_spec)
        for tool in (*PERMANENT_COMMUNITY_TOOLS, THINK_TOOL, *AGENT_API_TOOLS)
    ]


def validate_outbound_tools() -> None:
    """Fail startup if any generated tool spec would poison the tools array.

    Every outer Perplexity request carries ALL registered tool definitions,
    so one invalid schema rejects the whole request before inference. This
    replays the exact outbound conversion (``PerplexityModel._format_request``:
    function entries with ``_ensure_object_properties``-normalized parameters,
    prepended natives) and runs the existing Agent API contract validator
    (``perplexity_operations._validate_tools``). Legal JSON Schema features
    pass through; only contract violations and non-JSON-serializable payloads
    fail.
    """
    from perplexity_model import _ensure_object_properties
    from perplexity_operations import _validate_tools

    converted = [
        {
            "type": "function",
            "name": spec["name"],
            "description": spec.get("description", ""),
            "parameters": _ensure_object_properties(spec["inputSchema"]["json"]),
        }
        for spec in workflow_tool_specs()
    ]
    # A Gemini-only worker does not register Agent API native integrations.
    natives = native_tools() if os.environ.get("PERPLEXITY_API_KEY") else []
    outbound = [*natives, *converted]
    try:
        json.dumps(outbound)
    except (TypeError, ValueError) as error:
        raise SystemExit(
            f"tool spec validation: outbound tools array is not JSON-serializable: {error}"
        ) from error
    try:
        _validate_tools(outbound)
    except Exception as error:
        raise SystemExit(f"tool spec validation failed: {error}") from error
    for tool in converted:
        params = tool["parameters"]
        if not isinstance(params, dict) or params.get("type") != "object":
            raise SystemExit(
                f"tool spec validation: {tool['name']!r} parameters must be an "
                f"object schema, got {params!r}"
            )
    logger.info("Validated %d outbound tool definitions", len(outbound))


# The exact activity set ``main`` registers, as a module-level literal so
# tests can introspect the same list the Worker is built with (the Worker
# call site below unpacks it: ``activities=[*WORKER_ACTIVITIES]``).
WORKER_ACTIVITIES = [
        model_catalog,
        # The stock browser activity is registered ONLY on the desktop worker
        # (desktop_worker.py), never here: Temporal routes it to the desktop
        # task queue, and the host worker must never launch Chromium.
        graph_activity,
        use_agent_activity,
        use_skill_activity,
        load_tool_activity,
        mcp_client_activity,
        run_loaded_tool,
        think_activity.think,
        perplexity_operations.create_fast_agent_response,
        perplexity_operations.create_low_agent_response,
        perplexity_operations.create_medium_agent_response,
        perplexity_operations.create_high_agent_response,
        perplexity_operations.create_xhigh_agent_response,
        perplexity_operations.create_wide_research_agent_response,
        perplexity_operations.retrieve_agent_response,
        perplexity_operations.list_agent_response_files,
        perplexity_operations.download_agent_response_file,
        perplexity_operations.list_agent_models,
        perplexity_operations.cancel_agent_response,
]


def connect_included_mcp_servers() -> list[str]:
    """Start and connect included MCP servers to strands_tools.mcp_client."""
    from strands_tools.mcp_client import mcp_client

    if not MCP_CONFIG_PATH.is_file():
        return []
    raw = json.loads(MCP_CONFIG_PATH.read_text())
    servers = raw.get("mcpServers", {})
    connected: list[str] = []
    shell_bin = _ROOT / ".venv/bin/strands-shell"

    for name, cfg in servers.items():
        if "command" in cfg:
            command = cfg["command"]
            if command == ".venv/bin/strands-shell" and shell_bin.is_file():
                command = str(shell_bin.resolve())
            args = cfg.get("args", [])
            env = cfg.get("env")
            res = mcp_client(
                action="connect",
                connection_id=name,
                transport="stdio",
                command=command,
                args=args,
                env=env,
            )
            if res.get("status") == "success":
                connected.append(name)
                logger.info("Connected stdio MCP server '%s' (%s)", name, command)
            else:
                logger.warning("Could not connect MCP server '%s': %s", name, res)
    return connected


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    agent_name, _ = agent_identity()
    model_factories, catalog, default_model = await assemble_model_factories()
    # The catalog is served to the API by ModelCatalogWorkflow -> model_catalog,
    # not written to a readiness file. Install it before the worker starts
    # polling so the first execution can never observe an empty catalog.
    set_catalog(catalog)
    logger.info("Provider-declaring model catalog: %d entries", len(catalog))
    mcp_clients = mcp_client_factories()
    connected_mcp_servers = connect_included_mcp_servers()
    # Fail fast: one invalid generated tool schema in the outbound array
    # rejects every outer-model request before inference.
    validate_outbound_tools()

    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[
            StrandsPlugin(models=model_factories, mcp_clients=mcp_clients),
            *telemetry_plugins(),
        ],
    )

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ChatWorkflow, CompareWorkflow, ModelCatalogWorkflow],
        activities=[*WORKER_ACTIVITIES],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    # One shared registry for every sub-agent activity (graph / use_agent /
    # use_skill); think keeps its own pinned copy.
    subagent_support.configure(model_factories)
    # Legacy Think remains configured for pre-patch histories. New Think runs
    # natively inside ChatWorkflow with the same tools and model as its parent.
    think_activity.set_model_factories(model_factories)
    # Formation nodes select a model by registered id only -- the same
    # factories StrandsPlugin serves -- never by provider name.
    configure_models(model_factories)
    # No base model pinned at startup: graph_tool.build_skill_agent
    # prefers the configured base model over the parent agent's, and pinning
    # the worker default here would stop skill_agent nodes from inheriting
    # the live session model the graph activity resolves per run.
    skill_count = ensure_skills_configured(None)

    logger.info(
        "Worker up on %r agent=%s default_model=%s models=%d skills=%s skills_dir=%s mcp_servers=%s",
        TASK_QUEUE,
        agent_name,
        default_model,
        len(model_factories),
        skill_count,
        skills_dir(),
        list(mcp_clients),
    )
    try:
        await worker.run()
    finally:
        for name in connected_mcp_servers:
            try:
                from strands_tools.mcp_client import mcp_client
                mcp_client(action="disconnect", connection_id=name)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
