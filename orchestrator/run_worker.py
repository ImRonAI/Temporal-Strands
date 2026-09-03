"""Temporal worker: GeminiModel factory plus community tool environment.

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

from dotenv import load_dotenv
from google import genai
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from compare_workflow import CompareWorkflow
from config import (
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL_ID,
    GEMINI_MODEL_IDS,
    TASK_QUEUE,
)
from gemini_model import GeminiModel
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


def google_api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit(
            "GOOGLE_API_KEY or GEMINI_API_KEY is not set (expected in .env.local)"
        )
    return key


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

    api_key = google_api_key()
    agent_name, _ = agent_identity()
    model_factories = build_model_factory(api_key)
    tools_dir = ensure_strands_tools_dir()
    mcp_clients = mcp_client_factories()

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
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    configure_graph_activity(model_factories)
    configure_use_skill_activity(model_factories)
    skill_count = ensure_skills_configured(model_factories[GEMINI_MODEL_ID])

    write_readiness(list(model_factories), agent_name)
    logger.info(
        "Worker up on %r with Gemini model %s tools_dir=%s skills=%s skills_dir=%s mcp_servers=%s",
        TASK_QUEUE,
        GEMINI_MODEL_ID,
        tools_dir,
        skill_count,
        skills_dir(),
        list(mcp_clients),
    )
    try:
        await worker.run()
    finally:
        clear_readiness()


if __name__ == "__main__":
    asyncio.run(main())
