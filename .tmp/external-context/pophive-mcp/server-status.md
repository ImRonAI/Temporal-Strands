---
source: Public GitHub repository; no authoritative PopHIVE/Yale MCP documentation found
library: PopHIVE MCP Server
package: pophive-mcp-server
topic: Provenance, transport, tools, and trust status
tech_stack: Strands Agents, Temporal activities, MCP stdio
fetched: 2026-07-30T00:00:00Z
official_docs: https://www.pophive.org/
---

# Provenance finding

No official Yale/PopHIVE MCP endpoint or official MCP documentation was verified. `docs.popdata.org` was unreachable during research. The only concrete implementation found was the community repository `Cicatriiz/pophive-mcp-server`; it must not be represented as an official Yale service.

# Community server characteristics

- Repository: https://github.com/Cicatriiz/pophive-mcp-server
- MIT licensed, JavaScript, created 2025-07-02; last pushed 2025-07-29 in the fetched repository metadata.
- Documented client transport is local stdio using `node server/index.js`; no trusted hosted endpoint is documented.
- Node.js 18+ and local installation are required.
- No authentication is documented because the client launches a local subprocess.
- Advertised tools: `filter_data`, `compare_states`, `time_series_analysis`, `get_available_datasets`, and `search_health_data`.

# Trust conclusion

Treat this as untrusted third-party code despite its README's “production-ready” claim. Pin a reviewed commit, audit dependencies and scraper destinations, sandbox it, restrict filesystem/network access, and require explicit operator approval before installation or execution. Do not dynamically accept an arbitrary package, command, path, or hosted URL as “PopHIVE.”

# Sources

- Official PopHIVE site: https://www.pophive.org/
- Community repository: https://github.com/Cicatriiz/pophive-mcp-server
