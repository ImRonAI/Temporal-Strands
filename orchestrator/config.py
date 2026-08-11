from datetime import timedelta

from temporalio.common import RetryPolicy

TASK_QUEUE = "perplexity-orchestrator"
MODEL_START_TO_CLOSE = timedelta(minutes=10)
MODEL_SCHEDULE_TO_CLOSE = timedelta(minutes=30)
MODEL_HEARTBEAT = timedelta(seconds=30)
MODEL_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
)
# The think activity streams every model chunk to the thinking topic; same
# batching Temporal documents for LLM streaming (see workflow.py's
# streaming_batch_interval note -- this is a history-pressure dial).
THINK_STREAM_BATCH_INTERVAL = timedelta(milliseconds=200)
# Temporal's hard per-payload limit is 2MB; the continue-as-new input carries
# agent.messages, so oversized toolResult text (think transcripts, sandbox
# stdout) must be clamped before rollover or the workflow wedges (TMPRL1103).
ROLLOVER_TOOL_RESULT_MAX_CHARS = 20_000
EMBEDDING_GENERATIONS = {
    "memory-v1": {
        "model": "pplx-embed-context-v1-0.6b",
        "dimension": 1024,
        "encoding": "base64_int8",
    }
}
