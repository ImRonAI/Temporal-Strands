"""The agent's `think` tool, as a factory so every surface gets the same one.

Lives here rather than inline in a workflow because both ChatWorkflow and
CompareWorkflow need it: the compare view is meant to show *this* agent, and
an agent without `think` is not it. The tool body is unchanged from where it
was originally defined inline — only the two workflow-scoped values it closes
over (the session's model id and its thinking stream topic) became
parameters.

The tool itself is think.py, the strands-agents-tools `think` vendored
verbatim with only async streaming added. This wrapper exists solely to cross
the workflow/activity boundary; the real work runs in
thinking_activity.run_thinking_cycles.
"""

from datetime import timedelta
from typing import Any, Optional

from strands import tool
from temporalio import workflow
from temporalio.common import RetryPolicy

from thinking_activity import run_thinking_cycles


def make_think_tool(
    model_id: str,
    thinking_topic_name: str,
    *,
    start_to_close_timeout: timedelta,
    schedule_to_close_timeout: timedelta,
    heartbeat_timeout: timedelta,
    retry_policy: RetryPolicy,
) -> Any:
    """Build the agent-callable `think` tool bound to one session's model and
    stream topic. The model decides when and how much to think; it is never
    forced."""

    @tool
    async def think(
        thought: str,
        cycle_count: int,
        system_prompt: str,
        tools: Optional[list[str]] = None,
        model_settings: Optional[dict[str, Any]] = None,
        thinking_system_prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        """Deep multi-cycle reasoning to make your response better.

        A thinking agent (your model and tools by default) processes `thought`
        through cycle_count sequential cycles, each building on the previous
        cycle's conclusion. The result is returned to you, and each cycle is
        streamed to the user live as your chain of thought — so the reasoning
        shown must genuinely fit the response you go on to give. Use this
        whenever working a problem through first would make your answer more
        successful; skip it when the answer is straightforward. You control
        exactly how much thinking happens — keep it proportional.

        Args:
            thought: The question or problem to think through, stated fully and
                concretely — include the context the thinking agent needs.
            cycle_count: Number of thinking cycles, 1-10. Match it to difficulty:
                1-2 for a focused structured pass, more only when depth pays.
            system_prompt: You write this, with your best prompt engineering: the
                persona and expertise the thinking agent should have for THIS
                problem (who it is).
            tools: Names of your tools the thinking agent may use, e.g.
                ["create_agent_response"]. Pass [] for pure reasoning with no
                tools; omit to inherit all of your tools.
            model_settings: Optional. {"params": {"max_output_tokens": N}} caps
                how long each cycle's reasoning can run.
            thinking_system_prompt: You write this too: the thinking methodology
                (HOW it thinks — e.g. first-principles decomposition, weighing
                alternatives, step-by-step planning), separate from who it is.

        Returns:
            {"status": "success"|"error", "content": [{"text": "Cycle-by-cycle
            thinking output"}]}
        """
        return await workflow.execute_activity(
            run_thinking_cycles,
            args=[
                thought,
                cycle_count,
                system_prompt,
                model_id,
                thinking_topic_name,
                tools,
                # model_provider is deliberately NOT exposed to the model.
                # think.py's fallback for it is strands_tools' create_model(),
                # which builds a provider from scratch (bedrock/openai/ollama/
                # ...) — none of which are configured in this deployment. When
                # the model supplied one anyway, create_model() returned a bare
                # OpenAIModel whose config had no model_id, and the cycle died
                # with KeyError: 'model_id' inside strands/models/openai.py.
                # There is exactly one model backend here, so the thinking
                # agent always uses the parent's reconstructed PerplexityModel.
                None,
                model_settings,
                thinking_system_prompt,
            ],
            # Same bounds the orchestrator already uses for every model
            # activity — Temporal requires a timeout to be set at all, so
            # this reuses the existing ones rather than adding new caps of
            # its own.
            start_to_close_timeout=start_to_close_timeout,
            schedule_to_close_timeout=schedule_to_close_timeout,
            heartbeat_timeout=heartbeat_timeout,
            retry_policy=retry_policy,
        )

    return think
