import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

from temporalio.common import RetryPolicy

TASK_QUEUE = "perplexity-orchestrator"

# --- Provider declaration ---
# Providers are declared by the worker, never inferred client-side from id
# shapes: a "gemini*" id could mean Google AI Studio (direct) or google/* via
# the Perplexity gateway. Every registered model carries its declared provider.
PROVIDER_PERPLEXITY_AGENT_API = "perplexity-agent-api"
PROVIDER_GOOGLE_AI_STUDIO = "google-ai-studio"
PROVIDER_DISPLAY_NAMES = {
    "perplexity-agent-api": "Perplexity Agent API",
    "google-ai-studio": "Google AI Studio",
}


@dataclass(frozen=True)
class RegisteredModel:
    """A worker-registered model id with its declared provider and label."""

    id: str
    provider: str
    label: str
# Desktop browser routing: the stock strands browser activity executes only on
# the desktop worker, which polls this queue from inside the Linux desktop
# (Xvfb). Native Temporal task-queue routing — no custom platform code.
DESKTOP_BROWSER_TASK_QUEUE = "desktop-browser"
# Native x11vnc remote-control command used to fence/grant desktop input.
# Executed verbatim by the API on handoff; the default targets the local pilot
# container. Set to the deployment's equivalent for GCE.
DESKTOP_VNC_GRANT_COMMAND = os.environ.get(
    "DESKTOP_VNC_GRANT_COMMAND",
    "docker --context colima exec gwen-desktop x11vnc -display :99 -R script:noviewonly;nodeny -Q viewonly,deny",
)
DESKTOP_VNC_REVOKE_COMMAND = os.environ.get(
    "DESKTOP_VNC_REVOKE_COMMAND",
    "docker --context colima exec gwen-desktop x11vnc -display :99 -R script:viewonly;deny;disconnect:all;clear_all;fakebuttonevent:1,0;fakebuttonevent:2,0;fakebuttonevent:3,0 -Q viewonly,client_count,pointer_mask",
)
DESKTOP_VNC_COMMAND_TIMEOUT = 15
DESKTOP_HANDOFF_TIMEOUT = timedelta(seconds=45)
# --- Perplexity Agent API (outer model provider) ---
# Pinned explicitly rather than inherited from the environment: the Perplexity
# SDK reads PERPLEXITY_BASE_URL on construction, and .env.local may point it at
# the stateless router endpoint, which rejects catalog model ids and
# background/store/max_steps.
PERPLEXITY_API_BASE = "https://api.perplexity.ai"
# Perplexity-only ``response.output_item.done`` item types. These are the
# server-side tool payloads in the Agent API's OutputItem union that the
# OpenAI Responses stream loop has no branch for and silently skips
# (strands/models/openai_responses.py:334-449). PerplexityModel taps the raw
# SSE body and re-emits each one verbatim under a ``{"perplexity": ...}``
# StreamEvent so the UI can render its tool card. "message" is deliberately
# absent: its text already streams as output_text deltas.
NATIVE_OUTPUT_ITEM_TYPES = (
    "search_results",
    "fetch_url_results",
    "sandbox_results",
    "sandbox_write_file",
    "share_file",
    "mcp_call",
    "skill_loaded",
    "advisor_result",
)
# The six documented dynamic presets, in registration/readiness order. Each is
# registered as model id "preset:<name>" (perplexity_model.PRESET_PREFIX).
PERPLEXITY_PRESETS = ("fast", "low", "medium", "high", "xhigh", "wide-research")
# Session default whenever the Perplexity key is present; otherwise the worker
# falls back to the first Gemini id.
DEFAULT_MODEL_ID = "preset:high"
# Builtin Agent API skills attached to every outer PerplexityModel request.
# Tuple of mappings; consumers copy before sending.
BUILTIN_SKILLS = tuple(
    {"type": "builtin", "name": name}
    for name in (
        "office",
        "office/pdf",
        "office/docx",
        "office/pptx",
        "office/xlsx",
    )
)
# Dashboard-authorized Agent API connectors, attached to every request as
# native {"type": "connector"} tools (agent_api_tools.connector_tools).
# Authorization lives in the Perplexity dashboard; requests only reference the
# connector id. Overridable via PERPLEXITY_CONNECTOR_IDS as comma-separated
# label=id pairs (see agent_api_tools.CONNECTOR_IDS_ENV).
CONNECTORS: tuple[dict[str, str], ...] = (
    {
        "id": "connector_googledrive",
        "server_label": "google_drive",
        "server_description": (
            "The user's Google Drive: search, read, and reference their "
            "Docs, Sheets, Slides, and files."
        ),
    },
    {
        "id": "connector_github",
        "server_label": "github",
        "server_description": (
            "The user's GitHub: repositories, issues, pull requests, and "
            "file contents; credentials are shared into the sandbox for "
            "git/gh."
        ),
    },
)
# The Agent API's own documented ceilings, not numbers chosen by this client.
MAX_STEPS_CEILING = 100
MAX_OUTPUT_TOKENS_CEILING = 128_000
# Per-provider output ceilings for catalog provider/model ids. The catalog
# exposes no limit field; google/* rejects 128000 but accepts 65536 (verified
# against the live API on 2026-08-02). Anything not listed gets the platform
# ceiling. Presets own their budgets and never get max_output_tokens.
PROVIDER_OUTPUT_CEILINGS = {"google/": 65_536}
GEMINI_MODEL_ID = "gemini-3.8-flash"
# Every id the worker registers a GeminiModel factory for (and advertises in
# the readiness file). 3.7 is the fallback while 3.8 returns 503 UNAVAILABLE
# under load; gemini-flash-latest is Google's dynamic alias.
GEMINI_MODEL_IDS = (GEMINI_MODEL_ID, "gemini-3.7-flash", "gemini-flash-latest")
GEMINI_MAX_OUTPUT_TOKENS = 65_536
# Agent API / non-Gemini providers: 120k output tokens. Gemini is 65,536
# (official model-page maximum output tokens).
MAX_OUTPUT_TOKENS = 120_000
# Temporal activity options. Timeouts: TypeScript ActivityOptions documents
# scheduleToCloseTimeout default as unlimited; either StartToClose or
# ScheduleToClose must be set. We do not cap ScheduleToClose. StartToClose
# is left unset so a single model/tool attempt is not killed at 10 minutes.
# RetryPolicy() is the official SDK default: 1s / 2.0 / 100×initial /
# maximum_attempts=0 (unlimited).
# https://docs.temporal.io/encyclopedia/retry-policies
# https://python.temporal.io/temporalio.common.RetryPolicy.html
# https://typescript.temporal.io/api/interfaces/workflow.ActivityOptions
MODEL_START_TO_CLOSE: Optional[timedelta] = None
MODEL_SCHEDULE_TO_CLOSE: Optional[timedelta] = None
MODEL_HEARTBEAT: Optional[timedelta] = None
MODEL_RETRY_POLICY = RetryPolicy()
# Temporal validates that every activity carries start_to_close_timeout OR
# schedule_to_close_timeout (_workflow_instance._outbound_schedule_activity).
# The envelopes above are deliberately unset ("we do not cap"), which that
# validation rejects at schedule time. Callers wrap their activity options in
# ``closable_activity_options`` so a generous schedule-to-close fallback is
# applied only when both timeouts are None (graceful degradation, matching
# workflow.py's ``_closable``).
UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE = timedelta(days=1)

# Outer TemporalAgent streaming batch interval (workflow.py's
# ``streaming_batch_interval``). Temporal's own value for LLM streaming
# (docs.temporal.io, "Stream LLM output"). Every batch is a durable Signal
# appended to workflow history, so this is a history-pressure dial, not a
# latency dial: at 25 ms a single turn produced 5,158 signals and 24,953
# history events, and the workflow spent its time replaying history instead
# of streaming.
MODEL_STREAM_BATCH_INTERVAL = timedelta(milliseconds=200)
SSE_SUBSCRIBE_RESTART_LIMIT = 3
SSE_SUBSCRIBE_RESTART_DELAY = 0.25
SSE_HEARTBEAT_SECONDS = 10


def closable_activity_options(options: dict) -> dict:
    """Ensure the SDK's required timeout is present, preserving config intent."""
    if options.get("start_to_close_timeout") or options.get(
        "schedule_to_close_timeout"
    ):
        return options
    return {
        **options,
        "schedule_to_close_timeout": UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE,
    }
# Activity-side nested streams (think / graph / use_agent / use_skill) publish
# every nested-agent chunk on the thinking topic. These are multi-minute runs
# (graph: up to 35 min), not one ~30 s completion, and every flushed batch is a
# durable Signal in the hosting ChatWorkflow's history: one graph activity on
# chat-2a72daf5c76ec25f produced 1,769 Signals / 15.5 MB at 200 ms. This is
# Temporal's documented WorkflowStreamClient default
# (docs.temporal.io/workflow-streams#tuning). The outer TemporalAgent's own
# streaming_batch_interval in workflow.py is the documented LLM-completion case
# and is unaffected.
THINK_STREAM_BATCH_INTERVAL = timedelta(seconds=2)
# The think sub-agent runs up to 10 minutes wall-clock (multi-minute nested
# stream, not a ~30 s completion); the heartbeat keeps Temporal aware the
# stream is alive between flushed batches. Exactly one automatic attempt: a
# replayed think is a duplicate inference, so a failed one is deliberately
# retried by the orchestrator, not blindly replayed.
THINK_START_TO_CLOSE = timedelta(minutes=10)
THINK_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
THINK_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
# --- Formation graph / sub-agent activities ---
# A whole formation replay is expensive and non-idempotent (every node call
# is billable inference), so like the think envelope the graph and use_agent
# activities get exactly one automatic attempt; a failed formation is
# re-formed by the orchestrator deliberately, not blindly replayed.
GRAPH_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
# Whole-formation wall clock bound applied inside the activity (asyncio
# timeout around graph execution) and as the activity start_to_close.
GRAPH_EXECUTION_TIMEOUT = timedelta(minutes=30)
GRAPH_START_TO_CLOSE = timedelta(minutes=35)
# Heartbeats are per streamed chunk plus this quiet-period ticker, so the
# heartbeat timeout can be tight relative to the run length.
GRAPH_QUIET_HEARTBEAT_INTERVAL = timedelta(seconds=15)
GRAPH_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
# use_agent: one isolated sub-agent turn -- same envelope class as think.
USE_AGENT_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
USE_AGENT_START_TO_CLOSE = timedelta(minutes=15)
USE_AGENT_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
# --- Perplexity Agent API operation activities (perplexity_operations.py) ---
# Background+streamed preset runs can research for a long time; the heartbeat
# keeps Temporal aware the stream is alive between events.
AGENT_OPERATION_START_TO_CLOSE: Optional[timedelta] = None
AGENT_OPERATION_SCHEDULE_TO_CLOSE: Optional[timedelta] = None
AGENT_OPERATION_HEARTBEAT: Optional[timedelta] = None
AGENT_OPERATION_RETRY_POLICY = RetryPolicy()
# The Agent API documents no idempotency key for POST /v1/responses, so an
# automatic retry after an AMBIGUOUS failure (start-to-close timeout, dropped
# stream after the request was accepted) could start a second billable
# background run. The six preset create activities therefore get exactly one
# automatic attempt; recovery from an ambiguous create failure is deliberate
# (the orchestrator can call retrieve_agent_response). Retrieve/list/download
# are read-only and keep AGENT_OPERATION_RETRY_POLICY's ordinary retries.
AGENT_CREATE_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
# Sub-agent run events ride their own topic. Unlike the think stream (one
# short model call), a preset create_* activity streams a background run for
# many minutes, and every flushed batch is a durable Signal in the hosting
# ChatWorkflow's history. At 200 ms one turn produced ~1,000 publish Signals /
# 20 MB (chat-be6529f8005d0b0e): workflow-task replay then exceeded the SDK's
# 2 s deadlock detector on every attempt, and a 52 MB sibling run was
# terminated at the server history limit. This is Temporal's documented
# WorkflowStreamClient default (docs.temporal.io/workflow-streams#tuning:
# "raise it to amortize Signal cost"; 200 ms is their figure for a ~30 s
# completion, not a long nested run).
AGENT_RUNS_STREAM_BATCH_INTERVAL = timedelta(seconds=2)
# Where download_agent_response_file persists share_file bytes; never inside
# Temporal payloads. Overridable per deployment.
AGENT_FILE_STORE_DIR = Path(
    os.environ.get("AGENT_FILE_STORE_DIR", ".agent-files")
)
# Computer Use actions drive Playwright; same envelope as the model activity
# so a long navigate/screenshot loop is not killed mid-turn.
COMPUTER_USE_START_TO_CLOSE = MODEL_START_TO_CLOSE
COMPUTER_USE_SCHEDULE_TO_CLOSE = MODEL_SCHEDULE_TO_CLOSE
COMPUTER_USE_HEARTBEAT = MODEL_HEARTBEAT
# Browser actions can submit forms or otherwise cause non-idempotent effects.
BROWSER_RETRY_POLICY = RetryPolicy(maximum_attempts=1)

# --- Worker readiness (Temporal-native) ---
# There is no readiness file. Liveness is Temporal's own DescribeTaskQueue
# poller probe, cached for this long and bounded by this RPC timeout so the
# check stays cheap under load. Fail closed: unavailable Temporal, an RPC
# error, or a slow RPC all report no pollers.
READINESS_POLLER_CACHE = timedelta(seconds=5)
READINESS_POLLER_RPC_TIMEOUT = timedelta(seconds=2)
# --- Model catalog (catalog_workflow.ModelCatalogWorkflow) ---
# The provider-declaring catalog is served over Temporal's own unit of work:
# ModelCatalogWorkflow schedules the model_catalog activity, which returns the
# catalog the worker captured at boot. The activity is a pure in-memory read,
# so a short start-to-close is correct -- exceeding it means no worker is
# serving the queue, which is exactly the degraded signal /health reports.
CATALOG_ACTIVITY_TIMEOUT = timedelta(seconds=5)
# Whole-execution bound, applied twice: as the workflow's own
# execution_timeout so Temporal abandons it server-side, and as the client-side
# wait on execute_workflow. Without it a queue with no pollers leaves
# execute_workflow pending forever and /health never answers -- the endpoint
# `pnpm dev:web` waits on. Must exceed CATALOG_ACTIVITY_TIMEOUT so a normal
# activity attempt is never cut short by the outer bound.
CATALOG_WORKFLOW_TIMEOUT = timedelta(seconds=8)
# How long the API reuses a fetched catalog before re-executing the workflow.
# A model id missing from the cache also forces one immediate refresh, so a
# worker that registered new ids mid-window is picked up without waiting.
CATALOG_CACHE_TTL = timedelta(seconds=30)

EMBEDDING_GENERATIONS = {
    "memory-v1": {
        "model": "pplx-embed-context-v1-0.6b",
        "dimension": 1024,
        "encoding": "base64_int8",
    }
}

# Desktop-only budgets. Existing browser/model activity policy is unchanged.
DESKTOP_TASK_TIMEOUT = timedelta(minutes=30)
DESKTOP_MAX_MUTATIONS = 100
DESKTOP_OBSERVATION_TIMEOUT = timedelta(seconds=30)
DESKTOP_OBSERVATION_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
DESKTOP_MUTATION_TIMEOUT = timedelta(seconds=30)
DESKTOP_MUTATION_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
DESKTOP_JOB_HEARTBEAT_INTERVAL = timedelta(seconds=10)
DESKTOP_JOB_HEARTBEAT_TIMEOUT = timedelta(seconds=30)
DESKTOP_LEASE_TTL = timedelta(minutes=5)
DESKTOP_RECORD_MAX_BYTES = 65_536
DESKTOP_SQLITE_TIMEOUT_SECONDS = 5
DESKTOP_OBSERVATION_MAX_BYTES = 10 * 1024 * 1024
DESKTOP_MAX_DIMENSION = 16_384
WORKSPACE_FILE_MAX_BYTES = 1024 * 1024
WORKSPACE_DIRECTORY_MAX_ENTRIES = 2000
WORKSPACE_PATH_MAX_BYTES = 4096
WORKSPACE_PATH_MAX_DEPTH = 32
WORKSPACE_SEARCH_MAX_FILES = 200
WORKSPACE_SEARCH_MAX_DIRECTORIES = 200
WORKSPACE_SEARCH_MAX_PENDING_DIRECTORIES = 200
WORKSPACE_SEARCH_QUERY_MAX_BYTES = 4096
WORKSPACE_SEARCH_MAX_BYTES = 8 * 1024 * 1024
WORKSPACE_SEARCH_MAX_MATCHES = 100
WORKSPACE_SEARCH_TIMEOUT_SECONDS = 5
WORKSPACE_UPLOAD_PREFIX = ".gwen-upload-"
WORKSPACE_SESSION_TTL = timedelta(hours=8)
WORKSPACE_MAX_PROJECTS = 100
WORKSPACE_SERVICE_TIMEOUT_SECONDS = 30
WORKSPACE_AUTH_TIMEOUT_SECONDS = 5
WORKSPACE_HTTP_MAX_BYTES = 2 * WORKSPACE_FILE_MAX_BYTES
WORKSPACE_PROJECT_CREATE_ACTION = "project.create"
WORKSPACE_TOKEN_BYTES = 32
WORKSPACE_EXCLUDED_NAMES = frozenset({
    ".git", ".venv", "node_modules", "__pycache__", ".runtime", ".ssh",
    ".gnupg", ".kilo", ".omo", ".worktrees", ".next",
    "fair-expanse-493212-h8-138622c839d2.json",
})
