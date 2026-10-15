"""Durable chat.

The continue-as-new shape is the official sample
``strands_plugin/continue_as_new/workflow.py``. ``messages`` are the
conversation. ``stream_state`` is the WorkflowStream cursor the SDK's
continue-as-new recipe carries. ``model`` is a factory name because this
worker passes ``StrandsPlugin(models=...)``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from strands.types.content import Messages
from temporalio import workflow
from temporalio.contrib.strands import TemporalAgent
from temporalio.contrib.workflow_streams import WorkflowStream, WorkflowStreamState

from config import (
    DESKTOP_PROVISION_HEARTBEAT,
    DESKTOP_PROVISION_RETRY_POLICY,
    DESKTOP_PROVISION_TIMEOUT,
    DESKTOP_TEARDOWN_TIMEOUT,
    MCP_START_TO_CLOSE,
    MODEL_HEARTBEAT,
    MODEL_RETRY_POLICY,
    MODEL_START_TO_CLOSE,
    MODEL_STREAM_BATCH_INTERVAL,
    PERPLEXITY_MODEL_RETRY_POLICY,
    SELECTION_META_TOOLS,
    SELECTION_STARTER_TOOLS,
    THINK_STREAM_BATCH_INTERVAL,
)


with workflow.unsafe.imports_passed_through():
    from agent_tools import (
        DESKTOP_TOOL_NAMES,
        PERMANENT_COMMUNITY_TOOLS,
        desktop_activity_options,
        temporal_mcp_clients,
    )
    from browser_activity import desktop_control
    from desktop_hook import DesktopHook
    from desktop_provision_activity import provision_desktop, teardown_desktop
    from delegation_hook import DelegationHook
    from selection_hook import SelectionHook, TurnSelection
    from load_tool import (
        LoadedTools, catalog_tools, load_tool_activity,
        register_community_tool, replay_catalog,
    )

# Topic names shared with server.py. The model stream publishes on events.
EVENTS_TOPIC = "events"
THINKING_TOPIC = "thinking"
APPROVAL_TOPIC = "approval"
HANDOFF_TOPIC = "handoff"
TOOL_RESULTS_TOPIC = "tool_results"
AGENT_RUNS_TOPIC = "agent_runs"


@dataclass
class DesktopBinding:
    """The session's own Linux desktop: its task queue and viewer URL."""

    container: str
    queue: str
    novnc_url: str


@dataclass
class ChatInput:
    model_id: str
    system_prompt: str
    messages: Messages = field(default_factory=list)
    stream_state: WorkflowStreamState | None = None
    # Carried across Continue-As-New so rollover never re-provisions.
    desktop: DesktopBinding | None = None


@workflow.defn
class ChatWorkflow:
    @workflow.init
    def __init__(self, input: ChatInput) -> None:
        self._stream = WorkflowStream(prior_state=input.stream_state)
        self._model_id = input.model_id
        self._system_prompt = input.system_prompt
        self._lock = asyncio.Lock()
        self._agent: TemporalAgent | None = None
        self._selection = TurnSelection()
        self._loaded = LoadedTools()
        self._done = False
        self._forwarded_offset = input.stream_state.base_offset if input.stream_state else 0
        self._handoff_lock = asyncio.Lock()
        self._desktop = input.desktop
        self._desktop_lock = asyncio.Lock()

    @workflow.update
    async def desktop_control(self, action: str) -> dict:
        await self.ensure_desktop()
        options = desktop_activity_options(workflow.info().workflow_id)
        async with self._handoff_lock:
            if action == "give":
                await workflow.execute_activity(desktop_control, "prepare_resume", **options)
                action = "resume"
            return await workflow.execute_activity(desktop_control, action, **options)

    async def ensure_desktop(self) -> DesktopBinding:
        """Claim this session's desktop on first use; a no-op afterwards.

        Nothing claims eagerly: ``POST /sessions`` only starts the workflow, so
        a chat that never browses never pays for a container. The first browser
        tool call (``DesktopHook``) or a handoff update is what provisions, and
        concurrent first callers share one provisioning activity via the lock.
        """
        if self._desktop is None:
            async with self._desktop_lock:
                if self._desktop is None:
                    bound = await workflow.execute_activity(
                        provision_desktop,
                        workflow.info().workflow_id,
                        start_to_close_timeout=DESKTOP_PROVISION_TIMEOUT,
                        heartbeat_timeout=DESKTOP_PROVISION_HEARTBEAT,
                        retry_policy=DESKTOP_PROVISION_RETRY_POLICY,
                    )
                    self._desktop = DesktopBinding(**bound)
        return self._desktop

    @workflow.update
    async def desktop(self) -> dict:
        """The session's desktop binding, provisioning it if this is the first ask."""
        bound = await self.ensure_desktop()
        return {"container": bound.container, "queue": bound.queue, "novnc_url": bound.novnc_url}

    @workflow.query
    def desktop_url(self) -> str | None:
        """The claimed desktop's noVNC viewer, or None. Queries never provision."""
        return self._desktop.novnc_url if self._desktop is not None else None

    @workflow.update
    def truncate_stream(self, up_to_offset: int) -> None:
        self._stream.truncate(up_to_offset)
        self._forwarded_offset = max(self._forwarded_offset, up_to_offset)

    @workflow.update
    async def turn(
        self,
        prompt: str,
        tools: Sequence[str] | None = None,
        skills: Sequence[str] | None = None,
    ) -> str:
        await workflow.wait_condition(lambda: self._agent is not None)
        async with self._lock:
            assert self._agent is not None
            self._selection.tools = frozenset(tools or ())
            self._selection.skills = frozenset(skills or ())
            try:
                if self._selection.tools or self._selection.skills:
                    clauses = []
                    if self._selection.tools:
                        clauses.append(
                            "Selected tools for this turn: "
                            + ", ".join(sorted(self._selection.tools))
                            + "."
                        )
                    if self._selection.skills:
                        clauses.append(
                            "Selected skills: "
                            + ", ".join(sorted(self._selection.skills))
                            + "."
                        )
                    result = await self._agent.invoke_async(
                        [{"text": prompt}, {"text": " ".join(clauses)}],
                        invocation_state={"think_agent_factory": self._think_agent_factory},
                    )
                else:
                    result = await self._agent.invoke_async(
                        prompt,
                        invocation_state={"think_agent_factory": self._think_agent_factory},
                    )
            finally:
                self._selection.tools = frozenset()
                self._selection.skills = frozenset()
            return str(result).strip()

    @workflow.signal
    def end_chat(self) -> None:
        self._done = True

    @workflow.query
    def messages(self) -> Messages:
        return list(self._agent.messages) if self._agent else []

    @workflow.query
    def model_id(self) -> str:
        return self._model_id

    @workflow.query
    def loaded_tools(self) -> list[dict[str, str]]:
        return [{"path": path, "name": name} for name, path in self._loaded.paths.items()]

    def _think_agent_factory(self, **kwargs: object) -> TemporalAgent:
        """Nested Think agent on this session's model, without the think tool.

        The installed cycle calls this when ``model_provider`` is left empty.
        The model name is the session factory, never a model the tool picks.
        """
        tools = [
            tool
            for tool in (kwargs.get("tools") or [])
            if getattr(tool, "tool_name", None) != "think"
        ]
        parent = self._agent
        options = getattr(getattr(parent, "model", None), "_options", {}) or {}
        return TemporalAgent(
            model=self._model_id,
            messages=list(kwargs.get("messages") or []),
            tools=tools,
            system_prompt=kwargs.get("system_prompt") or "",
            trace_attributes=kwargs.get("trace_attributes") or {},
            streaming_topic=THINKING_TOPIC,
            streaming_batch_interval=THINK_STREAM_BATCH_INTERVAL,
            start_to_close_timeout=options.get("start_to_close_timeout", MODEL_START_TO_CLOSE),
            heartbeat_timeout=options.get("heartbeat_timeout", MODEL_HEARTBEAT),
            retry_policy=options.get("retry_policy", PERPLEXITY_MODEL_RETRY_POLICY),
        )

    @workflow.run
    async def run(self, input: ChatInput) -> None:
        is_gemini = input.model_id.startswith("gemini")
        session_id = workflow.info().workflow_id
        discover_tools, unload_tools = catalog_tools(self._loaded.paths)
        starters = {}
        for name in ("environment", "http_request", "journal"):
            result = await workflow.execute_activity(
                load_tool_activity,
                args=[name, name],
                start_to_close_timeout=MCP_START_TO_CLOSE,
                heartbeat_timeout=MODEL_HEARTBEAT,
                retry_policy=MODEL_RETRY_POLICY,
            )
            if result.get("status") != "success" or not result.get("tool_spec"):
                raise RuntimeError(f"Cannot register starter tool {name}: {result}")
            starters[name] = result
        self._agent = TemporalAgent(
            model=input.model_id,
            start_to_close_timeout=MODEL_START_TO_CLOSE,
            heartbeat_timeout=MODEL_HEARTBEAT,
            retry_policy=MODEL_RETRY_POLICY if is_gemini else PERPLEXITY_MODEL_RETRY_POLICY,
            streaming_batch_interval=MODEL_STREAM_BATCH_INTERVAL,
            streaming_topic=EVENTS_TOPIC,
            system_prompt=input.system_prompt,
            messages=list(input.messages),
            tools=[
                *(tool for tool in PERMANENT_COMMUNITY_TOOLS
                  if tool.tool_name in {"load_tool", "use_skill"}),
                discover_tools,
                unload_tools,
                *temporal_mcp_clients(),
            ],
            hooks=[self._loaded],
        )
        self._agent.hooks.add_hook(
            DelegationHook(self._agent.tool_registry.registry, self._loaded.paths)
        )
        mcp_tool_names = set(self._agent.tool_registry.registry) - set(
            SELECTION_STARTER_TOOLS
        ) - SELECTION_META_TOOLS
        self._agent.hooks.add_hook(SelectionHook(self._selection, mcp_tool_names))
        self._agent.hooks.add_hook(DesktopHook(DESKTOP_TOOL_NAMES, self.ensure_desktop))
        for name, result in starters.items():
            register_community_tool(self._agent, result["path"], name, result["tool_spec"])
        replay_catalog(self._agent, input.messages, self._loaded.paths)

        await workflow.wait_condition(
            lambda: self._done or workflow.info().is_continue_as_new_suggested()
        )
        self._stream.detach_pollers()
        await workflow.wait_condition(workflow.all_handlers_finished)
        if self._done or self._agent is None:
            # One container = one session, so a plain stop is sufficient here:
            # nothing else inherits the display, and all_handlers_finished has
            # already waited for an in-flight turn before end_chat reaches this.
            if self._desktop is not None:
                await workflow.execute_activity(
                    teardown_desktop,
                    session_id,
                    start_to_close_timeout=DESKTOP_TEARDOWN_TIMEOUT,
                    retry_policy=DESKTOP_PROVISION_RETRY_POLICY,
                )
            return
        self._stream.truncate(self._forwarded_offset)
        workflow.continue_as_new(
            ChatInput(
                model_id=self._model_id,
                system_prompt=self._system_prompt,
                messages=list(self._agent.messages),
                stream_state=self._stream.get_state(),
                desktop=self._desktop,
            )
        )
