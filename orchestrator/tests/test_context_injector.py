"""Official ContextInjector: the worker's model registry folded into one model call.

https://strandsagents.com/docs/user-guide/concepts/plugins/context-injector/
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from strands import Agent
from strands.models.model import Model
from strands.vended_plugins.context_injector import ContextInjector

from catalog_workflow import model_catalog
from config import PROVIDER_PERPLEXITY_AGENT_API, RegisteredModel
from workflow import _format_agent_api_models, render_agent_api_models


class RecordingExecutor:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[Any] = []

    async def __call__(self, activity_fn: Any, **options: Any) -> Any:
        self.calls.append({"activity_fn": activity_fn, "options": options})
        return self.result


class CaptureModel(Model):
    def __init__(self) -> None:
        self.seen: list[Any] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> dict[str, Any]:
        return {}

    async def structured_output(
        self, output_model: Any, prompt: Any, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError
        yield

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.seen.append(messages)
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "ok"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


def _catalog(*ids: str) -> list[RegisteredModel]:
    return [
        RegisteredModel(id=model_id, provider=PROVIDER_PERPLEXITY_AGENT_API, label=model_id)
        for model_id in ids
    ]


def test_format_agent_api_models_uses_registered_model_ids() -> None:
    text = _format_agent_api_models(
        _catalog("openai/gpt-5.6-sol", "anthropic/claude-opus-5")
    )
    assert text is not None
    assert text.startswith("<agent_api_models>")
    assert "openai/gpt-5.6-sol" in text
    assert "anthropic/claude-opus-5" in text
    assert text.endswith("</agent_api_models>")


def test_format_agent_api_models_skips_empty_catalog() -> None:
    assert _format_agent_api_models([]) is None
    assert _format_agent_api_models(None) is None


@pytest.mark.asyncio
async def test_render_agent_api_models_calls_catalog_activity() -> None:
    executor = RecordingExecutor(_catalog("openai/gpt-5.6-sol"))
    text = await render_agent_api_models(None, executor=executor)
    assert executor.calls[0]["activity_fn"] is model_catalog
    assert "openai/gpt-5.6-sol" in (text or "")


@pytest.mark.asyncio
async def test_context_injector_folds_catalog_into_model_input() -> None:
    model = CaptureModel()
    agent = Agent(
        model=model,
        callback_handler=None,
        plugins=[
            ContextInjector(
                lambda _context: (
                    "<agent_api_models>\n"
                    "openai/gpt-5.6-sol\n"
                    "</agent_api_models>"
                ),
                name="agent-api-models",
            )
        ],
    )
    await agent.invoke_async("plan a graph")
    assert model.seen
    texts = [
        block["text"]
        for message in model.seen[0]
        for block in message.get("content") or []
        if isinstance(block, dict) and "text" in block
    ]
    assert any("openai/gpt-5.6-sol" in text for text in texts)
    persisted = " ".join(
        block["text"]
        for message in agent.messages
        if message.get("role") == "user"
        for block in message.get("content") or []
        if isinstance(block, dict) and "text" in block
    )
    assert "plan a graph" in persisted
    assert "openai/gpt-5.6-sol" not in persisted
