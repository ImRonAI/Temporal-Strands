from datetime import timedelta

from config import (
    BUILTIN_SKILLS,
    DEFAULT_MODEL_ID,
    EMBEDDING_GENERATIONS,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL_ID,
    GEMINI_MODEL_IDS,
    MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_CEILING,
    MAX_STEPS_CEILING,
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
    MODEL_SCHEDULE_TO_CLOSE,
    MODEL_START_TO_CLOSE,
    PERPLEXITY_API_BASE,
    PERPLEXITY_PRESETS,
    PROVIDER_OUTPUT_CEILINGS,
    TASK_QUEUE,
)


def test_runtime_constants_are_versioned_and_non_secret() -> None:
    assert TASK_QUEUE == "perplexity-orchestrator"
    assert GEMINI_MODEL_ID == "gemini-3.8-flash"
    assert GEMINI_MODEL_IDS == (
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-flash-latest",
    )
    assert GEMINI_MAX_OUTPUT_TOKENS == 65_536
    assert MAX_OUTPUT_TOKENS == 120_000
    assert EMBEDDING_GENERATIONS["memory-v1"] == {
        "model": "pplx-embed-context-v1-0.6b",
        "dimension": 1024,
        "encoding": "base64_int8",
    }
    assert all("key" not in name.lower() for name in vars(__import__("config")))


def test_perplexity_agent_api_constants() -> None:
    assert PERPLEXITY_API_BASE == "https://api.perplexity.ai"
    assert PERPLEXITY_PRESETS == (
        "fast",
        "low",
        "medium",
        "high",
        "xhigh",
        "wide-research",
    )
    assert DEFAULT_MODEL_ID == "preset:high"
    assert BUILTIN_SKILLS == (
        {"type": "builtin", "name": "office"},
        {"type": "builtin", "name": "office/pdf"},
        {"type": "builtin", "name": "office/docx"},
        {"type": "builtin", "name": "office/pptx"},
        {"type": "builtin", "name": "office/xlsx"},
    )
    assert isinstance(BUILTIN_SKILLS, tuple)
    assert MAX_STEPS_CEILING == 100
    assert MAX_OUTPUT_TOKENS_CEILING == 128_000
    assert PROVIDER_OUTPUT_CEILINGS == {"google/": 65_536}


def test_model_activity_policy_uses_temporal_defaults() -> None:
    assert MODEL_START_TO_CLOSE is None
    assert MODEL_SCHEDULE_TO_CLOSE is None
    assert MODEL_HEARTBEAT is None
    assert MODEL_RETRY_POLICY.initial_interval == timedelta(seconds=1)
    assert MODEL_RETRY_POLICY.backoff_coefficient == 2.0
    assert MODEL_RETRY_POLICY.maximum_interval is None
    assert MODEL_RETRY_POLICY.maximum_attempts == 0
