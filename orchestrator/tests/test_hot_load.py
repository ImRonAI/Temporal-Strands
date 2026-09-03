"""Temporal tests: load_tool fires as an activity, survives continue-as-new, MCP call-tool.

https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from strands.models.model import Model
from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from load_tool import STRANDS_TOOLS_DIR, mcp_client_activity, run_loaded_tool
from workflow import ChatInput, ChatWorkflow, TurnInput, mcp_client_factories

os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")

TASK_QUEUE = "test-hot-load"
SCRIPTS: deque[list[dict[str, Any]]] = deque()
ECHO_SERVER = Path(__file__).resolve().parent / "echo_mcp_server.py"
FILE_READ_MARKER = "hot-load-file-read-marker-7f3a"


class ScriptedModel(Model):
    def update_config(self, **model_config: Any) -> None:  # pragma: no cover
        pass

    def get_config(self) -> Any:  # pragma: no cover
        return {}

    async def structured_output(
        self, output_model: Any, prompt: Any, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:  # pragma: no cover
        raise NotImplementedError("scripted model has no structured output")
        yield

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        if not SCRIPTS:
            raise ApplicationError(
                "test script exhausted: a model call arrived with no scripted reply",
                non_retryable=True,
            )
        for event in SCRIPTS.popleft():
            yield event


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


def tool_use_events(tool_use_id: str, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": json.dumps(arguments)}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
        {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        },
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncGenerator[Client, None]:
    env = await WorkflowEnvironment.start_local(
        plugins=[
            StrandsPlugin(
                models={"fake/text": lambda: ScriptedModel()},
                mcp_clients=mcp_client_factories(),
            )
        ]
    )
    try:
        worker = Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[ChatWorkflow],
            activities=[run_loaded_tool, mcp_client_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        async with worker:
            yield env.client
    finally:
        await env.shutdown()


@pytest.fixture(autouse=True)
def reset_scripts() -> None:
    SCRIPTS.clear()


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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.1)
    raise AssertionError("condition not met within deadline")


def scheduled_activity_types(history: Any) -> list[str]:
    names: list[str] = []
    for event in history.events:
        if not event.HasField("activity_task_scheduled_event_attributes"):
            continue
        name = event.activity_task_scheduled_event_attributes.activity_type.name
        if name:
            names.append(name)
    return names


def tool_result_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        for block in message.get("content", []):
            result = block.get("toolResult") if isinstance(block, dict) else None
            if not result:
                continue
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("text"):
                    texts.append(item["text"])
    return texts


@pytest.mark.asyncio(loop_scope="module")
async def test_load_tool_file_read_runs_as_activity(
    client: Client, tmp_path: Path
) -> None:
    """load_tool registers file_read; the next model call runs it as an activity."""
    target = tmp_path / "payload.txt"
    target.write_text(FILE_READ_MARKER)
    handle = await start_session(client, "hot-load-file-read")

    SCRIPTS.append(
        tool_use_events(
            "load-1",
            "load_tool",
            {"path": str(STRANDS_TOOLS_DIR / "file_read.py"), "name": "file_read"},
        )
    )
    SCRIPTS.append(text_events("loaded file_read"))
    assert (
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="load file_read"))
        == "loaded file_read"
    )

    SCRIPTS.append(
        tool_use_events(
            "read-1",
            "file_read",
            {"path": str(target), "mode": "view"},
        )
    )
    SCRIPTS.append(text_events("read complete"))
    assert (
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="read the file"))
        == "read complete"
    )

    history = await handle.fetch_history()
    types = scheduled_activity_types(history)
    assert "file_read" in types
    messages = await handle.query(ChatWorkflow.messages)
    assert any(FILE_READ_MARKER in text for text in tool_result_texts(messages))

    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_load_tool_survives_continue_as_new(
    client: Client, tmp_path: Path
) -> None:
    """file_read remains after continue-as-new and still runs as an activity."""
    target = tmp_path / "payload.txt"
    target.write_text(FILE_READ_MARKER)
    session_id = "hot-load-rollover"
    handle = await start_session(client, session_id, rollover_turns=1)
    first_run_id = (await handle.describe()).run_id

    SCRIPTS.append(
        tool_use_events(
            "load-1",
            "load_tool",
            {"path": str(STRANDS_TOOLS_DIR / "file_read.py"), "name": "file_read"},
        )
    )
    SCRIPTS.append(text_events("loaded file_read"))
    await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="load file_read"))

    latest = client.get_workflow_handle(session_id)

    async def rolled_over() -> bool:
        return (await latest.describe()).run_id != first_run_id

    await poll(rolled_over)
    successor_id = (await latest.describe()).run_id
    successor = client.get_workflow_handle(session_id, run_id=successor_id)

    SCRIPTS.append(
        tool_use_events(
            "read-1",
            "file_read",
            {"path": str(target), "mode": "view"},
        )
    )
    SCRIPTS.append(text_events("read complete"))
    assert (
        await successor.execute_update(ChatWorkflow.turn, TurnInput(prompt="read the file"))
        == "read complete"
    )

    types = scheduled_activity_types(
        await client.get_workflow_handle(session_id, run_id=successor_id).fetch_history()
    )
    assert "file_read" in types
    messages = await successor.query(ChatWorkflow.messages)
    assert any(FILE_READ_MARKER in text for text in tool_result_texts(messages))

    await latest.signal(ChatWorkflow.end_chat)
    await latest.result()


@pytest.mark.asyncio(loop_scope="module")
async def test_chat_load_tools_runs_temporal_mcp_call_tool(client: Client) -> None:
    """ChatWorkflow: mcp_client connect + load_tools, then Temporal {server}-call-tool."""
    handle = await start_session(client, "hot-load-echo-mcp")

    SCRIPTS.append(
        tool_use_events(
            "connect-1",
            "mcp_client",
            {
                "action": "connect",
                "connection_id": "hot-echo",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(ECHO_SERVER)],
            },
        )
    )
    SCRIPTS.append(text_events("connected"))
    assert (
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="connect echo"))
        == "connected"
    )

    SCRIPTS.append(
        tool_use_events(
            "load-mcp-1",
            "mcp_client",
            {"action": "load_tools", "connection_id": "hot-echo"},
        )
    )
    SCRIPTS.append(text_events("loaded echo"))
    assert (
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="load echo"))
        == "loaded echo"
    )
    load_texts = tool_result_texts(await handle.query(ChatWorkflow.messages))
    assert any("hot-echo" in text for text in load_texts)
    assert not any("agent instance is required" in text for text in load_texts)

    SCRIPTS.append(tool_use_events("echo-1", "echo", {"text": "pong"}))
    SCRIPTS.append(text_events("pong"))
    assert (
        await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt="echo pong"))
        == "pong"
    )

    types = scheduled_activity_types(await handle.fetch_history())
    assert "hot-echo-list-tools" in types
    assert "hot-echo-call-tool" in types

    await handle.signal(ChatWorkflow.end_chat)
    await handle.result()
