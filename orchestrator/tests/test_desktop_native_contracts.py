"""Native Temporal/Strands contracts the desktop branch must build on.

Offline compatibility increment for
``.kilo/plans/1788845228632-gce-desktop-native-infrastructure-spec.md``
section 3.2 / 9.1. Everything here runs against the pinned installed SDKs
(``temporalio==1.31.0``, ``strands-agents==1.50.2``) with
``workflow.execute_activity`` monkeypatched, so no Temporal server, cloud,
inference, or credential is touched.

The fixture activities below are throwaway stand-ins with the *shape* the
desktop activities will have; they are not the implementation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any, AsyncGenerator, Literal
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field
from strands import Agent
from strands.hooks import AfterToolCallEvent, BeforeModelCallEvent, BeforeToolCallEvent
from strands.models.model import Model
from strands.tools.executors import ConcurrentToolExecutor, SequentialToolExecutor
from strands.types._events import ToolInterruptEvent, ToolResultEvent
from strands.types.tools import ToolContext
from temporalio import activity
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.contrib.strands import StrandsPlugin, TemporalAgent
from temporalio.contrib.strands import _temporal_activity_tool as tat
from temporalio.contrib.strands import _temporal_model as tm
from temporalio.contrib.strands._failure_converter import STRANDS_INTERRUPT_TYPE
from temporalio.contrib.strands._model_activity import (
    ModelActivity,
    _StreamingInvokeModelInput,
)
from temporalio.contrib.strands._plugin import _data_converter
from temporalio.contrib.strands.workflow import activity_as_hook, activity_as_tool
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.workflow import ActivityCancellationType

from config import (
    DESKTOP_JOB_HEARTBEAT_TIMEOUT,
    DESKTOP_MUTATION_RETRY_POLICY,
    DESKTOP_MUTATION_TIMEOUT,
    DESKTOP_OBSERVATION_RETRY_POLICY,
    DESKTOP_OBSERVATION_TIMEOUT,
    DESKTOP_TASK_TIMEOUT,
)
from perplexity_model import _ensure_object_properties
from perplexity_operations import _validate_tools

# --------------------------------------------------------------------------
# Version baseline (spec 3.1). Fail loudly if the pins drift under the tests.
# --------------------------------------------------------------------------


def test_pinned_framework_versions_match_spec_baseline() -> None:
    import importlib.metadata as metadata

    assert metadata.version("temporalio") == "1.31.0"
    assert metadata.version("strands-agents") == "1.50.2"
    assert metadata.version("strands-agents-tools") == "0.8.5"
    assert metadata.version("pydantic") == "2.13.4"


def test_newer_doc_hook_apis_are_absent_in_installed_release() -> None:
    """Spec 3.2: do not code against BeforeToolsEvent/AfterToolsEvent/InterruptEvent."""
    import strands.hooks as hooks

    for missing in ("BeforeToolsEvent", "AfterToolsEvent", "InterruptEvent"):
        assert not hasattr(hooks, missing), missing
    for present in ("BeforeToolCallEvent", "AfterToolCallEvent", "BeforeModelCallEvent"):
        assert hasattr(hooks, present), present


# --------------------------------------------------------------------------
# Fixture activities shaped like the planned desktop activities.
# --------------------------------------------------------------------------


class ObservationRef(BaseModel):
    """Immutable descriptor; bytes never ride in Temporal payloads."""

    artifact_id: str
    generation: str
    sha256: str
    mime_type: str
    width: int
    height: int
    desktop_epoch: int
    operation_id: str


class ClickAction(BaseModel):
    type: Literal["click"]
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    button: Literal["left", "right", "middle"] = "left"


class TypeAction(BaseModel):
    type: Literal["type"]
    text: str = Field(max_length=4000)


@activity.defn
async def desktop_observe(workspace_id: str, desktop_epoch: int) -> ObservationRef:
    """Capture the scoped desktop and return an immutable observation descriptor.

    Args:
        workspace_id: Trusted workspace scope.
        desktop_epoch: Epoch the capture must belong to.
    """
    return ObservationRef(
        artifact_id="art-1",
        generation="17",
        sha256="0" * 64,
        mime_type="image/png",
        width=1920,
        height=1080,
        desktop_epoch=desktop_epoch,
        operation_id="obs-1",
    )


@activity.defn
async def desktop_action(
    action: ClickAction | TypeAction, frame_id: str, desktop_epoch: int
) -> dict[str, Any]:
    """Perform exactly one desktop mutation against a validated frame.

    Args:
        action: Discriminated action payload.
        frame_id: Observation the action targets.
        desktop_epoch: Epoch the frame belongs to.
    """
    return {"operation_id": "op-1", "state": "succeeded", "action": action.type}


@activity.defn
async def desktop_noop() -> dict[str, str]:
    """Zero-argument activity used to exercise the no-positional dispatch branch."""
    return {"state": "succeeded"}


MUTATION_OPTIONS: dict[str, Any] = dict(
    start_to_close_timeout=DESKTOP_MUTATION_TIMEOUT,
    schedule_to_close_timeout=DESKTOP_TASK_TIMEOUT,
    retry_policy=DESKTOP_MUTATION_RETRY_POLICY,
    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
)
OBSERVATION_OPTIONS: dict[str, Any] = dict(
    start_to_close_timeout=DESKTOP_OBSERVATION_TIMEOUT,
    schedule_to_close_timeout=DESKTOP_TASK_TIMEOUT,
    retry_policy=DESKTOP_OBSERVATION_RETRY_POLICY,
)


def observe_tool():
    return activity_as_tool(desktop_observe, **OBSERVATION_OPTIONS)


def action_tool():
    return activity_as_tool(desktop_action, **MUTATION_OPTIONS)


def outbound(spec: dict[str, Any]) -> dict[str, Any]:
    """Exact conversion run_worker.validate_outbound_tools performs."""
    return {
        "type": "function",
        "name": spec["name"],
        "description": spec.get("description", ""),
        "parameters": _ensure_object_properties(spec["inputSchema"]["json"]),
    }


# --------------------------------------------------------------------------
# Native activity_as_tool schema
# --------------------------------------------------------------------------


def test_activity_as_tool_is_native_temporal_activity_tool() -> None:
    tool = action_tool()
    assert isinstance(tool, tat.TemporalActivityTool)
    assert tool.tool_type == "temporal_activity"
    assert tool.tool_name == "desktop_action"
    assert tool._options["start_to_close_timeout"] == DESKTOP_MUTATION_TIMEOUT
    assert tool._options["retry_policy"] is DESKTOP_MUTATION_RETRY_POLICY
    assert (
        tool._options["cancellation_type"]
        is ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
    )


def test_tool_spec_is_derived_from_signature_and_docstring() -> None:
    spec = observe_tool().tool_spec
    schema = spec["inputSchema"]["json"]
    assert spec["name"] == "desktop_observe"
    assert spec["description"].startswith("Capture the scoped desktop")
    assert "Args:" not in spec["description"]
    assert schema["type"] == "object"
    assert list(schema["properties"]) == ["workspace_id", "desktop_epoch"]
    assert schema["required"] == ["workspace_id", "desktop_epoch"]
    assert schema["properties"]["workspace_id"] == {
        "description": "Trusted workspace scope.",
        "type": "string",
    }
    assert schema["properties"]["desktop_epoch"]["type"] == "integer"


def test_pydantic_union_action_survives_schema_and_provider_validation() -> None:
    spec = action_tool().tool_spec
    schema = spec["inputSchema"]["json"]
    action = schema["properties"]["action"]
    assert [ref["$ref"] for ref in action["anyOf"]] == [
        "#/$defs/ClickAction",
        "#/$defs/TypeAction",
    ]
    click = schema["$defs"]["ClickAction"]
    assert click["properties"]["type"] == {"const": "click", "title": "Type", "type": "string"}
    assert click["properties"]["x"]["minimum"] == 0
    assert click["properties"]["button"]["enum"] == ["left", "right", "middle"]
    assert schema["$defs"]["TypeAction"]["properties"]["text"]["maxLength"] == 4000
    # The same validator run_worker.validate_outbound_tools uses before readiness.
    _validate_tools([outbound(spec)])
    json.dumps(outbound(spec))


def test_all_fixture_specs_pass_existing_outbound_validator_together() -> None:
    specs = [t.tool_spec for t in (observe_tool(), action_tool(), activity_as_tool(desktop_noop))]
    converted = [outbound(s) for s in specs]
    json.dumps(converted)
    _validate_tools(converted)
    for tool in converted:
        assert tool["parameters"]["type"] == "object"
        assert "properties" in tool["parameters"]


def _define_without_pep563(source: str, name: str) -> Any:
    """Compile an activity in a namespace WITHOUT ``from __future__ import
    annotations`` so its annotations are real objects, as in most modules."""
    from typing import Annotated

    namespace: dict[str, Any] = {
        "activity": activity, "Annotated": Annotated, "Field": Field,
        "ToolContext": ToolContext, "Any": Any,
    }
    # dont_inherit: exec would otherwise inherit this module's PEP 563 flag.
    exec(compile(source, f"<{name}>", "exec", dont_inherit=True), namespace)
    return namespace[name]


def test_annotated_pydantic_field_is_rejected_by_installed_strands() -> None:
    """Constraint: bound coordinates via Pydantic models, not Annotated[..., Field]."""
    bad = _define_without_pep563(
        "@activity.defn\n"
        "async def bad(x: Annotated[int, Field(ge=0)]) -> dict:\n"
        '    """b"""\n'
        "    return {}\n",
        "bad",
    )
    with pytest.raises(NotImplementedError, match="pydantic.Field within Annotated"):
        activity_as_tool(bad)


def test_undecorated_function_is_rejected() -> None:
    async def plain(x: int) -> dict:  # pragma: no cover
        return {}

    with pytest.raises(ValueError, match="@activity.defn"):
        activity_as_tool(plain)


# --------------------------------------------------------------------------
# Positional binding and trusted-context limitations
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_arg_input_binds_positionally_in_signature_order() -> None:
    calls: list[tuple[Any, ...]] = []

    async def fake(name: str, *args: Any, **kwargs: Any) -> Any:
        calls.append((name, args, kwargs))
        return {"ok": True}

    tool = action_tool()
    # Model-supplied key order deliberately scrambled.
    tool_use = {
        "toolUseId": "t1",
        "name": "desktop_action",
        "input": {"desktop_epoch": 3, "action": {"type": "click", "x": 1, "y": 2}, "frame_id": "f"},
    }
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        events = [e async for e in tool.stream(tool_use, {})]
    name, args, kwargs = calls[0]
    assert name == "desktop_action"
    assert args == ()
    # Signature order, not input order; raw dict (activity-side converter parses it).
    assert kwargs["args"] == [{"type": "click", "x": 1, "y": 2}, "f", 3]
    assert kwargs["start_to_close_timeout"] == DESKTOP_MUTATION_TIMEOUT
    assert kwargs["cancellation_type"] is ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
    assert isinstance(events[-1], ToolResultEvent)


@pytest.mark.asyncio
async def test_single_and_zero_arg_dispatch_branches() -> None:
    calls: list[tuple[Any, ...]] = []

    async def fake(name: str, *args: Any, **kwargs: Any) -> Any:
        calls.append((name, args, "args" in kwargs))
        return {}

    @activity.defn
    async def one(operation_id: str) -> dict:
        """o
        Args:
            operation_id: id
        """
        return {}

    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        async for _ in activity_as_tool(one).stream(
            {"toolUseId": "a", "name": "one", "input": {"operation_id": "op"}}, {}
        ):
            pass
        async for _ in activity_as_tool(desktop_noop).stream(
            {"toolUseId": "b", "name": "desktop_noop", "input": {}}, {}
        ):
            pass
    assert calls == [("one", ("op",), False), ("desktop_noop", (), False)]


@pytest.mark.asyncio
async def test_missing_required_argument_fails_at_bind_not_dispatch() -> None:
    dispatched = False

    async def fake(*a: Any, **k: Any) -> Any:  # pragma: no cover
        nonlocal dispatched
        dispatched = True
        return {}

    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        with pytest.raises(TypeError, match="missing a required argument"):
            async for _ in action_tool().stream(
                {"toolUseId": "t", "name": "desktop_action", "input": {"frame_id": "f"}}, {}
            ):
                pass
    assert dispatched is False


def test_tool_context_and_agent_params_cannot_carry_trusted_context() -> None:
    """Trusted owner/workspace binding must come from the activity side, not
    hidden parameters: ToolContext is rejected outright (when annotations are
    real objects) and an ``agent`` parameter is excluded from the schema but
    never bound at dispatch.

    Gap: under ``from __future__ import annotations`` the installed Strands
    ``_validate_signature`` compares the *string* annotation and silently
    exposes ``ToolContext`` as a model-fillable object schema instead. Desktop
    activity modules must not rely on ToolContext at all."""
    with_ctx = _define_without_pep563(
        "@activity.defn\n"
        "async def with_ctx(tool_context: ToolContext, x: int) -> dict:\n"
        '    """c"""\n'
        "    return {}\n",
        "with_ctx",
    )
    with pytest.raises(ValueError, match="ToolContext"):
        activity_as_tool(with_ctx)

    @activity.defn
    async def with_ctx_pep563(tool_context: ToolContext, x: int) -> dict:  # pragma: no cover
        """c"""
        return {}

    leaked = activity_as_tool(with_ctx_pep563).tool_spec["inputSchema"]["json"]
    assert "tool_context" in leaked["properties"]  # documented hazard, not a feature

    @activity.defn
    async def with_agent(agent: Any, x: int) -> dict:  # pragma: no cover
        """a
        Args:
            x: X
        """
        return {}

    tool = activity_as_tool(with_agent)
    assert list(tool.tool_spec["inputSchema"]["json"]["properties"]) == ["x"]
    with pytest.raises(TypeError, match="'agent'"):
        tool._signature.bind(**{"x": 1})


def test_positional_only_params_are_unbindable_from_model_input() -> None:
    @activity.defn
    async def po(x: int, /, y: int) -> dict:  # pragma: no cover
        """p"""
        return {}

    tool = activity_as_tool(po)
    assert list(tool.tool_spec["inputSchema"]["json"]["properties"]) == ["x", "y"]
    with pytest.raises(TypeError, match="positional-only"):
        tool._signature.bind(**{"x": 1, "y": 2})


# --------------------------------------------------------------------------
# Result serialization: JSON text inside a single text content block
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dict_result_becomes_json_text_content_block() -> None:
    async def fake(*a: Any, **k: Any) -> Any:
        return {"operation_id": "op-1", "state": "succeeded", "generation": "17"}

    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        events = [e async for e in action_tool().stream(
            {"toolUseId": "t1", "name": "desktop_action",
             "input": {"action": {"type": "type", "text": "hi"}, "frame_id": "f", "desktop_epoch": 1}},
            {},
        )]
    result = events[-1].tool_result
    assert result["toolUseId"] == "t1"
    assert result["status"] == "success"
    assert len(result["content"]) == 1
    assert set(result["content"][0]) == {"text"}
    assert json.loads(result["content"][0]["text"]) == {
        "operation_id": "op-1", "state": "succeeded", "generation": "17",
    }


def test_to_text_pydantic_model_is_not_json() -> None:
    """A BaseModel returned through the workflow-side converter would already be
    a dict; a raw model instance falls back to str(). Activities must return
    JSON-native mappings (or the converter must decode) for the UI boundary."""
    ref = ObservationRef(
        artifact_id="a", generation="1", sha256="0" * 64, mime_type="image/png",
        width=1, height=1, desktop_epoch=0, operation_id="o",
    )
    text = tat._to_text(ref)
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    assert tat._to_text(ref.model_dump()) == json.dumps(ref.model_dump())


def test_to_text_bytes_fall_back_to_repr_and_pydantic_converter_rejects_them() -> None:
    """Raw image bytes are unsuitable at this boundary (spec finding)."""
    assert tat._to_text({"png": b"\x89PNG"}) == "{'png': b'\\x89PNG'}"
    with pytest.raises(Exception, match="utf-8"):
        pydantic_data_converter.payload_converter.to_payloads([{"png": b"\x89PNG"}])


def test_to_text_string_passthrough_and_large_generation_precision() -> None:
    assert tat._to_text("plain") == "plain"
    # GCS generations must cross as strings: an int survives json.dumps but
    # not JavaScript number precision.
    big = 12345678901234567890
    assert json.loads(tat._to_text({"generation": big}))["generation"] == big
    assert big > 2**53


def test_strands_plugin_converter_is_pydantic_with_strands_failures() -> None:
    from temporalio.contrib.pydantic import PydanticPayloadConverter
    from temporalio.contrib.strands._failure_converter import StrandsFailureConverter

    converter = _data_converter(None)
    assert converter.payload_converter_class is PydanticPayloadConverter
    assert converter.failure_converter_class is StrandsFailureConverter
    ref = ObservationRef(
        artifact_id="a", generation="1", sha256="0" * 64, mime_type="image/png",
        width=1, height=1, desktop_epoch=0, operation_id="o",
    )
    payload = converter.payload_converter.to_payloads([ref])[0]
    assert payload.metadata[b"encoding"] == b"json/plain"
    assert converter.payload_converter.from_payloads([payload]) == [ref.model_dump()]
    assert converter.payload_converter.from_payloads([payload], [ObservationRef]) == [ref]


@pytest.mark.asyncio
async def test_activity_environment_runs_fixture_activities_offline() -> None:
    env = ActivityEnvironment()
    ref = await env.run(desktop_observe, "ws-1", 4)
    assert isinstance(ref, ObservationRef)
    assert ref.desktop_epoch == 4
    result = await env.run(desktop_action, ClickAction(type="click", x=1, y=2), "f", 4)
    assert result == {"operation_id": "op-1", "state": "succeeded", "action": "click"}


# --------------------------------------------------------------------------
# Interrupt propagation through the activity boundary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_side_interrupt_surfaces_as_tool_interrupt_event() -> None:
    def make_error() -> ActivityError:
        cause = ApplicationError(
            "interrupt:approve",
            {"id": "v1:x", "name": "approve", "reason": {"operation_id": "op-1"}, "response": None},
            type=STRANDS_INTERRUPT_TYPE,
            non_retryable=True,
        )
        err = ActivityError(
            "failed", scheduled_event_id=1, started_event_id=2, identity="w",
            activity_type="desktop_action", activity_id="a1", retry_state=None,
        )
        err.__cause__ = cause
        return err

    async def raise_it(*a: Any, **k: Any) -> Any:
        raise make_error()

    with patch.object(tat.workflow, "execute_activity", side_effect=raise_it):
        events = [e async for e in action_tool().stream(
            {"toolUseId": "t", "name": "desktop_action",
             "input": {"action": {"type": "click", "x": 0, "y": 0}, "frame_id": "f", "desktop_epoch": 1}},
            {},
        )]
    assert len(events) == 1
    assert isinstance(events[0], ToolInterruptEvent)
    (interrupt,) = events[0].interrupts
    assert (interrupt.id, interrupt.name, interrupt.reason) == (
        "v1:x", "approve", {"operation_id": "op-1"},
    )


@pytest.mark.asyncio
async def test_non_interrupt_activity_error_propagates_unchanged() -> None:
    async def raise_it(*a: Any, **k: Any) -> Any:
        err = ActivityError(
            "failed", scheduled_event_id=1, started_event_id=2, identity="w",
            activity_type="desktop_action", activity_id="a1", retry_state=None,
        )
        err.__cause__ = ApplicationError("desktop unreachable", type="DesktopUnavailable")
        raise err

    with patch.object(tat.workflow, "execute_activity", side_effect=raise_it):
        with pytest.raises(ActivityError):
            async for _ in action_tool().stream(
                {"toolUseId": "t", "name": "desktop_action",
                 "input": {"action": {"type": "click", "x": 0, "y": 0}, "frame_id": "f", "desktop_epoch": 1}},
                {},
            ):
                pass


# --------------------------------------------------------------------------
# Agent-loop contracts: invocation state, hooks, executor, cancel, limits
# --------------------------------------------------------------------------


def _tool_use_events(uses: list[tuple[str, str, dict[str, Any]]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"messageStart": {"role": "assistant"}}]
    for index, (tool_use_id, name, payload) in enumerate(uses):
        events += [
            {"contentBlockStart": {"contentBlockIndex": index,
                                   "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}}}},
            {"contentBlockDelta": {"contentBlockIndex": index,
                                   "delta": {"toolUse": {"input": json.dumps(payload)}}}},
            {"contentBlockStop": {"contentBlockIndex": index}},
        ]
    events += [
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                      "metrics": {"latencyMs": 1}}},
    ]
    return events


def _text_events(text: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                      "metrics": {"latencyMs": 1}}},
    ]


class ScriptedModel(Model):
    """Offline stand-in: one scripted event list per stream() call."""

    def __init__(self, scripts: list[list[dict[str, Any]]]) -> None:
        self.scripts = list(scripts)
        self.calls: list[list[Any]] = []

    def update_config(self, **model_config: Any) -> None:  # pragma: no cover
        pass

    def get_config(self) -> Any:  # pragma: no cover
        return {}

    async def structured_output(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:  # pragma: no cover
        raise NotImplementedError
        yield

    async def stream(self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
                     ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append(list(messages))
        for event in self.scripts.pop(0):
            yield event


CLICK = {"action": {"type": "click", "x": 1, "y": 2}, "frame_id": "f", "desktop_epoch": 1}
OBSERVE = {"workspace_id": "ws", "desktop_epoch": 1}


def _agent(model: Model, executor: Any, hooks: list[Any] | None = None) -> Agent:
    return Agent(
        model=model,
        tools=[action_tool(), observe_tool()],
        tool_executor=executor,
        hooks=hooks,
        callback_handler=None,
    )


def _dispatch_log() -> tuple[list[tuple[str, str]], Any]:
    log: list[tuple[str, str]] = []

    async def fake(name: str, *args: Any, **kwargs: Any) -> Any:
        log.append(("start", name))
        await asyncio.sleep(0.01)
        log.append(("end", name))
        return {"op": name}

    return log, fake


def test_sequential_executor_is_opt_in_and_accepted_by_temporal_agent() -> None:
    assert isinstance(Agent(model=ScriptedModel([]), callback_handler=None).tool_executor,
                      ConcurrentToolExecutor)
    agent = TemporalAgent(
        model="preset:high",
        start_to_close_timeout=timedelta(seconds=1),
        tools=[action_tool()],
        tool_executor=SequentialToolExecutor(),
        callback_handler=None,
    )
    assert isinstance(agent.tool_executor, SequentialToolExecutor)
    with pytest.raises(ValueError, match="retry_strategy"):
        TemporalAgent(model="preset:high", retry_strategy=object(), callback_handler=None)


@pytest.mark.asyncio
async def test_sequential_executor_serializes_desktop_tools_concurrent_does_not() -> None:
    async def run(executor: Any) -> list[tuple[str, str]]:
        log, fake = _dispatch_log()
        model = ScriptedModel([
            _tool_use_events([("t1", "desktop_action", CLICK), ("t2", "desktop_observe", OBSERVE)]),
            _text_events("done"),
        ])
        agent = _agent(model, executor)
        with patch.object(tat.workflow, "execute_activity", side_effect=fake):
            result = await agent.invoke_async("go")
        assert result.stop_reason == "end_turn"
        return log

    assert await run(SequentialToolExecutor()) == [
        ("start", "desktop_action"), ("end", "desktop_action"),
        ("start", "desktop_observe"), ("end", "desktop_observe"),
    ]
    assert await run(ConcurrentToolExecutor()) == [
        ("start", "desktop_action"), ("start", "desktop_observe"),
        ("end", "desktop_action"), ("end", "desktop_observe"),
    ]


@pytest.mark.asyncio
async def test_tool_results_land_as_json_text_in_the_next_user_message() -> None:
    log, fake = _dispatch_log()
    model = ScriptedModel([
        _tool_use_events([("t1", "desktop_action", CLICK)]),
        _text_events("done"),
    ])
    agent = _agent(model, SequentialToolExecutor())
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        await agent.invoke_async("go")
    tool_message = model.calls[1][-1]
    assert tool_message["role"] == "user"
    block = tool_message["content"][0]["toolResult"]
    assert block["toolUseId"] == "t1"
    assert json.loads(block["content"][0]["text"]) == {"op": "desktop_action"}


@pytest.mark.asyncio
async def test_invocation_state_reaches_hooks_and_is_augmented_with_live_objects() -> None:
    """Callers pass a JSON-serializable dict; hooks see it, but by the time the
    tool runs the executor has injected agent/model/messages, so the dict as a
    whole is NOT serializable. Trusted context must be read by key, not dumped."""
    seen: dict[str, Any] = {}

    def before(event: BeforeToolCallEvent) -> None:
        seen["before"] = dict(event.invocation_state)

    def after(event: AfterToolCallEvent) -> None:
        seen["after"] = dict(event.invocation_state)

    def before_model(event: BeforeModelCallEvent) -> None:
        seen.setdefault("model", []).append(dict(event.invocation_state))

    log, fake = _dispatch_log()
    model = ScriptedModel([_tool_use_events([("t1", "desktop_action", CLICK)]), _text_events("ok")])
    agent = _agent(model, SequentialToolExecutor())
    agent.hooks.add_callback(BeforeToolCallEvent, before)
    agent.hooks.add_callback(AfterToolCallEvent, after)
    agent.hooks.add_callback(BeforeModelCallEvent, before_model)
    state = {"workspace_id": "ws-1", "desktop_epoch": 3, "lease_epoch": 9}
    json.dumps(state)
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        await agent.invoke_async("go", invocation_state=state)

    for key in ("workspace_id", "desktop_epoch", "lease_epoch"):
        assert seen["before"][key] == state[key]
        assert seen["after"][key] == state[key]
    assert seen["model"][0]["workspace_id"] == "ws-1"
    assert seen["before"]["agent"] is agent
    assert {"agent", "model", "messages", "system_prompt", "tool_config"} <= set(seen["before"])
    with pytest.raises(TypeError):
        json.dumps(seen["before"])


@pytest.mark.asyncio
async def test_temporal_model_stream_drops_non_serializable_state_before_scheduling() -> None:
    """Installed ``TemporalModel.stream`` runs ``_filter_serializable`` on
    invocation_state and ships only ``json.dumps``-able keys inside the model
    activity input, so live objects/bytes never reach history. Non-finite
    floats pass ``json.dumps`` (``NaN``) and are therefore NOT filtered."""
    captured: dict[str, Any] = {}

    async def fake(method: Any, arg: Any, **kwargs: Any) -> Any:
        captured["method"], captured["arg"], captured["kwargs"] = method, arg, kwargs
        return [{"messageStart": {"role": "assistant"}}]

    model = tm.TemporalModel(
        "preset:high",
        start_to_close_timeout=DESKTOP_TASK_TIMEOUT,
        streaming_topic="events",
        streaming_batch_interval=timedelta(milliseconds=200),
    )

    class Live:
        pass

    state = {
        "workspace_id": "ws-1",
        "desktop_epoch": 3,
        "observation": {"artifact_id": "a", "generation": "17"},
        "agent": Live(),
        "png": b"\x89PNG",
        "nan": float("nan"),
    }
    with (
        patch.object(tm.workflow, "execute_activity_method", side_effect=fake),
        patch.object(tm.workflow, "logger") as logger,
    ):
        events = [e async for e in model.stream(
            [{"role": "user", "content": [{"text": "hi"}]}], invocation_state=state,
        )]

    assert events == [{"messageStart": {"role": "assistant"}}]
    assert captured["method"] is ModelActivity.invoke_model_streaming
    payload = captured["arg"]
    assert isinstance(payload, _StreamingInvokeModelInput)
    assert payload.model_name == "preset:high"
    assert payload.streaming_topic == "events"
    assert payload.streaming_batch_interval_seconds == 0.2
    assert set(payload.invocation_state) == {"workspace_id", "desktop_epoch", "observation", "nan"}
    assert payload.invocation_state["observation"] == {"artifact_id": "a", "generation": "17"}
    # Caller's dict is untouched; only the shipped copy is filtered.
    assert set(state) == {"workspace_id", "desktop_epoch", "observation", "agent", "png", "nan"}
    logger.debug.assert_called_once_with(
        "Dropping non-serializable invocation_state keys: ['agent', 'png']"
    )
    assert captured["kwargs"]["start_to_close_timeout"] == DESKTOP_TASK_TIMEOUT
    assert captured["kwargs"]["cancellation_type"] is ActivityCancellationType.TRY_CANCEL
    assert tm._filter_serializable({}) == {}


@pytest.mark.asyncio
async def test_temporal_agent_ships_only_serializable_state_to_model_activity() -> None:
    """End to end through ``TemporalAgent``: the second model call happens
    after the executor injected ``agent``/``model`` into invocation_state, and
    those are filtered while ``messages``/``tool_config`` (plain dicts) and the
    caller's desktop keys ride along into the model activity input."""
    scripts = [_tool_use_events([("t1", "desktop_noop", {})]), _text_events("ok")]
    inputs: list[Any] = []

    async def fake_model(method: Any, arg: Any, **kwargs: Any) -> Any:
        inputs.append(arg)
        return scripts.pop(0)

    async def fake_tool(name: str, *args: Any, **kwargs: Any) -> Any:
        return {}

    agent = TemporalAgent(
        model="preset:high",
        start_to_close_timeout=DESKTOP_TASK_TIMEOUT,
        streaming_topic="events",
        tools=[activity_as_tool(desktop_noop, start_to_close_timeout=DESKTOP_MUTATION_TIMEOUT)],
        tool_executor=SequentialToolExecutor(),
        callback_handler=None,
    )
    with (
        patch.object(tm.workflow, "execute_activity_method", side_effect=fake_model),
        patch.object(tat.workflow, "execute_activity", side_effect=fake_tool),
        patch.object(tm.workflow, "logger"),
    ):
        result = await agent.invoke_async(
            "go", invocation_state={"workspace_id": "ws-1", "desktop_epoch": 3}
        )

    assert result.stop_reason == "end_turn"
    assert len(inputs) == 2
    first, second = inputs
    assert first.invocation_state == {
        "workspace_id": "ws-1", "desktop_epoch": 3, "request_state": {},
    }
    assert second.invocation_state["workspace_id"] == "ws-1"
    assert second.invocation_state["desktop_epoch"] == 3
    assert "agent" not in second.invocation_state
    assert "model" not in second.invocation_state
    assert {"messages", "tool_config", "system_prompt"} <= set(second.invocation_state)
    for payload in inputs:
        json.dumps(payload.invocation_state)
        assert payload.model_name == "preset:high"
        assert payload.streaming_topic == "events"


@pytest.mark.asyncio
async def test_before_tool_call_cancel_tool_yields_error_result_without_dispatch() -> None:
    def gate(event: BeforeToolCallEvent) -> None:
        if event.tool_use["input"].get("desktop_epoch") != 2:
            event.cancel_tool = "stale desktop epoch; observe again"

    log, fake = _dispatch_log()
    model = ScriptedModel([_tool_use_events([("t1", "desktop_action", CLICK)]), _text_events("ok")])
    agent = _agent(model, SequentialToolExecutor())
    agent.hooks.add_callback(BeforeToolCallEvent, gate)
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        result = await agent.invoke_async("go")
    assert result.stop_reason == "end_turn"
    assert log == []
    block = model.calls[1][-1]["content"][0]["toolResult"]
    assert block["status"] == "error"
    assert block["content"] == [{"text": "stale desktop epoch; observe again"}]


@pytest.mark.asyncio
async def test_before_tool_call_interrupt_pauses_and_resume_dispatches_once() -> None:
    def gate(event: BeforeToolCallEvent) -> None:
        event.interrupt("desktop_mutation_approval", reason={"operation_id": "op-1"})

    log, fake = _dispatch_log()
    model = ScriptedModel([_tool_use_events([("t1", "desktop_action", CLICK)]), _text_events("ok")])
    agent = _agent(model, SequentialToolExecutor())
    agent.hooks.add_callback(BeforeToolCallEvent, gate)
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        paused = await agent.invoke_async("go")
        assert paused.stop_reason == "interrupt"
        (interrupt,) = paused.interrupts
        assert interrupt.name == "desktop_mutation_approval"
        assert interrupt.id.startswith("v1:before_tool_call:t1:")
        assert interrupt.reason == {"operation_id": "op-1"}
        assert log == []
        json.dumps(interrupt.to_dict())

        resumed = await agent.invoke_async([
            {"interruptResponse": {"interruptId": interrupt.id, "response": {"approved": True}}}
        ])
    assert resumed.stop_reason == "end_turn"
    assert [n for _, n in log if _ == "start"] == ["desktop_action"]


@pytest.mark.asyncio
async def test_agent_cancel_stops_remaining_sequential_tools_with_error_results() -> None:
    """``Agent.cancel()`` from a BeforeToolCallEvent hook does NOT stop the
    *current* tool: the first mutation still dispatches and completes. Only
    subsequent tools in the same turn are short-circuited by the executor, and
    no further model call is made. Fencing the current mutation therefore
    needs ``cancel_tool`` (or an interrupt), not ``agent.cancel()``."""

    def before(event: BeforeToolCallEvent) -> None:
        event.agent.cancel()

    log, fake = _dispatch_log()
    model = ScriptedModel([
        _tool_use_events([("t1", "desktop_action", CLICK), ("t2", "desktop_action", CLICK)]),
        _text_events("never"),
    ])
    agent = _agent(model, SequentialToolExecutor())
    agent.hooks.add_callback(BeforeToolCallEvent, before)
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        result = await agent.invoke_async("go")
    assert result.stop_reason == "cancelled"
    assert log == [("start", "desktop_action"), ("end", "desktop_action")]
    blocks = [c["toolResult"] for c in agent.messages[-1]["content"]]
    assert [b["status"] for b in blocks] == ["success", "error"]
    assert blocks[1]["content"] == [{"text": "Tool execution cancelled"}]
    assert len(model.calls) == 1  # no model call after cancellation


@pytest.mark.asyncio
async def test_after_tool_call_retry_reissues_dispatch_and_is_blocked_after_cancel() -> None:
    """Hook-driven retry re-dispatches the activity: a desktop policy hook must
    never set ``retry`` on a mutation. Cancellation makes retry inert."""
    attempts = {"n": 0}

    def after(event: AfterToolCallEvent) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            event.retry = True

    log, fake = _dispatch_log()
    model = ScriptedModel([_tool_use_events([("t1", "desktop_action", CLICK)]), _text_events("ok")])
    agent = _agent(model, SequentialToolExecutor())
    agent.hooks.add_callback(AfterToolCallEvent, after)
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        await agent.invoke_async("go")
    assert [n for tag, n in log if tag == "start"] == ["desktop_action", "desktop_action"]

    def retry_forever(event: AfterToolCallEvent) -> None:
        event.retry = True

    def cancel_first(event: BeforeToolCallEvent) -> None:
        event.agent.cancel()

    log, fake = _dispatch_log()
    model = ScriptedModel([_tool_use_events([("t1", "desktop_action", CLICK)])])
    agent = _agent(model, SequentialToolExecutor())
    agent.hooks.add_callback(BeforeToolCallEvent, cancel_first)
    agent.hooks.add_callback(AfterToolCallEvent, retry_forever)
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        result = await agent.invoke_async("go")
    assert result.stop_reason == "cancelled"
    assert [n for tag, n in log if tag == "start"] == ["desktop_action"]


@pytest.mark.asyncio
async def test_turn_limit_stops_loop_but_is_not_a_mutation_counter() -> None:
    log, fake = _dispatch_log()
    model = ScriptedModel([
        _tool_use_events([("t1", "desktop_action", CLICK), ("t2", "desktop_action", CLICK)]),
        _tool_use_events([("t3", "desktop_action", CLICK)]),
        _text_events("never"),
    ])
    agent = _agent(model, SequentialToolExecutor())
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        result = await agent.invoke_async("go", limits={"turns": 1})
    assert result.stop_reason == "limit_turns"
    # One turn still executed two mutations: DESKTOP_MAX_MUTATIONS is domain policy.
    assert [n for tag, n in log if tag == "start"] == ["desktop_action", "desktop_action"]


def test_hook_events_reject_writes_outside_native_contract() -> None:
    agent = Agent(model=ScriptedModel([]), callback_handler=None)
    event = BeforeToolCallEvent(
        agent=agent, selected_tool=None,
        tool_use={"toolUseId": "t", "name": "x", "input": {}}, invocation_state={},
    )
    event.cancel_tool = True
    with pytest.raises(AttributeError, match="not writable"):
        event.invocation_state = {}
    model_event = BeforeModelCallEvent(agent=agent)
    model_event.cancel = "fenced"
    with pytest.raises(AttributeError, match="not writable"):
        model_event.projected_input_tokens = 1


# --------------------------------------------------------------------------
# activity_as_hook: side effects only, no return value fed back
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_as_hook_dispatches_serializable_input_and_discards_result() -> None:
    @activity.defn
    async def journal(entry: dict[str, Any]) -> dict[str, str]:  # pragma: no cover - patched
        """j"""
        return {"journal_id": "j-1"}

    dispatched: list[Any] = []

    async def fake(fn: Any, *args: Any, **kwargs: Any) -> Any:
        dispatched.append((fn, args, kwargs["retry_policy"], kwargs["start_to_close_timeout"]))
        return {"journal_id": "j-1"}

    callback = activity_as_hook(
        journal,
        activity_input=lambda event: {
            "tool": event.tool_use["name"],
            "tool_use_id": event.tool_use["toolUseId"],
            "status": event.result["status"],
        },
        start_to_close_timeout=DESKTOP_OBSERVATION_TIMEOUT,
        retry_policy=DESKTOP_OBSERVATION_RETRY_POLICY,
    )
    agent = Agent(model=ScriptedModel([]), callback_handler=None)
    event = AfterToolCallEvent(
        agent=agent, selected_tool=None,
        tool_use={"toolUseId": "t1", "name": "desktop_action", "input": {}},
        invocation_state={}, result={"toolUseId": "t1", "status": "success", "content": []},
    )
    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        returned = await callback(event)
    assert returned is None
    fn, args, policy, timeout = dispatched[0]
    assert fn is journal
    assert args == ({"tool": "desktop_action", "tool_use_id": "t1", "status": "success"},)
    json.dumps(args[0])
    assert policy is DESKTOP_OBSERVATION_RETRY_POLICY
    assert timeout == DESKTOP_OBSERVATION_TIMEOUT
    assert event.result["status"] == "success"  # hook cannot rewrite via return value


# --------------------------------------------------------------------------
# Finite desktop activity options (config.py constants)
# --------------------------------------------------------------------------


def test_desktop_config_constants_are_finite_and_distinct_from_model_policy() -> None:
    from config import MODEL_RETRY_POLICY, MODEL_START_TO_CLOSE

    assert DESKTOP_MUTATION_RETRY_POLICY.maximum_attempts == 1
    assert DESKTOP_OBSERVATION_RETRY_POLICY.maximum_attempts == 3
    assert MODEL_RETRY_POLICY.maximum_attempts == 0  # unlimited, model-only
    assert MODEL_START_TO_CLOSE is None  # existing "uncapped" model envelope untouched
    assert timedelta(0) < DESKTOP_MUTATION_TIMEOUT <= timedelta(minutes=1)
    assert timedelta(0) < DESKTOP_OBSERVATION_TIMEOUT <= timedelta(minutes=1)
    assert DESKTOP_JOB_HEARTBEAT_TIMEOUT == timedelta(seconds=30)
    assert DESKTOP_MUTATION_TIMEOUT < DESKTOP_TASK_TIMEOUT
    assert DESKTOP_OBSERVATION_TIMEOUT < DESKTOP_TASK_TIMEOUT
    assert isinstance(DESKTOP_MUTATION_RETRY_POLICY, RetryPolicy)


def test_desktop_options_satisfy_temporal_schedule_validation_without_fallback() -> None:
    """Temporal rejects an activity with neither start_to_close nor
    schedule_to_close (``_outbound_schedule_activity``). Desktop options carry
    real values, so ``closable_activity_options`` never applies its 1-day shim."""
    from config import UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE, closable_activity_options

    for options in (MUTATION_OPTIONS, OBSERVATION_OPTIONS):
        assert options["start_to_close_timeout"] or options["schedule_to_close_timeout"]
        assert closable_activity_options(dict(options)) == options
        assert options["schedule_to_close_timeout"] != UNCAPPED_FALLBACK_SCHEDULE_TO_CLOSE


@pytest.mark.asyncio
async def test_activity_as_tool_forwards_desktop_options_verbatim_to_execute_activity() -> None:
    captured: dict[str, Any] = {}

    async def fake(name: str, *args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return {}

    with patch.object(tat.workflow, "execute_activity", side_effect=fake):
        async for _ in observe_tool().stream(
            {"toolUseId": "t", "name": "desktop_observe", "input": OBSERVE}, {}
        ):
            pass
    assert captured["start_to_close_timeout"] == DESKTOP_OBSERVATION_TIMEOUT
    assert captured["schedule_to_close_timeout"] == DESKTOP_TASK_TIMEOUT
    assert captured["retry_policy"] is DESKTOP_OBSERVATION_RETRY_POLICY
    assert captured["cancellation_type"] is ActivityCancellationType.TRY_CANCEL
    assert captured["heartbeat_timeout"] is None


@pytest.mark.asyncio
async def test_cancelled_mutation_activity_can_acknowledge_cleanup_with_heartbeats() -> None:
    """WAIT_CANCELLATION_COMPLETED only helps if the activity observes the
    cancel and returns a cleanup outcome; heartbeats carry the stable
    operation id, never the attempt number."""

    @activity.defn
    async def long_mutation(operation_id: str) -> dict[str, Any]:
        """m"""
        activity.heartbeat({"operation_id": operation_id})
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            assert activity.is_cancelled()
            return {"operation_id": operation_id, "state": "cancelled", "inputs_released": True}
        return {"operation_id": operation_id, "state": "succeeded"}  # pragma: no cover

    env = ActivityEnvironment()
    beats: list[Any] = []
    env.on_heartbeat = lambda *details: beats.append(details)
    task = asyncio.create_task(env.run(long_mutation, "op-7"))
    await asyncio.sleep(0.05)
    env.cancel()
    result = await task
    assert result == {"operation_id": "op-7", "state": "cancelled", "inputs_released": True}
    assert beats == [({"operation_id": "op-7"},)]
    assert env.info.attempt == 1  # diagnostic only


def test_activity_cancellation_types_available() -> None:
    assert {t.name for t in ActivityCancellationType} == {
        "TRY_CANCEL", "WAIT_CANCELLATION_COMPLETED", "ABANDON",
    }


def test_strands_plugin_registers_model_factories_only() -> None:
    """Spec 3.2: factories live on the plugin/worker; the plugin owns the
    Pydantic converter and Strands failure converter. No live Model here."""
    plugin = StrandsPlugin(models={"fake": lambda: ScriptedModel([])})
    assert plugin.name() == "aws.StrandsPlugin"
