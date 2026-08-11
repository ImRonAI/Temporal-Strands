"""Durable-behavior tests for ChatWorkflow against a local Temporal server.

Task 8 acceptance, exercised end to end with fake registered model factories:
serializable state, fixed model, serialized concurrent turns, idempotent end,
queryable completed history, disconnect-safe updates, surfaced failures, and
Continue-As-New preserving identity, messages, session, and stream offsets
without duplicate frames.

The worker runs in this process, so the fake models are scripted through
module-level state: each test preloads SCRIPTS with the exact event lists its
turn should produce, in model-call order. ModelActivity caches factory
instances for the worker's lifetime, which is why the script lives at module
level rather than on the instance.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, AsyncGenerator, Callable

import pytest
import pytest_asyncio
from strands.models.model import Model
from temporalio.client import (
    Client,
    WorkflowUpdateFailedError,
    WorkflowUpdateStage,
)
from temporalio.contrib.strands import StrandsPlugin
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflow import ChatInput, ChatWorkflow, TurnInput

TASK_QUEUE = "test-chat-workflow"

# Event lists consumed one per Model.stream() call, in call order. Turns run
# under the workflow lock, so call order is deterministic within a test.
SCRIPTS: deque[list[dict[str, Any]]] = deque()


class Tracker:
    """Records model-activity concurrency to prove turns are serialized."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    def reset(self) -> None:
        self.active = 0
        self.max_active = 0


TRACKER = Tracker()


def text_events(reply: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": reply}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        },
    ]


class ScriptedModel(Model):
    """Pops the next scripted event list per stream() call."""

    def update_config(self, **model_config: Any) -> None:  # pragma: no cover
        pass

    def get_config(self) -> Any:  # pragma: no cover
        return {}

    async def structured_output(
        self, output_model: Any, prompt: Any, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:  # pragma: no cover
        raise NotImplementedError("scripted model has no structured output")
        yield  # unreachable; makes this an async generator

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        TRACKER.active += 1
        TRACKER.max_active = max(TRACKER.max_active, TRACKER.active)
        try:
            # Long enough that two truly concurrent turns would overlap here.
            await asyncio.sleep(0.2)
            if not SCRIPTS:
                raise ApplicationError(
                    "test script exhausted: a model call arrived with no scripted reply",
                    non_retryable=True,
                )
            for event in SCRIPTS.popleft():
                yield event
        finally:
            TRACKER.active -= 1


class BrokenModel(ScriptedModel):
    """Fails every call, non-retryably, so the failure surfaces immediately."""

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise ApplicationError("model exploded", non_retryable=True)
        yield  # unreachable; makes this an async generator


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncGenerator[Client, None]:
    env = await WorkflowEnvironment.start_local(
        plugins=[
            StrandsPlugin(
                models={
                    "fake/text": lambda: ScriptedModel(),
                    "fake/broken": lambda: BrokenModel(),
                }
            )
        ]
    )
    try:
        worker = Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[ChatWorkflow],
        )
        async with worker:
            yield env.client
    finally:
        await env.shutdown()


@pytest.fixture(autouse=True)
def reset_scripts() -> None:
    SCRIPTS.clear()
    TRACKER.reset()


def chat_input(session_id: str, **overrides: Any) -> ChatInput:
    kwargs: dict[str, Any] = {
        "model_id": "fake/text",
        "system_prompt": "You are a test assistant.",
        "session_id": session_id,
    }
    kwargs.update(overrides)
    return ChatInput(**kwargs)


async def start_session(client: Client, session_id: str, **overrides: Any):
    return await client.start_workflow(
        ChatWorkflow.run,
        chat_input(session_id, **overrides),
        id=session_id,
        task_queue=TASK_QUEUE,
    )


async def poll(predicate: Callable[[], Any], timeout: float = 20.0) -> Any:
    """Await an async predicate until it returns truthy or the deadline hits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.1)
    raise AssertionError("condition not met within deadline")


def assistant_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for block in message.get("content", []):
            if isinstance(block, dict) and "text" in block:
                texts.append(block["text"])
    return texts


@pytest.mark.asyncio(loop_scope="module")
async def test_turn_replies_and_history_is_queryable(client: Client) -> None:
    """A turn returns its reply; the model is fixed; history is queryable."""
    handle = await start_session(client, "chat-basic")
    SCRIPTS.append(text_events("first reply"))

    reply = await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="hello"))

    assert reply == "first reply"
    assert await handle.query(ChatWorkflow.model_id) == "fake/text"
    messages = await handle.query(ChatWorkflow.messages)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert "first reply" in assistant_texts(messages)

    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_turns_are_serialized(client: Client) -> None:
    """Two overlapping turn updates never overlap inside the model."""
    handle = await start_session(client, "chat-serialized")
    SCRIPTS.append(text_events("reply one"))
    SCRIPTS.append(text_events("reply two"))

    replies = await asyncio.gather(
        handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="one")),
        handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="two")),
    )

    assert sorted(replies) == ["reply one", "reply two"]
    assert TRACKER.max_active == 1, "turns overlapped inside the model activity"
    messages = await handle.query(ChatWorkflow.messages)
    # Strictly alternating: user, assistant, user, assistant. Interleaved
    # turns would break this ordering.
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]

    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_end_chat_is_idempotent(client: Client) -> None:
    """Repeated end signals complete the workflow exactly once, cleanly."""
    handle = await start_session(client, "chat-end")
    await handle.signal(ChatWorkflow.end_chat)
    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()  # completes without error


@pytest.mark.asyncio(loop_scope="module")
async def test_failed_model_activity_surfaces_to_the_caller(client: Client) -> None:
    """A failed model activity fails the update, not the workflow."""
    handle = await start_session(client, "chat-broken", model_id="fake/broken")

    with pytest.raises(WorkflowUpdateFailedError):
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="boom"))

    # The session survives the failed turn.
    assert await handle.query(ChatWorkflow.model_id) == "fake/broken"
    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_disconnected_caller_does_not_cancel_the_turn(client: Client) -> None:
    """A turn keeps running after its caller stops waiting on the update."""
    handle = await start_session(client, "chat-disconnect")
    SCRIPTS.append(text_events("finished alone"))

    await handle.start_update(
        ChatWorkflow.turn,
        TurnInput(prompt="carry on"),
        wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    # The update handle is deliberately abandoned: nothing awaits its result.

    async def turn_completed() -> bool:
        messages = await handle.query(ChatWorkflow.messages)
        return "finished alone" in assistant_texts(messages)

    await poll(turn_completed)

    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_continue_as_new_preserves_the_session(client: Client) -> None:
    """Rollover carries model, messages, session, and offsets; no duplicate frames."""
    session_id = "chat-rollover"
    handle = await start_session(client, session_id, rollover_turns=1)
    first_run_id = (await handle.describe()).run_id

    SCRIPTS.append(text_events("turn-one"))
    assert (
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="one"))
        == "turn-one"
    )

    # The threshold is 1, so the workflow must hand off to a new run.
    latest = client.get_workflow_handle(session_id)

    async def rolled_over() -> bool:
        description = await latest.describe()
        return description.run_id != first_run_id

    await poll(rolled_over)

    # Identity, session, and history all survive the handoff.
    assert await latest.query(ChatWorkflow.model_id) == "fake/text"
    assert await latest.query(ChatWorkflow.session_id) == session_id
    carried = await latest.query(ChatWorkflow.messages)
    assert "turn-one" in assistant_texts(carried)

    # Stream offsets are carried, not reset: the new run's offset accounts for
    # every frame the first run published.
    stream_client = WorkflowStreamClient.create(client, session_id)
    offset_after_rollover = await stream_client.get_offset()
    assert offset_after_rollover > 0

    # A second turn on the same workflow id lands on the successor run, and a
    # subscriber reading from the carried offset sees ONLY turn two's frames —
    # turn one is not replayed.
    SCRIPTS.append(text_events("turn-two"))
    frames: list[Any] = []

    async def consume() -> None:
        async for item in stream_client.subscribe(
            ["events"], from_offset=offset_after_rollover
        ):
            frames.append(item.data)

    consumer = asyncio.create_task(consume())
    try:
        assert (
            await latest.execute_update(ChatWorkflow.turn, TurnInput(prompt="two"))
            == "turn-two"
        )
        await asyncio.sleep(0.5)  # drain in-flight frames
    finally:
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    texts = [
        event["contentBlockDelta"]["delta"]["text"]
        for event in frames
        if isinstance(event, dict)
        and "contentBlockDelta" in event
        and "text" in event["contentBlockDelta"]["delta"]
    ]
    assert "turn-two" in texts
    assert "turn-one" not in texts, "rollover replayed the previous turn's frames"

    messages = await latest.query(ChatWorkflow.messages)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]

    await latest.signal(ChatWorkflow.end_chat)
    await latest.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_rollover_clamps_oversized_tool_results(client: Client) -> None:
    """continue-as-new input stays under Temporal's 2MB payload limit.

    A think transcript in the megabytes wedged a live session (TMPRL1103):
    every rollover retry re-sent the same oversized ChatInput forever. The
    carried messages must clamp giant toolResult text.
    """
    from config import ROLLOVER_TOOL_RESULT_MAX_CHARS

    # Over the clamp threshold while under Temporal's 2MB start-input limit
    # (a 3MB fixture can't even start: "Blob data size exceeds limit").
    huge = "x" * (ROLLOVER_TOOL_RESULT_MAX_CHARS * 5)
    prior: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": "research this"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "t1",
                        "name": "think",
                        "input": {"thought": "…", "cycle_count": 1},
                    }
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "t1",
                        "status": "success",
                        "content": [{"text": huge}],
                    }
                }
            ],
        },
        {"role": "assistant", "content": [{"text": "done thinking"}]},
    ]

    session_id = "chat-rollover-clamp"
    handle = await start_session(
        client, session_id, rollover_turns=1, messages=prior
    )
    first_run_id = (await handle.describe()).run_id

    SCRIPTS.append(text_events("turn-one"))
    await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="one"))

    latest = client.get_workflow_handle(session_id)

    async def rolled_over() -> bool:
        description = await latest.describe()
        return description.run_id != first_run_id

    await poll(rolled_over)

    carried = await latest.query(ChatWorkflow.messages)
    clamped_texts = [
        content["text"]
        for message in carried
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("toolResult")
        for content in block["toolResult"].get("content", [])
        if isinstance(content, dict) and content.get("text") is not None
    ]
    assert clamped_texts, "the toolResult message must survive the rollover"
    for text in clamped_texts:
        assert len(text) <= ROLLOVER_TOOL_RESULT_MAX_CHARS

    await latest.signal(ChatWorkflow.end_chat)
    await latest.result()
