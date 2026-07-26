"""Side-by-side model comparison, run through the orchestrator.

The compare view used to call Perplexity's HTTP API directly from the Next.js
route, which meant it was not comparing *this agent* at all — no tools, no
`think`, no durability, no retries, just raw model completions. This runs the
real thing instead: one `TemporalAgent` per model, same tools and same
identity as the chat session, so what the view shows is the orchestrator
answering N times rather than N bare models.

Each model gets its own stream topic (`model:<id>`) so the panes stay
independent — a slow or failing model can't hold up the others, and the
bridge can attribute every chunk without inspecting its content.

Deliberately NOT a chat session: there is no continue_as_new, no update
handler, and no conversation carried forward. A comparison is one prompt
answered once per model, so the workflow runs to completion and exits. That
also means it self-cleans, unlike ChatWorkflow which lives until it is ended.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream

from strands.agent.conversation_manager import NullConversationManager
from temporalio.contrib.strands import TemporalAgent

from perplexity_tools import AGENT_API_FUNCTION_TOOLS, make_create_agent_response_tool
from think_tool import make_think_tool
from workflow import (
    AGENT_KWARGS,
    TURN_LIMITS,
    MODEL_HEARTBEAT_TIMEOUT,
    MODEL_RETRY_POLICY,
    MODEL_SCHEDULE_TO_CLOSE_TIMEOUT,
    MODEL_START_TO_CLOSE_TIMEOUT,
    ToolResultStreamer,
)


def topic_for(model_id: str) -> str:
    """Stream topic carrying one model's output. Shared by the workflow and
    the bridge, so they cannot disagree on the name."""
    return f"model:{model_id}"


@dataclass
class CompareInput:
    prompt: str
    model_ids: list[str] = field(default_factory=list)


@dataclass
class CompareResult:
    """Final text per model, and the error for any that failed. A model that
    fails is reported rather than aborting the comparison — losing three good
    answers because a fourth model was down would be the wrong trade."""

    replies: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


@workflow.defn
class CompareWorkflow:
    @workflow.init
    def __init__(self, input: CompareInput) -> None:
        self.stream = WorkflowStream()
        self._topics = {m: self.stream.topic(topic_for(m)) for m in input.model_ids}

    @workflow.run
    async def run(self, input: CompareInput) -> CompareResult:
        result = CompareResult()

        async def run_one(model_id: str) -> None:
            topic = self._topics[model_id]
            try:
                agent = TemporalAgent(
                    # Same agent.json identity the chat session uses, so the
                    # compare view provably shows this agent.
                    model=model_id,
                    start_to_close_timeout=MODEL_START_TO_CLOSE_TIMEOUT,
                    schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE_TIMEOUT,
                    heartbeat_timeout=MODEL_HEARTBEAT_TIMEOUT,
                    retry_policy=MODEL_RETRY_POLICY,
                    streaming_topic=topic_for(model_id),
                    **AGENT_KWARGS,
                    # Same tool set as a chat session, `think` included —
                    # comparing the agent without its reasoning tool would
                    # not be comparing the agent. Each model's cycles stream
                    # to that model's own topic, so panes stay separate.
                    tools=[
                        *AGENT_API_FUNCTION_TOOLS,
                        make_create_agent_response_tool(topic_for(model_id)),
                        make_think_tool(
                            model_id,
                            topic_for(model_id),
                            start_to_close_timeout=MODEL_START_TO_CLOSE_TIMEOUT,
                            schedule_to_close_timeout=MODEL_SCHEDULE_TO_CLOSE_TIMEOUT,
                            heartbeat_timeout=MODEL_HEARTBEAT_TIMEOUT,
                            retry_policy=MODEL_RETRY_POLICY,
                        ),
                    ],
                    conversation_manager=NullConversationManager(),
                    hooks=[ToolResultStreamer(topic)],
                )
                reply = await agent.invoke_async(input.prompt, limits=TURN_LIMITS)
                result.replies[model_id] = str(reply).strip()
            except Exception as exc:  # noqa: BLE001 - reported per model
                # Recorded and published rather than raised: one bad model
                # must not fail the whole comparison.
                result.errors[model_id] = str(exc)
                topic.publish({"error": str(exc)})

        # Every model runs concurrently; gather so one failure can't cancel
        # the rest (they're already caught above, but this makes it explicit).
        await asyncio.gather(*(run_one(m) for m in input.model_ids))

        # Let the bridge drain what it hasn't read yet, then release its
        # pollers so it sees a clean end-of-stream instead of an
        # "execution already completed" error when this workflow returns.
        self.stream.detach_pollers()
        await workflow.wait_condition(workflow.all_handlers_finished)
        return result
