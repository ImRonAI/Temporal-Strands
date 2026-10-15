import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

from temporalio.common import RetryPolicy

TASK_QUEUE = "perplexity-orchestrator"

OFFLOAD_BUCKET = "gwen-offload"
MEMORY_BUCKET = "gwen-memory"
OFFLOAD_PAYLOAD_BYTES = 524_288
DREAM_MIN_INTERVAL = timedelta(hours=24)
DREAM_MIN_SESSIONS = 5

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
# Deployment command prefix retained for the API's read-only native VNC probe.
# Control transfers execute only inside the desktop worker, never on the host.
# x11vnc remote control multiplexes every -R/-Q through one X property
# (X11VNC_REMOTE); a concurrent probe clobbers a transfer's receipt. The probe
# therefore takes the worker's handoff.lock (flock(2), same Linux kernel) via
# util-linux flock inside the container, bounded below the command timeout.
DESKTOP_VNC_GRANT_COMMAND = os.environ.get(
    "DESKTOP_VNC_GRANT_COMMAND",
    "docker --context colima exec gwen-desktop flock -w 10 /var/lib/gwen-desktop-artifacts/handoff.lock "
    "x11vnc -display :99 -R script:noviewonly;nodeny -Q viewonly,deny",
)
DESKTOP_VNC_REVOKE_COMMAND = os.environ.get(
    "DESKTOP_VNC_REVOKE_COMMAND",
    "docker --context colima exec gwen-desktop x11vnc -display :99 -R script:viewonly;deny;disconnect:all;clear_all;fakebuttonevent:1,0;fakebuttonevent:2,0;fakebuttonevent:3,0 -Q viewonly,client_count,pointer_mask",
)
DESKTOP_VNC_REVOKE_SCRIPT = "script:viewonly;deny;disconnect:all;clear_all;fakebuttonevent:1,0;fakebuttonevent:2,0;fakebuttonevent:3,0"
DESKTOP_VNC_GRANT_SCRIPT = "script:nodeny;noviewonly"
# Runs inside the Linux desktop worker, inheriting its DISPLAY and XAUTHORITY.
# Admit viewers only after verified revocation; never enable native input here.
DESKTOP_VNC_VIEW_COMMAND = os.environ.get(
    "DESKTOP_VNC_VIEW_COMMAND",
    "x11vnc -R script:viewonly;nodeny -Q viewonly,deny",
)
DESKTOP_VNC_COMMAND_TIMEOUT = 15
DESKTOP_HANDOFF_TIMEOUT = timedelta(seconds=45)
# The handoff update must outlive the wait it performs server-side (turn-lock
# wait bounded by DESKTOP_HANDOFF_TIMEOUT), hence the 2x envelope.
DESKTOP_HANDOFF_START_TO_CLOSE = 2 * DESKTOP_HANDOFF_TIMEOUT
# Backoff between retries when a handoff update loses the race with a
# continue-as-new rollover (SessionRollingOver) — server.py take/give paths.
HANDOFF_ROLLOVER_RETRY_BACKOFF = timedelta(milliseconds=100)
# Startup probe: is the workflow that owns the desktop still running? Bounded so
# a slow Temporal cannot stall worker startup; on doubt ownership is kept.
DESKTOP_OWNER_PROBE_TIMEOUT = timedelta(seconds=10)
# --- Perplexity Agent API (outer model provider) ---
# Pinned explicitly rather than inherited from the environment: the Perplexity
# SDK reads PERPLEXITY_BASE_URL on construction, and .env.local may point it at
# the stateless router endpoint, which rejects catalog model ids and
# background/store/max_steps.
PERPLEXITY_API_BASE = "https://api.perplexity.ai"
# Worker boot catalog fetch (run_worker.fetch_catalog_ids) — bounded so a hung
# /v1/models cannot stall worker startup.
PERPLEXITY_CATALOG_HTTP_TIMEOUT = timedelta(seconds=30)
# FastAPI lifespan's shared outbound client (server.py).
SERVER_HTTP_TIMEOUT = timedelta(seconds=60)
# Perplexity-only ``response.output_item.done`` item types. These are the
# server-side tool payloads in the Agent API's OutputItem union that the
# OpenAI Responses stream loop has no branch for and silently skips
# (strands/models/openai_responses.py:334-449). PerplexityModel taps the raw
# SSE body and re-emits each one verbatim under a ``{"perplexity": ...}``
# StreamEvent so the UI can render its tool card. "message" is deliberately
# absent: its text already streams as output_text deltas. "function_call" is
# absent too: the parent loop owns it (PerplexityModel records its arguments
# separately). The sandbox file-operation items (read/write/edit/glob/grep/
# apply_patch) appear in live streams and the SDK's OutputItem union but not
# in the OpenAPI discriminator; they are forwarded so the UI can show file
# work. ``tool_search_output`` is the deferred-MCP catalog search.
NATIVE_OUTPUT_ITEM_TYPES = (
    "search_results",
    "image_search_results",
    "people_search_results",
    "finance_results",
    "fetch_url_results",
    "sandbox_results",
    "sandbox_read_file",
    "sandbox_write_file",
    "sandbox_edit_file",
    "sandbox_glob",
    "sandbox_grep",
    "sandbox_apply_patch",
    "share_file",
    "mcp_call",
    "mcp_list_tools",
    "tool_search_output",
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
# HTTP client statuses the Agent API returns for a request no retry can fix:
# bad request, auth, permission, not found, validation, and 424
# external_connector_error ("Managed connector ... is not connected", observed
# live 2026-09-13 for a connector the Project has not authorized). Both
# Perplexity adapters classify these as non-retryable.
PERMANENT_HTTP_STATUSES = frozenset({400, 401, 403, 404, 422, 424})
# SSE failures can arrive after HTTP 200, with symbolic codes instead of a
# status. Observed live for Fable 5.1: code/type="invalid_request".
PERMANENT_API_ERROR_TYPES = frozenset({
    "invalid_request", "invalid_request_error", "external_connector_error",
})
# The Agent API's documented reasoning-effort enum (OpenAPI), enforced
# pre-request by PerplexityModel.
REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})

# --- Per-turn tool/skill selection (workflow.py turn update) ---
# load_tool is checked against its requested name in the hook input instead of
# its own name; discover_tools/unload_tools cannot gain a new capability
# without a passing load_tool; use_skill is gated on selection.skills.
SELECTION_META_TOOLS = frozenset({"load_tool", "discover_tools", "unload_tools", "use_skill"})
# Registered by ChatWorkflow.run() itself, never by load_tool, so a turn
# restriction may still hold a starter subset.
SELECTION_STARTER_TOOLS = frozenset({"environment", "http_request", "journal"})
SELECTION_CANCEL_REASON = "Tool {name} is not in this turn's selected tools."
SKILL_SELECTION_CANCEL_REASON = "Skill {name} is not in this turn's selected skills."
# Desktop vision proxy. Perplexity rejects any image input for these model ids
# with a bare 400 "invalid request" (live probe 2026-10-09: PNG, JPEG, 720px
# JPEG and https URL all rejected for perplexity/glm-5.3; glm-5.3-flash and
# preset:high accepted the same 1440x900 screenshot). For a listed model the
# screenshot goes to VISION_PROXY_MODEL instead, and only its text read-out
# (layout, targets, 0-999 click coordinates) reaches the session model.
VISION_INCAPABLE_MODELS = frozenset({"perplexity/glm-5.3"})
VISION_PROXY_MODEL = "perplexity/glm-5.3-flash"
VISION_PROXY_EFFORT = "high"
VISION_PROXY_MAX_OUTPUT_TOKENS = 4096
# Configured Agent API connectors, attached to every request as
# native {"type": "connector"} tools (agent_api_tools.connector_tools).
# Authorization lives in the Perplexity Project; requests only reference the
# connector id. A connector the gateway reports as not connected fails the
# WHOLE request with HTTP 424 before inference (live 2026-09-15:
# connector_googledrive, request id ac36f1f9-faa5-4dfb-afbc-e2c829d6f8af),
# so disconnected connectors must not be defaults. Google Drive and Linear
# are omitted until reconnected and verified against this API group. The
# provider can omit another explicitly disconnected connector before progress.
CONNECTORS: tuple[dict[str, str], ...] = (
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
# max_steps: OpenAPI ``max_steps`` is 1-100. max_output_tokens: the schema has
# no maximum (model-dependent); 128000 is the budget the medium/high/xhigh
# presets publish (docs.perplexity.ai/docs/agent-api/presets, "Current preset
# values") and the largest value the live gateway accepts for non-Google ids.
MAX_STEPS_CEILING = 100
MAX_OUTPUT_TOKENS_CEILING = 128_000
# Per-provider output ceilings for catalog provider/model ids. The catalog
# exposes no limit field; google/* rejects 128000 but accepts 65536 (verified
# against the live API on 2026-08-02). Anything not listed gets the platform
# ceiling. Preset ids are also sent the ceiling: presets merge request fields
# over their defaults, and the fast/low presets would otherwise cap output at
# 8192/32768 (their published budgets) while this orchestrator asks for the
# whole tool-using run in one response.
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
# The Agent API validates a fixed content-part union and rejects OpenAI's
# ``input_file`` (400 ``invalid type "input_file"``), so document attachments
# reach it only as inlined text. Per-document character ceiling for that
# inlining; larger documents truncate with a marker rather than blowing up
# the prompt.
DOCUMENT_INLINE_MAX_CHARS = 100_000
# Temporal activity options. The Python worker rejects an activity with neither
# start_to_close_timeout nor schedule_to_close_timeout
# (temporalio/worker/_workflow_instance.py _outbound_schedule_activity).
# Schedule-to-close stays unset: the TypeScript ActivityOptions contract cited
# below defaults that field to unlimited once start-to-close is set.
# The guide's 60s example is not suitable for slow reasoning models. This
# timeout applies to EACH model-call activity, including calls inside Think,
# not to the whole nested agent. Give a slow streamed completion room to finish
# while bounding an attempt; the installed plugin's auto_heartbeater reports
# worker liveness at half the configured heartbeat interval.
# Model calls can produce billable partial output before an attempt fails;
# retrying automatically can duplicate that output and hide a real timeout.
# https://docs.temporal.io/encyclopedia/retry-policies
# https://python.temporal.io/temporalio.common.RetryPolicy.html
# https://typescript.temporal.io/api/interfaces/workflow.ActivityOptions
MODEL_START_TO_CLOSE = timedelta(minutes=10)
MODEL_SCHEDULE_TO_CLOSE: Optional[timedelta] = None
MODEL_HEARTBEAT = timedelta(minutes=2)
MODEL_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
# Only Perplexity's explicit pre-output overload is marked retryable by its
# provider. Native Temporal backoff retries that model call, never the turn.
# Timeout names: temporalio/temporal common/retrypolicy/retry_policy.go.
PERPLEXITY_MODEL_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[
        "TemporalTimeout:StartToClose", "TemporalTimeout:Heartbeat",
        "ModelThrottledException", "ContextWindowOverflowException",
    ],
)
# Same guide: TemporalMCPClient(start_to_close_timeout=timedelta(seconds=30)).
MCP_START_TO_CLOSE = timedelta(seconds=30)

# Outer TemporalAgent streaming batch interval (workflow.py's
# ``streaming_batch_interval``). Temporal's own value for LLM streaming
# (docs.temporal.io, "Stream LLM output"). Every batch is a durable Signal
# appended to workflow history, so this is a history-pressure dial, not a
# latency dial: at 25 ms a single turn produced 5,158 signals and 24,953
# history events, and the workflow spent its time replaying history instead
# of streaming.
MODEL_STREAM_BATCH_INTERVAL = timedelta(milliseconds=200)

# Strands' vended ContextOffloader (strands.vended_plugins.context_offloader):
# tool results above ``max_result_tokens`` leave the message list and are
# replaced by a ``preview_tokens`` preview plus a retrievable reference. These
# are the benchmark-validated values Strands itself uses for
# ``Agent(context_manager="auto")`` (strands/agent/agent.py
# _CONTEXT_MANAGER_MAX_RESULT_TOKENS / _CONTEXT_MANAGER_PREVIEW_TOKENS).
# History-pressure dial: every model activity input carries the full message
# list (and Strands' invocation_state duplicates it), so three uncapped
# ~111 KB browser get_text results alone cost ~20 MB of a 52 MB history in
# chat-d6d70dc8b3ee69f8 and Temporal terminated the turn at the 50 MB limit.
CONTEXT_OFFLOAD_MAX_RESULT_TOKENS = 1_500
CONTEXT_OFFLOAD_PREVIEW_TOKENS = 750
SSE_SUBSCRIBE_RESTART_LIMIT = 3
SSE_SUBSCRIBE_RESTART_DELAY = 0.25
SSE_HEARTBEAT_SECONDS = 10
SSE_COMPLETION_DRAIN_TIMEOUT = 10
# Workflow Streams closing overlap (docs Pattern 1: sleep after the last
# publish so in-flight polls deliver before continue-as-new). After the turn
# update commits, the SSE bridge drains to get_offset(); truncating the log
# in that same workflow task would drop the tail and blow up CAN. Shorter
# than the docs' 30s default because a caught-up chat subscriber is one poll
# behind, not a cold consumer.
STREAM_CAN_DRAIN_OVERLAP = timedelta(seconds=1)

# Activity-side nested streams (think / graph / use_agent / use_skill) publish
# every nested-agent chunk on the thinking topic. These are multi-minute runs
# (graph: up to 35 min), not one ~30 s completion, and every flushed batch is a
# durable Signal in the hosting ChatWorkflow's history: one graph activity on
# chat-2a72daf5c76ec25f produced 1,769 Signals / 15.5 MB at 200 ms. This is
# Temporal's documented WorkflowStreamClient default
# (docs.temporal.io/workflow-streams#tuning). The outer TemporalAgent's own
# streaming_batch_interval in workflow.py is the documented LLM-completion case
# and is unaffected.
# Think has a dedicated model stream: flush genuine nested model events promptly
# without changing the 2s batching of long-running graph/skill/agent activities.
# 500ms is bounded to avoid the workflow-history pressure seen at 200ms on
# long-running nested activities; idle periods emit no invented events.
THINK_STREAM_BATCH_INTERVAL = timedelta(milliseconds=500)
SUBAGENT_STREAM_BATCH_INTERVAL = timedelta(seconds=2)
# The think sub-agent runs up to 10 minutes wall-clock (multi-minute nested
# stream, not a ~30 s completion); the heartbeat keeps Temporal aware the
# stream is alive between flushed batches. Exactly one automatic attempt: a
# replayed think is a duplicate inference, so a failed one is deliberately
# retried by the orchestrator, not blindly replayed.
THINK_START_TO_CLOSE = timedelta(minutes=10)
THINK_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
THINK_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
THINK_MODEL_ID = "openai/gpt-6-astra"
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
# --- Local Python execution (python_repl_activity -> strands_tools.python_repl) ---
# The installed community REPL runs on the host worker as a thin activity.
# Code can have side effects (files, network), so exactly one automatic
# attempt; the orchestrator re-runs deliberately. Ten minutes bounds a long
# computation; the activity heartbeats before executing so a hung interpreter
# is detected by the heartbeat timeout rather than the full start-to-close.
PYTHON_REPL_START_TO_CLOSE = timedelta(minutes=10)
PYTHON_REPL_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
PYTHON_REPL_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
# Where the REPL persists its dill namespace between calls
# (PYTHON_REPL_PERSISTENCE_DIR). Runtime data, not source: the tool defaults
# to ``$CWD/repl_state`` and would otherwise litter orchestrator/.
PYTHON_REPL_PERSISTENCE_DIR = Path(__file__).resolve().parent / ".runtime" / "python-repl"
# Computer Use / browser actions are Temporal activities on desktop-browser.
# Start-to-close is assigned after DESKTOP_TASK_TIMEOUT. Schedule-to-start and
# schedule-to-close stay unset so Temporal's unlimited default applies.
COMPUTER_USE_SCHEDULE_TO_CLOSE = None
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

# Desktop-only budgets. GUI attempts use Temporal heartbeats for liveness
# (DESKTOP_JOB_HEARTBEAT_*), not a 30s start-to-close or a 5s schedule-to-start.
# DESKTOP_TASK_TIMEOUT is the handoff/release schedule-to-close, the fixture
# mutation ceiling, and the native tool start-to-close. Schedule-to-start and
# schedule-to-close on those tools stay unset (Temporal default: unlimited).
DESKTOP_TASK_TIMEOUT = timedelta(minutes=30)
# Native desktop tools use this as start-to-close. Schedule-to-start and
# schedule-to-close stay unset (Temporal default: unlimited) so a serialized
# desktop worker can queue the next action. Liveness after the attempt starts
# is DESKTOP_JOB_HEARTBEAT_TIMEOUT. Do not substitute a 1-day schedule-to-close.
COMPUTER_USE_START_TO_CLOSE = DESKTOP_TASK_TIMEOUT
DESKTOP_MAX_MUTATIONS = 100
DESKTOP_OBSERVATION_TIMEOUT = timedelta(seconds=30)
DESKTOP_OBSERVATION_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
DESKTOP_MUTATION_TIMEOUT = DESKTOP_TASK_TIMEOUT
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
# Graph / skill / use_agent file work. Not the operator $HOME.
AGENT_WORKSPACE_ROOT = Path(
    os.environ.get(
        "GWEN_AGENT_WORKSPACE_ROOT",
        str(Path(os.environ.get("TMPDIR", "/tmp")) / "gwen-agent-workspaces"),
    )
)
