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


def test_agent_runs_stream_uses_documented_default_batch_interval() -> None:
    """Sub-agent runs stream for many minutes, not one chat completion.

    Every flushed batch is a durable Signal in the hosting workflow's history.
    At 200 ms a single create_* activity produced ~1,000 publish Signals /
    20 MB in one turn (chat-be6529f8005d0b0e), pushing workflow-task replay
    past the SDK's 2 s deadlock detector and, at 52 MB, past the server's
    history limit (chat-637b23e1f45d5b73 was terminated). Temporal's Workflow
    Streams docs set the default at 2 s and say to raise it to amortize
    Signal cost; 200 ms is their number for a ~30 s completion only.
    """
    from config import AGENT_RUNS_STREAM_BATCH_INTERVAL

    assert AGENT_RUNS_STREAM_BATCH_INTERVAL == timedelta(seconds=2)


def test_nested_activity_streams_use_documented_default_batch_interval() -> None:
    """think / graph / use_agent / use_skill are multi-minute nested runs.

    Same failure class as agent_runs through a different topic: one 35-minute
    graph activity on chat-2a72daf5c76ec25f published 1,769 Signals / 15.5 MB
    on ``thinking`` at 200 ms. The outer TemporalAgent's own
    ``streaming_batch_interval`` (workflow.py) is the documented ~30 s LLM
    completion case and stays at 200 ms; the activity-side nested streams
    take the Workflow Streams client default.
    """
    from config import THINK_STREAM_BATCH_INTERVAL

    assert THINK_STREAM_BATCH_INTERVAL == timedelta(seconds=2)


def test_model_activity_policy_uses_temporal_defaults() -> None:
    assert MODEL_START_TO_CLOSE is None
    assert MODEL_SCHEDULE_TO_CLOSE is None
    assert MODEL_HEARTBEAT is None
    assert MODEL_RETRY_POLICY.initial_interval == timedelta(seconds=1)
    assert MODEL_RETRY_POLICY.backoff_coefficient == 2.0
    assert MODEL_RETRY_POLICY.maximum_interval is None
    assert MODEL_RETRY_POLICY.maximum_attempts == 0


def test_think_envelope() -> None:
    """Think sub-agent envelope mirrors workflow.py _THINK_ACTIVITY_OPTIONS."""
    from datetime import timedelta

    from temporalio.common import RetryPolicy

    from config import (
        THINK_HEARTBEAT_TIMEOUT,
        THINK_RETRY_POLICY,
        THINK_START_TO_CLOSE,
    )

    assert THINK_START_TO_CLOSE == timedelta(minutes=10)
    assert THINK_HEARTBEAT_TIMEOUT == timedelta(minutes=2)
    assert isinstance(THINK_RETRY_POLICY, RetryPolicy)
    assert THINK_RETRY_POLICY.maximum_attempts == 1
