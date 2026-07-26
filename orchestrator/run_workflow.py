"""Internal smoke test for the worker — not part of the app. The real
interface is server.py's HTTP API, used by the Next.js chat UI.

Usage:
    python run_workflow.py "Your prompt here" [model_id]
"""

import asyncio
import os
import sys
import uuid

from temporalio.client import Client
from temporalio.contrib.strands import StrandsPlugin

from workflow import ChatInput, ChatWorkflow, DEFAULT_MODEL, TurnInput


async def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "What is the capital of France?"
    model_id = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL

    # StrandsPlugin belongs on the client as well as the worker — it installs
    # the pydantic data converter and the StrandsFailureConverter, and both
    # ends of a workflow must agree on them. Without it this script would
    # encode ChatInput and decode the update result with the default
    # converter while the worker used the pydantic one. No models= mapping:
    # factories are only ever invoked inside the worker's model activity.
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        plugins=[StrandsPlugin()],
    )

    session_id = f"chat-{uuid.uuid4().hex}"
    await client.start_workflow(
        ChatWorkflow.run,
        ChatInput(model_id=model_id),
        id=session_id,
        task_queue="perplexity-orchestrator",
    )

    handle = client.get_workflow_handle(session_id)
    reply = await handle.execute_update(ChatWorkflow.turn, TurnInput(prompt=prompt))
    print(reply)

    await handle.signal(ChatWorkflow.end_chat)


if __name__ == "__main__":
    asyncio.run(main())
