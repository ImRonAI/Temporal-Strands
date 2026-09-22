"""Activity-side parent Agent and official graph_tool._select_tools.

TemporalAgent cannot cross the activity boundary (skill Pattern 2).
``parent_agent`` rebuilds a plain Agent; ``_select_tools`` filters
``parent.tool_registry`` by name.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.models.model import Model

from graph_tool import _select_tools, _tool_name
from load_tool import STRANDS_TOOLS_DIR
from subagent_support import agent_workspace, parent_agent, workspace_task_prefix

os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

CALCULATOR = str(STRANDS_TOOLS_DIR / "calculator.py")


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


def _activity_mock(loaded: Any) -> MagicMock:
    info = MagicMock()
    info.workflow_id = "chat-test"

    async def query(name: str, *_: Any, **__: Any) -> Any:
        return {"model_id": "fake/text", "loaded_tools": loaded}[name]

    handle = MagicMock()
    handle.query = AsyncMock(side_effect=query)
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    return MagicMock(
        info=MagicMock(return_value=info),
        client=MagicMock(return_value=client),
        heartbeat=MagicMock(),
        logger=MagicMock(),
    )


@pytest.mark.asyncio
async def test_parent_agent_restores_official_load_tool_records() -> None:
    loaded = [{"path": CALCULATOR, "name": "calculator"}]
    with patch("subagent_support.activity", _activity_mock(loaded)):
        parent = await parent_agent(SilentModel())

    names = set(parent.tool_registry.registry)
    assert names == {"calculator"}


@pytest.mark.asyncio
async def test_select_tools_named_subset_unknown_is_official_warning() -> None:
    loaded = [{"path": CALCULATOR, "name": "calculator"}]
    with patch("subagent_support.activity", _activity_mock(loaded)):
        parent = await parent_agent(SilentModel())

    with patch("graph_tool.logger") as logger:
        selected = _select_tools(
            parent, ["file_read", "calculator", "strands_tools.current_time"]
        )

    assert {_tool_name(tool) for tool in selected} == {"calculator"}
    warned = {call.args[1] for call in logger.warning.call_args_list}
    assert warned == {"file_read", "strands_tools.current_time"}


@pytest.mark.asyncio
async def test_select_tools_omit_inherits_registry() -> None:
    loaded = [{"path": CALCULATOR, "name": "calculator"}]
    with patch("subagent_support.activity", _activity_mock(loaded)):
        parent = await parent_agent(SilentModel())
    selected = _select_tools(parent, None)
    assert {_tool_name(t) for t in selected} == {"calculator"}


@pytest.mark.asyncio
async def test_loaded_tools_query_failure_degrades_to_empty() -> None:
    """A run started before the query existed cannot answer it."""
    mock = _activity_mock([])
    mock.client.return_value.get_workflow_handle.return_value.query = AsyncMock(
        side_effect=RuntimeError("unknown query")
    )
    with patch("subagent_support.activity", mock):
        parent = await parent_agent(SilentModel())
    assert parent.tool_registry.registry == {}
    mock.logger.warning.assert_called()


def test_agent_workspace_is_not_host_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GWEN_AGENT_WORKSPACE_ROOT", str(tmp_path))
    import config

    monkeypatch.setattr(config, "AGENT_WORKSPACE_ROOT", tmp_path)
    root = agent_workspace("chat-abc")
    assert root == tmp_path / "chat-abc"
    assert (root / "workspace").is_dir()
    assert str(root) != str(Path.home())
    text = workspace_task_prefix("chat-abc")
    assert str(root / "workspace") in text
    assert "/home/user" in text
