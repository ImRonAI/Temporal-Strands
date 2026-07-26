"""MCP wiring for the orchestrator, in the Temporal-native form.

Two different things in this codebase are called "MCP", and they are not
interchangeable:

- `perplexity_tools.mcp_tool()` declares MCP to *Perplexity*, server-side.
  Perplexity connects to the server itself and the calls never touch this
  process, so Temporal cannot see, retry, or time-bound them.
- `TemporalMCPClient` (here) routes each MCP call through a Temporal
  activity. Tool discovery and every tool call become real activities with
  the same retry policy, timeouts, and heartbeat behaviour as the rest of
  the agent's work, and they show up in workflow history. That is what makes
  MCP usable in a long-horizon run: a flaky MCP server retries with backoff
  instead of failing a turn outright.

Both are gated on the same MCP_SERVER_URL / MCP_SERVER_LABEL /
MCP_AUTHORIZATION variables the codebase already uses, so configuring an MCP
server lights up whichever path is appropriate rather than introducing a
second, competing convention.

`MCP_ENABLED` is resolved once at import, not per call. The workflow's tool
list has to be identical on every replay of a given execution, so it cannot
depend on an env var read at run time. Resolving at import makes it constant
for the life of a worker process; changing MCP_SERVER_URL and restarting
workers is therefore a deploy-shaped change, exactly like editing the tool
list in code, and should be rolled out the same way (drain, or version, in
-flight executions).
"""

import os
from collections.abc import Callable
from datetime import timedelta

from temporalio.common import RetryPolicy

# Name the server is registered under, shared by the worker-side factory and
# the workflow-side handle — they must agree or tool discovery fails.
MCP_SERVER_NAME = "mcp"

MCP_ENABLED = bool(os.getenv("MCP_SERVER_URL"))

# Same bounded-retry shape used for the other tool activities: enough
# attempts to ride out a restarting MCP server, capped so a permanently-down
# one surfaces as an error instead of retrying forever.
MCP_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
)
MCP_START_TO_CLOSE_TIMEOUT = timedelta(minutes=2)
MCP_SCHEDULE_TO_CLOSE_TIMEOUT = timedelta(minutes=10)


def mcp_client_factories() -> dict[str, Callable[[], object]]:
    """Worker-side `mcp_clients=` mapping for StrandsPlugin. Empty when no
    MCP server is configured, which leaves the plugin registering no MCP
    activities at all."""
    if not MCP_ENABLED:
        return {}

    # Imported lazily: `mcp` and `strands.tools.mcp` pull in transport
    # machinery that only the worker process needs, and this module is also
    # imported from workflow code.
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp.mcp_client import MCPClient

    server_url = os.environ["MCP_SERVER_URL"]
    headers: dict[str, str] = {}
    if os.getenv("MCP_AUTHORIZATION"):
        headers["Authorization"] = os.environ["MCP_AUTHORIZATION"]

    def build() -> MCPClient:
        return MCPClient(
            lambda: streamablehttp_client(server_url, headers=headers or None)
        )

    return {MCP_SERVER_NAME: build}
