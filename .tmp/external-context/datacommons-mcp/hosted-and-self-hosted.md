---
source: Official Data Commons documentation
library: Data Commons MCP
package: datacommons-mcp
topic: Hosted endpoint, authentication, tools, and transports
tech_stack: Strands Agents, Temporal activities, MCP
fetched: 2026-07-30T00:00:00Z
official_docs: https://docs.datacommons.org/mcp/
---

# Hosted service

- Managed endpoint: `https://api.datacommons.org/mcp`
- Authentication: obtain a Data Commons API key for `api.datacommons.org` and send it as `X-API-Key`.

```python
MCPClient(lambda: streamablehttp_client(
    "https://api.datacommons.org/mcp",
    headers={"X-API-Key": os.environ["DC_API_KEY"]},
))
```

The server currently exposes:

- `search_indicators`: find statistical variables/topics for places or metrics.
- `get_observations`: fetch statistical observations by variable and place.

The hosted server queries base `datacommons.org`. Custom Data Commons requires a self-hosted MCP server.

# Self-hosting

- Stdio: `uvx datacommons-mcp@latest serve stdio`
- Streamable HTTP: `uvx datacommons-mcp serve http --host HOST --port PORT`
- Default HTTP bind is localhost:8080; endpoint path is `/mcp`.
- Official docs state self-hosting supports stdio and Streamable HTTP.

# Limitations and operational notes

- Current unsupported areas include non-geographical custom entities, events, graph relationship exploration, and graphical visualization output.
- Data Commons warns that agent answers can be wrong and should be checked.
- For Temporal, keep `DC_API_KEY` in the worker environment and construct the transport in `StrandsPlugin(mcp_clients=...)`, not in workflow inputs.

# Sources

- https://docs.datacommons.org/mcp/
- https://raw.githubusercontent.com/datacommonsorg/docsite/master/mcp/index.md
- https://raw.githubusercontent.com/datacommonsorg/docsite/master/mcp/run_tools.md
- https://raw.githubusercontent.com/datacommonsorg/docsite/master/mcp/host_server.md
