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
    PERPLEXITY_MODEL_RETRY_POLICY,
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


def test_think_stream_flushes_promptly_without_lowering_other_nested_batches() -> None:
    """Nested activity streams flush sooner than the outer model stream.

    Same failure class as the retired agent_runs stream through a different
    topic: one 35-minute graph activity on chat-2a72daf5c76ec25f published
    1,769 Signals / 15.5 MB on ``thinking`` at 200 ms. The outer
    TemporalAgent's own ``streaming_batch_interval`` (workflow.py) is the
    documented ~30 s LLM completion case and stays at 200 ms; the
    activity-side nested streams keep the Workflow Streams client default.
    """
    from config import SUBAGENT_STREAM_BATCH_INTERVAL, THINK_STREAM_BATCH_INTERVAL
    import graph_activity
    import use_agent_activity
    import use_skill_activity

    assert THINK_STREAM_BATCH_INTERVAL == timedelta(milliseconds=500)
    assert SUBAGENT_STREAM_BATCH_INTERVAL == timedelta(seconds=2)
    for module in (graph_activity, use_agent_activity, use_skill_activity):
        assert module.SUBAGENT_STREAM_BATCH_INTERVAL is SUBAGENT_STREAM_BATCH_INTERVAL
        assert not hasattr(module, "THINK_STREAM_BATCH_INTERVAL")


def test_model_stream_batch_interval() -> None:
    """Outer TemporalAgent streams stay at Temporal's 200 ms LLM figure.

    Every flushed batch is a durable Signal in the workflow's history, so this
    is a history-pressure dial, not a latency dial: at 25 ms a single turn
    produced 5,158 signals and 24,953 history events, and the workflow spent
    its time replaying history instead of streaming.
    """
    from config import MODEL_STREAM_BATCH_INTERVAL

    assert MODEL_STREAM_BATCH_INTERVAL == timedelta(milliseconds=200)


def test_stream_can_drain_overlap_matches_workflow_streams_recipe() -> None:
    """CAN waits after the turn so subscribers drain before truncate().

    Workflow Streams Pattern 1 is a sleep overlap after the last publish.
    The value lives in config.py, not as a literal in ChatWorkflow.run.
    """
    from config import STREAM_CAN_DRAIN_OVERLAP
    import workflow

    assert STREAM_CAN_DRAIN_OVERLAP == timedelta(seconds=1)
    assert workflow.STREAM_CAN_DRAIN_OVERLAP is STREAM_CAN_DRAIN_OVERLAP


def test_model_activity_policy_allows_slow_streams_without_automatic_retries() -> None:
    from config import MCP_START_TO_CLOSE

    assert MODEL_START_TO_CLOSE == timedelta(minutes=10)
    assert MCP_START_TO_CLOSE == timedelta(seconds=30)
    assert MODEL_SCHEDULE_TO_CLOSE is None
    assert MODEL_HEARTBEAT == timedelta(minutes=2)
    assert MODEL_HEARTBEAT < MODEL_START_TO_CLOSE
    assert MODEL_RETRY_POLICY.initial_interval == timedelta(seconds=1)
    assert MODEL_RETRY_POLICY.backoff_coefficient == 2.0
    assert MODEL_RETRY_POLICY.maximum_interval is None
    assert MODEL_RETRY_POLICY.maximum_attempts == 1


def test_perplexity_overload_retries_are_bounded_and_exclude_timeouts() -> None:
    assert PERPLEXITY_MODEL_RETRY_POLICY.maximum_attempts == 3
    assert PERPLEXITY_MODEL_RETRY_POLICY.initial_interval == timedelta(seconds=1)
    assert PERPLEXITY_MODEL_RETRY_POLICY.backoff_coefficient == 2.0
    assert set(PERPLEXITY_MODEL_RETRY_POLICY.non_retryable_error_types) == {
        "TemporalTimeout:StartToClose", "TemporalTimeout:Heartbeat",
        "ModelThrottledException", "ContextWindowOverflowException",
    }
