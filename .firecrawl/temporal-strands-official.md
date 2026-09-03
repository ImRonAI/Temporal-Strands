[Skip to main content](https://temporal.io/comms#main-content)

sessionId: 1788136404414

userId:

deviceId: 8be9617e-3352-4f46-a4f7-3270862def76

UpdateResetUpdate User IDUpdate Device ID

[Skip to main content](https://docs.temporal.io/develop/python/integrations/strands-agents#__docusaurus_skipToContent_fallback)

[![Temporal logo](https://docs.temporal.io/img/assets/temporal-logo-dark.svg)](https://temporal.io/)[Home](https://docs.temporal.io/) [Courses](https://learn.temporal.io/getting_started/) [SDKs](https://docs.temporal.io/develop) [Durable AI](https://docs.temporal.io/ai) [Code Exchange](https://temporal.io/code-exchange) [Temporal Cloud](https://docs.temporal.io/cloud)

Ask AI

Search`Ctrl`  `K`

- [Home](https://docs.temporal.io/)
- [Quickstarts](https://docs.temporal.io/quickstarts)
- [Evaluate](https://docs.temporal.io/evaluate/)

- [Develop](https://docs.temporal.io/develop/)

  - [Go SDK](https://docs.temporal.io/develop/go/)

  - [Java SDK](https://docs.temporal.io/develop/java/)

  - [.NET SDK](https://docs.temporal.io/develop/dotnet/)

  - [PHP SDK](https://docs.temporal.io/develop/php/)

  - [Python SDK](https://docs.temporal.io/develop/python/)

    - [Quickstart](https://docs.temporal.io/develop/python/set-up-your-local-python)
    - [Workflows](https://docs.temporal.io/develop/python/workflows/)

    - [Activities](https://docs.temporal.io/develop/python/activities/)

    - [Workers](https://docs.temporal.io/develop/python/workers/)

    - [Client](https://docs.temporal.io/develop/python/client/)

    - [Nexus](https://docs.temporal.io/develop/python/nexus/)

    - [Platform](https://docs.temporal.io/develop/python/platform/)

    - [Best practices](https://docs.temporal.io/develop/python/best-practices/)

    - [Integrations](https://docs.temporal.io/develop/python/integrations/)

      - [Deep Agents](https://docs.temporal.io/develop/python/integrations/deepagents)
      - [Google ADK](https://docs.temporal.io/develop/python/integrations/google-adk)
      - [Google GenAI](https://docs.temporal.io/develop/python/integrations/google-genai)
      - [LangGraph](https://docs.temporal.io/develop/python/integrations/langgraph)
      - [LangSmith](https://docs.temporal.io/develop/python/integrations/langsmith)
      - [OpenAI Agents SDK integration](https://docs.temporal.io/develop/python/integrations/openai-agents)
      - [Strands Agents](https://docs.temporal.io/develop/python/integrations/strands-agents)
  - [Ruby SDK](https://docs.temporal.io/develop/ruby/)

  - [Rust SDK](https://docs.temporal.io/develop/rust/)

  - [TypeScript SDK](https://docs.temporal.io/develop/typescript/)

  - [Environment configuration](https://docs.temporal.io/develop/environment-configuration)
  - [Worker performance](https://docs.temporal.io/develop/worker-performance/)

  - [Safe deployments](https://docs.temporal.io/develop/safe-deployments)
  - [Plugins guide](https://docs.temporal.io/develop/plugins-guide)
  - [Task queue priority and fairness](https://docs.temporal.io/develop/task-queue-priority-fairness)
- [Temporal Cloud](https://docs.temporal.io/cloud)

- [Deploy to production](https://docs.temporal.io/production-deployment)

- [CLI (temporal)](https://docs.temporal.io/cli)

- [Design Patterns](https://docs.temporal.io/design-patterns)

- [Durable AI](https://docs.temporal.io/ai)

- [References](https://docs.temporal.io/references/)

- [Troubleshooting](https://docs.temporal.io/troubleshooting/)

- [Best practices](https://docs.temporal.io/best-practices/)

- [Encyclopedia](https://docs.temporal.io/encyclopedia/)

- [Interactive Demos](https://docs.temporal.io/develop/python/integrations/strands-agents#)

- [Guides](https://docs.temporal.io/guides)

- [Integrations](https://docs.temporal.io/integrations)
- [Glossary](https://docs.temporal.io/glossary)
- [Develop with AI](https://docs.temporal.io/with-ai)

- [Home page](https://docs.temporal.io/)
- [Develop](https://docs.temporal.io/develop/)
- [Python SDK](https://docs.temporal.io/develop/python/)
- [Integrations](https://docs.temporal.io/develop/python/integrations/)
- Strands Agents

On this page

For the complete documentation index, see [/llms.txt](https://docs.temporal.io/llms.txt).This page is also available as raw Markdown at [/develop/python/integrations/strands-agents.md](https://docs.temporal.io/develop/python/integrations/strands-agents.md). Append`.md` to any page URL to fetch its Markdown.

# Strands Agents integration

Copy for LLM [View Markdown](https://docs.temporal.io/develop/python/integrations/strands-agents.md "View this page as Markdown")

[Open in ChatGPT](https://chatgpt.com/?prompt=Read%20https%3A%2F%2Fdocs.temporal.io%2Fdevelop%2Fpython%2Fintegrations%2Fstrands-agents.md%20and%20answer%20questions%20about%20the%20content. "Open in ChatGPT")[Open in Claude](https://claude.ai/new?q=Read%20https%3A%2F%2Fdocs.temporal.io%2Fdevelop%2Fpython%2Fintegrations%2Fstrands-agents.md%20and%20answer%20questions%20about%20the%20content. "Open in Claude")

Temporal's integration with [Strands Agents](https://strandsagents.com/) is an [SDK Plugin](https://docs.temporal.io/develop/plugins-guide) that
gives your Strands agents [Durable Execution](https://docs.temporal.io/temporal#durable-execution) via the Temporal platform. The plugin routes
model invocations, tool calls, MCP tool calls, and hooks through Temporal Activities, so every step your agent takes is
recorded in Workflow history and can survive crashes, restarts, and infrastructure failures.

[Currently in: Public Preview](https://docs.temporal.io/evaluate/development-production-features/release-stages#public-preview)

APIs and configuration may change before the stable release.

Code snippets in this guide are taken from the
[Strands Agents plugin samples](https://github.com/temporalio/samples-python/tree/main/strands_plugin). Refer to the
samples for the complete code.

## Get started [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#get-started "Direct link to Get started")

Install the plugin, then run a minimal Strands agent inside a Temporal Workflow.

### Prerequisites [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#prerequisites "Direct link to Prerequisites")

- This guide assumes you are already familiar with Strands Agents. If you are not, refer to the
[Strands Agents documentation](https://strandsagents.com/) for more details.
- If you are new to Temporal, read [Understanding Temporal](https://docs.temporal.io/evaluate/understanding-temporal) or take the
[Temporal 101](https://learn.temporal.io/courses/temporal_101/) course.
- Set up your local development environment by following the
[Set up your local development environment](https://docs.temporal.io/develop/python/set-up-your-local-python) guide. Leave the Temporal
development server running if you want to test your code locally.

### Install the plugin [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#install-the-plugin "Direct link to Install the plugin")

Install the Temporal Python SDK with Strands Agents support (requires `temporalio` 1.28.0 or later):

```bash
uv add "temporalio[strands-agents]"
```

or with pip:

```bash
pip install "temporalio[strands-agents]"
```

### Run a Strands agent with Durable Execution [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#run-a-strands-agent-with-durable-execution "Direct link to Run a Strands agent with Durable Execution")

The following example runs a Strands agent inside a Temporal Workflow. Model calls execute as Temporal Activities, which
means they get automatic retries, timeouts, and durable execution. If the Worker process crashes mid-conversation,
Temporal replays the Workflow and resumes from the last completed Activity.

**1\. Define the Workflow**

Create a Workflow that holds a `TemporalAgent` and invokes it with a prompt. The `start_to_close_timeout` sets the
maximum time each model call Activity can run:

[strands\_plugin/hello\_world/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/hello_world/workflow.py)

```py
from datetime import timedelta

from temporalio import workflow

from temporalio.contrib.strands import TemporalAgent

@workflow.defn

class HelloWorldWorkflow:

    def __init__(self) -> None:

        self.agent = TemporalAgent(start_to_close_timeout=timedelta(seconds=60))

    @workflow.run

    async def run(self, prompt: str) -> str:

        result = await self.agent.invoke_async(prompt)

        return str(result)
```

caution

Inside a Workflow, always call `agent.invoke_async(message)`, not `agent(message)`. The synchronous form spawns a worker
thread, which the Workflow sandbox blocks.

**2\. Start a Worker**

Create a Worker that registers the Workflow and the `StrandsPlugin`. The plugin automatically registers the Activities
that handle model calls:

[strands\_plugin/hello\_world/run\_worker.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/hello_world/run_worker.py)

```py
import asyncio

import os

from temporalio.client import Client

from temporalio.contrib.strands import StrandsPlugin

from temporalio.worker import Worker

from strands_plugin.hello_world.workflow import HelloWorldWorkflow

async def main() -> None:

    plugin = StrandsPlugin()

    client = await Client.connect(

        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),

        plugins=[plugin],

    )

    worker = Worker(

        client,

        task_queue="strands-hello-world",

        workflows=[HelloWorldWorkflow],

    )

    print("Worker started. Ctrl+C to exit.")

    await worker.run()

if __name__ == "__main__":

    asyncio.run(main())
```

**3\. Run the Workflow**

Start the Workflow from a separate client script. This example sends the prompt "Write a haiku about durable execution"
and prints the agent's response:

[strands\_plugin/hello\_world/run\_workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/hello_world/run_workflow.py)

```py
import asyncio

import os

from temporalio.client import Client

from strands_plugin.hello_world.workflow import HelloWorldWorkflow

async def main() -> None:

    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"))

    result = await client.execute_workflow(

        HelloWorldWorkflow.run,

        "Write a haiku about durable execution.",

        id="strands-hello-world",

        task_queue="strands-hello-world",

    )

    print(f"Result: {result}")

if __name__ == "__main__":

    asyncio.run(main())
```

## Build the agent [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#build-the-agent "Direct link to Build the agent")

Customize which model provider your agent uses, add tools that run as Activities, subscribe to lifecycle events with
hooks, and connect to MCP servers.

### Choose and configure models [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#choose-and-configure-models "Direct link to Choose and configure models")

By default, `StrandsPlugin` uses Strands' own default model (`BedrockModel`). To use a different model, pass a `models`
mapping to `StrandsPlugin` on the Worker. When you provide a custom `models` mapping, each `TemporalAgent` must specify
which model to use by name.

Each entry in the mapping pairs a name with a factory function that creates a model provider (such as `AnthropicModel`
or `BedrockModel`). The provider is created on first use and reused for the Worker's lifetime:

```python
from strands.models.anthropic import AnthropicModel

from strands.models.bedrock import BedrockModel

# Workflow

@workflow.defn

class MultiModelWorkflow:

    def __init__(self) -> None:

        self.agent_a = TemporalAgent(

            model="claude",

            start_to_close_timeout=timedelta(seconds=60),

        )

        self.agent_b = TemporalAgent(

            model="bedrock",

            start_to_close_timeout=timedelta(seconds=60),

        )

# Worker

Worker(..., plugins=[StrandsPlugin(models={\
\
    "claude": lambda: AnthropicModel(client_args={"api_key": "..."}),\
\
    "bedrock": lambda: BedrockModel(),\
\
})])
```

Each `TemporalAgent` carries its own Activity options (timeouts, retry policy, task queue, streaming topic) and
dispatches to a shared model Activity, which resolves the model name against the registered factories at runtime. A
model name not present in the `models` mapping raises `ValueError` inside the Activity.

### Run non-deterministic tools as Activities [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#run-non-deterministic-tools-as-activities "Direct link to Run non-deterministic tools as Activities")

Strands tools that perform I/O, access external services, or produce non-deterministic results need to run as Temporal
Activities rather than inline in the Workflow. Wrap each tool in an `@activity.defn` function, register the Activities
on the Worker, and pass them to the agent using `activity_as_tool`.

Define an Activity for the tool:

[strands\_plugin/tools/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/tools/workflow.py)

```py
@activity.defn

async def fetch_weather(city: str) -> dict:

    """Stub weather lookup — replace with a real HTTP call in production."""

    return {

        "city": city,

        "temperature_f": 72,

        "conditions": "sunny",

    }
```

Pass the Activity to the agent in the Workflow using `activity_as_tool`:

[strands\_plugin/tools/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/tools/workflow.py)

```py
@workflow.defn

class ToolsWorkflow:

    def __init__(self) -> None:

        self.agent = TemporalAgent(

            start_to_close_timeout=timedelta(seconds=60),

            tools=[\
\
                letter_counter,\
\
                activity_as_tool(\
\
                    fetch_weather,\
\
                    start_to_close_timeout=timedelta(seconds=30),\
\
                ),\
\
                activity_as_tool(\
\
                    environment_activity,\
\
                    start_to_close_timeout=timedelta(seconds=30),\
\
                ),\
\
            ],

        )

    @workflow.run

    async def run(self, prompt: str) -> str:

        result = await self.agent.invoke_async(prompt)

        return str(result)
```

Register the Activity functions on the Worker:

[strands\_plugin/tools/run\_worker.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/tools/run_worker.py)

```py
import asyncio

import os

from temporalio.client import Client

from temporalio.contrib.strands import StrandsPlugin

from temporalio.worker import Worker

from strands_plugin.tools.workflow import (

    ToolsWorkflow,

    environment_activity,

    fetch_weather,

)

async def main() -> None:

    plugin = StrandsPlugin()

    client = await Client.connect(

        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),

        plugins=[plugin],

    )

    worker = Worker(

        client,

        task_queue="strands-tools",

        workflows=[ToolsWorkflow],

        activities=[fetch_weather, environment_activity],

    )

    print("Worker started. Ctrl+C to exit.")

    await worker.run()

if __name__ == "__main__":

    asyncio.run(main())
```

If you are using built-in `strands_tools`, wrap them in a thin async function decorated with `@activity.defn` so they
run as Temporal Activities.

### React to agent lifecycle events [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#react-to-agent-lifecycle-events "Direct link to React to agent lifecycle events")

Strands' [hook system](https://strandsagents.com/docs/user-guide/concepts/agents/hooks/) lets you subscribe callbacks to events in the agent lifecycle, such
as invocation start/end, model call before/after, tool call before/after, and message added. Use hooks to add logging,
metrics, or custom logic at each stage.

Pass `hooks=[MyHookProvider()]` to `TemporalAgent`. Hook callbacks fire in Workflow context, so deterministic callbacks
work without any extra setup.

For callbacks that need I/O (audit logging, metrics, alerting), use `activity_as_hook` to dispatch the work as a
Temporal Activity. The following example shows both patterns in one `HookProvider`. The `_record` callback runs in
Workflow context (deterministic), while `persist_tool_call` runs as an Activity (I/O-safe):

[strands\_plugin/hooks/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/hooks/workflow.py)

```py
@activity.defn

async def persist_tool_call(tool_name: str) -> None:

    # In production, write to a database / S3 / your audit pipeline.

    activity.logger.info(f"audit: tool {tool_name} completed")
```

[strands\_plugin/hooks/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/hooks/workflow.py)

```py
class AuditHook(HookProvider):

    def __init__(self) -> None:

        self.fired: list[str] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:

        registry.add_callback(AfterToolCallEvent, self._record)

        registry.add_callback(

            AfterToolCallEvent,

            activity_as_hook(

                persist_tool_call,

                activity_input=lambda event: event.tool_use["name"],

                start_to_close_timeout=timedelta(seconds=15),

            ),

        )

    def _record(self, event: AfterToolCallEvent) -> None:

        self.fired.append(event.tool_use["name"])
```

caution

Hook callbacks run in Workflow context, so they must be
[deterministic](https://docs.temporal.io/develop/python/workflows/basics#workflow-logic-requirements). Do not use `time.time()`, `uuid.uuid4()`,
or I/O inside hook callbacks. Use `activity_as_hook` for anything that requires I/O.

The `activity_input` parameter extracts serializable values from the event to pass as the Activity's input. Use a
dataclass or Pydantic model for multiple values. This is needed because hook events hold references to `Agent`,
`AgentTool` instances, and other objects that cannot cross the Activity boundary.

### Connect to MCP servers [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#connect-to-mcp-servers "Direct link to Connect to MCP servers")

If your agent needs access to tools provided by an [MCP](https://modelcontextprotocol.io/) server, configure the MCP
clients on the Worker and reference them by name in the Workflow.

`StrandsPlugin(mcp_clients=...)` takes a mapping of `name` to `MCPClient` factory, mirroring the `models` pattern. The
plugin registers a per-server Activity and connects at Worker startup to enumerate available tools. In the Workflow,
`TemporalMCPClient(server="name")` is a handle that references the server by name and carries per-call Activity options.

Define the Workflow with a `TemporalMCPClient`:

[strands\_plugin/mcp/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/mcp/workflow.py)

```py
from datetime import timedelta

from temporalio import workflow

from temporalio.contrib.strands import TemporalAgent, TemporalMCPClient

@workflow.defn

class MCPWorkflow:

    def __init__(self) -> None:

        echo = TemporalMCPClient(

            server="echo",

            start_to_close_timeout=timedelta(seconds=30),

        )

        self.agent = TemporalAgent(

            start_to_close_timeout=timedelta(seconds=60),

            tools=[echo],

        )

    @workflow.run

    async def run(self, prompt: str) -> str:

        result = await self.agent.invoke_async(prompt)

        return str(result)
```

Register the MCP client factory on the Worker:

[strands\_plugin/mcp/run\_worker.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/mcp/run_worker.py)

```py
# ...

from mcp import StdioServerParameters, stdio_client

from strands.tools.mcp.mcp_client import MCPClient

from temporalio.client import Client

from temporalio.contrib.strands import StrandsPlugin

from temporalio.worker import Worker

# ...

def _make_echo_client() -> MCPClient:

    return MCPClient(

        lambda: stdio_client(

            StdioServerParameters(

                command=sys.executable,

                args=[str(ECHO_SERVER)],

            )

        )

    )

# ...

async def main() -> None:

    plugin = StrandsPlugin(mcp_clients={"echo": _make_echo_client})

    client = await Client.connect(

        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),

        plugins=[plugin],

    )

    worker = Worker(

        client,

        task_queue="strands-mcp",

        workflows=[MCPWorkflow],

    )

    print("Worker started. Ctrl+C to exit.")

    await worker.run()
```

Each factory returns a fully configured `MCPClient`, so you can pass options like `tool_filters`, `prefix`,
`elicitation_callback`, or `tasks_config` to it.

info

The plugin connects to each MCP server once at Worker startup to enumerate tools. The schema is frozen for the Worker's
lifetime. Restart Workers to pick up MCP server changes. If a server is unavailable at startup, the Worker fails to
start.

## Interact with the agent [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#interact-with-the-agent "Direct link to Interact with the agent")

Control the shape of agent responses, stream output in real time, and pause the agent for human approval.

### Add human approval gates [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#add-human-approval-gates "Direct link to Add human approval gates")

Some agent actions, such as deleting resources or sending messages, may require human approval before proceeding.
Strands offers two ways to interrupt an agent and wait for a response. Both work with the plugin.

In each case, `agent.invoke_async()` returns `AgentResult(stop_reason="interrupt", interrupts=[...])` instead of
raising. Pair this with a Signal handler that supplies responses, then resume by calling
`agent.invoke_async(responses)`.

#### Interrupt from a hook [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#interrupt-from-a-hook "Direct link to Interrupt from a hook")

A hook on an interruptible event such as `BeforeToolCallEvent` can pause the agent by calling
`event.interrupt(name, reason=...)`. The hook runs in Workflow context, so it must be deterministic.

Define the approval hook:

[strands\_plugin/human\_in\_the\_loop/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/human_in_the_loop/workflow.py)

```py
class ApprovalHook(HookProvider):

    def register_hooks(self, registry: HookRegistry, **kwargs: object) -> None:

        registry.add_callback(BeforeToolCallEvent, self._gate)

    def _gate(self, event: BeforeToolCallEvent) -> None:

        if event.tool_use["name"] != "delete_file":

            return

        approval = event.interrupt(

            "approval",

            reason=f"approve delete of {event.tool_use['input']['path']}?",

        )

        if approval != "approve":

            event.cancel_tool = "denied"
```

The Workflow waits for a Signal carrying the approval response, then resumes the agent:

[strands\_plugin/human\_in\_the\_loop/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/human_in_the_loop/workflow.py)

```py
@workflow.defn

class HumanInTheLoopWorkflow:

    def __init__(self) -> None:

        self.agent = TemporalAgent(

            start_to_close_timeout=timedelta(seconds=60),

            tools=[delete_file],

            hooks=[ApprovalHook()],

        )

        self._approval: str | None = None

        self._pending_reason: str | None = None

    @workflow.signal

    def approve(self, response: str) -> None:

        self._approval = response

    @workflow.query

    def pending_approval(self) -> str | None:

        return self._pending_reason

    @workflow.run

    async def run(self, prompt: str) -> str:

        result = await self.agent.invoke_async(prompt)

        while result.stop_reason == "interrupt":

            interrupts = list(result.interrupts or [])

            self._pending_reason = interrupts[0].reason if interrupts else None

            await workflow.wait_condition(lambda: self._approval is not None)

            response = self._approval

            self._approval = None

            self._pending_reason = None

            responses: list[InterruptResponseContent] = [\
\
                {"interruptResponse": {"interruptId": i.id, "response": response}}\
\
                for i in interrupts\
\
            ]

            result = await self.agent.invoke_async(responses)

        return str(result)
```

#### Interrupt from a tool [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#interrupt-from-a-tool "Direct link to Interrupt from a tool")

A `@strands.tool` function can raise `InterruptException(Interrupt(...))` directly. The agent stops with the interrupt,
and the Workflow handles the resume the same way as for hooks:

```python
from strands import tool

from strands.interrupt import Interrupt, InterruptException

@tool

def delete_thing(name: str) -> str:

    raise InterruptException(

        Interrupt(id=f"delete:{name}", name="approval", reason=f"delete {name}?")

    )
```

The same approach works from an `activity_as_tool`-wrapped Activity. The plugin's failure converter preserves the
`Interrupt` payload across the Activity boundary, so `AgentResult.interrupts` is populated the same way.

Define the Activity that raises the interrupt:

[strands\_plugin/activity\_interrupt/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/activity_interrupt/workflow.py)

```py
@activity.defn

async def delete_thing(name: str) -> str:

    if name not in _APPROVED:

        _APPROVED.add(name)

        raise InterruptException(

            Interrupt(

                id=f"delete:{name}",

                name="approval",

                reason=f"approve delete of protected resource '{name}'?",

            )

        )

    return f"deleted {name}"
```

caution

Activity-tool interrupts rely on the plugin's failure converter, which is installed via the client's data converter.
Attach `StrandsPlugin` to the **client** (not just the Worker) for Activity-tool interrupts to work.

Workers built from that client pick up the plugin automatically:

[strands\_plugin/activity\_interrupt/run\_worker.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/activity_interrupt/run_worker.py)

```py
import asyncio

import os

from temporalio.client import Client

from temporalio.contrib.strands import StrandsPlugin

from temporalio.worker import Worker

from strands_plugin.activity_interrupt.workflow import (

    ActivityInterruptWorkflow,

    delete_thing,

)

async def main() -> None:

    plugin = StrandsPlugin()

    # The plugin MUST be on the client so its failure converter is installed.

    client = await Client.connect(

        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),

        plugins=[plugin],

    )

    worker = Worker(

        client,

        task_queue="strands-activity-interrupt",

        workflows=[ActivityInterruptWorkflow],

        activities=[delete_thing],

    )

    print("Worker started. Ctrl+C to exit.")

    await worker.run()

if __name__ == "__main__":

    asyncio.run(main())
```

### Return structured data from an agent [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#return-structured-data-from-an-agent "Direct link to Return structured data from an agent")

To have the agent return a typed object instead of free-form text, pass a `structured_output_model` to `TemporalAgent`.
The plugin defaults to the [`pydantic_data_converter`](https://docs.temporal.io/develop/python/data-handling/data-conversion), so Pydantic types
serialize cleanly across the Activity and Workflow boundary:

[strands\_plugin/structured\_output/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/structured_output/workflow.py)

```py
from datetime import timedelta

from pydantic import BaseModel, Field

from temporalio import workflow

from temporalio.contrib.strands import TemporalAgent

class PersonInfo(BaseModel):

    name: str = Field(description="Name of the person")

    age: int = Field(description="Age of the person")

    occupation: str = Field(description="Occupation of the person")

@workflow.defn

class StructuredOutputWorkflow:

    def __init__(self) -> None:

        self.agent = TemporalAgent(

            start_to_close_timeout=timedelta(seconds=60),

            structured_output_model=PersonInfo,

        )

    @workflow.run

    async def run(self, prompt: str) -> PersonInfo:

        result = await self.agent.invoke_async(prompt)

        assert isinstance(result.structured_output, PersonInfo)

        return result.structured_output
```

### Stream agent output to clients [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#stream-agent-output-to-clients "Direct link to Stream agent output to clients")

For long-running agent calls, you may want to forward model output chunks to an external consumer as they arrive rather
than waiting for the full response.

Pass `streaming_topic="..."` to `TemporalAgent` and host a `WorkflowStream` on the Workflow. Each `StreamEvent` is
published from inside the model Activity. Subscribers read events through `WorkflowStreamClient`. Chunks are batched on
`streaming_batch_interval` (default 100 ms).

Define the Workflow with a `WorkflowStream` and a streaming topic:

[strands\_plugin/streaming/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/streaming/workflow.py)

```py
from datetime import timedelta

from temporalio import workflow

from temporalio.contrib.strands import TemporalAgent

from temporalio.contrib.workflow_streams import WorkflowStream

@workflow.defn

class StreamingWorkflow:

    def __init__(self) -> None:

        self.stream = WorkflowStream()

        self.agent = TemporalAgent(

            start_to_close_timeout=timedelta(seconds=60),

            streaming_topic="events",

        )

    @workflow.run

    async def run(self, prompt: str) -> str:

        result = await self.agent.invoke_async(prompt)

        return str(result)
```

Subscribe to the stream from a client:

[strands\_plugin/streaming/run\_workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/streaming/run_workflow.py)

```py
import asyncio

import os

from datetime import timedelta

from strands.types.streaming import StreamEvent

from temporalio.client import Client

from temporalio.contrib.workflow_streams import WorkflowStreamClient

from strands_plugin.streaming.workflow import StreamingWorkflow

async def main() -> None:

    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"))

    workflow_id = "strands-streaming"

    handle = await client.start_workflow(

        StreamingWorkflow.run,

        "Count from 1 to 5, one number per sentence.",

        id=workflow_id,

        task_queue="strands-streaming",

    )

    async def consume() -> None:

        stream = WorkflowStreamClient.create(client, workflow_id)

        async for item in stream.subscribe(

            ["events"],

            from_offset=0,

            result_type=StreamEvent,

            poll_cooldown=timedelta(milliseconds=50),

        ):

            event: StreamEvent = item.data

            if "contentBlockDelta" in event:

                delta = event["contentBlockDelta"].get("delta", {})

                if "text" in delta:

                    print(delta["text"], end="", flush=True)

            elif "messageStop" in event:

                print()

                return

    consume_task = asyncio.create_task(consume())

    result = await handle.result()

    await asyncio.wait_for(consume_task, timeout=10.0)

    print(f"Final result: {result}")

if __name__ == "__main__":

    asyncio.run(main())
```

## Run in production [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#run-in-production "Direct link to Run in production")

Configure retry policies, handle long-running chat sessions, and add distributed tracing.

### Configure retries [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#configure-retries "Direct link to Configure retries")

`TemporalAgent` disables Strands' built-in `ModelRetryStrategy` so that retries are handled exclusively by Temporal.
Configure retries with `retry_policy` on `TemporalAgent` for model calls, and on the Activity options accepted by
`activity_as_tool`, `activity_as_hook`, and `TemporalMCPClient` for their respective calls:

```python
from temporalio.common import RetryPolicy

TemporalAgent(

    start_to_close_timeout=timedelta(seconds=60),

    retry_policy=RetryPolicy(maximum_attempts=3),

)
```

Passing `retry_strategy=...` to `TemporalAgent(...)` raises `ValueError`. Remove the argument (or pass
`retry_strategy=None`) and use `retry_policy` instead.

### Handle long-running chat sessions [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#handle-long-running-chat-sessions "Direct link to Handle long-running chat sessions")

A chat-style Workflow accumulates message history with every turn. Over a long session, the Workflow's event history can
grow large enough to hit Temporal's per-Workflow history limit. To avoid this, use
[Continue-as-New](https://docs.temporal.io/develop/python/workflows/continue-as-new) to start a fresh Workflow execution while carrying the
agent's message history forward as input.

In this example, each user turn arrives as a Workflow [Update](https://docs.temporal.io/develop/python/workflows/message-passing#updates), so
the caller gets the agent's reply back from the same call. The `run` method creates the agent, then waits until either
the chat ends or Temporal suggests continue-as-new. When it does, the Workflow drains any in-flight updates and starts a
fresh execution with the agent's accumulated messages:

[strands\_plugin/continue\_as\_new/workflow.py](https://github.com/temporalio/samples-python/blob/main/strands_plugin/continue_as_new/workflow.py)

```py
import asyncio

from dataclasses import dataclass, field

from datetime import timedelta

from strands.types.content import Messages

from temporalio import workflow

from temporalio.contrib.strands import TemporalAgent

@dataclass

class ChatInput:

    messages: Messages = field(default_factory=list)

@workflow.defn

class ChatWorkflow:

    def __init__(self) -> None:

        self._done = False

        self._lock = asyncio.Lock()

        self._agent: TemporalAgent | None = None

    @workflow.update

    async def turn(self, prompt: str) -> str:

        await workflow.wait_condition(lambda: self._agent is not None)

        async with self._lock:

            assert self._agent is not None

            result = await self._agent.invoke_async(prompt)

            return str(result).strip()

    @workflow.signal

    def end_chat(self) -> None:

        self._done = True

    @workflow.query

    def messages(self) -> Messages:

        return list(self._agent.messages) if self._agent else []

    @workflow.run

    async def run(self, input: ChatInput) -> None:

        self._agent = TemporalAgent(

            start_to_close_timeout=timedelta(seconds=60),

            messages=list(input.messages),

        )

        await workflow.wait_condition(

            lambda: self._done or workflow.info().is_continue_as_new_suggested()

        )

        await workflow.wait_condition(workflow.all_handlers_finished)

        if not self._done:

            workflow.continue_as_new(ChatInput(messages=self._agent.messages))
```

### Add tracing with OpenTelemetry [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#add-tracing-with-opentelemetry "Direct link to Add tracing with OpenTelemetry")

To get distributed traces across model, tool, and MCP Activities, combine `StrandsPlugin` with the
[OpenTelemetry plugin](https://docs.temporal.io/develop/python/platform/observability#tracing). Register `OpenTelemetryPlugin` on the client and
`StrandsPlugin` on the Worker. Workers built from that client pick up the OpenTelemetry plugin automatically:

```python
import opentelemetry.trace

from temporalio.client import Client

from temporalio.contrib.opentelemetry import OpenTelemetryPlugin, create_tracer_provider

from temporalio.contrib.strands import StrandsPlugin

from temporalio.worker import Worker

opentelemetry.trace.set_tracer_provider(create_tracer_provider())

client = await Client.connect("localhost:7233", plugins=[OpenTelemetryPlugin()])

Worker(

    client,

    task_queue="strands",

    workflows=[MyWorkflow],

    plugins=[StrandsPlugin()],

)
```

Set the tracer provider before connecting the client.

### Snapshots are not supported [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#snapshots-are-not-supported "Direct link to Snapshots are not supported")

`TemporalAgent.take_snapshot()` and `TemporalAgent.load_snapshot()` raise `NotImplementedError`. Temporal's event
history already persists Workflow state durably at a finer granularity than Strands snapshots, so snapshots are
redundant inside a Workflow.

### Samples [​](https://docs.temporal.io/develop/python/integrations/strands-agents\#samples "Direct link to Samples")

The [Strands Agents plugin samples](https://github.com/temporalio/samples-python/tree/main/strands_plugin) demonstrate
all supported patterns end-to-end.

**Tags:**

- [Strands Agents](https://docs.temporal.io/tags/strands-agents)
- [Python SDK](https://docs.temporal.io/tags/python-sdk)
- [Temporal SDKs](https://docs.temporal.io/tags/temporal-sd-ks)

Help us make Temporal better. [View this page](https://github.com/temporalio/documentation/blob/main/docs/develop/python/integrations/strands-agents.mdx) on GitHub.

[Previous\\
\\
OpenAI Agents SDK integration](https://docs.temporal.io/develop/python/integrations/openai-agents) [Next\\
\\
Ruby SDK](https://docs.temporal.io/develop/ruby/)

- [Get started](https://docs.temporal.io/develop/python/integrations/strands-agents#get-started)
  - [Prerequisites](https://docs.temporal.io/develop/python/integrations/strands-agents#prerequisites)
  - [Install the plugin](https://docs.temporal.io/develop/python/integrations/strands-agents#install-the-plugin)
  - [Run a Strands agent with Durable Execution](https://docs.temporal.io/develop/python/integrations/strands-agents#run-a-strands-agent-with-durable-execution)
- [Build the agent](https://docs.temporal.io/develop/python/integrations/strands-agents#build-the-agent)
  - [Choose and configure models](https://docs.temporal.io/develop/python/integrations/strands-agents#choose-and-configure-models)
  - [Run non-deterministic tools as Activities](https://docs.temporal.io/develop/python/integrations/strands-agents#run-non-deterministic-tools-as-activities)
  - [React to agent lifecycle events](https://docs.temporal.io/develop/python/integrations/strands-agents#react-to-agent-lifecycle-events)
  - [Connect to MCP servers](https://docs.temporal.io/develop/python/integrations/strands-agents#connect-to-mcp-servers)
- [Interact with the agent](https://docs.temporal.io/develop/python/integrations/strands-agents#interact-with-the-agent)
  - [Add human approval gates](https://docs.temporal.io/develop/python/integrations/strands-agents#add-human-approval-gates)
  - [Return structured data from an agent](https://docs.temporal.io/develop/python/integrations/strands-agents#return-structured-data-from-an-agent)
  - [Stream agent output to clients](https://docs.temporal.io/develop/python/integrations/strands-agents#stream-agent-output-to-clients)
- [Run in production](https://docs.temporal.io/develop/python/integrations/strands-agents#run-in-production)
  - [Configure retries](https://docs.temporal.io/develop/python/integrations/strands-agents#configure-retries)
  - [Handle long-running chat sessions](https://docs.temporal.io/develop/python/integrations/strands-agents#handle-long-running-chat-sessions)
  - [Add tracing with OpenTelemetry](https://docs.temporal.io/develop/python/integrations/strands-agents#add-tracing-with-opentelemetry)
  - [Snapshots are not supported](https://docs.temporal.io/develop/python/integrations/strands-agents#snapshots-are-not-supported)
  - [Samples](https://docs.temporal.io/develop/python/integrations/strands-agents#samples)

Resources

- [Glossary](https://docs.temporal.io/glossary)
- [Learn Temporal](https://learn.temporal.io/)
- [Code Exchange](https://temporal.io/code-exchange)
- [Blog](https://temporal.io/blog)
- [YouTube](https://www.youtube.com/c/Temporalio)
- [Support forum](https://community.temporal.io/)
- [Ask an expert](https://pages.temporal.io/ask-an-expert)

Company

- [Temporal Cloud](https://temporal.io/cloud)
- [GitHub](https://github.com/temporalio)
- [Trust Center](https://trust.temporal.io/)
- [Security](https://temporal.io/security)
- [Changelog](https://temporal.io/changelog)

For agents

- [Develop with AI](https://docs.temporal.io/with-ai)
- [llms.txt](https://docs.temporal.io/llms.txt)

[![Temporal logo](https://docs.temporal.io/img/favicon.png)](https://temporal.io/)

Copyright © 2026 Temporal Technologies Inc.

Feedback

reCAPTCHA

Recaptcha requires verification.

protected by **reCAPTCHA**