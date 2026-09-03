from datetime import timedelta

from config import (
    EMBEDDING_GENERATIONS,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL_ID,
    MAX_OUTPUT_TOKENS,
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
    MODEL_SCHEDULE_TO_CLOSE,
    MODEL_START_TO_CLOSE,
    TASK_QUEUE,
)


def test_runtime_constants_are_versioned_and_non_secret() -> None:
    assert TASK_QUEUE == "perplexity-orchestrator"
    assert GEMINI_MODEL_ID == "gemini-3.8-flash"
    assert GEMINI_MAX_OUTPUT_TOKENS == 65_536
    assert MAX_OUTPUT_TOKENS == 120_000
    assert EMBEDDING_GENERATIONS["memory-v1"] == {
        "model": "pplx-embed-context-v1-0.6b",
        "dimension": 1024,
        "encoding": "base64_int8",
    }
    assert all("key" not in name.lower() for name in vars(__import__("config")))


def test_model_activity_policy_uses_temporal_defaults() -> None:
    assert MODEL_START_TO_CLOSE is None
    assert MODEL_SCHEDULE_TO_CLOSE is None
    assert MODEL_HEARTBEAT is None
    assert MODEL_RETRY_POLICY.initial_interval == timedelta(seconds=1)
    assert MODEL_RETRY_POLICY.backoff_coefficient == 2.0
    assert MODEL_RETRY_POLICY.maximum_interval is None
    assert MODEL_RETRY_POLICY.maximum_attempts == 0
