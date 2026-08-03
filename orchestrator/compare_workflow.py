"""Independent same-prompt execution across several models.

Each model gets its own ``TemporalAgent`` with its own fresh message list and
its own stream topic, so nothing is shared between them and one model failing
cannot remove another's reply. Built on the same verified surface as
``workflow.py``: ``model=`` is a registered string name (guide R1), the agent is
constructed per run inside the workflow, and topic names must match what
``server.py`` subscribes to (guide R10).

Topics are ``model:<model_id>``. ``server.py`` tags every non-terminal frame it
forwards with the model id parsed back out of the topic name, which is what
``app/api/compare/route.ts`` keys its panes on.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from dataclasses import dataclass, field

from temporalio import workflow
from temporalio.contrib.strands import TemporalAgent
from temporalio.contrib.workflow_streams import WorkflowStream

from config import (
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
    MODEL_SCHEDULE_TO_CLOSE,
    MODEL_START_TO_CLOSE,
)

MODEL_TOPIC_PREFIX = "model:"


def model_topic(model_id: str) -> str:
    """Topic hosting one model's stream events. Shared with server.py."""
    return f"{MODEL_TOPIC_PREFIX}{model_id}"


@dataclass
class CompareInput:
    prompt: str
    model_ids: list[str]
    system_prompt: str


@dataclass
class CompareResult:
    """Per-model final text and per-model failure, keyed by model id.

    Both maps are returned so a partial failure still delivers the models that
    did succeed -- app/api/compare/route.ts reads `replies` and `errors`
    independently off the terminal frame.
    """

    replies: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


@workflow.defn
class CompareWorkflow:
    @workflow.init
    def __init__(self, input: CompareInput) -> None:
        # Hosts every per-model topic. Constructed directly in __init__ because
        # WorkflowStream inspects its caller's frame and rejects anything else.
        self._stream = WorkflowStream()

    @workflow.run
    async def run(self, input: CompareInput) -> CompareResult:
        result = CompareResult()

        async def run_one(model_id: str) -> None:
            # A fresh agent and a fresh (empty) message list per model. Nothing
            # mutable is shared, so the models cannot observe each other.
            agent = TemporalAgent(
                model=model_id,
                start_to_close_timeout=MODEL_START_TO_CLOSE,
                schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE,
                heartbeat_timeout=MODEL_HEARTBEAT,
                retry_policy=MODEL_RETRY_POLICY,
                streaming_batch_interval=timedelta(milliseconds=200),
                streaming_topic=model_topic(model_id),
                system_prompt=input.system_prompt,
            )
            try:
                reply = await agent.invoke_async(input.prompt)
                result.replies[model_id] = str(reply).strip()
            except Exception as error:  # noqa: BLE001 - one model must not sink the rest
                result.errors[model_id] = str(error)

        # Deduplicate while preserving the caller's order; a repeated id would
        # otherwise collide on the same topic and interleave two streams.
        seen: set[str] = set()
        ordered = [
            model_id
            for model_id in input.model_ids
            if not (model_id in seen or seen.add(model_id))
        ]

        # return_exceptions is unnecessary: run_one already converts a model
        # failure into an entry in result.errors, so no task raises.
        await asyncio.gather(*(run_one(model_id) for model_id in ordered))

        # detach BEFORE waiting: the stream's long-poll is itself an update
        # handler, so a parked subscriber keeps all_handlers_finished false
        # forever and this deadlocks. The SDK docstring on detach_pollers
        # states this order, and ChatWorkflow.run already follows it.
        self._stream.detach_pollers()
        await workflow.wait_condition(workflow.all_handlers_finished)
        return result
