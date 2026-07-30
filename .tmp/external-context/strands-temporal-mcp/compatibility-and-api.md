---
source: Context7 API and official source/docs
library: Strands Agents + Temporal Python SDK
package: strands-agents, temporalio[strands-agents]
topic: Native MCP API and Temporal compatibility
tech_stack: Python, Temporal workflows, Strands Agents, MCP
fetched: 2026-07-30T00:00:00Z
official_docs: https://github.com/temporalio/sdk-python/tree/main/temporalio/contrib/strands
---

# Version and maturity

- Strands Agents Python: latest verified release `python/v1.50.1` (2026-07-24).
- Temporal Python SDK: latest verified release `1.31.0` (2026-07-29).
- The Temporal Strands package README calls the integration **experimental release stage**. If another Temporal page calls it **Public Preview**, treat those as two labels for a pre-GA API, not evidence of stability; the package-local warning is the most specific source.
- Install with `uv add temporalio[strands-agents]`. Previously verified package metadata requires `temporalio >= 1.28.0` for this integration.

# Plain Strands MCP client

```python
from strands.tools.mcp import MCPClient

MCPClient(
    transport_callable,
    *,
    startup_timeout=30,
    tool_filters=None,
    prefix=None,
    application_name=None,
    application_version=None,
    continue_on_error=False,
    elicitation_callback=None,
    progress_callback=None,
    tasks_config=None,
)
```

The transport callable returns an MCP SDK async transport context manager. Supported documented transports:

```python
from mcp import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

stdio = MCPClient(lambda: stdio_client(
    StdioServerParameters(command="uvx", args=["server-package"])
))
sse = MCPClient(lambda: sse_client("https://trusted.example/sse"))
http = MCPClient(lambda: streamablehttp_client(
    "https://trusted.example/mcp",
    headers={"Authorization": "Bearer ..."},
))
```

Lifecycle options:

- Managed: `Agent(tools=[mcp_client])`; the agent starts and releases the provider.
- Manual: `with mcp_client:` then `list_tools_sync()` and construct an agent from those tools.
- Calling discovery or tools without an active context/session raises `MCPClientInitializationError`.
- One background thread/event loop owns the MCP `ClientSession`; calls are submitted to it. A started client cannot be started a second time. The instance resets on `stop()` and can be reused.

Relevant methods:

```python
list_tools_sync(pagination_token=None, prefix=None, tool_filters=None)
call_tool_sync(tool_use_id, name, arguments=None, read_timeout_seconds=None,
               meta=None, progress_callback=None, *, cancel_signal=None)
await call_tool_async(tool_use_id, name, arguments=None, read_timeout_seconds=None,
                      meta=None, progress_callback=None, *, cancel_signal=None)
```

- `tools/list` pagination is preserved. `MCPAgentTool` dynamically adapts discovered schemas to Strands tools.
- Direct tool calls return only after the MCP result, but MCP progress notifications can be observed through `progress_callback(progress, total, message)`. This is progress streaming, not incremental tool-result content streaming.
- `read_timeout_seconds` controls a call. `startup_timeout` defaults to 30 seconds.
- Tool exceptions are converted into an error `MCPToolResult`; local cancellation also returns an error result with `cancelled=True`. Remote cancellation is best effort and may not stop remote work.
- `continue_on_error=True` suppresses connection failure while loading a provider and yields no tools; a failure after connection during tool listing still propagates.
- Transport credentials belong in the worker/server environment or transport factory. Streamable HTTP accepts headers, including bearer tokens. Do not place secrets in workflow arguments/history.
- Strands agents may execute independent tool uses concurrently. The MCP SDK session multiplexes requests by JSON-RPC request id, but server-side concurrency and rate limits still apply.

# Temporal-native MCP bridge

```python
from temporalio.contrib.strands import StrandsPlugin, TemporalAgent, TemporalMCPClient
```

Workflow-side handle:

```python
TemporalMCPClient(
    server,
    *,
    cache_tools=False,
    task_queue=None,
    schedule_to_close_timeout=None,
    schedule_to_start_timeout=None,
    start_to_close_timeout=None,
    heartbeat_timeout=None,
    retry_policy=None,
    cancellation_type=ActivityCancellationType.TRY_CANCEL,
    versioning_intent=None,
    summary=None,
    priority=Priority.default,
)
```

Worker-side registration:

```python
StrandsPlugin(
    *,
    models=None,
    mcp_clients={"server-name": lambda: MCPClient(...)},
    mcp_connection_idle_timeout=None,
)
```

Correct placement:

```python
# workflow module
mcp = TemporalMCPClient(
    server="datacommons",
    start_to_close_timeout=timedelta(seconds=30),
)
agent = TemporalAgent(tools=[mcp], start_to_close_timeout=timedelta(seconds=60))
result = await agent.invoke_async(prompt)

# worker startup
plugin = StrandsPlugin(mcp_clients={
    "datacommons": lambda: MCPClient(
        lambda: streamablehttp_client(
            "https://api.datacommons.org/mcp",
            headers={"X-API-Key": os.environ["DC_API_KEY"]},
        )
    )
})
```

- Never open network transports in workflow code. `TemporalMCPClient` is a serializable, workflow-side named handle; the real `MCPClient` factory and credentials remain in the activity worker.
- The plugin registers `{server}-list-tools` and `{server}-call-tool` activities.
- `cache_tools=False` re-lists before each agent turn, allowing tool additions/removals after a server restart. `cache_tools=True` lists once per workflow lifetime.
- List and call activities share one lazily opened MCP connection per server name in each worker process. Concurrent first callers deduplicate connection setup; concurrent calls use the same multiplexed MCP session.
- Idle eviction defaults to five minutes and pauses while calls are in flight. A broken call connection is evicted so the next activity reconnects.
- Temporal activity retries/timeouts are configured on `TemporalMCPClient`. Avoid combining retries with non-idempotent MCP tools unless the tool or application provides idempotency keys.
- Use `await TemporalAgent.invoke_async(...)`; synchronous agent invocation creates a thread blocked by the workflow sandbox.
- Current bridge source calls `session.call_tool(name, arguments)` directly. It does not forward plain Strands per-call progress callbacks, task-augmented execution settings, or a custom MCP read timeout into that call; Temporal activity timeout/cancellation governs durability. `tool_filters` and `prefix` are applied during list-tools.

# Compatibility matrix

| Capability | Plain `MCPClient` | `TemporalMCPClient` bridge |
|---|---|---|
| stdio | Yes | Yes, worker factory |
| legacy SSE | Yes | Yes, worker factory |
| Streamable HTTP | Yes | Yes, worker factory |
| Runtime tool discovery | Yes | Yes; every turn by default |
| Dynamic invocation | Yes | Yes, as Temporal Activity |
| Progress notifications | Callback supported | Not forwarded by current bridge |
| Incremental tool-result streaming | No documented adapter | No |
| Tool-call concurrency | Shared multiplexed session | Shared worker-process multiplexed session |
| Credentials | Transport factory/headers/env | Worker factory only; never workflow history |
| Retries | Application/transport behavior | Temporal RetryPolicy |
| Connection reuse | Context/provider lifetime | Lazy worker cache, 5-minute idle default |
| Maturity | Native Strands feature | Experimental/pre-GA integration |

# Trust and security boundary

- A runtime-provided MCP URL or command is equivalent to granting network access or local code execution. Do not pass it directly to a transport factory.
- Resolve runtime server identifiers through an allowlisted registry. Validate scheme, host, port, path, redirects, and resolved IPs; reject loopback, link-local, private, metadata, and internal DNS destinations unless explicitly approved.
- Re-resolve and revalidate on redirects/reconnects to reduce DNS rebinding/TOCTOU risk. Apply egress firewall rules as a second boundary.
- Keep credentials server-scoped and out of prompts, workflow inputs, logs, and Temporal history. Never forward one server's authorization headers to another.
- Filter/rename tools and require approval for side-effecting or high-risk calls. MCP's specification recommends a human ability to deny tool invocations.
- For stdio, allowlist executable/package/version and arguments, use a restricted account/container, and constrain filesystem/network access.
- MCP Streamable HTTP servers must validate `Origin`, authenticate connections, and bind local-only deployments to localhost. Clients must protect session IDs and authorization tokens.

# Sources

- https://strandsagents.com/docs/user-guide/concepts/tools/mcp-tools/
- https://github.com/strands-agents/harness-sdk/blob/main/strands-py/src/strands/tools/mcp/mcp_client.py
- https://github.com/temporalio/sdk-python/tree/main/temporalio/contrib/strands
- https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_temporal_mcp_client.py
- https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/_plugin.py
- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices
