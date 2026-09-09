"""Framework-adherence proof for the vendored ``orchestrator/graph_tool.py``.

Asserts, against the installed frameworks' OWN types and markers, that the
vendored graph tool is fully adherent as an ``activity_as_tool`` IO option:

(a) ``graph_tool.graph`` is a real Strands tool: an instance of
    ``strands.tools.decorator.DecoratedFunctionTool`` carrying a ToolSpec.
(b) ``graph_activity.graph_activity`` is a real Temporal activity: the
    ``__temporal_activity_definition`` marker ``temporalio.activity``
    attaches via ``@activity.defn`` resolves through
    ``activity._Definition.from_callable``.
(c) ``activity_as_tool(graph_activity, ...)`` produces a
    ``TemporalActivityTool`` whose spec name is ``graph`` and whose input
    schema is fully structural (``topology`` is a ``$ref`` to a typed
    ``GraphTopology`` definition, never a bare ``{"type": "object"}``).
(d) The streamed frames match the ``{"tool_use", "data"}`` envelope with a
    terminal ``{"status", "content"}`` frame -- the publish contract the
    frontend consumes.
(e) End-to-end: a one-shot execute against a scripted model emits a
    ``graph_topology`` event and a terminal success frame.

Plus the vendor-purity check (``graph_tool`` never imports the external
``strands_graph_tool`` package) and the malformed-input contract (unknown
``model_id`` raises ``ValueError`` at create).
"""

from __future__ import annotations

import inspect
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.models.model import Model
from strands.tools.decorator import DecoratedFunctionTool
from temporalio import activity
from temporalio.contrib.strands._temporal_activity_tool import TemporalActivityTool
from temporalio.contrib.strands.workflow import activity_as_tool

import graph_tool
import subagent_support
from graph_activity import graph_activity


# --- scripted-model harness (test_graph_activity.py pattern) -----------------


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
    graph_tool._manager.graphs.clear()
    graph_tool._model_factories.clear()
    yield
    subagent_support._MODEL_FACTORIES.clear()
    graph_tool._manager.graphs.clear()
    graph_tool._model_factories.clear()


def run_graph(model: Model, stream: FakeStreamClient):
    subagent_support.configure({"fake/text": lambda: model})

    activity_info = MagicMock()
    activity_info.workflow_id = "chat-adherence"
    activity_info.activity_id = "act-adherence-1"
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


# --- (a) Strands tool adherence ----------------------------------------------


def test_graph_is_a_strands_decorated_function_tool() -> None:
    assert isinstance(graph_tool.graph, DecoratedFunctionTool)
    assert graph_tool.graph.tool_name == "graph"
    assert graph_tool.graph.tool_type == "function"
    spec = graph_tool.graph.tool_spec
    assert spec["name"] == "graph"
    assert spec["description"]
    assert "inputSchema" in spec


def test_graph_tool_never_imports_the_external_package() -> None:
    source = inspect.getsource(graph_tool)
    assert "strands_graph_tool" not in source
    assert graph_tool.__file__.endswith("orchestrator/graph_tool.py")


# --- (b) Temporal activity adherence ------------------------------------------


def test_graph_activity_carries_the_temporal_activity_definition() -> None:
    assert hasattr(graph_activity, "__temporal_activity_definition")
    defn = activity._Definition.from_callable(graph_activity)
    assert defn is not None
    assert defn.name == "graph"


# --- (c) activity_as_tool spec adherence --------------------------------------


def test_activity_as_tool_produces_structural_graph_spec() -> None:
    wrapped = activity_as_tool(graph_activity)
    assert isinstance(wrapped, TemporalActivityTool)
    assert wrapped.tool_name == "graph"
    assert wrapped.tool_type == "temporal_activity"

    spec = wrapped.tool_spec
    assert spec["name"] == "graph"
    schema = spec["inputSchema"]["json"]
    props = schema["properties"]
    assert set(props) >= {"action", "graph_id", "topology", "task", "tools"}

    # topology must be a typed $ref, never an untyped {"type": "object"}.
    topology = props["topology"]
    assert topology.get("$ref") == "#/$defs/GraphTopology"
    assert topology.get("type") != "object"
    defs = schema["$defs"]
    assert {"GraphTopology", "GraphNode", "GraphEdge", "GraphTask"} <= set(defs)
    assert "nodes" in defs["GraphTopology"]["properties"]

    # task is a plain string parameter.
    assert props["task"]["type"] == "string"


def test_activity_as_tool_rejects_undecorated_callables() -> None:
    async def not_an_activity() -> None:  # pragma: no cover - never called
        pass

    with pytest.raises(ValueError, match="activity.defn"):
        activity_as_tool(not_an_activity)


# --- (d) + (e) IO contract: envelope frames and end-to-end execution ----------


@pytest.mark.asyncio
async def test_one_shot_execute_emits_envelope_topology_and_terminal_success() -> None:
    model = ScriptedModel()
    stream = FakeStreamClient()
    topo = {"nodes": [{"id": "solo", "system_prompt": "answer briefly"}]}
    a, b, c = run_graph(model, stream)
    with a, b, c:
        result = await graph_activity(
            action="execute", graph_id="adherence", topology=topo, task="go"
        )

    # Terminal return value: the {"status", "content"} tool-result shape.
    assert result["status"] == "success"
    assert isinstance(result["content"], list)
    assert any("executed" in block.get("text", "") for block in result["content"])

    published = stream.topics["thinking"].published
    assert published, "no frames were published on the thinking topic"

    # (d) every frame matches the {"tool_use", "data"} envelope.
    for frame in published:
        assert set(frame) == {"tool_use", "data"}
        assert frame["tool_use"] == {"name": "graph", "toolUseId": "act-adherence-1"}

    datas = [frame["data"] for frame in published]

    # (e) graph_topology first, terminal {"status", "content"} last.
    assert datas[0]["type"] == "graph_topology"
    assert [node["node_id"] for node in datas[0]["nodes"]] == ["solo"]
    terminal = datas[-1]
    assert terminal["status"] == "success"
    assert isinstance(terminal["content"], list)

    # The registry cleaned up after the one-shot run.
    assert graph_tool._manager.graphs == {}


# --- malformed input: unknown model_id fails at create ------------------------


def test_resolve_model_unknown_id_raises_value_error() -> None:
    graph_tool.configure_models({"known/model": lambda: ScriptedModel()})
    with pytest.raises(ValueError, match="Unknown model_id 'nope'"):
        graph_tool.resolve_model("nope", None)
    # The error lists the registered ids so a bad id is debuggable at create.
    with pytest.raises(ValueError, match="known/model"):
        graph_tool.resolve_model("nope", None)


@pytest.mark.asyncio
async def test_create_with_unknown_model_id_yields_error_at_create() -> None:
    graph_tool.configure_models({"known/model": lambda: ScriptedModel()})
    tool_use = {
        "toolUseId": "t-bad-model",
        "name": "graph",
        "input": {
            "action": "create",
            "graph_id": "bad-model",
            "topology": {"nodes": [{"id": "x", "system_prompt": "s", "model_id": "nope"}]},
        },
    }
    events = []
    stream = graph_tool.graph.stream(tool_use, {"agent": None})
    async for event in stream:
        events.append(event)
    from strands.types._events import ToolResultEvent

    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert results, "graph tool produced no ToolResultEvent"
    result = results[-1].tool_result
    assert result["status"] == "error"
    text = "".join(block.get("text", "") for block in result["content"])
    assert "Unknown model_id" in text
    assert "bad-model" not in graph_tool._manager.graphs
