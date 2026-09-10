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
import json
import copy
import os
import platform
import time
import unittest.mock
from collections import deque
from concurrent.futures import ThreadPoolExecutor
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
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from config import DESKTOP_BROWSER_TASK_QUEUE, THINK_MODEL_ID

import think_activity
from load_tool import mcp_client_activity, run_loaded_tool
from workflow import ChatInput, ChatWorkflow, TurnInput, mcp_client_factories
from browser_activity import browser_activity

TASK_QUEUE = "test-chat-workflow"


@activity.defn(name="list_agent_models")
async def stub_list_agent_models() -> dict[str, Any]:
    CATALOG_CALLS.append(activity.info().workflow_id)
    return {"object": "list", "data": []}


@activity.defn(name="take_screenshot")
async def stub_desktop_screenshot(*arguments) -> dict:
    return {"status": "success", "content": [{"text": "Fresh screenshot from test desktop worker"}]}


DESKTOP_RELEASES: list[tuple[str, str, str]] = []


@activity.defn(name="release_desktop")
async def stub_release_desktop() -> None:
    info = activity.info()
    DESKTOP_RELEASES.append((info.workflow_id, info.workflow_run_id, info.task_queue))


GRAPH_FAILURE = "topology with a non-empty 'nodes' list is required for create action"


@activity.defn(name="graph")
async def stub_failing_graph(
    action: str = "execute",
    graph_id: Any = None,
    topology: Any = None,
    task: Any = None,
    tools: Any = None,
) -> dict[str, Any]:
    """Stands in for graph_activity's failure path: raise, never return an error dict."""
    raise ApplicationError(GRAPH_FAILURE, type="GraphActivityError", non_retryable=True)

# Event lists consumed one per Model.stream() call, in call order. Turns run
# under the workflow lock, so call order is deterministic within a test.
SCRIPTS: deque[list[dict[str, Any]]] = deque()
REASONING_STATES: list[dict[str, Any]] = []
THINK_REQUESTS: deque[dict[str, Any]] = deque()
THINK_SCRIPTS: deque[list[dict[str, Any]]] = deque()
THINK_CALLS: list[dict[str, Any]] = []
CATALOG_CALLS: list[str] = []


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


def tool_call_events(name: str, tool_id: str, arguments: dict) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_id, "name": name}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}},
        {"contentBlockStop": {}}, {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}},
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
        if (kwargs.get("invocation_state") or {}).get("require_think"):
            arguments = THINK_REQUESTS.popleft() if THINK_REQUESTS else {
                "thought": "Analyze the user request", "cycle_count": 1, "reasoning_effort": "minimal",
            }
            for event in tool_call_events("think", "initial-think", arguments):
                yield event
            return
        TRACKER.active += 1
        REASONING_STATES.append(dict(kwargs.get("invocation_state") or {}))
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


"""Event lists for the SECOND registered model factory, so a per-turn model
switch is observable: replies scripted here can only come from "fake/alt"."""
ALT_SCRIPTS: deque[list[dict[str, Any]]] = deque()


class AltScriptedModel(ScriptedModel):
    """Same scripted contract, separate script deque — the switch target."""

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        if (kwargs.get("invocation_state") or {}).get("require_think"):
            for event in tool_call_events("think", "alt-initial-think", {
                "thought": "Analyze the user request", "cycle_count": 0, "reasoning_effort": "minimal",
            }):
                yield event
            return
        if not ALT_SCRIPTS:
            raise ApplicationError(
                "alt test script exhausted: a model call arrived with no scripted reply",
                non_retryable=True,
            )
        for event in ALT_SCRIPTS.popleft():
            yield event


class BrokenModel(ScriptedModel):
    """Fails every call, non-retryably, so the failure surfaces immediately."""

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise ApplicationError("model exploded", non_retryable=True)
        yield  # unreachable; makes this an async generator


THINK_NOTES = "scripted think notes"


class ThinkModel(Model):
    """The nested think-cycle model: a fixed reply, independent of SCRIPTS.

    The think-first hook runs the think activity before EVERY turn's model
    call; keeping its model outside the SCRIPTS deque means existing tests'
    script accounting is untouched.
    """

    def update_config(self, **model_config: Any) -> None:  # pragma: no cover
        pass

    def get_config(self) -> Any:  # pragma: no cover
        return {}

    async def structured_output(
        self, output_model: Any, prompt: Any, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:  # pragma: no cover
        raise NotImplementedError
        yield

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        THINK_CALLS.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tool_specs),
                            "invocation_state": dict(kwargs.get("invocation_state") or {})})
        for event in THINK_SCRIPTS.popleft() if THINK_SCRIPTS else text_events(THINK_NOTES):
            yield event


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncGenerator[Client, None]:
    model_factories = {
        "fake/text": lambda: ScriptedModel(),
        "fake/alt": lambda: AltScriptedModel(),
        "fake/broken": lambda: BrokenModel(),
        THINK_MODEL_ID: lambda: ThinkModel(),
    }
    env = await WorkflowEnvironment.start_local(
        plugins=[
            StrandsPlugin(
                models=model_factories,
                mcp_clients=mcp_client_factories(),
            )
        ]
    )
    # The think-first hook runs the think activity before every turn; its
    # nested agent resolves the session model id through configure(), exactly
    # as run_worker.py does. ThinkModel keeps it independent of SCRIPTS.
    think_activity.configure(
        {model_id: (lambda: ThinkModel()) for model_id in model_factories}
    )
    try:
        worker = Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[ChatWorkflow],
            activities=[
                mcp_client_activity,
                run_loaded_tool,
                think_activity.think,
                stub_list_agent_models,
                stub_failing_graph,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
            activity_executor=ThreadPoolExecutor(max_workers=1),
        )
        async with worker, Worker(
            env.client, task_queue=DESKTOP_BROWSER_TASK_QUEUE,
            activities=[stub_desktop_screenshot, stub_release_desktop, browser_activity],
            activity_executor=ThreadPoolExecutor(max_workers=1),
        ):
            yield env.client
    finally:
        think_activity.configure({})
        await env.shutdown()


@pytest.fixture(autouse=True)
def reset_scripts() -> None:
    DESKTOP_RELEASES.clear()
    SCRIPTS.clear()
    ALT_SCRIPTS.clear()
    THINK_REQUESTS.clear()
    THINK_SCRIPTS.clear()
    THINK_CALLS.clear()
    CATALOG_CALLS.clear()
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
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert "first reply" in assistant_texts(messages)

    # The model chooses a mandatory Think call; notes return as its tool result,
    # not an automatically injected, hardcoded pre-turn cycle.
    assert messages[1]["content"][0]["toolUse"]["name"] == "think"
    assert messages[1]["content"][0]["toolUse"]["input"]["reasoning_effort"] == "minimal"
    assert THINK_NOTES in str(messages[2]["content"])

    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_think_zero_cycles_skips_astra_and_first_call_parameters_are_model_chosen(client: Client):
    handle = await start_session(client, "chat-zero-think")
    THINK_REQUESTS.append({"thought": "Greeting", "cycle_count": 0, "reasoning_effort": "minimal"})
    SCRIPTS.append(text_events("Hello"))
    assert await asyncio.wait_for(handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="Hey there")), 15) == "Hello"
    assert THINK_CALLS == []
    calls = [block["toolUse"] for message in await handle.query(ChatWorkflow.messages) for block in message["content"] if "toolUse" in block]
    assert calls[0]["input"]["cycle_count"] == 0
    assert CATALOG_CALLS == [handle.id]  # Existing workflow bootstrap catalog refresh only.
    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_astra_uses_inherited_tool_and_returns_evidence_with_retained_cycle_context(client: Client):
    handle = await start_session(client, "chat-astra-evidence")
    THINK_REQUESTS.append({"thought": "Inspect catalog", "cycle_count": 2, "reasoning_effort": "high"})
    THINK_SCRIPTS.extend([tool_call_events("list_agent_models", "think-catalog", {}), text_events("cycle one"), text_events("cycle two")])
    SCRIPTS.append(text_events("Evidence received"))
    assert await asyncio.wait_for(handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="Use catalog")), 15) == "Evidence received"
    assert CATALOG_CALLS == [handle.id, handle.id]  # Bootstrap, then Think's inherited tool.
    assert len(THINK_CALLS) == 3
    for request in THINK_CALLS:
        assert "think" not in {spec["name"] for spec in request["tools"]}
        assert {"browser", "take_screenshot", "graph", "use_skill", "list_agent_models"}.issubset({spec["name"] for spec in request["tools"]})
        assert request["invocation_state"]["reasoning_effort"] == "high"
        assert "require_think" not in request["invocation_state"]
    assert "think-catalog" in str(THINK_CALLS[-1]["messages"])
    assert "cycle one" in str(THINK_CALLS[-1]["messages"])
    messages = await handle.query(ChatWorkflow.messages)
    think_result = messages[2]["content"][0]["toolResult"]
    assert "think-catalog" in str(think_result)
    assert "cycle two" in str(think_result)
    history = await handle.fetch_history()
    names = [event.activity_task_scheduled_event_attributes.activity_type.name for event in history.events if event.HasField("activity_task_scheduled_event_attributes")]
    assert "list_agent_models" in names
    assert "think" not in names
    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_later_explicit_think_call_uses_same_native_contract(client: Client):
    handle = await start_session(client, "chat-later-think")
    THINK_REQUESTS.append({"thought": "Initial classification", "cycle_count": 0, "reasoning_effort": "minimal"})
    SCRIPTS.extend([tool_call_events("think", "later-think", {"thought": "Inspect evidence", "cycle_count": 1, "reasoning_effort": "medium"}), text_events("Complete")])
    THINK_SCRIPTS.extend([tool_call_events("list_agent_models", "later-catalog", {}), text_events("Later result")])
    assert await asyncio.wait_for(handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="Interleave")), 15) == "Complete"
    assert len(THINK_CALLS) == 2
    assert "initial-think" in str(THINK_CALLS[0]["messages"])
    assert "later-think" not in str(THINK_CALLS[0]["messages"])
    assert THINK_CALLS[0]["invocation_state"]["reasoning_effort"] == "medium"
    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_take_control_waits_for_turn_and_blocks_new_turns(client: Client) -> None:
    handle = await start_session(client, "chat-browser-handoff")
    SCRIPTS.append(text_events("paused work"))
    turn = await handle.start_update(
        ChatWorkflow.turn, TurnInput(prompt="Work on the browser"),
        wait_for_stage=WorkflowUpdateStage.ACCEPTED,
    )
    async def turn_started():
        return await handle.query(ChatWorkflow.turn_start_offset) is not None

    await poll(turn_started)

    await asyncio.wait_for(handle.execute_update("claim_control"), 15)


    assert (await handle.query("control_status"))["human_control"] is True
    assert await handle.query(ChatWorkflow.turn_start_offset) is None
    await asyncio.wait_for(turn.result(), 15)
    with pytest.raises(WorkflowUpdateFailedError):
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="must not run"))
    assert DESKTOP_RELEASES == []
    run_id = (await handle.query("control_status"))["run_id"]
    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()
    assert DESKTOP_RELEASES == [(handle.id, run_id, DESKTOP_BROWSER_TASK_QUEUE)]


@pytest.mark.asyncio(loop_scope="module")
async def test_relinquish_continues_as_new_preserving_session(client: Client) -> None:
    handle = await start_session(client, "chat-browser-relinquish")
    SCRIPTS.append(text_events("before handoff"))
    await asyncio.wait_for(handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="initial task")), 15)
    await asyncio.wait_for(handle.execute_update("claim_control"), 15)
    before = await handle.query(ChatWorkflow.messages)

    old_run = await asyncio.wait_for(handle.execute_update("relinquish_control", "I signed in; continue"), 15)

    async def successor_ready():
        state = await handle.query("control_status")
        return state if state["run_id"] != old_run and state["ready"] else None

    state = await poll(successor_ready)
    assert state["human_control"] is False
    assert await asyncio.wait_for(handle.execute_update("relinquish_control", "I signed in; continue"), 15) == old_run
    assert await handle.query(ChatWorkflow.session_id) == "chat-browser-relinquish"
    assert await handle.query(ChatWorkflow.messages) == before
    SCRIPTS.append(text_events("continued work"))
    assert await asyncio.wait_for(handle.execute_update(
        ChatWorkflow.turn, TurnInput(prompt="I signed in; continue")
    ), 15) == "continued work"
    assert "before handoff" in assistant_texts(await handle.query(ChatWorkflow.messages))
    assert DESKTOP_RELEASES == []
    await handle.signal(ChatWorkflow.end_chat)
    await asyncio.wait_for(handle.result(), 15)
    assert DESKTOP_RELEASES == [(handle.id, state["run_id"], DESKTOP_BROWSER_TASK_QUEUE)]


def browser_events(tool_id: str, action: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_id, "name": "browser"}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"browser_input": {"action": action}})}}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}},
    ]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.skipif(platform.system() != "Linux" or not os.environ.get("DISPLAY"),
                    reason="Real headed browser requires the isolated Linux desktop; never launch on the Mac")
async def test_browser_survives_handoff_rollover(client: Client, monkeypatch, tmp_path) -> None:
    import json

    monkeypatch.setenv("STRANDS_BROWSER_USER_DATA_DIR", str(tmp_path))
    handle = await start_session(client, "chat-real-browser-handoff")
    session = "browser-handoff-session"
    SCRIPTS.extend([
        browser_events("create", {"type": "init_session", "session_name": session, "description": "Handoff test"}),
        browser_events("before", {"type": "execute_cdp", "session_name": session, "method": "Target.getTargetInfo"}),
        text_events("ready for user"),
    ])
    await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="Open browser"))

    def target_id(messages):
        results = [block["toolResult"] for message in messages for block in message["content"] if "toolResult" in block]
        for result in reversed(results):
            envelope = json.loads(result["content"][0]["text"])
            content = envelope.get("content", [])
            if content and "text" in content[0] and "targetInfo" in content[0]["text"]:
                return json.loads(content[0]["text"])["targetInfo"]["targetId"]
        raise AssertionError("No native target result")

    before = target_id(await handle.query(ChatWorkflow.messages))
    await handle.execute_update("claim_control")
    old_run = await handle.execute_update("relinquish_control", "Continue in this browser")

    async def ready():
        state = await handle.query("control_status")
        return state["run_id"] != old_run and state["ready"]

    await poll(ready)
    SCRIPTS.extend([
        browser_events("after", {"type": "execute_cdp", "session_name": session, "method": "Target.getTargetInfo"}),
        text_events("same browser"),
    ])
    await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="Continue in this browser"))
    assert target_id(await handle.query(ChatWorkflow.messages)) == before
    SCRIPTS.extend([
        browser_events("close", {"type": "close", "session_name": session}),
        text_events("closed"),
    ])
    await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="Close browser"))
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
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"] * 2

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


def graph_call_events(tool_id: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_id, "name": "graph"}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"action": "create", "graph_id": "g"})}}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}},
    ]


@pytest.mark.asyncio(loop_scope="module")
async def test_failed_activity_tool_reaches_the_model_as_an_error_result(client: Client) -> None:
    """A tool activity that raises becomes a status=error tool result carrying
    the activity's own message -- the turn continues, and the model can read
    what went wrong (not just "Activity task failed")."""
    handle = await start_session(client, "chat-graph-error")
    SCRIPTS.extend([graph_call_events("graph-1"), text_events("I saw the error")])

    assert await handle.execute_update(
        ChatWorkflow.turn, TurnInput(prompt="run a graph")
    ) == "I saw the error"

    messages = await handle.query(ChatWorkflow.messages)
    results = [
        block["toolResult"]
        for message in messages
        for block in message["content"]
        if "toolResult" in block
    ]
    assert results[-1]["status"] == "error"
    assert GRAPH_FAILURE in results[-1]["content"][0]["text"]
    assert "Activity task failed" not in results[-1]["content"][0]["text"]
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
async def test_turn_model_id_switches_the_factory_for_the_next_turn(
    client: Client,
) -> None:
    """A turn carrying a different model_id rebuilds the agent on the new
    registered factory, the reply comes from that model, the model_id query
    reflects the switch, and the conversation is carried across the rebuild."""
    handle = await start_session(client, "chat-model-switch")

    SCRIPTS.append(text_events("from the first model"))
    reply_one = await handle.execute_update(
        ChatWorkflow.turn, TurnInput(prompt="one")
    )
    assert reply_one == "from the first model"
    assert await handle.query(ChatWorkflow.model_id) == "fake/text"

    # Second turn switches models: only ALT_SCRIPTS is loaded, so the reply
    # can only have come from the "fake/alt" factory.
    ALT_SCRIPTS.append(text_events("from the second model"))
    reply_two = await handle.execute_update(
        ChatWorkflow.turn, TurnInput(prompt="two", model_id="fake/alt")
    )
    assert reply_two == "from the second model"
    # think_activity resolves its model through this query; it must reflect
    # the new value.
    assert await handle.query(ChatWorkflow.model_id) == "fake/alt"

    # Each turn includes the mandatory Think call/result before the response.
    messages = await handle.query(ChatWorkflow.messages)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"] * 2
    texts = assistant_texts(messages)
    assert "from the first model" in texts
    assert "from the second model" in texts

    # A third turn with model_id omitted stays on the switched model.
    ALT_SCRIPTS.append(text_events("still the second model"))
    reply_three = await handle.execute_update(
        ChatWorkflow.turn, TurnInput(prompt="three")
    )
    assert reply_three == "still the second model"
    assert await handle.query(ChatWorkflow.model_id) == "fake/alt"

    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_reasoning_effort_is_per_turn(client: Client) -> None:
    handle = await start_session(client, "chat-reasoning")
    REASONING_STATES.clear()
    for effort in ["low", "high", None]:
        SCRIPTS.append(text_events("reply"))
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="hi", reasoning_effort=effort))
    assert [state.get("reasoning_effort") for state in REASONING_STATES] == ["low", "high", None]
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
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"] * 2

    await latest.signal(ChatWorkflow.end_chat)
    await latest.result()


def test_workflow_uses_config_closable() -> None:
    """The MCP activity envelope carries config's shared schedule-to-close fallback.

    workflow.py used to define its own ``_closable`` + ``_UNCAPPED_FALLBACK_*``
    duplicates of config.closable_activity_options. With MODEL_START_TO_CLOSE /
    MODEL_SCHEDULE_TO_CLOSE unset (the default), config's generous 1-day
    fallback must apply, and workflow must not still define a private copy.
    """
    import config
    import workflow

    assert (
        workflow._MCP_ACTIVITY_OPTIONS["schedule_to_close_timeout"]
        == config.UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE
    )
    assert not hasattr(workflow, "_closable")
    assert not hasattr(workflow, "_UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE")


def test_workflow_builds_agent_with_config_model_stream_batch_interval() -> None:
    """The outer TemporalAgent stream uses config's batch interval.

    Every flushed batch is a durable Signal appended to workflow history, so
    the batch interval is a history-pressure dial, not a latency dial (at 25 ms
    a single turn produced 5,158 signals and 24,953 history events). The value
    must live in config.py, not as a literal at the construction site.
    """
    import config
    import workflow

    from types import SimpleNamespace

    wf = workflow.ChatWorkflow.__new__(workflow.ChatWorkflow)
    wf._extra_mcp_servers = []
    wf._model_id = config.DEFAULT_MODEL_ID
    wf._system_prompt = "system"
    wf._human_control = [False]
    wf._handoff_requested = [False]
    wf._handoffs = SimpleNamespace(publish=lambda *a, **k: None)
    wf._tool_results = SimpleNamespace(publish=lambda *a, **k: None)
    wf._loaded_tools = []
    wf._connected_mcp_servers = []
    # _ThinkFirstHook reads turn-scoped notes off the workflow's own stream.
    wf._stream = SimpleNamespace(get_state=lambda **k: SimpleNamespace(log=[], base_offset=0))

    with (
        unittest.mock.patch.object(workflow, "TemporalAgent") as ta,
        unittest.mock.patch.object(workflow, "temporal_mcp_clients", return_value=()),
    ):
        agent = wf._build_agent([])

    assert agent is ta.return_value
    ta.assert_called_once()
    assert (
        ta.call_args.kwargs["streaming_batch_interval"]
        == config.MODEL_STREAM_BATCH_INTERVAL
    )
