"""Graph activity: recursive formations, streaming, one-shot lifecycle.

Runs the activity body with the workflow-stream client and workflow query
patched out, so formation construction, native event flattening, publishes,
cleanup, and the return shape are exercised without a Temporal server and
without paid model calls. Real native SDK executors (Graph/Swarm via the
vendored ``graph_tool``) run against a scripted model.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.models.model import Model
from temporalio.contrib.strands.workflow import activity_as_tool
from temporalio.exceptions import ApplicationError


import graph_activity as ga
import subagent_support
from graph_activity import (
    flatten_native_event,
    graph_activity,
    planned_topology,
)


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
    """Yields a numbered scripted text reply per stream() call."""

    def __init__(self) -> None:
        self.calls = 0

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
        self.calls += 1
        for event in text_events(f"reply-{self.calls}"):
            yield event


class HangingModel(ScriptedModel):
    async def stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        await asyncio.sleep(3600)
        yield {}


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
    # The sibling tool keeps a process-global manager: leaking entries across
    # tests would mask the cleanup assertions.
    from graph_tool import _manager

    _manager.graphs.clear()
    yield
    subagent_support._MODEL_FACTORIES.clear()
    _manager.graphs.clear()


def run_graph(model: Model, stream: FakeStreamClient, **kwargs: Any):
    subagent_support.configure({"fake/text": lambda: model})

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-test"
    activity_info.activity_id = "act-graph-1"
    activity_info.attempt = 1

    handle = AsyncMock()
    handle.query = AsyncMock(return_value="fake/text")
    client = MagicMock()
    client.get_workflow_handle.return_value = handle

    return patch.multiple(
        "graph_activity.activity",
        info=MagicMock(return_value=activity_info),
        client=MagicMock(return_value=client),
        heartbeat=MagicMock(),
        logger=MagicMock(),
    ), patch(
        "graph_activity.WorkflowStreamClient.from_within_activity",
        return_value=stream,
    ), patch(
        "subagent_support.activity",
        MagicMock(
            info=MagicMock(return_value=activity_info),
            client=MagicMock(return_value=client),
            heartbeat=MagicMock(),
        ),
    )


THINKING = "thinking"


def frames(stream: FakeStreamClient) -> list[dict[str, Any]]:
    return stream.topics[THINKING].published


# --- planned topology ------------------------------------------------------


def test_planned_topology_paths_and_edges() -> None:
    topo = {
        "nodes": [
            {"id": "research", "role": "research", "system_prompt": "r"},
            {
                "id": "writer",
                "role": "write",
                "system_prompt": "w",
                "model_settings": {"model_id": "preset:high"},
            },
        ],
        "edges": [{"from": "research", "to": "writer"}],
        "entry_points": ["research"],
    }
    planned = planned_topology(topo, "demo")
    ids = {node["node_id"]: node for node in planned["nodes"]}
    assert ids["research"]["node_type"] == "agent"
    assert ids["research"]["parent_id"] is None
    assert ids["research"]["role"] == "research"
    assert ids["writer"]["model"] == "preset:high"
    edges = {(edge["from"], edge["to"]) for edge in planned["edges"]}
    assert edges == {("research", "writer")}


# --- native event flattening -----------------------------------------------


def test_flatten_unwraps_nested_events_and_qualifies_paths() -> None:
    nested = {
        "type": "multiagent_node_stream",
        "node_id": "outer",
        "event": {
            "type": "multiagent_node_start",
            "node_id": "inner",
            "node_type": "agent",
        },
    }
    [frame] = flatten_native_event(nested)
    assert frame["type"] == "multiagent_node_start"
    assert frame["node_id"] == "outer/inner"
    assert frame["label"] == "inner"
    assert frame["parent_id"] == "outer"


def test_flatten_leaf_stream_keeps_tool_events_and_strips_handles() -> None:
    class Handle:  # non-serializable runtime object
        pass

    leaf = {
        "type": "multiagent_node_stream",
        "node_id": "coder",
        "event": {
            "event": {"contentBlockStart": {"start": {"toolUse": {"name": "file_write", "toolUseId": "t1"}}}},
            "agent": Handle(),
            "model": Handle(),
        },
    }
    [frame] = flatten_native_event(leaf)
    assert frame["type"] == "multiagent_node_stream"
    assert frame["node_id"] == "coder"
    inner = frame["event"]
    assert "agent" not in inner and "model" not in inner
    assert inner["event"]["contentBlockStart"]["start"]["toolUse"]["name"] == "file_write"


def test_flatten_prefixes_handoff_paths() -> None:
    nested = {
        "type": "multiagent_node_stream",
        "node_id": "team",
        "event": {
            "type": "multiagent_handoff",
            "from_node_ids": ["coder"],
            "to_node_ids": ["reviewer"],
            "message": "over to you",
        },
    }
    [frame] = flatten_native_event(nested)
    assert frame["from_node_ids"] == ["team/coder"]
    assert frame["to_node_ids"] == ["team/reviewer"]
    assert frame["message"] == "over to you"


def test_flatten_enriches_from_planned_metadata() -> None:
    planned = planned_topology(
        {"nodes": [{"id": "writer", "role": "write", "system_prompt": "w", "model_settings": {"model_id": "preset:high"}}]},
        "demo",
    )
    meta = {node["node_id"]: node for node in planned["nodes"]}
    [frame] = flatten_native_event(
        {"type": "multiagent_node_start", "node_id": "writer", "node_type": "agent"},
        meta=meta,
    )
    assert frame["node_type"] == "agent"
    assert frame["model"] == "preset:high"


# --- validation ------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_without_task_errors() -> None:
    info = MagicMock()
    info.activity_id = "a1"
    info.attempt = 1
    with patch.object(ga.activity, "info", return_value=info):
        with pytest.raises(ApplicationError, match="task prompt is required") as exc:
            await graph_activity(action="execute", topology={"nodes": [{"id": "x"}]})
    assert exc.value.non_retryable


@pytest.mark.asyncio
async def test_create_without_topology_errors() -> None:
    info = MagicMock()
    info.activity_id = "a1"
    info.attempt = 1
    with patch.object(ga.activity, "info", return_value=info):
        with pytest.raises(ApplicationError, match="non-empty 'nodes'"):
            await graph_activity(action="create", graph_id="g1")


@pytest.mark.asyncio
async def test_unknown_tools_are_official_warning_not_an_import() -> None:
    """Formation-wide tools= filters the parent registry by name. A package
    or invented name is the official warning; the graph still runs."""
    model = ScriptedModel()
    stream = FakeStreamClient()
    a, b, c = run_graph(model, stream)
    with a, b, c, patch("graph_tool.logger") as logger:
        result = await graph_activity(
            action="execute",
            topology={
                "nodes": [{
                    "id": "solo",
                    "system_prompt": "s",
                    "model_settings": {"model_id": ""},
                }],
            },
            task="go",
            tools=["definitely_not_a_tool"],
        )
    assert result["status"] == "success"
    logger.warning.assert_any_call(
        "Tool '%s' not found in parent agent's tool registry",
        "definitely_not_a_tool",
    )


# --- one-shot execution through real native executors -----------------------


@pytest.mark.asyncio
async def test_one_shot_recursive_execution_streams_and_cleans_up() -> None:
    from graph_tool import _manager

    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {
        "nodes": [
            {"id": "research", "role": "research", "system_prompt": "r"},
            {"id": "pipeline", "role": "write", "system_prompt": "w"},
        ],
        "edges": [{"from": "research", "to": "pipeline"}],
        "entry_points": ["research"],
    }
    a, b, c = run_graph(model, stream)
    with a, b, c:
        result = await graph_activity(
            action="execute", graph_id="demo", topology=topo, task="do it"
        )

    assert result["status"] == "success"
    assert result["toolUseId"] == "act-graph-1"
    assert "executed" in result["content"][0]["text"]

    published = frames(stream)
    assert all(frame["tool_use"] == {"name": "graph", "toolUseId": "act-graph-1"} for frame in published)
    datas = [frame["data"] for frame in published]

    # 1. Initial planned topology frame with structural edges.
    assert datas[0]["type"] == "graph_topology"
    assert {node["node_id"] for node in datas[0]["nodes"]} == {"research", "pipeline"}
    assert datas[0]["edges"] == [{"from": "research", "to": "pipeline"}]

    types = [(d.get("type"), d.get("node_id")) for d in datas]
    assert ("multiagent_node_start", "research") in types
    assert ("multiagent_node_start", "pipeline") in types
    assert ("multiagent_node_stop", "pipeline") in types
    handoffs = [d for d in datas if d.get("type") == "multiagent_handoff"]
    assert any(
        d.get("from_node_ids") == ["research"] and d.get("to_node_ids") == ["pipeline"]
        for d in handoffs
    )

    # Text deltas ride inside leaf multiagent_node_stream frames.
    stream_frames = [
        d for d in datas
        if d.get("type") == "multiagent_node_stream" and isinstance(d.get("event"), dict)
    ]
    texts = [
        d["event"].get("data")
        for d in stream_frames
        if isinstance(d["event"].get("data"), str)
    ]
    assert any("reply-" in t for t in texts)

    # Frames are JSON-safe (no runtime handles).
    import json

    json.dumps(datas)

    # 3. One-shot cleanup: no registry entry remains.
    assert _manager.graphs == {}


@pytest.mark.asyncio
async def test_one_shot_fanout_emits_batch_handoff() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {
        "nodes": [
            {"id": "coordinator", "role": "coordinate", "system_prompt": "c"},
            {"id": "worker_a", "role": "research", "system_prompt": "a"},
            {"id": "worker_b", "role": "research", "system_prompt": "b"},
            {"id": "aggregator", "role": "write", "system_prompt": "g"},
        ],
        "edges": [
            {"from": "coordinator", "to": "worker_a"},
            {"from": "coordinator", "to": "worker_b"},
            {"from": "worker_a", "to": "aggregator"},
            {"from": "worker_b", "to": "aggregator"},
        ],
        "entry_points": ["coordinator"],
    }
    a, b, c = run_graph(model, stream)
    with a, b, c:
        result = await graph_activity(action="execute", topology=topo, task="build")

    assert result["status"] == "success"
    datas = [frame["data"] for frame in frames(stream)]
    handoffs = [d for d in datas if d.get("type") == "multiagent_handoff"]
    assert any(
        set(d.get("to_node_ids") or []) == {"worker_a", "worker_b"}
        for d in handoffs
    )


@pytest.mark.asyncio
async def test_sequential_agents_execute() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {
        "nodes": [
            {"id": "gather", "role": "research", "system_prompt": "collect"},
            {"id": "report", "role": "write", "system_prompt": "write"},
        ],
        "edges": [{"from": "gather", "to": "report"}],
        "entry_points": ["gather"],
    }
    a, b, c = run_graph(model, stream)
    with a, b, c:
        result = await graph_activity(action="execute", topology=topo, task="go")

    assert result["status"] == "success"
    datas = [frame["data"] for frame in frames(stream)]
    starts = {d["node_id"] for d in datas if d.get("type") == "multiagent_node_start"}
    assert {"gather", "report"} <= starts
    handoffs = [d for d in datas if d.get("type") == "multiagent_handoff"]
    assert any(
        d.get("from_node_ids") == ["gather"] and d.get("to_node_ids") == ["report"]
        for d in handoffs
    )


@pytest.mark.asyncio
async def test_model_inheritance_all_nodes_use_session_model() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {
        "nodes": [
            {"id": "a", "system_prompt": "sa"},
            {"id": "b", "system_prompt": "sb"},
        ],
        "edges": [{"from": "a", "to": "b"}],
    }
    a, b, c = run_graph(model, stream)
    with a, b, c:
        result = await graph_activity(action="execute", topology=topo, task="go")
    assert result["status"] == "success"
    # Both nodes streamed on the ONE session model instance resolved from the
    # registered factory (no other model exists in this test).
    assert model.calls == 2


@pytest.mark.asyncio
async def test_node_model_id_selects_a_registered_model() -> None:
    from graph_tool import configure_models

    session = ScriptedModel()
    alt = ScriptedModel()
    stream = FakeStreamClient()
    configure_models({"fake/text": lambda: session, "fake/alt": lambda: alt})
    topo = {
        "nodes": [
            {"id": "a", "system_prompt": "sa"},
            {"id": "b", "system_prompt": "sb", "model_settings": {"model_id": "fake/alt"}},
        ],
        "edges": [{"from": "a", "to": "b"}],
    }
    a_, b_, c_ = run_graph(session, stream)
    try:
        with a_, b_, c_:
            result = await graph_activity(action="execute", topology=topo, task="go")
    finally:
        configure_models({})
    assert result["status"] == "success"
    assert session.calls == 1 and alt.calls == 1
    planned = frames(stream)[0]["data"]
    assert next(n for n in planned["nodes"] if n["node_id"] == "b")["model"] == "fake/alt"


@pytest.mark.asyncio
async def test_unknown_node_model_id_fails_at_create() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    a_, b_, c_ = run_graph(model, stream)
    with a_, b_, c_:
        with pytest.raises(ApplicationError, match="Unknown model_id"):
            await graph_activity(
                action="execute",
                topology={"nodes": [{"id": "x", "system_prompt": "s", "model_settings": {"model_id": "nope"}}]},
                task="go",
            )
    from graph_tool import _manager

    assert _manager.graphs == {}


@pytest.mark.asyncio
async def test_node_error_yields_error_terminal_state() -> None:
    class ExplodingModel(ScriptedModel):
        async def stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
            raise RuntimeError("model exploded")
            yield {}

    model = ExplodingModel()
    stream = FakeStreamClient()
    a, b, c = run_graph(model, stream)
    with a, b, c:
        with pytest.raises(ApplicationError):
            await graph_activity(
                action="execute",
                topology={"nodes": [{"id": "solo", "system_prompt": "s"}]},
                task="go",
            )
    from graph_tool import _manager

    assert _manager.graphs == {}  # cleanup ran despite the failure


@pytest.mark.asyncio
async def test_execution_timeout_produces_error_and_cleanup(monkeypatch) -> None:
    from datetime import timedelta

    monkeypatch.setattr(ga, "GRAPH_EXECUTION_TIMEOUT", timedelta(milliseconds=50))
    model = HangingModel()
    stream = FakeStreamClient()
    a, b, c = run_graph(model, stream)
    with a, b, c:
        with pytest.raises(ApplicationError, match="exceeded"):
            await graph_activity(
                action="execute",
                topology={"nodes": [{"id": "solo", "system_prompt": "s"}]},
                task="go",
            )
    # The terminal error frame still reaches the frontend.
    assert frames(stream)[-1]["data"]["status"] == "error"
    from graph_tool import _manager

    assert _manager.graphs == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_fails", [False, True])
async def test_cancellation_is_not_swallowed_and_cleans_up(monkeypatch, publish_fails) -> None:
    original_publish = FakeTopic.publish

    def publish(self, value, *, force_flush=False):
        if publish_fails and value.get("data", {}).get("status") == "cancelled":
            raise RuntimeError("stream unavailable")
        original_publish(self, value, force_flush=force_flush)

    monkeypatch.setattr(FakeTopic, "publish", publish)
    model = HangingModel()
    stream = FakeStreamClient()
    a, b, c = run_graph(model, stream)
    with a, b, c:
        task = asyncio.create_task(
            graph_activity(
                action="execute",
                topology={"nodes": [{"id": "solo", "system_prompt": "s"}]},
                task="go",
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        if publish_fails:
            ga.activity.logger.warning.assert_called_once()
            assert "cancellation notification failed" in ga.activity.logger.warning.call_args.args[0]
            assert str(ga.activity.logger.warning.call_args.args[2]) == "stream unavailable"
    from graph_tool import _manager

    assert _manager.graphs == {}
    # Publishing is best-effort; cancellation and cleanup survive its failure.
    datas = [frame["data"] for frame in frames(stream)]
    assert any(d.get("status") == "cancelled" for d in datas) is not publish_fails


@pytest.mark.asyncio
async def test_no_cross_run_collision_same_graph_id() -> None:
    """Two one-shot runs with the same graph_id do not collide."""
    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {"nodes": [{"id": "solo", "system_prompt": "s"}]}
    a, b, c = run_graph(model, stream)
    with a, b, c:
        first = await graph_activity(action="execute", graph_id="same", topology=topo, task="one")
        second = await graph_activity(action="execute", graph_id="same", topology=topo, task="two")
    assert first["status"] == "success"
    assert second["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_non_agent_node_type_is_rejected() -> None:
    """Official graph nodes are agents. A type string is not a formation."""
    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {"nodes": [{"id": "writer", "type": "swarm", "system_prompt": "w"}]}
    a, b, c = run_graph(model, stream)
    with a, b, c:
        with pytest.raises(ApplicationError, match="Unknown node type"):
            await graph_activity(action="execute", topology=topo, task="write")


def test_skill_agent_node_assigned_skills_become_inline_tools(monkeypatch) -> None:
    """A skill_agent node's ``skills`` list assigns those skills to the
    sub-agent as a scoped Pattern-2 skill() tool plus the scoped catalog in
    its system prompt — traditional inline use, never nested sub-agents."""
    from pathlib import Path

    import graph_tool
    import skills_config
    from skills_config import ensure_skills_configured

    fixtures = (
        Path(__file__).resolve().parents[2]
        / ".."
        / "strands-tools"
        / "tests"
        / "fixtures_skills"
    ).resolve()
    if not fixtures.is_dir():
        pytest.skip("fixtures_skills catalog unavailable")
    monkeypatch.setenv("SKILLS_DIR", str(fixtures))
    skills_config.discovered_skills.cache_clear()
    try:
        ensure_skills_configured(None)
        agent = graph_tool.build_skill_agent(
            {"id": "writer", "skill": "wf-skill", "skills": ["wf-skill"]},
            parent_agent=None,
            model=ScriptedModel(),
        )
        tool_names = set(agent.tool_registry.registry)
        assert "skill" in tool_names
        assert "use_skill" not in tool_names  # inline use, never nested
        assert "<name>wf-skill</name>" in agent.system_prompt

        # Without an assignment there is no inline skill tool and no catalog.
        plain = graph_tool.build_skill_agent(
            {"id": "writer2", "skill": "wf-skill"},
            parent_agent=None,
            model=ScriptedModel(),
        )
        assert "skill" not in set(plain.tool_registry.registry)
        assert "<available_skills>" not in plain.system_prompt
    finally:
        skills_config.discovered_skills.cache_clear()
        ensure_skills_configured(None)


def test_skill_agent_node_tools_come_from_parent_registry(monkeypatch) -> None:
    """A skill_agent node's ``tools`` are the official parent-registry filter
    into the reference ``_create_skill_agent`` tools slot."""
    from pathlib import Path

    from strands import Agent
    from strands_tools import calculator

    import graph_tool
    import skills_config
    from skills_config import ensure_skills_configured

    fixtures = (
        Path(__file__).resolve().parents[2]
        / ".."
        / "strands-tools"
        / "tests"
        / "fixtures_skills"
    ).resolve()
    if not fixtures.is_dir():
        pytest.skip("fixtures_skills catalog unavailable")
    monkeypatch.setenv("SKILLS_DIR", str(fixtures))
    skills_config.discovered_skills.cache_clear()
    try:
        ensure_skills_configured(None)
        parent = Agent(model=ScriptedModel(), tools=[calculator], callback_handler=None)
        agent = graph_tool.build_skill_agent(
            {"id": "writer", "skill": "wf-skill", "tools": ["calculator"]},
            parent_agent=parent,
            model=ScriptedModel(),
        )
        names = set(agent.tool_registry.registry)
        assert "calculator" in names
        assert {"file_read", "file_write"} <= names
    finally:
        skills_config.discovered_skills.cache_clear()
        ensure_skills_configured(None)


def test_agent_node_ignores_skills_like_official_create_agent_with_model() -> None:
    """Official ``create_agent_with_model`` has no node ``skills`` field.

    Catalog work is ``skill_agent`` / ``_create_skill_agent``. An agent node's
    ``skills`` list must not inject an inline skill tool — that is how
    Agent API names such as ``pplx_sdk`` became ``SkillNotFoundError``.
    """
    from strands import Agent

    import graph_tool

    parent = Agent(model=ScriptedModel(), tools=[], callback_handler=None)
    agent = graph_tool._build_agent(
        {"id": "coder", "system_prompt": "Code.", "skills": ["pplx_sdk"]},
        parent, None, None,
    )
    assert "skill" not in set(agent.tool_registry.registry)
    assert agent.system_prompt == "Code."


@pytest.mark.asyncio
async def test_node_tools_inherit_what_the_orchestrator_loaded() -> None:
    """A node may name a tool the orchestrator load_tool-ed onto itself even
    when the formation-wide ``tools`` default omits it: the parent is rebuilt
    from the workflow's loaded_tools query, and the official per-node filter
    finds the name there."""
    from load_tool import STRANDS_TOOLS_DIR

    import graph_tool

    model = ScriptedModel()
    stream = FakeStreamClient()
    loaded = [{"path": str(STRANDS_TOOLS_DIR / "calculator.py"), "name": "calculator"}]

    async def query(name: str, *_: Any, **__: Any) -> Any:
        return {"model_id": "fake/text", "loaded_tools": loaded}[name]

    seen: list[set[str]] = []
    real_build = graph_tool._build_agent

    def spy(node_def: Any, parent: Any, model_id: Any, tools: Any) -> Any:
        agent = real_build(node_def, parent, model_id, tools)
        seen.append(set(agent.tool_registry.registry))
        return agent

    a, b, c = run_graph(model, stream)
    with a, b, c, patch.object(graph_tool, "_build_agent", side_effect=spy):
        subagent_support.activity.client.return_value.get_workflow_handle.return_value.query = AsyncMock(
            side_effect=query
        )
        result = await graph_activity(
            action="execute",
            topology={
                "nodes": [
                    {"id": "calc", "system_prompt": "s", "tools": ["calculator"]},
                    {"id": "all", "system_prompt": "s"},
                ]
            },
            task="go",
        )
    assert result["status"] == "success"
    assert seen[0] == {"calculator"}
    assert seen[1] == {"calculator"}


# --- management actions (process-local) --------------------------------------


@pytest.mark.asyncio
async def test_management_actions_pass_through() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {"nodes": [{"id": "solo", "system_prompt": "s"}]}
    a, b, c = run_graph(model, stream)
    with a, b, c:
        created = await graph_activity(action="create", graph_id="managed", topology=topo)
        assert created["status"] == "success"
        listed = await graph_activity(action="list")
        assert "managed" in listed["content"][0]["text"]
        deleted = await graph_activity(action="delete", graph_id="managed")
        assert deleted["status"] == "success"
    from graph_tool import _manager

    assert _manager.graphs == {}


# --- schema ------------------------------------------------------------------


def test_activity_as_tool_spec_is_flat_and_documented() -> None:
    spec = activity_as_tool(graph_activity).tool_spec
    schema = spec["inputSchema"]["json"]
    props = schema["properties"]
    assert set(props) == {
        "action", "graph_id", "topology", "task", "tools",
    }
    for name, prop in props.items():
        assert prop.get("description"), f"{name} lacks a description"
    assert props["tools"]["type"] == "array"
    # topology is a typed Pydantic model: the schema is structural, not a bare
    # object (which the Perplexity Agent API rejects and which leaves the model
    # nothing to fill -- observed live as topology={}).
    topology = schema["$defs"]["GraphTopology"]
    assert set(topology["properties"]) == {"nodes", "edges", "entry_points"}
    assert topology["required"] == ["nodes"]
    node = schema["$defs"]["GraphNode"]
    assert set(node["properties"]) == {"id", "role", "system_prompt", "model_settings", "tools"}
    settings = schema["$defs"]["GraphModelSettings"]["properties"]
    assert set(settings) == {"model_id"}
    import json as _json

    assert "model_provider" not in _json.dumps(schema)
    assert schema["$defs"]["GraphEdge"]["required"] == ["from", "to"]
    # No untyped object anywhere in the outbound schema.
    def bare_objects(node: Any) -> list[Any]:
        found = []
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" not in node:
                found.append(node)
            for value in node.values():
                found.extend(bare_objects(value))
        elif isinstance(node, list):
            for value in node:
                found.extend(bare_objects(value))
        return found

    assert bare_objects(schema) == []
    # The outbound conversion (verbatim schema) passes the Agent API validator.
    from perplexity_operations import _validate_tools

    _validate_tools([{
        "type": "function",
        "name": spec["name"],
        "description": spec.get("description", ""),
        "parameters": schema,
    }])


def test_topology_model_round_trips_to_sibling_dict() -> None:
    from graph_activity import GraphTopology, _as_topology_dict

    topo = GraphTopology.model_validate({
        "nodes": [
            {"id": "a", "role": "research", "system_prompt": "s"},
            {"id": "b", "system_prompt": "t", "model_settings": {"model_id": "preset:high"}},
        ],
        "edges": [{"from": "a", "to": "b"}],
    })
    as_dict = _as_topology_dict(topo)
    assert as_dict["edges"] == [{"from": "a", "to": "b"}]
    assert as_dict["nodes"][0] == {"id": "a", "role": "research", "system_prompt": "s"}
    assert as_dict["nodes"][1]["model_settings"] == {"model_id": "preset:high"}
    # Plain dicts (unit tests, direct callers) pass through unchanged.
    assert _as_topology_dict({"nodes": []}) == {"nodes": []}
