"""Perplexity Agent API native tool union and its outbound request shape.

The Agent API executes these tools server-side. ``native_tools()`` names them
in the request: the static ``Tool`` union members, one ``{"type": "mcp"}``
entry per configured remote server, then one ``{"type": "connector"}`` entry
per ``config.CONNECTORS`` item. ``run_worker`` attaches that array to every
PerplexityModel factory.

Reference: https://docs.perplexity.ai/api-reference/agent-post (``Tool``
discriminator), https://docs.perplexity.ai/docs/agent-api/tools/*.
"""

from __future__ import annotations

import os
import re
from typing import Any
from collections.abc import Mapping

from temporalio.exceptions import ApplicationError

from config import CONNECTORS

# Static native server-side tools in the Agent API Tool union. The API
# executes them. They are request objects, not Strands tools.
NATIVE_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search"},
    {"type": "fetch_url"},
    {"type": "image_search"},
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
    optional ``server_description``, optional ``allowed_tools``); the connector
    id references an integration the API Group connected in the Perplexity
    portal. https://docs.perplexity.ai/docs/agent-api/tools/connectors
    """
    return [{"type": "connector", **connector} for connector in CONNECTORS]


def native_tools() -> list[dict[str, Any]]:
    """The Agent API's own server-side tools: static union members, remote
    MCP servers, then dashboard connectors."""
    return [dict(entry) for entry in NATIVE_TOOLS] + mcp_tools() + connector_tools()


# --- outbound tools-array contract validation ---------------------------------

# The ``Tool`` discriminator mapping of the Agent API OpenAPI document.
_NATIVE_TOOL_TYPES = frozenset(
    {
        "web_search",
        "image_search",
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
_WEB_SEARCH_TYPES = frozenset({"web", "fast"})
_IMAGE_FORMATS = frozenset({"bmp", "gif", "jpeg", "png", "webp", "svg"})
# OpenAPI: ^[a-zA-Z0-9_-]{1,64}$, and labels are unique per request.
_MCP_SERVER_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WEB_SEARCH_MAX_RESULTS_RANGE = (1, 50)
_IMAGE_SEARCH_MAX_RESULTS_RANGE = (1, 30)
_IMAGE_SEARCH_MAX_FILTER_ITEMS = 10
_FETCH_URL_MAX_URLS_RANGE = (1, 10)

# --- ``skills`` request field (https://docs.perplexity.ai/docs/agent-api/skills)
SKILLS_MAX_ITEMS = 16
_BUILTIN_SKILL_NAMES = frozenset(
    {"office", "office/docx", "office/pdf", "office/pptx", "office/xlsx"}
)
_INLINE_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_INLINE_SKILL_INSTRUCTIONS_MAX_BYTES = 65_536
_INLINE_SKILL_DESCRIPTION_MAX_BYTES = 1_024
_CUSTOM_SKILL_ID_RE = re.compile(r"^skill_[A-Za-z0-9_-]{1,122}$")


def _invalid(message: str) -> ApplicationError:
    # A contract violation cannot succeed on retry.
    return ApplicationError(message, type="PerplexityOperationError", non_retryable=True)


def _validate_server_label(label: Any, seen: set[str], kind: str) -> None:
    if not isinstance(label, str) or not _MCP_SERVER_LABEL_RE.match(label):
        raise _invalid(f"invalid {kind} server_label {label!r}")
    if label in seen:
        raise _invalid(f"{kind} server_label {label!r} is not unique within the request")
    seen.add(label)


def _validate_allowed_tools(tool: Mapping[str, Any], kind: str) -> None:
    allowed = tool.get("allowed_tools")
    if allowed is not None and not (
        isinstance(allowed, list) and all(isinstance(name, str) and name for name in allowed)
    ):
        raise _invalid(f"{kind} allowed_tools must be a list of tool names")


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
            search_type = tool.get("search_type")
            if search_type is not None and search_type not in _WEB_SEARCH_TYPES:
                raise _invalid(
                    f"web_search search_type must be one of {sorted(_WEB_SEARCH_TYPES)}"
                )
        elif tool_type == "image_search":
            max_results = tool.get("max_results")
            low, high = _IMAGE_SEARCH_MAX_RESULTS_RANGE
            if max_results is not None and not low <= max_results <= high:
                raise _invalid(f"image_search max_results must be {low}-{high}")
            filters = tool.get("filters")
            if filters is not None:
                if not isinstance(filters, Mapping):
                    raise _invalid("image_search filters must be an object")
                for key in ("domain_filter", "format_filter"):
                    values = filters.get(key)
                    if values is None:
                        continue
                    if not isinstance(values, list) or len(values) > _IMAGE_SEARCH_MAX_FILTER_ITEMS:
                        raise _invalid(
                            f"image_search {key} must be a list of at most "
                            f"{_IMAGE_SEARCH_MAX_FILTER_ITEMS} items"
                        )
                    if key == "format_filter" and not set(values) <= _IMAGE_FORMATS:
                        raise _invalid(
                            f"image_search format_filter values must be in {sorted(_IMAGE_FORMATS)}"
                        )
                safe = filters.get("safe_search")
                if safe is not None and not isinstance(safe, bool):
                    raise _invalid("image_search safe_search must be a boolean")
        elif tool_type == "fetch_url":
            max_urls = tool.get("max_urls")
            low, high = _FETCH_URL_MAX_URLS_RANGE
            if max_urls is not None and not low <= max_urls <= high:
                raise _invalid(f"fetch_url max_urls must be {low}-{high}")
        elif tool_type == "function":
            if not tool.get("name"):
                raise _invalid("function tools require a name")
        elif tool_type == "mcp":
            _validate_server_label(tool.get("server_label"), seen_mcp_labels, "mcp")
            url = tool.get("server_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise _invalid("mcp server_url must use https")
            _validate_allowed_tools(tool, "mcp")
        elif tool_type == "connector":
            connector_id = tool.get("id")
            if not isinstance(connector_id, str) or not connector_id:
                raise _invalid("connector tools require an id")
            _validate_server_label(tool.get("server_label"), seen_mcp_labels, "connector")
            _validate_allowed_tools(tool, "connector")


def _validate_skills(skills: Any) -> None:
    """Agent Skills request-field contract (``skills`` on the request body).

    The API rejects a request whose skills do not exist, conflict with a
    built-in, or resolve to the same name; all of those are permanent 400s,
    so they fail here before any request goes out.
    """
    if not isinstance(skills, list):
        raise _invalid("skills must be a list")
    if len(skills) > SKILLS_MAX_ITEMS:
        raise _invalid(f"at most {SKILLS_MAX_ITEMS} skills per request")
    names: set[str] = set()
    for skill in skills:
        if not isinstance(skill, Mapping):
            raise _invalid("each skill must be an object")
        kind = skill.get("type")
        if kind == "builtin":
            name = skill.get("name")
            if name not in _BUILTIN_SKILL_NAMES:
                raise _invalid(f"unknown builtin skill {name!r}")
        elif kind == "inline":
            name = skill.get("name")
            if not isinstance(name, str) or not (1 <= len(name) <= 64) or not _INLINE_SKILL_NAME_RE.match(name):
                raise _invalid(f"invalid inline skill name {name!r}")
            if name in _BUILTIN_SKILL_NAMES:
                raise _invalid(f"inline skill {name!r} conflicts with a built-in skill")
            description = skill.get("description")
            if not isinstance(description, str) or not (
                1 <= len(description.encode("utf-8")) <= _INLINE_SKILL_DESCRIPTION_MAX_BYTES
            ):
                raise _invalid(f"inline skill {name!r} description must be 1-{_INLINE_SKILL_DESCRIPTION_MAX_BYTES} bytes")
            instructions = skill.get("instructions")
            if not isinstance(instructions, str) or not (
                1 <= len(instructions.encode("utf-8")) <= _INLINE_SKILL_INSTRUCTIONS_MAX_BYTES
            ):
                raise _invalid(
                    f"inline skill {name!r} instructions must be 1-{_INLINE_SKILL_INSTRUCTIONS_MAX_BYTES} bytes"
                )
        elif kind == "custom":
            name = skill.get("id")
            if not isinstance(name, str) or not _CUSTOM_SKILL_ID_RE.match(name):
                raise _invalid(f"invalid custom skill id {name!r}")
            version = skill.get("version")
            if version is not None and not (
                isinstance(version, str) and (version == "latest" or version.isdigit())
            ):
                raise _invalid(f"custom skill {name!r} version must be a revision number or 'latest'")
        else:
            raise _invalid(f"unknown skill type {kind!r}")
        if name in names:
            raise _invalid(f"skill {name!r} is listed more than once")
        names.add(name)
