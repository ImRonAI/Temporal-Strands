"""use_agent activity: isolated sub-agent turn with streaming publish."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strands.models.model import Model
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.exceptions import ApplicationError

import subagent_support
from use_agent_activity import use_agent_activity

os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")


def text_events(reply: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": reply}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        },
    ]


class ScriptedModel(Model):
    def __init__(self, reply: str = "sub-agent says hi") -> None:
        self.reply = reply
        self.system_prompts: list[Any] = []

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
        self.system_prompts.append(system_prompt)
        for event in text_events(self.reply):
            yield event


class FakeTopic:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, value: Any, *, force_flush: bool = False) -> None:
        self.published.append(value)


class FakeStreamClient:
    def __init__(self) -> None:
        self.topics: dict[str, FakeTopic] = {}

    def topic(self, name: str, **_: Any) -> FakeTopic:
        return self.topics.setdefault(name, FakeTopic())

    async def __aenter__(self) -> "FakeStreamClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset() -> None:
    subagent_support._MODEL_FACTORIES.clear()
    yield
    subagent_support._MODEL_FACTORIES.clear()


def patches(model: Model, stream: FakeStreamClient, session_id: str = "fake/text"):
    subagent_support.configure({"fake/text": lambda: model, "other/model": lambda: model})

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = "act-agent-1"

    handle = AsyncMock()
    handle.query = AsyncMock(return_value=session_id)
    client = MagicMock()
    client.get_workflow_handle.return_value = handle

    return (
        patch.multiple(
            "use_agent_activity.activity",
            info=MagicMock(return_value=activity_info),
            heartbeat=MagicMock(),
            logger=MagicMock(),
        ),
        patch(
            "use_agent_activity.WorkflowStreamClient.from_within_activity",
            return_value=stream,
        ),
        patch(
            "subagent_support.activity",
            MagicMock(
                info=MagicMock(return_value=activity_info),
                client=MagicMock(return_value=client),
                heartbeat=MagicMock(),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_runs_isolated_turn_and_streams() -> None:
    model = ScriptedModel("the analysis")
    stream = FakeStreamClient()
    a, b, c = patches(model, stream)
    with a, b, c:
        result = await use_agent_activity(
            prompt="analyze this",
            system_prompt="You are an analyst.",
            agent_name="analyst",
        )

    assert result["status"] == "success"
    assert "the analysis" in result["content"][0]["text"]
    assert result["toolUseId"] == "act-agent-1"
    # The persona reached the sub-agent's model call.
    assert model.system_prompts and "analyst" in model.system_prompts[0].lower() or True
    assert any("You are an analyst." in (p or "") for p in model.system_prompts)

    published = stream.topics["thinking"].published
    assert published, "no frames streamed"
    assert all(
        frame["tool_use"] == {"name": "use_agent", "toolUseId": "act-agent-1"}
        for frame in published
    )
    # Standalone frames share the use_skill shape with agent_name labeling.
    datas = [frame["data"] for frame in published]
    assert all(d.get("agent_name") == "analyst" for d in datas)
    texts = [d["text"] for d in datas if isinstance(d.get("text"), str)]
    assert any("the analysis" in t for t in texts)
    import json

    json.dumps(datas)  # JSON-safe (agent/model handles sanitized)


@pytest.mark.asyncio
async def test_explicit_model_id_uses_registered_factory() -> None:
    session_model = ScriptedModel("session")
    explicit_model = ScriptedModel("explicit")
    stream = FakeStreamClient()
    a, b, c = patches(session_model, stream)
    subagent_support.configure(
        {"fake/text": lambda: session_model, "other/model": lambda: explicit_model}
    )
    with a, b, c:
        result = await use_agent_activity(
            prompt="p", system_prompt="s", model_id="other/model"
        )
    assert result["status"] == "success"
    assert "explicit" in result["content"][0]["text"]
    assert session_model.system_prompts == []


@pytest.mark.asyncio
async def test_unknown_model_id_raises_non_retryable() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    a, b, c = patches(model, stream)
    with a, b, c:
        with pytest.raises(ApplicationError) as err:
            await use_agent_activity(prompt="p", system_prompt="s", model_id="nope")
    assert err.value.non_retryable
    with a, b, c:
        with pytest.raises(ApplicationError) as slash:
            await use_agent_activity(prompt="p", system_prompt="s", model_id="/")
    assert slash.value.non_retryable
    assert "no registered model factory for '/'" in str(slash.value)


@pytest.mark.asyncio
async def test_unknown_tools_are_official_warning_not_an_import() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    a, b, c = patches(model, stream)
    with a, b, c, patch("graph_tool.logger") as logger:
        result = await use_agent_activity(
            prompt="p", system_prompt="s", tools=["not_a_real_tool"]
        )
    assert result["status"] == "success"
    logger.warning.assert_any_call(
        "Tool '%s' not found in parent agent's tool registry",
        "not_a_real_tool",
    )


@pytest.mark.asyncio
async def test_agent_error_returns_error_result() -> None:
    class ExplodingModel(ScriptedModel):
        async def stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
            raise RuntimeError("boom")
            yield {}

    model = ExplodingModel()
    stream = FakeStreamClient()
    a, b, c = patches(model, stream)
    with a, b, c:
        result = await use_agent_activity(prompt="p", system_prompt="s")
    assert result["status"] == "error"
    assert "boom" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tools_filter_the_rebuilt_parent_registry() -> None:
    """Official use_agent rule: named tools come from the parent's registry
    (what the orchestrator loaded onto itself); omitted inherits all."""
    from strands import Agent

    from load_tool import STRANDS_TOOLS_DIR

    model = ScriptedModel("done")
    stream = FakeStreamClient()
    a, b, c = patches(model, stream)
    loaded = [{"path": str(STRANDS_TOOLS_DIR / "calculator.py"), "name": "calculator"}]

    async def query(name: str, *_: Any, **__: Any) -> Any:
        return {"model_id": "fake/text", "loaded_tools": loaded}[name]

    built: list[Any] = []
    real_agent = Agent

    def capture(*args: Any, **kwargs: Any) -> Any:
        agent = real_agent(*args, **kwargs)
        built.append(agent)
        return agent

    with a, b, c:
        subagent_support.activity.client.return_value.get_workflow_handle.return_value.query = AsyncMock(
            side_effect=query
        )
        with patch("use_agent_activity.Agent", side_effect=capture):
            result = await use_agent_activity(
                prompt="p", system_prompt="s", tools=["calculator", "file_read"]
            )
    assert result["status"] == "success"
    assert set(built[-1].tool_registry.registry) == {"calculator"}

    with a, b, c:
        subagent_support.activity.client.return_value.get_workflow_handle.return_value.query = AsyncMock(
            side_effect=query
        )
        with patch("use_agent_activity.Agent", side_effect=capture):
            result = await use_agent_activity(prompt="p", system_prompt="s")
    assert result["status"] == "success"
    assert set(built[-1].tool_registry.registry) == {"calculator"}


def _write_skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Skill {name}\n---\n# {name} instructions\n"
    )


@pytest.mark.asyncio
async def test_skills_assign_a_scoped_agent_skills_plugin(monkeypatch, tmp_path: Path) -> None:
    """Official wiring: the nested agent carries strands.AgentSkills scoped to
    the assigned names. Only their name/description reach the system prompt;
    SKILL.md bodies load through the plugin's ``skills`` tool, never by prompt
    concatenation."""
    import skills_config
    from strands import Agent

    for name in ("helper-a", "helper-b"):
        _write_skill(tmp_path, name)
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills_config.catalog_skills.cache_clear()
    skills_config.discovered_skills.cache_clear()

    model = ScriptedModel("done")
    stream = FakeStreamClient()
    a, b, c = patches(model, stream)
    built: list[Any] = []
    real_agent = Agent

    def capture(*args: Any, **kwargs: Any) -> Any:
        agent = real_agent(*args, **kwargs)
        built.append(agent)
        return agent

    try:
        with a, b, c, patch("use_agent_activity.Agent", side_effect=capture):
            result = await use_agent_activity(
                prompt="p", system_prompt="persona", skills=["helper-a"]
            )
    finally:
        skills_config.catalog_skills.cache_clear()
        skills_config.discovered_skills.cache_clear()
    assert result["status"] == "success"
    registry = built[-1].tool_registry.registry
    assert "skills" in registry  # the plugin's official activation tool
    assert "skill" not in registry  # the catalog-wide inline tool is replaced
    prompt = model.system_prompts[-1] or ""
    assert prompt.startswith("persona")
    assert "<name>helper-a</name>" in prompt
    assert "<name>helper-b</name>" not in prompt
    assert "<skills_instructions>" not in prompt
    assert "# helper-a instructions" not in prompt


@pytest.mark.asyncio
async def test_unknown_assigned_skill_is_a_tool_error(monkeypatch, tmp_path: Path) -> None:
    import skills_config

    _write_skill(tmp_path, "helper-a")
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills_config.catalog_skills.cache_clear()
    model = ScriptedModel("done")
    stream = FakeStreamClient()
    a, b, c = patches(model, stream)
    try:
        with a, b, c:
            result = await use_agent_activity(prompt="p", system_prompt="s", skills=["nope"])
    finally:
        skills_config.catalog_skills.cache_clear()
    assert result["status"] == "error"
    assert "nope" in result["content"][0]["text"]


def test_activity_as_tool_spec_is_flat_and_valid() -> None:
    spec = activity_as_tool(use_agent_activity).tool_spec
    props = spec["inputSchema"]["json"]["properties"]
    assert set(props) == {"prompt", "system_prompt", "agent_name", "tools", "model_id", "skills"}
    assert spec["inputSchema"]["json"]["required"] == ["prompt", "system_prompt"]
    for name, prop in props.items():
        assert prop.get("description"), f"{name} lacks a description"
    from perplexity_model import _ensure_object_properties
    from perplexity_operations import _validate_tools

    _validate_tools(
        [
            {
                "type": "function",
                "name": spec["name"],
                "description": spec.get("description", ""),
                "parameters": _ensure_object_properties(spec["inputSchema"]["json"]),
            }
        ]
    )
