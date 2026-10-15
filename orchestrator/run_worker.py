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
from dotenv import load_dotenv
from google import genai
from temporalio import activity
from temporalio.client import (
    Client, Schedule, ScheduleActionStartWorkflow, ScheduleAlreadyRunningError,
    ScheduleIntervalSpec, ScheduleOverlapPolicy, SchedulePolicy, ScheduleSpec,
)
from temporalio.contrib.strands import StrandsPlugin
from temporalio.worker import (
    ActivityInboundInterceptor, ExecuteActivityInput, Interceptor,
    UnsandboxedWorkflowRunner, Worker,
)

import think_activity
# Shared native-tools builders live in agent_api_tools; the public names are
# re-exported here so existing callers and tests keep working against
# run_worker.
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
    DEFAULT_MODEL_ID,
    DREAM_MIN_INTERVAL,
    GEMINI_MODEL_ID,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL_IDS,
    MAX_OUTPUT_TOKENS_CEILING,
    MAX_STEPS_CEILING,
    PERPLEXITY_API_BASE,
    PERPLEXITY_CATALOG_HTTP_TIMEOUT,
    PERPLEXITY_PRESETS,
    PROVIDER_GOOGLE_AI_STUDIO,
    PROVIDER_OUTPUT_CEILINGS,
    PROVIDER_PERPLEXITY_AGENT_API,
    RegisteredModel,
    REASONING_EFFORTS,
    TASK_QUEUE,
    THINK_MODEL_ID,
)
from delegation_hook import DELEGATION_SOP
from dream_workflow import DreamWorkflow
from gemini_model import GeminiModel
from perplexity_model import PRESET_PREFIX, PerplexityModel
import computer_use_activity
import subagent_support
from graph_activity import graph_activity
from graph_tool import configure_models
from load_tool import (
    discover_tools_activity,
    load_tool_activity,
    mcp_client_activity,
    run_loaded_tool,
)
import python_repl_activity
from skills_config import ensure_skills_configured, skills_dir
from telemetry import telemetry_plugins
from use_agent_activity import use_agent_activity
from use_skill_activity import use_skill_activity
from agent_tools import mcp_client_factories
from workflow import ChatWorkflow

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env.local", override=False)
os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
# Official strands_tools.file_write / editor / shell consent skip.
# STRANDS_NON_INTERACTIVE does not disable file_write's [y/*] prompt.
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

logger = logging.getLogger(__name__)

MCP_CONFIG_PATH = _ROOT / "mcp.json"
REASONING_LEVELS_PATH = _ROOT.parent / "lib" / "reasoning-levels.json"

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

    Every id gets ``max_output_tokens`` at the platform ceiling: a small
    budget lets a reasoning model spend everything thinking and emit no
    answer ("model produced no usable answer (reasoning_only)"). Presets
    merge request fields over their defaults, so the ceiling lifts the
    fast/low presets' published 8192/32768 budgets without changing the
    preset's model, prompt, or tools. Catalog provider/model ids get that
    provider's ceiling (``PROVIDER_OUTPUT_CEILINGS``); presets resolve to a
    model only server-side, so they get the platform ceiling.
    """
    return {
        **MODEL_PARAMS,
        "tools": tools,
        "skills": [dict(skill) for skill in BUILTIN_SKILLS],
        "max_output_tokens": (
            MAX_OUTPUT_TOKENS_CEILING if model_id.startswith(PRESET_PREFIX)
            else max_output_tokens_for(model_id)
        ),
    }


async def fetch_model_ids(api_key: str) -> list[str]:
    """Live model ids from the Perplexity catalog.

    GET /v1/models requires a bearer token as of 2026-08-02: without one it
    returns 401.
    """
    async with httpx.AsyncClient(timeout=PERPLEXITY_CATALOG_HTTP_TIMEOUT.total_seconds()) as http:
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
    logger.info(
        "Registered Agent API connectors: %s",
        [tool["server_label"] for tool in tools if tool.get("type") == "connector"],
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
    previous = json.loads(REASONING_LEVELS_PATH.read_text())
    levels = {key: previous[key] for key in GEMINI_MODEL_IDS if key in previous}
    efforts = sorted(REASONING_EFFORTS, key=("minimal", "low", "medium", "high", "xhigh", "max").index)
    levels.update({model_id: efforts for model_id in registered_ids})
    temporary = REASONING_LEVELS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(levels, indent=2) + "\n")
    temporary.replace(REASONING_LEVELS_PATH)
    return factories, catalog


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
    return name, f"{prompt}\n\n{DELEGATION_SOP}"


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
    key skips Gemini. SystemExit only when no factories remain.
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
    from agent_tools import PERMANENT_COMMUNITY_TOOLS, THINK_TOOL
    from load_tool import catalog_tools

    return [
        dict(tool.tool_spec)
        for tool in (*PERMANENT_COMMUNITY_TOOLS, *catalog_tools({}), THINK_TOOL)
    ]


def validate_outbound_tools() -> None:
    """Fail startup if any generated tool spec would poison the tools array.

    Every outer Perplexity request carries ALL registered tool definitions,
    so one invalid schema rejects the whole request before inference. This
    replays the exact outbound conversion (``PerplexityModel._format_request``:
    function entries with ``function_parameters``-normalized parameters,
    prepended natives) and runs the Agent API contract validator
    (``agent_api_tools._validate_tools``). Legal JSON Schema features
    pass through; only contract violations and non-JSON-serializable payloads
    fail.
    """
    from perplexity_model import function_parameters
    from agent_api_tools import _validate_skills, _validate_tools
    converted = [
        {
            "type": "function",
            "name": spec["name"],
            "description": spec.get("description", ""),
            "parameters": function_parameters(spec["inputSchema"]["json"]),
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
        _validate_skills([dict(skill) for skill in BUILTIN_SKILLS])
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
        discover_tools_activity,
        mcp_client_activity,
        run_loaded_tool,
        python_repl_activity.python_repl,
        think_activity.think,
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
        if cfg.get("continue_on_error"):
            continue
        if "command" in cfg:
            command = cfg["command"]
            if command == ".venv/bin/strands-shell" and shell_bin.is_file():
                command = str(shell_bin.resolve())
            raw_args = [str(item) for item in cfg.get("args", [])]
            args = []
            for index, item in enumerate(raw_args):
                previous = raw_args[index - 1] if index else ""
                candidate = _ROOT / "strands-tools" / item
                if previous == "--from" and candidate.exists():
                    args.append(str(candidate.resolve()))
                else:
                    args.append(item)
            env = cfg.get("env")
            if isinstance(env, dict):
                from load_tool import _expand_env

                expanded, missing = _expand_env(env)
                if missing:
                    logger.warning("MCP server '%s' skipped: missing %s", name, ", ".join(missing))
                    continue
                env = expanded
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


async def ensure_dream_schedule(client: Client, model_factories: dict[str, Callable]) -> None:
    model_id = next(
        (model for model in (THINK_MODEL_ID, GEMINI_MODEL_ID) if model in model_factories),
        None,
    )
    if model_id is None:
        raise RuntimeError("Dream requires a registered GPT 6 or Gemini 3.8 Flash factory")
    try:
        await client.create_schedule(
            "gwen-dream",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    DreamWorkflow.run, model_id, id="gwen-dream", task_queue=TASK_QUEUE,
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=DREAM_MIN_INTERVAL)]),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
    except ScheduleAlreadyRunningError:
        logger.info("Dream schedule already exists")


class OptionalMCPDiscoveryInterceptor(Interceptor):
    def __init__(self, optional_servers: set[str]) -> None:
        self.activities = {f"{name}-list-tools": name for name in optional_servers}

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _OptionalMCPDiscovery(next, self.activities)


class _OptionalMCPDiscovery(ActivityInboundInterceptor):
    def __init__(self, next: ActivityInboundInterceptor, activities: dict[str, str]) -> None:
        super().__init__(next)
        self.activities = activities

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        try:
            return await self.next.execute_activity(input)
        except Exception:
            server = self.activities.get(activity.info().activity_type)
            if server is None:
                raise
            logger.warning("Optional MCP server '%s' discovery failed; no tools available", server, exc_info=True)
            return []


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
    configured_servers = json.loads(MCP_CONFIG_PATH.read_text()).get("mcpServers", {})
    optional_servers = {
        name for name, cfg in configured_servers.items()
        if name in mcp_clients and cfg.get("continue_on_error")
    }
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
        workflows=[ChatWorkflow, CompareWorkflow, ModelCatalogWorkflow, DreamWorkflow],
        activities=[*WORKER_ACTIVITIES],
        workflow_runner=UnsandboxedWorkflowRunner(),
        interceptors=[OptionalMCPDiscoveryInterceptor(optional_servers)],
    )

    # One shared registry for every sub-agent activity (graph / use_agent /
    # use_skill); Think receives the same named factories below.
    subagent_support.configure(model_factories)
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
    await ensure_dream_schedule(client, model_factories)
    try:
        await worker.run()
    finally:
        for name in connected_mcp_servers:
            try:
                from strands_tools.mcp_client import mcp_client
                mcp_client(action="disconnect", connection_id=name)
            except Exception as error:
                logger.warning("MCP disconnect failed for %s: %s", name, error)


if __name__ == "__main__":
    asyncio.run(main())
