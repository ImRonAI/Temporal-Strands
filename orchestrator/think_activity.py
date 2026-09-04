"""The ``think`` tool from strands-agents-tools, as a streaming Temporal activity.

A faithful copy of ``strands_tools.think`` (installed at
``.venv/.../strands_tools/think.py``) with exactly two changes:

1. **Async streaming**: ``process_cycle`` consumes ``agent.stream_async()``
   instead of the blocking ``agent(prompt)``, and publishes every raw model
   ``StreamEvent`` chunk onto ``THINKING_TOPIC`` through
   ``WorkflowStreamClient.from_within_activity()`` as it arrives -- the same
   publish-per-event mechanism the SDK's own model activity uses
   (``temporalio/contrib/strands/_model_activity.py:87-95``), so the Chain of
   Thought streams live instead of arriving as one lump.
2. **activity_as_tool**: ``think`` is decorated ``@activity.defn(name="think")``
   and handed to the orchestrator via
   ``temporalio.contrib.strands.workflow.activity_as_tool`` (workflow.py), per
   the strands-temporal guide. The nested per-cycle agent is a plain
   ``strands.Agent`` running inside this activity -- NOT a TemporalAgent.

This activity is LIVE, wired in two ways by ``workflow.py``:

- ``THINK_TOOL`` -- a model-callable tool on every session agent; the
  orchestrator is encouraged to call it frequently (agent.json guidance).
- ``_ThinkFirstHook`` -- forced ahead of the model on every new user prompt
  (``BeforeInvocationEvent``); the notes it returns are folded into the user
  message as a ``<think_notes>`` block.

``activity.heartbeat()`` is sent for every streamed chunk so long cycles stay
visibly alive to Temporal whenever a heartbeat timeout is configured.

Tool inheritance note: only model-authored arguments cross the activity
boundary (``activity_as_tool`` contract), so the nested agent CANNOT see the
parent agent's tool list. It runs toolless (``tools=[]``) apart from the
session model's own server-side natives -- the b8e1d70 behavior, kept
deliberately.

The activity name must stay ``think``: ``activity_as_tool`` derives the tool
name from the ``@activity.defn`` name, and the UI keys Chain-of-Thought
suppression off that exact string (components/v0/agent-activity.tsx:908,
AGENTS.md hard rule).

One unavoidable delta from the original: the ``agent`` parameter there is the
live parent Agent injected in-process by Strands. Across an activity boundary
no live Agent exists, so parent-tool/model inheritance is resolved differently:
``model_provider=None`` (the default) now means "the session's model", looked
up by querying the parent workflow's ``model_id`` query through
``activity.client()`` and resolved against the worker's registered model
factories (installed via :func:`configure` at worker startup, the same mapping
StrandsPlugin gets). The session models are GeminiModel factories, so they
carry Google's built-in tools (Search, Code Execution, URL Context) on
``gemini_tools``. The nested agent therefore researches with the same model
tools the orchestrator has -- which is what tool inheritance bought the original.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Dict, List, Optional

from strands import Agent
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError

from config import THINK_STREAM_BATCH_INTERVAL

logger = logging.getLogger(__name__)

# Default persona (system_prompt): WHO the thinker is. Passed to the nested
# Agent on every cycle when the orchestrator supplies no system_prompt.
DEFAULT_THINK_SYSTEM_PROMPT = """You are an evidence-based reasoning specialist operating as a nested thinking agent inside a larger orchestration system. You never interact with end users. Your analysis will be consumed by an orchestrator agent that decides what to do next.

Your role:
- Analyze the assigned thought with rigor, using whatever tools are available to you.
- Treat all tool outputs as evidence to be appraised, not as facts to be accepted.
- Surface contradictions, rate the strength of what you find, and make uncertainty explicit.
- Conclude each analysis with a clear, actionable finding and a recommendation for what the orchestrator should do next — including when evidence is insufficient and further investigation or human review is required.

You are precise, skeptical, and decision-oriented. You do not pad, speculate without labeling it as speculation, or hide weak evidence behind confident language."""

# Default methodology (thinking_system_prompt): HOW the thinker works each
# cycle. Injected by create_thinking_prompt in place of the generic defaults
# when the orchestrator supplies no thinking_system_prompt.
DEFAULT_THINKING_SYSTEM_PROMPT = """THINKING METHODOLOGY — Evidence-Based Parallel Analysis

For this cycle, follow this exact procedure:

1. DECOMPOSE
   - Restate the thought as a precise, answerable question.
   - Break it into independent sub-questions or evidence gaps.

2. PARALLEL EVIDENCE GATHERING
   - Identify all independent information needs that do not depend on each other's outputs.
   - Call the tools for ALL of them in a single turn so they execute in parallel. Do not serialize independent read-only calls (search, retrieve, file reads, API fetches).
   - Only make sequential calls when one tool's output is required as another tool's input, or when a tool mutates shared state.
   - If no tool can fill a gap, note it explicitly as a missing-evidence item instead of guessing.

3. APPRAISE
   For every tool result:
   - Extract the specific claim or data point it contributes.
   - Rate its quality: HIGH (authoritative, current, directly on-point) / MEDIUM (relevant but indirect or dated) / LOW (opinion, unverified, or tangential).
   - Actively look for contradictions across sources. Agreement across independent sources raises confidence; conflict must be reported, not resolved by preference.

4. SYNTHESIZE
   - Connect the appraised evidence to the question: what does it support, what does it rule out?
   - Separate established findings from assumptions. Label every inference as inference.
   - State what remains unknown and how much it matters to the conclusion.

5. DECIDE AND HAND OFF
   End your analysis with this structured block:

   FINDINGS: The evidence-supported conclusions, each tagged with evidence strength.
   CONFIDENCE: high / medium / low, with a one-line justification.
   GAPS: What is missing and which tool (or human) could fill it.
   NEXT ACTION: Exactly one of:
     - MORE_TOOLS: <which tools, with what arguments, and whether they can run in parallel>
     - REFINE: <how the question should be narrowed or reframed>
     - READY: <the final synthesis the orchestrator should use, plus any safety flags requiring human review before acting>

Rules:
- Never fabricate evidence to fill a gap. An honest GAP beats a confident guess.
- For clinical, safety, compliance, or irreversible decisions: if confidence is below high, NEXT ACTION must include human review.
- Carry forward unresolved questions into the next cycle rather than dropping them."""

# Worker-set registry of model factories, keyed by live catalog model id --
# the same mapping run_worker hands StrandsPlugin. The api-key-bearing clients
# stay inside the factory closures and never enter workflow state or activity
# payloads. Empty until configure() runs, which only happens in the worker.
_MODEL_FACTORIES: dict[str, Callable[[], Any]] = {}


def configure(model_factories: Mapping[str, Callable[[], Any]]) -> None:
    """Install the worker's model factories for the think activity."""
    _MODEL_FACTORIES.clear()
    _MODEL_FACTORIES.update(model_factories)


async def _session_model() -> Any:
    """The session's model: parent workflow's model_id query -> factory."""
    info = activity.info()
    if not info.workflow_id:
        raise ApplicationError(
            "think must be scheduled by a workflow",
            type="ThinkActivityError",
            non_retryable=True,
        )
    handle = activity.client().get_workflow_handle(info.workflow_id)
    model_id = await handle.query("model_id")
    factory = _MODEL_FACTORIES.get(model_id)
    if factory is None:
        # Non-retryable: the catalog is fixed for the worker's lifetime, so
        # retrying cannot make an unregistered id appear.
        raise ApplicationError(
            f"think: no registered model factory for {model_id!r}",
            type="ThinkActivityError",
            non_retryable=True,
        )
    return factory()


class ThoughtProcessor:
    """Verbatim from strands_tools.think, minus the rich Console (activities
    have no console) and with process_cycle made async + streaming."""

    def __init__(self, tool_context: Dict[str, Any]):
        self.system_prompt = tool_context.get("system_prompt", "")
        self.messages = tool_context.get("messages", [])
        self.tool_use_id = str(uuid.uuid4())

    def create_thinking_prompt(
        self,
        thought: str,
        cycle: int,
        total_cycles: int,
        thinking_system_prompt: Optional[str] = None,
    ) -> str:
        """Create a focused prompt for the thinking process with optional custom thinking instructions."""

        # Default thinking instructions
        default_instructions = """
Direct Tasks:
1. Process this thought deeply and analytically
2. Generate clear, structured insights
3. Consider implications and connections
4. Provide actionable conclusions
5. Use other available tools as needed for analysis
"""

        # Use custom thinking instructions if provided, otherwise use defaults
        if thinking_system_prompt:
            thinking_instructions = f"\n{thinking_system_prompt}\n"
        else:
            thinking_instructions = default_instructions

        prompt = f"""{thinking_instructions}
Current Cycle: {cycle}/{total_cycles}

Thought to process:
{thought}

Please provide your analysis directly:
"""
        return prompt.strip()

    async def process_cycle(
        self,
        thought: str,
        cycle: int,
        total_cycles: int,
        custom_system_prompt: str,
        model: Any,
        publish: Callable[[Any], None],
        thinking_system_prompt: Optional[str] = None,
    ) -> str:
        """Process a single thinking cycle, streaming every model chunk out.

        The original's model-switching branches (parent model / "env" /
        explicit provider) collapse to the ``model`` argument resolved by the
        caller, and the parent's callback_handler is replaced by ``publish``
        -- the WorkflowStream topic publisher that carries each raw
        StreamEvent to the UI.
        """

        logger.debug(f"🧠 Thinking Cycle {cycle}/{total_cycles}: Processing cycle...")

        # Create cycle-specific prompt with custom thinking instructions
        prompt = self.create_thinking_prompt(thought, cycle, total_cycles, thinking_system_prompt)

        # Display input prompt
        logger.debug(f"\n--- Input Prompt ---\n{prompt}\n")

        # Initialize the new Agent with the session model. Plain strands.Agent
        # (never TemporalAgent): the durable unit is THIS activity. Tools are
        # the model's own server-side natives; no client-side tools cross the
        # activity boundary, and 'think' itself is thereby excluded -- the
        # original's recursion guard, achieved structurally.
        agent = Agent(
            model=model,
            messages=[],
            tools=[],
            system_prompt=custom_system_prompt,
            callback_handler=None,
        )

        # Run the agent with the provided prompt, streaming instead of
        # blocking. ModelStreamChunkEvent surfaces each raw provider
        # StreamEvent as {"event": chunk} (strands/types/_events.py:104-118);
        # publishing the chunk verbatim keeps the thinking topic's frame shape
        # identical to what the SDK's model activity publishes, so the
        # protected route consumes it unchanged.
        assistant_response = str()
        result = None
        async for event in agent.stream_async(prompt):
            if "event" in event:
                publish(event["event"])
                # One beat per streamed chunk: long cycles stay visibly alive
                # to Temporal whenever a heartbeat timeout is configured.
                # No-op outside an activity context (unit tests drive this
                # method directly through ActivityEnvironment, which accepts
                # heartbeats).
                try:
                    activity.heartbeat()
                except RuntimeError:  # pragma: no cover - no activity context
                    pass
            if "result" in event:
                result = event["result"]

        # Extract response
        assistant_response = str(result) if result is not None else ""

        # Display assistant response
        logger.debug(f"\n--- Assistant Response ---\n{assistant_response.strip()}\n")

        return assistant_response.strip()


@activity.defn(name="think")
async def think(
    thought: str,
    cycle_count: int,
    system_prompt: str,
    thinking_system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Recursive thinking tool for sophisticated thought generation.

    This tool implements a multi-cycle cognitive analysis approach that
    progressively refines thoughts through iterative processing. Each cycle
    builds upon insights from the previous cycle, creating a depth of analysis
    that would be difficult to achieve in a single pass.

    Args:
        thought: The detailed thought or idea to process through multiple thinking cycles.
            This can be a question, statement, problem description, or creative prompt.
        cycle_count: Number of thinking cycles to perform (1-10). More cycles allow for
            deeper analysis but require more time and resources. Typically 3-5 cycles
            provide a good balance of depth and efficiency.
        system_prompt: Custom system prompt to use for the LLM thinking process. This should
            specify the expertise domain and thinking approach for processing the thought.
            Pass "" to use the built-in default: an evidence-based reasoning specialist
            persona (skeptical, decision-oriented, appraises tool output as evidence).
        thinking_system_prompt: Optional custom thinking instructions that override the default
            thinking methodology. This controls HOW the agent thinks about the problem, separate
            from the system_prompt which controls the agent's persona/role. If omitted, the
            built-in evidence-based parallel methodology is used: decompose the thought,
            gather evidence with independent tool calls issued in parallel in a single turn,
            appraise and rate each result, synthesize, and end with a structured
            FINDINGS / CONFIDENCE / GAPS / NEXT ACTION handoff block. Prefer omitting this
            unless the task needs a specialized methodology.

    Returns:
        Dict containing status and response content in the format:
        {
            "status": "success|error",
            "content": [{"text": "Detailed thinking output across all cycles"}]
        }

        Success case: Returns concatenated results from all thinking cycles
        Error case: Returns information about what went wrong during processing
    """
    # activity_as_tool derives the ToolSpec from this signature + docstring
    # (FunctionToolMetadata, _temporal_activity_tool.py:28-31). The original's
    # tools/model_provider/model_settings/agent parameters are dropped from
    # the LLM-facing surface: there is no parent registry or provider zoo
    # across the activity boundary -- the session model is the model (see
    # module docstring).
    try:
        # Use provided system prompt or fall back to a default
        custom_system_prompt = system_prompt
        if not custom_system_prompt:
            custom_system_prompt = DEFAULT_THINK_SYSTEM_PROMPT

        if not thinking_system_prompt:
            thinking_system_prompt = DEFAULT_THINKING_SYSTEM_PROMPT

        model = await _session_model()

        # Create thought processor instance with the available context
        processor = ThoughtProcessor({})

        # Initialize variables for cycle processing
        current_thought = thought
        all_responses = []

        # Every chunk from every cycle rides THINKING_TOPIC, batched exactly
        # like the SDK model activity's streaming (200ms).
        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=THINK_STREAM_BATCH_INTERVAL,
        )
        # Topic name imported lazily to avoid a workflow<->activity import
        # cycle: workflow.py imports think for activity_as_tool.
        from workflow import THINKING_TOPIC

        topic = stream_client.topic(THINKING_TOPIC)
        async with stream_client:
            # Process through each cycle
            for cycle in range(1, cycle_count + 1):
                cycle_response = await processor.process_cycle(
                    current_thought,
                    cycle,
                    cycle_count,
                    custom_system_prompt,
                    model=model,
                    publish=topic.publish,
                    thinking_system_prompt=thinking_system_prompt,
                )

                # Store response
                all_responses.append({"cycle": cycle, "thought": current_thought, "response": cycle_response})

                # Update thought for next cycle based on current response
                current_thought = (
                    f"Previous cycle concluded: {cycle_response}\nContinue developing these ideas further."
                )

        # Combine all responses into final output
        final_output = "\n\n".join([f"Cycle {r['cycle']}/{cycle_count}:\n{r['response']}" for r in all_responses])

        # Return combined result
        return {
            "status": "success",
            "content": [{"text": final_output}],
        }

    except ApplicationError:
        raise
    except Exception as e:
        error_msg = f"Error in think tool: {str(e)}\n{traceback.format_exc()}"
        logger.error("Error in think tool: %s", e)
        return {
            "status": "error",
            "content": [{"text": error_msg}],
        }
