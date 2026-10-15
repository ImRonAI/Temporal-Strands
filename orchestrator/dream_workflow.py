from temporalio import workflow
from temporalio.contrib.strands import TemporalAgent

with workflow.unsafe.imports_passed_through():
    from agent_tools import temporal_mcp_clients
    from config import (
        DREAM_MIN_INTERVAL,
        DREAM_MIN_SESSIONS,
        MCP_START_TO_CLOSE,
        MODEL_HEARTBEAT,
        MODEL_RETRY_POLICY,
        MODEL_START_TO_CLOSE,
    )
    from load_tool import load_tool_activity, register_community_tool


DREAM_PROMPT = f"""Consolidate durable session evidence in this order:

1. Read gwen-memory/index.md. If gwen-memory has never been embedded, call
   create_embeddings once for that bucket. Do not call it for each memory file.
2. Read gwen-memory/dream-cursor.md, then use the journal tool to read journal
   entries newer than the date in that cursor.
3. List gwen-offload/sessions/ for prefixes newer than that cursor and read
   those objects. Count distinct new sessions across the journal and offload
   evidence, counting a session appearing in both only once.
4. Stop without writing when the cursor is newer than DREAM_MIN_INTERVAL
   ({DREAM_MIN_INTERVAL.total_seconds():g} seconds) relative to the supplied
   current time AND the new session count is below DREAM_MIN_SESSIONS
   ({DREAM_MIN_SESSIONS}). Both conditions must hold to stop.
5. Write or replace separate memory files in gwen-memory. A session may produce
   several files, or none. One file is one decision, one preference, one skill
   reflection, or one verified fact. Each file names the session id and the
   offload key supporting it.
6. Replace an existing memory file when the new sessions contradict it.
7. Rewrite gwen-memory/index.md and gwen-memory/dream-cursor.md to describe
   the resulting memories and the evidence processed in this pass.
8. Do not modify cwd()/journal or gwen-offload. Do not promote a to-do or a raw
   tool body into durable memory.
"""

DREAM_TASK = "Perform one dream pass using the available journal and MCP tools."


@workflow.defn
class DreamWorkflow:
    @workflow.run
    async def run(self, model_id: str) -> str:
        journal = await workflow.execute_activity(
            load_tool_activity,
            args=["journal", "journal"],
            start_to_close_timeout=MCP_START_TO_CLOSE,
            heartbeat_timeout=MODEL_HEARTBEAT,
            retry_policy=MODEL_RETRY_POLICY,
        )
        if journal.get("status") != "success" or not journal.get("tool_spec"):
            raise RuntimeError(f"Cannot register dream journal tool: {journal}")
        agent = TemporalAgent(
            model=model_id,
            system_prompt=DREAM_PROMPT,
            tools=[*temporal_mcp_clients()],
            start_to_close_timeout=MODEL_START_TO_CLOSE,
            heartbeat_timeout=MODEL_HEARTBEAT,
            retry_policy=MODEL_RETRY_POLICY,
        )
        register_community_tool(agent, journal["path"], "journal", journal["tool_spec"])
        result = await agent.invoke_async(
            f"{DREAM_TASK}\nCurrent time: {workflow.now().isoformat()}"
        )
        return str(result)
