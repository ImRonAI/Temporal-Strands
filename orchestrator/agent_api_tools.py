"""Shared builders for the Perplexity Agent API's native tools array.

Both ``run_worker.py`` (which attaches the array to every PerplexityModel
factory) and ``perplexity_operations.py`` (which defaults the six preset
create activities to the same array when the caller sends none) import from
here, so the outer model and the delegated sub-agents always see the same
server-side tool surface. run_worker re-exports these names so its public
API stays unchanged.

Members built here, in order: the static ``NATIVE_TOOLS`` union members,
one ``{"type": "mcp"}`` entry per configured remote server, then one
``{"type": "connector"}`` entry per connector in ``config.CONNECTORS``.
"""

from __future__ import annotations

import os
import re
from typing import Any
from collections.abc import Mapping

from temporalio.exceptions import ApplicationError

from config import CONNECTORS

# Every static native server-side tool in the documented `Tool` union (Agent
# API OpenAPI spec, POST /v1/agent). These execute on Perplexity's side: the
# model calls them directly and their results arrive as response.reasoning.*
# stream events and output items. All of them, always.
#
#   web_search      fetch_url       people_search
#   finance_search  sandbox         mcp (one entry per server URL)
#   connector       (one entry per config.CONNECTORS entry)
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
#
# ``defer_loading`` is the Agent API's documented per-server flag
# (https://docs.perplexity.ai/docs/agent-api/tools/mcp, "Defer tool
# definitions"): without it every tool schema enters the model's initial
# context on EVERY request and the server emits a full ``mcp_list_tools``
# item each time. Live chat-d6d70dc8b3ee69f8: 162 such items (~28 KB each,
# 10.5 MB of a 52 MB history) in a single browsing turn that never touched
# Data Commons. Deferred, the model searches the catalog and loads only the
# schemas it needs.
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
        "defer_loading": True,
    },
    "pophive": {"url_env": "POPHIVE_MCP_URL", "defer_loading": True},
}

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
        if spec.get("defer_loading"):
            tool["defer_loading"] = True
        tools.append(tool)
    return tools


def connector_tools() -> list[dict[str, Any]]:
    """A native {"type": "connector"} entry per ``config.CONNECTORS`` item.

    Exactly the documented request shape (``type``, ``id``, ``server_label``,
    optional ``server_description``); the connector id references an
    integration the API Group connected in the Perplexity portal.
    https://docs.perplexity.ai/docs/agent-api/tools/connectors
    """
    return [{"type": "connector", **connector} for connector in CONNECTORS]


def native_tools() -> list[dict[str, Any]]:
    """The Agent API's own server-side tools: static union members, remote
    MCP servers, then dashboard connectors."""
    return [dict(tool) for tool in NATIVE_TOOLS] + mcp_tools() + connector_tools()


# --- outbound tools-array contract validation ---------------------------------

_NATIVE_TOOL_TYPES = frozenset(
    {
        "web_search",
        "finance_search",
        "people_search",
        "fetch_url",
        "function",
        "sandbox",
        "mcp",
        "connector",
    }
)
_SEARCH_CONTEXT_SIZES = frozenset({"low", "medium", "high"})
# OpenAPI: ^[a-zA-Z0-9_-]{1,64}$, and labels are unique per request.
_MCP_SERVER_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WEB_SEARCH_MAX_RESULTS_RANGE = (1, 50)
_FETCH_URL_MAX_URLS_RANGE = (1, 10)


def _invalid(message: str) -> ApplicationError:
    # A contract violation cannot succeed on retry.
    return ApplicationError(message, type="PerplexityOperationError", non_retryable=True)


def _validate_tools(tools: list[Any]) -> None:
    # OpenAPI: server_label is ^[a-zA-Z0-9_-]{1,64}$ and unique per request,
    # across BOTH mcp and connector entries.
    seen_mcp_labels: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping):
            raise _invalid("each tool must be an object")
        tool_type = tool.get("type")
        if tool_type not in _NATIVE_TOOL_TYPES:
            raise _invalid(f"unknown tool type {tool_type!r}")
        if tool_type == "web_search":
            max_results = tool.get("max_results")
            low, high = _WEB_SEARCH_MAX_RESULTS_RANGE
            if max_results is not None and not low <= max_results <= high:
                raise _invalid(f"web_search max_results must be {low}-{high}")
            context_size = tool.get("search_context_size")
            if context_size is not None and context_size not in _SEARCH_CONTEXT_SIZES:
                raise _invalid(
                    f"web_search search_context_size must be one of "
                    f"{sorted(_SEARCH_CONTEXT_SIZES)}"
                )
        elif tool_type == "fetch_url":
            max_urls = tool.get("max_urls")
            low, high = _FETCH_URL_MAX_URLS_RANGE
            if max_urls is not None and not low <= max_urls <= high:
                raise _invalid(f"fetch_url max_urls must be {low}-{high}")
        elif tool_type == "function":
            if not tool.get("name"):
                raise _invalid("function tools require a name")
        elif tool_type == "mcp":
            label = tool.get("server_label")
            if not isinstance(label, str) or not _MCP_SERVER_LABEL_RE.match(label):
                raise _invalid(f"invalid mcp server_label {label!r}")
            if label in seen_mcp_labels:
                raise _invalid(
                    f"mcp server_label {label!r} is not unique within the request"
                )
            seen_mcp_labels.add(label)
            url = tool.get("server_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise _invalid("mcp server_url must use https")
        elif tool_type == "connector":
            connector_id = tool.get("id")
            if not isinstance(connector_id, str) or not connector_id:
                raise _invalid("connector tools require an id")
            label = tool.get("server_label")
            if not isinstance(label, str) or not _MCP_SERVER_LABEL_RE.match(label):
                raise _invalid(f"invalid connector server_label {label!r}")
            if label in seen_mcp_labels:
                raise _invalid(
                    f"connector server_label {label!r} is not unique within the request"
                )
            seen_mcp_labels.add(label)
