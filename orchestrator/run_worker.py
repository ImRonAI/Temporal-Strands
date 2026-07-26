"""Run the Temporal worker for the Perplexity-backed orchestrator.

Registers one named model factory per model returned by Perplexity's live
GET /v1/models on StrandsPlugin — the officially documented
`models: dict[str, Callable[[], Model]]` mapping
(https://docs.temporal.io/develop/python/integrations/strands-agents#choose-and-configure-models),
populated dynamically from the live catalog instead of hand-typed, so every
model the frontend's model picker can select is available here under the
exact same id string.
"""

import asyncio
import os
from pathlib import Path

import httpx
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.worker import Worker

from mcp_config import MCP_ENABLED, mcp_client_factories
from perplexity_model import MAX_OUTPUT_TOKENS_CEILING, PerplexityModel
from perplexity_tools import AGENT_API_ACTIVITIES, finance_search_tool, sandbox_tool
from telemetry import telemetry_plugins
from thinking_activity import run_thinking_cycles
from compare_workflow import CompareWorkflow
from workflow import ChatWorkflow

# Load the repo-root .env.local (PERPLEXITY_API_KEY, optional MCP_* / OTEL_*)
# so this process works whether it is launched directly or by `pnpm dev`.
# python-dotenv is already a declared dependency. Real environment variables
# win — override=False — so an explicitly exported key is never clobbered.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local", override=False)

PERPLEXITY_BASE_URL = "https://api.perplexity.ai/v1"

# The platform maximum, shared with every other Agent API caller — see
# MAX_OUTPUT_TOKENS_CEILING in perplexity_model.py. This value applies to the
# chat session AND the compare view, since both resolve their model through
# these same factories.
#
# It was 2048, and that caused real failures: reasoning tokens are generated
# tokens, so a reasoning model would spend the entire 2048-token budget
# thinking and have nothing left for the answer. The Agent API then rejects the
# whole response with
#   model_error: "model produced no usable answer (reasoning_only)"
# which surfaced as a 15-minute dead turn once MODEL_RETRY_POLICY had retried
# it six times. The agent was being starved by its own client.
DEFAULT_MAX_OUTPUT_TOKENS = MAX_OUTPUT_TOKENS_CEILING


def _default_native_tools() -> list[dict]:
    """sandbox + finance_search, attached to every turn.

    sandbox's preinstalled SDK already covers web_search, fetch_url, and
    people_search internally (confirmed in Perplexity's own sandbox docs) —
    finance_search is the one native capability it does NOT cover, so it's
    declared alongside it. This only works reliably because PerplexityModel
    sets an explicit, generous max_steps by default; see that module's
    docstring for why the same tools without it reliably failed."""
    return [sandbox_tool(), finance_search_tool()]


async def build_model_factories(api_key: str) -> dict:
    """One named PerplexityModel factory per live Perplexity model id, each
    carrying the default native tool set (see _default_native_tools).

    Async because it is awaited from main()'s event loop — a blocking
    httpx.get() here stalls that loop for the length of the round trip, which
    is the one synchronous HTTP call that was left in the process."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{PERPLEXITY_BASE_URL}/models")
    resp.raise_for_status()
    model_ids = [m["id"] for m in resp.json()["data"]]

    tools = _default_native_tools()
    factories = {}
    for model_id in model_ids:
        factories[model_id] = lambda model_id=model_id: PerplexityModel(
            api_key=api_key,
            model_id=model_id,
            params={"max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS, "tools": tools},
        )
    return factories


async def main() -> None:
    api_key = os.environ["PERPLEXITY_API_KEY"]
    model_factories = await build_model_factories(api_key)

    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[
            *telemetry_plugins(),
            # mcp_clients is empty unless MCP_SERVER_URL is set, in which case
            # the plugin registers no MCP activities at all — matching the
            # workflow side, which only builds a TemporalMCPClient under the
            # same condition. See mcp_config.py.
            StrandsPlugin(
                models=model_factories,
                mcp_clients=mcp_client_factories(),
            ),
        ],
    )

    worker = Worker(
        client,
        task_queue="perplexity-orchestrator",
        workflows=[ChatWorkflow, CompareWorkflow],
        # The Agent-API-as-tool activities from perplexity_tools.py — every
        # @activity.defn function wrapped by activity_as_tool must be listed
        # here or it fails at call time with ActivityNotRegisteredError.
        # run_thinking_cycles is a plain activity backing the agent's
        # `think` tool (the wrapper in workflow.py dispatches it directly)
        # and needs the same registration for the same reason.
        activities=[*AGENT_API_ACTIVITIES, run_thinking_cycles],
    )

    print(
        f"Worker started on task queue 'perplexity-orchestrator' with "
        f"{len(model_factories)} models registered (live from GET /v1/models, "
        f"via PerplexityModel), {len(_default_native_tools())} native tools "
        f"per turn + {len(AGENT_API_ACTIVITIES)} Agent API function tools, "
        f"MCP {'enabled' if MCP_ENABLED else 'not configured'}. "
        f"Press Ctrl+C to exit."
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
