"""Tests for the thin think activity wrapper (Pattern 2, surface 3).

``think_activity.think`` owns no cycle logic: it builds a ToolUse from the
model's input and delegates to the installed ``strands_tools.think`` callable
on the activity executor (workflows block ``run_in_executor``; activities do
not). The cycle loop itself is upstream in strands-tools — these tests pin
only the wrapper contract: delegation, provider defaults, replay-compatible
input forms, the activity registration, and the custom model-provider module
the nested cycles resolve through.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from temporalio import activity
from temporalio.testing import ActivityEnvironment
from temporalio.contrib.workflow_streams import WorkflowStreamClient

import think_activity
from think_activity import think


@pytest.fixture(autouse=True)
def thinking_stream(monkeypatch):
    class Stream:
        def __init__(self):
            self.frames = []
            self.closed = False

        def topic(self, name):
            assert name == "thinking"
            return self

        def publish(self, frame):
            assert not self.closed
            self.frames.append(frame)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

    stream = Stream()
    monkeypatch.setattr(WorkflowStreamClient, "from_within_activity", lambda **kwargs: stream)
    return stream


@pytest.mark.asyncio
async def test_stock_think_streams_cycles_before_returning(monkeypatch, thinking_stream):
    """Exercise the installed cycle loop and real provider parser, not a fake Think."""
    import httpx
    from perplexity_model import PerplexityModel
    from test_perplexity_model import COMPLETED, CREATED, sse, text_delta

    module = load_provider_module(monkeypatch)
    requests = []
    release = asyncio.Event()

    async def handler(request):
        requests.append(request)
        if len(requests) == 2:
            await asyncio.wait_for(release.wait(), 5)
        return httpx.Response(200, content=sse(CREATED, text_delta(f"Analysis {len(requests)}."), COMPLETED),
                             headers={"content-type": "text/event-stream"})

    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setattr(module, "native_tools", lambda: [])
    monkeypatch.setattr(module, "model_params", lambda *args: {})
    monkeypatch.setattr(module, "PerplexityModel", lambda **kwargs: PerplexityModel(
        **kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr("strands_tools.think.create_model", lambda provider, config: module.instance(**config))
    pending = asyncio.create_task(ActivityEnvironment().run(
        think, thought="Compare queues and stacks", cycle_count=2, model_settings={"model_id": "test/model"}))
    try:
        async with asyncio.timeout(4):
            while not thinking_stream.frames:
                await asyncio.sleep(0.01)
        assert not pending.done()
    finally:
        release.set()
        result = await pending

    text = "".join(frame.get("contentBlockDelta", {}).get("delta", {}).get("text", "")
                   for frame in thinking_stream.frames)
    assert text == "Analysis 1.Analysis 2."
    assert "Cycle 1/2:" in result["content"][0]["text"]
    assert "Cycle 2/2:" in result["content"][0]["text"]
    assert thinking_stream.closed


def recorded_native(calls: list[dict[str, Any]], result: dict[str, Any] | None = None):
    """A stand-in for the installed strands_tools.think callable."""

    async def stub(**kwargs: Any) -> dict[str, Any]:
        kwargs["model_settings"] = {key: value for key, value in kwargs["model_settings"].items()
                                   if key != "stream_callback"}
        calls.append(kwargs)
        return result or {"status": "success", "content": [{"text": "think notes"}]}

    return stub


async def run_think(calls: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    env = ActivityEnvironment()
    heartbeats: list[Any] = []
    env.on_heartbeat = lambda *details: heartbeats.append(details)
    with patch.object(think_activity, "native_think", recorded_native(calls)):
        result = await env.run(think, **kwargs)
    # The activity heartbeats the requested cycle count before delegating.
    assert heartbeats == [({"cycle_count": calls[0]["cycle_count"]},)]
    return result


def test_activity_is_registered_under_the_think_name() -> None:
    """The worker registers this callable as the ``think`` activity, and its
    docstring/signature are what the model-facing schema is derived from."""
    assert activity._Definition.from_callable(think).name == "think"
    signature = inspect.signature(think)
    assert list(signature.parameters) == [
        "thought",
        "cycle_count",
        "system_prompt",
        "tools",
        "model_provider",
        "model_settings",
        "thinking_system_prompt",
    ]
    assert signature.parameters["cycle_count"].default == 3
    # The model-facing guidance: cycles chain recursively.
    assert "Each cycle builds on the previous cycle" in " ".join((think.__doc__ or "").split())


@pytest.mark.asyncio
async def test_activity_delegates_to_native_think_with_keyword_arguments() -> None:
    calls: list[dict[str, Any]] = []
    result = await run_think(calls, thought="Investigate", cycle_count=2, system_prompt="sys")

    assert result == {"status": "success", "content": [{"text": "think notes"}]}
    assert calls == [{
        "thought": "Investigate",
        "cycle_count": 2,
        "system_prompt": "sys",
        "model_provider": "perplexity",
        "model_settings": {},
    }]


@pytest.mark.asyncio
async def test_omitted_provider_defaults_to_perplexity_custom_provider() -> None:
    """With no parent agent to inherit from, the wrapper injects the tool's
    documented custom-provider path; {} routes create_model straight to
    ``.models/perplexity.py`` (get_provider_config knows only built-ins)."""
    calls: list[dict[str, Any]] = []
    await run_think(calls, thought="t")
    tool_input = calls[0]
    assert tool_input["model_provider"] == "perplexity"
    assert tool_input["model_settings"] == {}
    # Optional parameters stay absent unless the model supplies them.
    assert "tools" not in tool_input
    assert "thinking_system_prompt" not in tool_input


@pytest.mark.asyncio
async def test_explicit_provider_settings_and_options_pass_through() -> None:
    calls: list[dict[str, Any]] = []
    await run_think(
        calls,
        thought="t",
        cycle_count=3,
        system_prompt="sp",
        tools=["calculator"],
        model_provider="env",
        model_settings={"model_id": "other/model"},
        thinking_system_prompt="Use first principles.",
    )
    assert calls[0] == {
        "thought": "t",
        "cycle_count": 3,
        "system_prompt": "sp",
        "tools": ["calculator"],
        "model_provider": "env",
        "model_settings": {"model_id": "other/model"},
        "thinking_system_prompt": "Use first principles.",
    }


def load_provider_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load ``orchestrator/.models/perplexity.py`` the way create_model does.

    Importing run_worker reads the protected .env.local via dotenv, so the
    loader is stubbed first (same guard as test_perplexity_model.py).
    """
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    path = Path(__file__).resolve().parents[1] / ".models" / "perplexity.py"
    spec = importlib.util.spec_from_file_location("think_perplexity_provider", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "argument,environment,expected",
    [
        pytest.param("explicit/model", {}, "explicit/model", id="argument-wins"),
        pytest.param(None, {"STRANDS_MODEL_ID": "env/model"}, "env/model", id="environment-fallback"),
        pytest.param(None, {}, None, id="config-default-fallback"),
    ],
)
def test_custom_provider_instance_builds_the_session_perplexity_model(
    monkeypatch: pytest.MonkeyPatch, argument: str | None, environment: dict[str, str], expected: str | None
) -> None:
    module = load_provider_module(monkeypatch)
    if expected is None:
        from config import DEFAULT_MODEL_ID

        expected = DEFAULT_MODEL_ID
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    monkeypatch.delenv("STRANDS_MODEL_ID", raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    tools = [{"type": "web_search"}]
    params = {"tools": tools, "max_steps": 100}
    built: list[dict[str, Any]] = []
    monkeypatch.setattr(module, "native_tools", lambda: tools)
    monkeypatch.setattr(module, "model_params", lambda model_id, native: {**params, "model_id": model_id})
    monkeypatch.setattr(
        module,
        "PerplexityModel",
        lambda model_id, params, api_key: built.append(
            {"model_id": model_id, "params": params, "api_key": api_key}
        )
        or SimpleNamespace(model_id=model_id),
    )

    model = module.instance(argument) if argument else module.instance()

    assert model.model_id == expected
    # Params come from run_worker.model_params over agent_api_tools.native_tools
    # — the identical surface the outer agent gets.
    assert built == [{
        "model_id": expected,
        "params": {**params, "model_id": expected},
        "api_key": "test-key",
    }]
