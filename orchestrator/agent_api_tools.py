"""Shared builders for the Perplexity Agent API's native tools array.

Both ``run_worker.py`` (which attaches the array to every PerplexityModel
factory) and ``perplexity_operations.py`` (which defaults the six preset
create activities to the same array when the caller sends none) import from
here, so the outer model and the delegated sub-agents always see the same
server-side tool surface. run_worker re-exports these names so its public
API stays unchanged.

Members built here, in order: the static ``NATIVE_TOOLS`` union members,
one ``{"type": "mcp"}`` entry per configured remote server, then one
``{"type": "connector"}`` entry per dashboard-authorized connector
(``config.CONNECTORS``, overridable via ``PERPLEXITY_CONNECTOR_IDS``).
"""

from __future__ import annotations

import os
from typing import Any

from config import CONNECTORS

# Every static native server-side tool in the documented `Tool` union (Agent
# API OpenAPI spec, POST /v1/agent). These execute on Perplexity's side: the
# model calls them directly and their results arrive as response.reasoning.*
# stream events and output items. All of them, always.
#
#   web_search      fetch_url       people_search
#   finance_search  sandbox         mcp (one entry per server URL)
#   connector       (one entry per dashboard-authorized connector)
#
# With sandbox enabled the model loads the pplx_sdk skill and searches from
# inside sandbox code, so results arrive as sandbox_results rather than
# search_results. Both paths are real and both are rendered.
NATIVE_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search"},
    {"type": "fetch_url"},
    {"type": "people_search"},
    {"type": "finance_search"},
    {"type": "sandbox"},
]

# MCP reaches the model as the Agent API's OWN native remote-MCP tool: the
# {"type": "mcp"} member of the documented Tool union. This is deliberately
# NOT the Strands MCP path (that stays on StrandsPlugin(mcp_clients=...) for
# the Gemini side); registering these servers as client-side Strands function
# tools makes zero-argument calls arrive as arguments == "" and kills the turn.
#
# ``allowed_tools`` for datacommons excludes get_multi_entity_observations,
# whose bare {"type": "object"} entities schema the Agent API rejects for the
# WHOLE request (external_connector_error before inference) -- established by
# bisection against the live API. Drop the allowlist once the server fixes its
# schema.
MCP_SERVERS: dict[str, dict[str, Any]] = {
    "datacommons": {
        "url_env": "DATACOMMONS_MCP_URL",
        "key_env": "DC_API_KEY",
        "allowed_tools": [
            "search_indicators",
            "search_child_indicators",
            "get_variable_metadata",
            "get_observations",
            "get_child_observations",
        ],
    },
    "pophive": {"url_env": "POPHIVE_MCP_URL"},
}

# Optional override for config.CONNECTORS: comma-separated label=id pairs,
# e.g. "google_drive=connector_googledrive,github=connector_github".
CONNECTOR_IDS_ENV = "PERPLEXITY_CONNECTOR_IDS"


def mcp_tools() -> list[dict[str, Any]]:
    """A native {"type": "mcp"} entry per configured remote server.

    Data Commons rejects unauthenticated initialize with 401 UNAUTHENTICATED
    unless DC_API_KEY is sent as X-API-Key; PopHIVE needs no auth. A server
    whose URL env var is unset is left out entirely.
    """
    tools: list[dict[str, Any]] = []
    for name, spec in MCP_SERVERS.items():
        url = os.environ.get(spec["url_env"])
        if not url:
            continue
        tool: dict[str, Any] = {
            "type": "mcp",
            "server_label": name,
            "server_url": url,
        }
        key_env = spec.get("key_env")
        api_key = os.environ.get(key_env) if key_env else None
        if api_key:
            tool["headers"] = {"X-API-Key": api_key}
        allowed = spec.get("allowed_tools")
        if allowed:
            tool["allowed_tools"] = list(allowed)
        tools.append(tool)
    return tools


def connector_tools() -> list[dict[str, Any]]:
    """A native {"type": "connector"} entry per dashboard-authorized connector.

    Connectors are authorized in the Perplexity dashboard; the request only
    references the connector id. ``PERPLEXITY_CONNECTOR_IDS`` (comma-separated
    ``label=id`` pairs) replaces ``config.CONNECTORS`` entirely when set;
    malformed pairs are skipped.
    """
    override = os.environ.get(CONNECTOR_IDS_ENV)
    if override:
        connectors: list[dict[str, str]] = []
        for pair in override.split(","):
            label, _, connector_id = pair.partition("=")
            label, connector_id = label.strip(), connector_id.strip()
            if label and connector_id:
                connectors.append({"id": connector_id, "server_label": label})
        return [{"type": "connector", **connector} for connector in connectors]
    return [{"type": "connector", **connector} for connector in CONNECTORS]


def native_tools() -> list[dict[str, Any]]:
    """The Agent API's own server-side tools: static union members, remote
    MCP servers, then dashboard connectors."""
    return [dict(tool) for tool in NATIVE_TOOLS] + mcp_tools() + connector_tools()
