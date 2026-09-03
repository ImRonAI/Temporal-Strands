"""GWEN-30 evidence: live multi-node formation via the public graph tool API.

Runs the async-generator `graph` tool from strands_graph_tool with a
heterogeneous formation (one model `agent` node + one `skill_agent` node,
connected by an edge) against a live provider (Perplexity native endpoint
via strands OpenAIModel), capturing EVERY yielded event verbatim-shaped
to a JSON log for contract reconciliation (GWEN-27).

Public API only: graph, configure_skills (per GWEN-30 requirements).

Usage (from strands-tools repo root):
    .venv/bin/python docs_evidence_gwen30_run.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "src/strands_tools/sessions_and_skills/sample_agent_skills"))

from agentskills import discover_skills  # noqa: E402
from strands import Agent  # noqa: E402
from strands.models.openai import OpenAIModel  # noqa: E402

from strands_graph_tool import configure_skills, graph  # noqa: E402

MODEL_ID = "sonar"
BASE_URL = "https://api.perplexity.ai"
GRAPH_ID = "evidence-run-1"
TOPOLOGY = {
    "nodes": [
        {
            "id": "research",
            "system_prompt": "You are a researcher. Answer the task in exactly one short sentence.",
        },
        {"id": "expert", "type": "skill_agent", "skill": "wf-skill"},
    ],
    "edges": [{"from": "research", "to": "expert"}],
}
TASK = "State one interesting fact about graphs (the data structure). One sentence."


def sanitize(value, depth=0):
    """JSON-safe copy preserving structure; non-serializable leaves become typed reprs."""
    if depth > 12:
        return {"__truncated__": True}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): sanitize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in value]
    return {"__type__": type(value).__qualname__, "__repr__": repr(value)[:500]}


async def main() -> None:
    model = OpenAIModel(
        client_args={"api_key": os.environ["PERPLEXITY_API_KEY"], "base_url": BASE_URL},
        model_id=MODEL_ID,
        params={"max_tokens": 256},
    )
    configure_skills(discover_skills(str(REPO / "tests" / "fixtures_skills")), base_agent_model=model)
    parent = Agent(model=model, system_prompt="Parent agent.", callback_handler=None)

    log: list[dict] = []

    def record(phase: str, event) -> None:
        log.append(
            {
                "phase": phase,
                "seq": len(log),
                "t": round(time.time(), 3),
                "event_keys": sorted(event.keys()) if isinstance(event, dict) else None,
                "event": sanitize(event),
            }
        )

    async for event in graph._tool_func(
        action="create", graph_id=GRAPH_ID, topology=TOPOLOGY, agent=parent
    ):
        record("create", event)

    async for event in graph._tool_func(action="execute", graph_id=GRAPH_ID, task=TASK, agent=parent):
        record("execute", event)

    async for event in graph._tool_func(action="delete", graph_id=GRAPH_ID, agent=parent):
        record("delete", event)

    out = {
        "issue": "GWEN-30",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": {"model_id": MODEL_ID, "base_url": BASE_URL, "adapter": "strands.models.openai.OpenAIModel"},
        "graph_id": GRAPH_ID,
        "topology": TOPOLOGY,
        "task": TASK,
        "event_count": len(log),
        "event_type_histogram": _histogram(log),
        "events": log,
    }
    out_path = REPO / "gwen30-event-log.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"captured {len(log)} events -> {out_path}")
    for item in log:
        keys = item["event_keys"]
        print(f"  [{item['phase']}] #{item['seq']} keys={keys}")


def _histogram(log: list[dict]) -> dict:
    hist: dict[str, int] = {}
    for item in log:
        keys = item["event_keys"] or []
        label = "+".join(keys) if keys else "<non-dict>"
        hist[label] = hist.get(label, 0) + 1
    return hist


if __name__ == "__main__":
    asyncio.run(main())
