"""use_skill Temporal activity streaming publish."""

from __future__ import annotations

import os
import copy
from pathlib import Path
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest
from strands.models.model import Model
from strands.types._events import ToolResultEvent, ToolStreamEvent
from temporalio.exceptions import ApplicationError

from graph_tool import _tool_name
from load_tool import STRANDS_TOOLS_DIR
from subagent_support import sanitize_stream_payload
from use_skill_activity import use_skill_activity

os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
CALCULATOR = str(STRANDS_TOOLS_DIR / "sleep.py")


@pytest.mark.asyncio
async def test_skill_stream_is_closed_when_publish_fails() -> None:
    import use_skill_activity as module

    closed = []

    async def stream(*args):
        try:
            yield ToolStreamEvent(
                {"name": "use_skill", "toolUseId": "skill", "input": {}},
                {"skill_name": "demo", "event": {"data": "chunk"}},
            )
        finally:
            closed.append(True)

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.topic.return_value.publish.side_effect = [None, RuntimeError("publish failed")]
    with (
        patch.object(module.activity, "info", return_value=SimpleNamespace(
            workflow_id="test", activity_id="skill", attempt=1,
        )),
        patch.object(module, "session_model", AsyncMock(return_value=SilentModel())),
        patch.object(module, "parent_agent", AsyncMock(return_value=SimpleNamespace(
            tool_registry=SimpleNamespace(registry={}),
        ))),
        patch.object(module, "create_use_skill_tool", return_value=SimpleNamespace(stream=stream)),
        patch.object(module.WorkflowStreamClient, "from_within_activity", return_value=client),
    ):
        with pytest.raises(ApplicationError) as err:
            await use_skill_activity("demo", "request")
    assert err.value.non_retryable
    assert "publish failed" in str(err.value)
    assert closed == [True]


class SilentModel(Model):
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
    ) -> AsyncGenerator[dict[str, Any], None]:  # pragma: no cover
        raise NotImplementedError
        yield


@pytest.mark.asyncio
async def test_reference_pattern_three_runs_isolated_skill_agents(monkeypatch, tmp_path: Path) -> None:
    """Exercise the upstream factory and real Agent loop, not a fake skill tool."""
    import skills_config
    import use_skill_activity as module
    from strands import Agent

    for name in ("alpha", "beta"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Specialist {name}\n---\n"
            f"# {name} instructions\nAct as the {name} specialist.\n"
        )
    (tmp_path / "alpha" / "references").mkdir()
    (tmp_path / "alpha" / "references" / "guide.md").write_text("alpha facts")
    (tmp_path / "alpha" / "scripts").mkdir()
    (tmp_path / "alpha" / "scripts" / "run.py").write_text("print(1)\n")
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills_config.discovered_skills.cache_clear()
    agentskills = skills_config._import_agentskills()
    from agentskills.tool import agent_skill

    calls = []

    class RecordingModel(SilentModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            calls.append((copy.deepcopy(messages), system_prompt, tool_specs))
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "specialist result"}}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                                "metrics": {"latencyMs": 1}}}

    model = RecordingModel()
    parent = Agent(model=model, system_prompt="Parent instructions must stay private", callback_handler=None)
    parent.messages.append({"role": "user", "content": [{"text": "Private parent conversation"}]})
    parent_messages = copy.deepcopy(parent.messages)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    published = []
    client.topic.return_value.publish.side_effect = published.append
    agents = []

    def record_agent(*args, **kwargs):
        agent = Agent(*args, **kwargs)
        agents.append(agent)
        return agent

    try:
        with (
            patch.object(module.activity, "info", return_value=SimpleNamespace(
                workflow_id="test", activity_id="skill", attempt=1,
            )),
            patch.object(module, "session_model", AsyncMock(return_value=model)),
            patch.object(module, "parent_agent", AsyncMock(return_value=parent)),
            patch.object(module.subagent_support, "session_model_id", AsyncMock(return_value=None)),
            patch.object(module.WorkflowStreamClient, "from_within_activity", return_value=client),
            patch.object(agentskills, "create_skill_agent_tool", wraps=agentskills.create_skill_agent_tool) as factory,
            patch.object(agent_skill, "Agent", side_effect=record_agent),
        ):
            for name in ("alpha", "beta"):
                result = await use_skill_activity(name, f"Task for {name}")
                assert result["status"] == "success"
                assert "specialist result" in result["content"][0]["text"]
            assert factory.call_count == 2

        assert len(agents) == len(calls) == 2
        assert agents[0] is not agents[1]
        for name, agent, (messages, prompt, specs) in zip(("alpha", "beta"), agents, calls):
            assert agent is not parent
            assert agent.name == f"skill-{name}"
            assert agent.model is model
            assert f"# {name} instructions" in prompt
            assert "Parent instructions" not in prompt
            assert len(messages) == 1
            text = messages[0]["content"][0]["text"]
            assert f"Task for {name}" in text
            if name == "alpha":
                assert "knowledge base" in text
                assert "guide.md" in text
                assert "run.py" in text
            else:
                assert "knowledge base" not in text
            assert "Private parent conversation" not in str(messages)
            assert {spec["name"] for spec in specs} == {"shell", "python_repl"}
        assert parent.messages == parent_messages
        assert any(frame["data"].get("text") == "specialist result" for frame in published)
        assert sum(frame["data"].get("type") == "skill_complete" for frame in published) == 2
    finally:
        skills_config.discovered_skills.cache_clear()


def test_sanitize_stream_payload_strips_agent() -> None:
    out = sanitize_stream_payload(
        {
            "skill_name": "demo",
            "agent": object(),
            "event": {"data": "hello"},
        }, name_key="skill_name"
    )
    assert out["skill_name"] == "demo"
    assert out["text"] == "hello"
    assert "agent" not in out


def test_skill_resource_note_lists_references_and_scripts(tmp_path, monkeypatch) -> None:
    skill = tmp_path / "with-resources"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: with-resources\ndescription: Uses files.\n---\nBody\n")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_text("the facts")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "run.py").write_text("print(1)\n")
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    import skills_config

    skills_config.discovered_skills.cache_clear()
    try:
        note = skills_config.skill_resource_note("with-resources")
        assert "knowledge base" in note
        assert str(skill / "references" / "guide.md") in note
        assert "python_repl" in note
        assert str(skill / "scripts" / "run.py") in note
        assert skills_config.skill_resource_note("missing") == ""
    finally:
        skills_config.discovered_skills.cache_clear()


@pytest.mark.asyncio
async def test_use_skill_activity_publishes_thinking_frames() -> None:
    tool_use_id = "act-skill-1"
    terminal = {
        "status": "success",
        "content": [{"text": "done"}],
        "toolUseId": tool_use_id,
    }

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        yield ToolStreamEvent(
            tool_use,
            {
                "skill_name": "demo-skill",
                "agent": SimpleNamespace(
                    system_prompt="Exact runtime system prompt",
                    tool_registry=SimpleNamespace(registry={"read": SimpleNamespace(tool_spec={
                        "name": "file_read", "description": "Read a file",
                        "inputSchema": {"json": {"type": "object"}},
                    })}),
                    model=SimpleNamespace(api_key="never-publish-this"),
                ),
                "event": {"data": "chunk"},
            },
        )
        yield ToolResultEvent(terminal)

    published: list[dict] = []

    class FakeTopic:
        def publish(self, payload: dict[str, Any]) -> None:
            published.append(payload)

    fake_client = MagicMock()
    fake_client.topic.return_value = FakeTopic()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id
    activity_info.attempt = 1

    handle = AsyncMock()
    handle.query = AsyncMock(return_value="gemini-3.7-flash")

    mock_tool = MagicMock()
    mock_tool.stream = fake_stream

    with (
        patch("use_skill_activity.activity.info", return_value=activity_info),
        patch(
            "use_skill_activity.activity.client",
            return_value=MagicMock(get_workflow_handle=lambda _id: handle),
        ),
        patch("use_skill_activity.session_model", new=AsyncMock(return_value=SilentModel())),
        patch(
            "use_skill_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("use_skill_activity.create_use_skill_tool", return_value=mock_tool),
        patch("subagent_support.session_loaded_tools", new=AsyncMock(return_value=[])),
    ):
        result = await use_skill_activity("demo-skill", "do something")

    assert result == terminal
    assert len(published) == 4
    assert published[0]["tool_use"]["name"] == "use_skill"
    assert published[0]["data"]["skill_name"] == "demo-skill"
    assert published[0]["data"]["type"] == "skill_start"
    cfg = published[1]["data"]["configuration"]
    assert cfg["systemPrompt"] == "Exact runtime system prompt"
    assert cfg["userPrompt"].endswith("do something")
    assert "Session workspace" in cfg["userPrompt"]
    assert cfg["model"] == "gemini-3.7-flash"
    assert cfg["skills"] == []
    assert cfg["tools"] == [
        {"name": "file_read", "description": "Read a file", "inputSchema": {"json": {"type": "object"}}},
    ]
    assert published[2]["data"]["event"] == {"data": "chunk"}
    assert published[3]["data"]["type"] == "skill_complete"
    assert "never-publish-this" not in str(published)


@pytest.mark.asyncio
async def test_use_skill_activity_assigns_inline_skills() -> None:
    """``skills`` reaches the factory as ``assigned_skills`` and the scoped
    catalog rides with the request."""
    tool_use_id = "act-skill-2"
    terminal = {
        "status": "success",
        "content": [{"text": "done"}],
        "toolUseId": tool_use_id,
    }
    seen_requests: list[str] = []

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        seen_requests.append(tool_use["input"]["request"])
        yield ToolResultEvent(terminal)

    fake_client = MagicMock()
    fake_client.topic.return_value = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    mock_tool = MagicMock()
    mock_tool.stream = fake_stream
    factory = MagicMock(return_value=mock_tool)

    with (
        patch("use_skill_activity.activity.info", return_value=activity_info),
        patch(
            "use_skill_activity.activity.client",
            return_value=MagicMock(get_workflow_handle=lambda _id: AsyncMock()),
        ),
        patch("use_skill_activity.session_model", new=AsyncMock(return_value=SilentModel())),
        patch(
            "use_skill_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("use_skill_activity.create_use_skill_tool", factory),
        patch(
            "skills_config.skills_prompt",
            return_value="<available_skills>scoped</available_skills>",
        ),
        patch("subagent_support.session_loaded_tools", new=AsyncMock(return_value=[])),
    ):
        result = await use_skill_activity(
            "demo-skill", "do something", skills=["helper-a", "helper-b"]
        )

    assert result == terminal
    assert factory.call_args.kwargs["assigned_skills"] == ["helper-a", "helper-b"]
    assert seen_requests[0].endswith(
        "do something\n\n<available_skills>scoped</available_skills>"
    )
    assert "Session workspace" in seen_requests[0]


@pytest.mark.asyncio
async def test_use_skill_activity_assigns_parent_registry_names() -> None:
    """``tools`` are parent-registry names after official load. A loaded
    tool resolves to its recorded file; a package path is the official
    warning, not an import."""
    tool_use_id = "act-skill-4"
    terminal = {"status": "success", "content": [{"text": "ok"}], "toolUseId": tool_use_id}

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        yield ToolResultEvent(terminal)

    fake_client = MagicMock()
    fake_client.topic.return_value = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    mock_tool = MagicMock()
    mock_tool.stream = fake_stream
    factory = MagicMock(return_value=mock_tool)

    with (
        patch("use_skill_activity.activity.info", return_value=activity_info),
        patch("use_skill_activity.session_model", new=AsyncMock(return_value=SilentModel())),
        patch(
            "use_skill_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("use_skill_activity.create_use_skill_tool", factory),
        patch(
            "subagent_support.session_loaded_tools",
            new=AsyncMock(return_value=[{"path": CALCULATOR, "name": "sleep"}]),
        ),
    ):
        result = await use_skill_activity(
            "demo-skill", "do it", tools=["sleep", "strands_tools.shell"]
        )

    assert result == terminal
    assigned = factory.call_args.kwargs["tools"]
    assert {_tool_name(tool) for tool in assigned} == {"sleep"}


def test_use_skill_spec_exposes_tools_and_skills() -> None:
    from temporalio.contrib.strands.workflow import activity_as_tool

    spec = activity_as_tool(use_skill_activity).tool_spec
    props = spec["inputSchema"]["json"]["properties"]
    assert set(props) == {"skill_name", "request", "skills", "tools"}
    assert spec["inputSchema"]["json"]["required"] == ["skill_name", "request"]
    for name, prop in props.items():
        assert prop.get("description"), f"{name} lacks a description"


@pytest.mark.asyncio
async def test_use_skill_activity_without_skills_keeps_request_verbatim() -> None:
    tool_use_id = "act-skill-3"
    terminal = {"status": "success", "content": [{"text": "ok"}], "toolUseId": tool_use_id}
    seen_requests: list[str] = []

    async def fake_stream(
        tool_use: dict[str, Any], invocation_state: dict[str, Any]
    ) -> AsyncIterator[Any]:
        seen_requests.append(tool_use["input"]["request"])
        yield ToolResultEvent(terminal)

    fake_client = MagicMock()
    fake_client.topic.return_value = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = tool_use_id

    mock_tool = MagicMock()
    mock_tool.stream = fake_stream
    factory = MagicMock(return_value=mock_tool)

    with (
        patch("use_skill_activity.activity.info", return_value=activity_info),
        patch(
            "use_skill_activity.activity.client",
            return_value=MagicMock(get_workflow_handle=lambda _id: AsyncMock()),
        ),
        patch("use_skill_activity.session_model", new=AsyncMock(return_value=SilentModel())),
        patch(
            "use_skill_activity.WorkflowStreamClient.from_within_activity",
            return_value=fake_client,
        ),
        patch("use_skill_activity.create_use_skill_tool", factory),
        patch("subagent_support.session_loaded_tools", new=AsyncMock(return_value=[])),
    ):
        await use_skill_activity("demo-skill", "do something")

    assert factory.call_args.kwargs["assigned_skills"] is None
    assert seen_requests[0].endswith("do something")
    assert "Session workspace" in seen_requests[0]
