"""Official ``strands_tools.graph`` as a Temporal activity.

https://docs.temporal.io/develop/python/integrations/strands-agents
https://strandsagents.com/docs/user-guide/concepts/multi-agent/graph/
"""

from __future__ import annotations

from typing import Any, Optional

from strands_tools.graph import graph
from temporalio import activity


@activity.defn(name="graph")
async def graph_activity(
    action: str,
    graph_id: Optional[str] = None,
    topology: Optional[dict] = None,
    task: Optional[str] = None,
    model_provider: Optional[str] = None,
    model_settings: Optional[dict[str, Any]] = None,
    tools: Optional[list[str]] = None,
) -> dict:
    return graph(
        action=action,
        graph_id=graph_id,
        topology=topology,
        task=task,
        model_provider=model_provider,
        model_settings=model_settings,
        tools=tools,
    )


graph_activity.__doc__ = graph.__doc__
