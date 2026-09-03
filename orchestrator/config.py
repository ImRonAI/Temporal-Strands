import os
from datetime import timedelta
from pathlib import Path
from typing import Optional

from temporalio.common import RetryPolicy

TASK_QUEUE = "perplexity-orchestrator"
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
