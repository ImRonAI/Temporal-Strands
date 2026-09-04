import os
from datetime import timedelta
from pathlib import Path
from typing import Optional

from temporalio.common import RetryPolicy

TASK_QUEUE = "perplexity-orchestrator"
# --- Perplexity Agent API (outer model provider) ---
# Pinned explicitly rather than inherited from the environment: the Perplexity
# SDK reads PERPLEXITY_BASE_URL on construction, and .env.local may point it at
# the stateless router endpoint, which rejects catalog model ids and
# background/store/max_steps.
PERPLEXITY_API_BASE = "https://api.perplexity.ai"
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
# The think activity streams every model chunk to the thinking topic; same
# batching Temporal documents for LLM streaming (see workflow.py's
# streaming_batch_interval note -- this is a history-pressure dial).
THINK_STREAM_BATCH_INTERVAL = timedelta(milliseconds=200)
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
# Sub-agent run events ride their own topic; batched like the think stream.
AGENT_RUNS_STREAM_BATCH_INTERVAL = timedelta(milliseconds=200)
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

EMBEDDING_GENERATIONS = {
    "memory-v1": {
        "model": "pplx-embed-context-v1-0.6b",
        "dimension": 1024,
        "encoding": "base64_int8",
    }
}
