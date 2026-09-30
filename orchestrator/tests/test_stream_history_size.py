"""Real Temporal history footprint of the nested-agent publish projection.

Objective regression for the workflow-history pressure behind the 50 MB /
51,200-event cap: the same native ``Agent.stream_async`` run (installed
Strands SDK, scripted model, no paid API) is published through the native
``WorkflowStreamClient`` into a test-only stream-hosting workflow on an
isolated ``WorkflowEnvironment.start_local`` server, once with the legacy
unfiltered leaf projection (``publishable(event)``) and once with the fixed
projections production uses today (``graph_activity.flatten_native_event`` and
``subagent_support.sanitize_stream_payload`` → ``publishable(event,
agent_event=True)``). A concurrent subscriber polls the stream exactly like
``server.py`` does, so the poll-Update responses that Temporal persists again
in history are included in the measurement.

The scripted model fragments the tool argument across many raw deltas (400
chunks ≈ 40 KB), which is the Google/OpenAI-style fragmented shape and the
worst case for the cumulative ``current_tool_use.input`` snapshot. Perplexity
emits a whole argument in one raw chunk, so per-event invocation tracing, not
cumulative input, dominates its overhead; both are stripped by the fix, and
this test measures the total, not one cause.

No production wrappers: the workflow and activity below exist only here.
"""

from __future__ import annotations

import json
import shutil
from contextlib import aclosing
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from strands import Agent, tool
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.contrib.workflow_streams import WorkflowStream, WorkflowStreamClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from config import SUBAGENT_STREAM_BATCH_INTERVAL
from graph_activity import flatten_native_event
from subagent_support import publishable, sanitize_stream_payload
from test_graph_activity import ScriptedModel, text_events
from workflow import THINKING_TOPIC

pytestmark = pytest.mark.asyncio

NODE = "writer"
CHUNK = "x" * 100
TOOL_USE = {"toolUseId": "graph-1", "name": "graph", "input": {"task": "generate"}}
END_MARKER = {"type": "stream_end"}
# The legacy leaf carries the full cumulative tool input once per token, so an
# unbounded 2 s batch would exceed Temporal's per-payload blob limit; the same
# SDK-public bound is applied to every mode so the comparison isolates payload
# size rather than batch count.
MAX_BATCH_SIZE = 20


@tool
def accept_content(content: str) -> str:
    """Accept generated content without writing it anywhere."""
    return str(len(content))


class ContentModel(ScriptedModel):
    """One tool call whose JSON argument streams as ``chunks`` raw deltas, then text."""

    def __init__(self, chunks: int) -> None:
        super().__init__()
        self.chunks = chunks

    async def stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        self.calls += 1
        if self.calls > 1:
            for event in text_events("finished"):
                yield event
            return
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "call-1", "name": "accept_content"}}}}
        for part in ['{"content":"', *([CHUNK] * self.chunks), '"}']:
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": part}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


@dataclass
class StreamCase:
    mode: str  # legacy | graph | use_agent
    chunks: int


def project(mode: str, event: Any) -> list[dict[str, Any]]:
    """The leaf frame each publish path produces for one native agent event."""
    if mode == "legacy":
        # Pre-fix flatten_native_event leaf: unfiltered publishable(event).
        return [{"type": "multiagent_node_stream", "node_id": NODE, "event": publishable(event)}]
    if mode == "graph":
        return flatten_native_event({"type": "multiagent_node_stream", "node_id": NODE, "event": event})
    if mode == "use_agent":
        return [sanitize_stream_payload({"agent_name": NODE, "event": event}, name_key="agent_name")]
    raise ValueError(mode)


@activity.defn(name="stream_native_agent")
async def stream_native_agent(case: StreamCase) -> int:
    """Run the installed Agent stream and publish every event to the parent stream."""
    agent = Agent(model=ContentModel(case.chunks), tools=[accept_content], callback_handler=None)
    stream_client = WorkflowStreamClient.from_within_activity(
        batch_interval=SUBAGENT_STREAM_BATCH_INTERVAL, max_batch_size=MAX_BATCH_SIZE,
    )
    topic = stream_client.topic(THINKING_TOPIC)
    published = 0
    async with stream_client:
        async for event in agent.stream_async("Generate content"):
            for frame in project(case.mode, event):
                topic.publish({"tool_use": TOOL_USE, "data": frame})
                published += 1
                if published % MAX_BATCH_SIZE == 0:
                    # A real provider stream awaits network I/O between
                    # chunks, which lets the background flusher honour
                    # max_batch_size; the scripted model never yields, so
                    # the public flush() applies the same bound explicitly.
                    await stream_client.flush()
        topic.publish({"tool_use": TOOL_USE, "data": END_MARKER}, force_flush=True)
    return published


@workflow.defn
class StreamHostWorkflow:
    """Test-only host: native WorkflowStream + one activity, held open for the subscriber."""

    @workflow.init
    def __init__(self, case: StreamCase) -> None:
        self._stream = WorkflowStream()
        self._released = False

    @workflow.run
    async def run(self, case: StreamCase) -> int:
        published = await workflow.execute_activity(
            stream_native_agent, case,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        await workflow.wait_condition(lambda: self._released)
        self._stream.detach_pollers()
        await workflow.wait_condition(workflow.all_handlers_finished)
        return published

    @workflow.signal
    def release(self) -> None:
        self._released = True


@dataclass
class Footprint:
    mode: str
    chunks: int
    events: int
    total_bytes: int
    signal_events: int
    signal_bytes: int
    poll_events: int
    poll_bytes: int
    frames: list[dict[str, Any]]


_POLL_EVENT_TYPES = {
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ADMITTED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED,
}


async def measure(client: Client, queue: str, case: StreamCase) -> Footprint:
    workflow_id = f"stream-history-{case.mode}-{case.chunks}-{uuid4()}"
    handle: WorkflowHandle = await client.start_workflow(
        StreamHostWorkflow.run, case, id=workflow_id, task_queue=queue,
    )
    frames: list[dict[str, Any]] = []
    subscriber = WorkflowStreamClient.create(client, workflow_id)
    async with aclosing(subscriber.subscribe([THINKING_TOPIC])) as items:
        async for item in items:
            assert item.data["tool_use"] == TOOL_USE
            if item.data["data"] == END_MARKER:
                break
            frames.append(item.data["data"])
    await handle.signal(StreamHostWorkflow.release)
    published = await handle.result()
    assert published == len(frames)

    history = await handle.fetch_history()
    footprint = Footprint(case.mode, case.chunks, len(history.events), 0, 0, 0, 0, 0, frames)
    for event in history.events:
        size = len(event.SerializeToString())
        footprint.total_bytes += size
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED:
            footprint.signal_events += 1
            footprint.signal_bytes += size
        elif event.event_type in _POLL_EVENT_TYPES:
            footprint.poll_events += 1
            footprint.poll_bytes += size
    return footprint


def inner_events(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = [frame["event"] for frame in frames]
    assert all(isinstance(event, dict) for event in events)
    return events


def raw_input_fragments(events: list[dict[str, Any]]) -> list[str]:
    return [
        event["event"]["contentBlockDelta"]["delta"]["toolUse"]["input"]
        for event in events
        if "toolUse" in (event.get("event") or {}).get("contentBlockDelta", {}).get("delta", {})
    ]


def message_blocks(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [
        block[kind]
        for event in events
        for block in (event.get("message") or {}).get("content", [])
        if kind in block
    ]


def assert_stream_content(footprint: Footprint) -> None:
    """Tool input, final message, tool result, text and result events all survive."""
    expected = {"content": CHUNK * footprint.chunks}
    events = inner_events(footprint.frames)
    assert json.loads("".join(raw_input_fragments(events))) == expected
    assert message_blocks(events, "toolUse") == [
        {"toolUseId": "call-1", "name": "accept_content", "input": expected}
    ]
    [tool_result] = message_blocks(events, "toolResult")
    assert tool_result["status"] == "success"
    assert tool_result["content"] == [{"text": str(len(expected["content"]))}]
    assert [event["data"] for event in events if isinstance(event.get("data"), str)] == ["finished"]
    assert any("result" in event for event in events)
    starts = [event["current_tool_use"] for event in events if event.get("type") == "tool_use_stream"]
    assert starts and all(
        current["toolUseId"] == "call-1" and current["name"] == "accept_content" for current in starts
    )


def report(label: str, footprint: Footprint) -> None:
    print(
        f"{label:<22} chunks={footprint.chunks:<4} frames={len(footprint.frames):<5} "
        f"history_events={footprint.events:<4} total={footprint.total_bytes:>10,} B  "
        f"signals={footprint.signal_events:>3} ({footprint.signal_bytes:>10,} B)  "
        f"poll_updates={footprint.poll_events:>3} ({footprint.poll_bytes:>10,} B)"
    )


async def test_fixed_projection_shrinks_real_temporal_history() -> None:
    cli = shutil.which("temporal")
    assert cli, "The Temporal CLI is required; this test never connects to the live server"
    queue = f"stream-history-{uuid4()}"
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli) as env:
        async with Worker(
            env.client, task_queue=queue, workflows=[StreamHostWorkflow],
            activities=[stream_native_agent], workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await measure(env.client, queue, StreamCase("legacy", 400))
            graph = await measure(env.client, queue, StreamCase("graph", 400))
            use_agent = await measure(env.client, queue, StreamCase("use_agent", 400))
            graph_double = await measure(env.client, queue, StreamCase("graph", 800))

    print()
    print(f"batch_interval={SUBAGENT_STREAM_BATCH_INTERVAL.total_seconds()}s max_batch_size={MAX_BATCH_SIZE}")
    report("legacy publishable", legacy)
    report("graph flatten (fixed)", graph)
    report("use_agent (fixed)", use_agent)
    report("graph flatten x2", graph_double)
    for fixed in (graph, use_agent):
        reduction = 1 - fixed.total_bytes / legacy.total_bytes
        print(f"{fixed.mode:<10} history reduction vs legacy: {reduction:.2%} "
              f"({legacy.total_bytes:,} -> {fixed.total_bytes:,} B)")

    for footprint in (legacy, graph, use_agent, graph_double):
        assert_stream_content(footprint)

    legacy_events = inner_events(legacy.frames)
    legacy_text = [event["data"] for event in legacy_events if isinstance(event.get("data"), str)]
    legacy_raw = [event["event"] for event in legacy_events if "event" in event]
    for fixed in (graph, use_agent):
        # Every native event still produces exactly one frame; raw model
        # chunks and text deltas are byte-identical to the legacy stream.
        assert len(fixed.frames) == len(legacy.frames)
        fixed_events = inner_events(fixed.frames)
        assert [event["data"] for event in fixed_events if isinstance(event.get("data"), str)] == legacy_text
        assert [event["event"] for event in fixed_events if "event" in event] == legacy_raw
        assert not any(
            "input" in event.get("current_tool_use", {})
            or "event_loop_cycle_span" in event
            or "request_state" in event
            for event in fixed_events
        )
        assert fixed.total_bytes < legacy.total_bytes * 0.10, (
            f"{fixed.mode}: {fixed.total_bytes:,} B is not >90% below legacy {legacy.total_bytes:,} B"
        )

    # The legacy leaf carried the growing input once per token; a 400-token
    # argument alone persisted more than 100x its own size in history.
    assert any("input" in event.get("current_tool_use", {}) for event in legacy_events)
    assert legacy.total_bytes > 100 * len(CHUNK) * 400
    # Fixed history grows about linearly with the streamed argument.
    assert graph_double.total_bytes < graph.total_bytes * 2.5
