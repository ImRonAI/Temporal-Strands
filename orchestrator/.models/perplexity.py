"""Custom ``strands_tools`` model provider for think's nested cycles.

``strands_tools.think`` resolves an unknown ``model_provider`` through its own
documented custom-provider path: ``create_model`` falls back to
``$CWD/.models/<provider>.py`` and calls its ``instance(**config)``
(``strands-tools/utils/models/model.py:52-73,96-112``). The worker runs with
``orchestrator/`` as cwd, so ``model_provider="perplexity"`` lands here.

The returned model is the same ``PerplexityModel`` the session's
``TemporalAgent`` uses — a real Perplexity Agent API model whose server-side
native tools (``web_search``, ``fetch_url``, connectors, remote MCP) come from
``agent_api_tools.native_tools()``, the identical surface the outer agent
gets. No provider fork, no copied configuration: ``run_worker.model_params``
is the single source.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from strands.types.streaming import StreamEvent

from agent_api_tools import native_tools
from config import DEFAULT_MODEL_ID
from perplexity_model import PerplexityModel
from run_worker import model_params


def instance(model_id: str | None = None, stream_callback: Callable[[StreamEvent], None] | None = None,
             **_: Any) -> PerplexityModel:
    """``create_model``'s custom-provider entry point (see module docstring)."""
    resolved = model_id or os.environ.get("STRANDS_MODEL_ID") or DEFAULT_MODEL_ID
    return PerplexityModel(
        model_id=resolved,
        params=model_params(resolved, native_tools()),
        api_key=os.environ["PERPLEXITY_API_KEY"],
        **({"stream_callback": stream_callback} if stream_callback else {}),
    )
