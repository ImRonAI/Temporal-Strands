"""Official strands_tools.graph activity wrap."""

from __future__ import annotations

import pytest
from strands_tools.graph import graph
from temporalio.contrib.strands.workflow import activity_as_tool

from graph_activity import graph_activity


def test_activity_uses_official_graph_schema() -> None:
    assert graph_activity.__doc__ == graph.__doc__
    spec = activity_as_tool(graph_activity).tool_spec["inputSchema"]["json"]["properties"]
    official = graph.tool_spec["inputSchema"]["json"]["properties"]
    assert spec["graph_id"]["description"] == official["graph_id"]["description"]
    assert spec["topology"]["description"] == official["topology"]["description"]
    assert spec["task"]["description"] == official["task"]["description"]


@pytest.mark.asyncio
async def test_official_create_and_execute_errors() -> None:
    missing = await graph_activity(action="create")
    assert missing["status"] == "error"
    assert "graph_id and topology are required" in missing["content"][0]["text"]

    empty = await graph_activity(action="create", graph_id="g1", topology={})
    assert empty["status"] == "error"
    assert "graph_id and topology are required" in empty["content"][0]["text"]

    no_task = await graph_activity(action="execute", graph_id="g1")
    assert no_task["status"] == "error"
    assert "graph_id and task are required" in no_task["content"][0]["text"]
