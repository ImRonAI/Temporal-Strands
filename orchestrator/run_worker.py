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
handles. Remaining community tools and any extra tool repos live under
orchestrator/tools for official load_tool (cwd()/tools/<name>.py).
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
from compare_workflow import CompareWorkflow
from config import (
    BUILTIN_SKILLS,
    DEFAULT_MODEL_ID,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL_IDS,
    MAX_OUTPUT_TOKENS_CEILING,
    MAX_STEPS_CEILING,
    PERPLEXITY_API_BASE,
    PERPLEXITY_PRESETS,
    PROVIDER_OUTPUT_CEILINGS,
    TASK_QUEUE,
)
from gemini_model import GeminiModel
from perplexity_model import PRESET_PREFIX, PerplexityModel
import computer_use_activity
from graph_activity import configure as configure_graph_activity
from graph_activity import graph_activity
from load_tool import mcp_client_activity, run_loaded_tool
from skills_config import ensure_skills_configured, skills_dir
from telemetry import telemetry_plugins
from use_skill_activity import configure as configure_use_skill_activity
from use_skill_activity import use_skill_activity
from workflow import ChatWorkflow, mcp_client_factories

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env.local", override=False)
os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")

logger = logging.getLogger(__name__)

READINESS_PATH = _ROOT / ".runtime" / "worker-readiness.json"
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


def build_perplexity_factories(
    api_key: str, model_ids: list[str]
) -> dict[str, Callable[[], PerplexityModel]]:
    """One named PerplexityModel factory per preset and catalog id.

    The ``model_id=model_id`` default-argument bind is required: without it
    every closure captures the loop variable. The api_key stays captured in
    the closure and never enters model configuration or workflow state.
    """
    tools = native_tools()
    logger.info(
        "Registered Agent API connectors: %s",
        [tool["server_label"] for tool in tools if tool.get("type") == "connector"],
    )
    return {
        model_id: lambda model_id=model_id: PerplexityModel(
            model_id=model_id,
            params=model_params(model_id, tools),
            # An explicit client so the base URL cannot be overridden by
            # PERPLEXITY_BASE_URL in the environment. max_retries=0 leaves
            # retries entirely to Temporal.
            client=perplexity.AsyncPerplexity(
                api_key=api_key,
                base_url=PERPLEXITY_API_BASE,
                max_retries=0,
            ),
        )
        for model_id in [*PRESET_MODEL_IDS, *model_ids]
    }


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


_SKIP_COMMUNITY_FILES = frozenset(
    {"__init__.py", "load_tool.py", "mcp_client.py", "think.py"}
)


def ensure_strands_tools_dir() -> Path:
    """Real orchestrator/tools/ directory for official load_tool.

    Links unregistered strands-agents-tools modules here. Clone extra
    tool repos into this same directory as subfolders.
    """
    import strands_tools

    src = Path(strands_tools.__file__).resolve().parent
    dest = _ROOT / "tools"
    if dest.is_symlink():
        dest.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    for py_file in sorted(src.glob("*.py")):
        if py_file.name in _SKIP_COMMUNITY_FILES or py_file.name.startswith("_"):
            continue
        link = dest / py_file.name
        if link.exists() and not link.is_symlink():
            continue
        if link.is_symlink() and link.resolve() == py_file.resolve():
            continue
        if link.is_symlink():
            link.unlink()
        link.symlink_to(py_file)
    return dest


def build_model_factory(api_key: str) -> dict[str, Callable[[], GeminiModel]]:
    """Named GeminiModel factory. Built-in tools go on ``gemini_tools``.

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

    return {
        model_id: (lambda model_id=model_id: _create_model(model_id))
        for model_id in GEMINI_MODEL_IDS
    }


async def assemble_model_factories() -> tuple[dict[str, Callable[[], Any]], str]:
    """The combined factory mapping and its default model id.

    Order: the six presets, then sorted live catalog ids, then Gemini ids.
    Graceful startup: a missing Perplexity key skips Perplexity with a warning;
    a catalog fetch failure keeps the six preset factories; a missing Google
    key skips Gemini. SystemExit only when no factories remain.
    """
    factories: dict[str, Callable[[], Any]] = {}
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
        factories.update(build_perplexity_factories(perplexity_key, catalog_ids))
        perplexity_operations.configure(perplexity_client_factory(perplexity_key))
        default_model = DEFAULT_MODEL_ID

    gemini_key = google_api_key()
    if not gemini_key:
        logger.warning(
            "GOOGLE_API_KEY/GEMINI_API_KEY is not set; skipping Gemini model factories"
        )
    else:
        factories.update(build_model_factory(gemini_key))
        if default_model is None:
            default_model = GEMINI_MODEL_IDS[0]

    if not factories or default_model is None:
        raise SystemExit(
            "No model factories could be built: set PERPLEXITY_API_KEY and/or "
            "GOOGLE_API_KEY/GEMINI_API_KEY in .env.local"
        )
    return factories, default_model


def write_readiness(
    model_ids: list[str], agent_name: str, default_model: str
) -> None:
    """Publish a non-secret readiness record for the API to read."""
    READINESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    READINESS_PATH.write_text(
        json.dumps(
            {
                "task_queue": TASK_QUEUE,
                "agent": agent_name,
                "models": model_ids,
                "default_model": default_model,
            },
            indent=2,
        )
    )


def clear_readiness() -> None:
    READINESS_PATH.unlink(missing_ok=True)


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
        elif "url" in cfg:
            url = os.path.expandvars(cfg["url"])
            if not url or url.startswith("${"):
                continue
            headers = {k: os.path.expandvars(v) for k, v in cfg.get("headers", {}).items()}
            res = mcp_client(
                action="connect",
                connection_id=name,
                transport="streamable_http",
                server_url=url,
                headers=headers or None,
            )
            if res.get("status") == "success":
                connected.append(name)
                logger.info("Connected HTTP MCP server '%s' (%s)", name, url)
            else:
                logger.warning("Could not connect MCP server '%s': %s", name, res)
    return connected


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    agent_name, _ = agent_identity()
    model_factories, default_model = await assemble_model_factories()
    tools_dir = ensure_strands_tools_dir()
    mcp_clients = mcp_client_factories()
    connected_mcp_servers = connect_included_mcp_servers()

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
        workflows=[ChatWorkflow, CompareWorkflow],
        activities=[
            *computer_use_activity.COMPUTER_USE_ACTIVITIES,
            graph_activity,
            use_skill_activity,
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
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    configure_graph_activity(model_factories)
    configure_use_skill_activity(model_factories)
    think_activity.configure(model_factories)
    skill_count = ensure_skills_configured(model_factories[default_model])

    write_readiness(list(model_factories), agent_name, default_model)
    logger.info(
        "Worker up on %r default_model=%s models=%d tools_dir=%s skills=%s skills_dir=%s mcp_servers=%s",
        TASK_QUEUE,
        default_model,
        len(model_factories),
        tools_dir,
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
        clear_readiness()


if __name__ == "__main__":
    asyncio.run(main())
