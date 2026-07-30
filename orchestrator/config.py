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
EMBEDDING_GENERATIONS = {
    "memory-v1": {
        "model": "pplx-embed-context-v1-0.6b",
        "dimension": 1024,
        "encoding": "base64_int8",
    }
}
