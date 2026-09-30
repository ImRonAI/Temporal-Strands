# Telnyx remote MCP

The runtime catalog in `orchestrator/mcp.json` uses three hosted Streamable HTTP servers:

| Connection ID | Endpoint | Capabilities |
| --- | --- | --- |
| `telnyx` | `https://api.telnyx.com/v2/mcp` | Discover endpoint names, retrieve schemas, and invoke Telnyx API operations. Uses `TELNYX_API_KEY` from the worker environment. |
| `telnyx-readonly` | `https://api.telnyx.com/v2/ai/mcp` | Anonymous API reference discovery and schemas. Account call status, call events, and recording metadata require per-user OAuth, which Gwen does not currently provide. |
| `telnyx-docs` | `https://developers.telnyx.com/mcp` | Search and retrieve documentation and OpenAPI specifications. The server also exposes a separate documentation-feedback submission tool. |

All three are on demand (`continue_on_error: true` in this application's catalog). They are not attached to every new session's agent, and their tool discovery cannot block an unrelated first turn. The local Python `telnyx-mcp-server` process is no longer configured.

The agent discovers connection IDs with `load_tool(action="list_mcp_servers")`, then uses the installed `mcp_client` tool through its existing Temporal activity. For a configured remote connection, `mcp_client(action="connect", connection_id="telnyx")` resolves the URL and authentication inside the worker. No API key needs to be included in a prompt or tool argument. A configured connection ID cannot redirect its credentials to a different URL. Explicit connection failures are returned as errors.

The native `mcp_client` provides `list_tools` and `call_tool`. On the API server, call `list_api_endpoints`, then `get_api_endpoint_schema`, then `invoke_api_endpoint` with the discovered endpoint name and schema. The read-only connector documents write endpoints but cannot execute them. Its OAuth account tools remain unavailable until per-user OAuth is implemented; the API server's key does not authenticate those tools.

Configured remote clients reconstructed for Temporal tool activities use native `MCPClient.load_servers`, preserving header interpolation after worker restart. Arbitrary dynamically connected HTTP clients also retain headers when constructing their Temporal MCP client.

## Fax coverage

Live discovery on September 30, 2026 returned `create_faxes`, `retrieve_faxes`, `list_faxes`, `delete_faxes`, `cancel_faxes_actions`, and `refresh_faxes_actions`, plus create/retrieve/update/list/delete fax-application operations. These are endpoint identifiers for the API MCP's schema and invocation tools, not separate top-level MCP tools. Actual transmission still requires a configured Telnyx fax application, suitable number, destination, and document. No fax was sent during verification.

## Sources

Verification results and remaining limitations: [September 30 evidence](evidence/telnyx-remote-mcp-2026-09-30.md).

- https://developers.telnyx.com/docs/development/mcp/remote-mcp
- https://developers.telnyx.com/docs/development/mcp/ai-connector/index
- https://telnyx.com/.well-known/mcp/server-card.json
- https://github.com/team-telnyx/telnyx-mcp-server (deprecated Python server)
- https://strandsagents.com/docs/user-guide/concepts/tools/mcp-tools
