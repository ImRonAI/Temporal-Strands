"""run_worker model-factory assembly, params, and readiness. No network.

Run from inside orchestrator/ (no conftest.py):

    .venv/bin/python -m pytest tests/test_run_worker.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_worker
from config import (
    BUILTIN_SKILLS,
    DEFAULT_MODEL_ID,
    GEMINI_MODEL_IDS,
    PERPLEXITY_PRESETS,
)
from gemini_model import GeminiModel
from perplexity_model import PerplexityModel

PRESET_IDS = [f"preset:{name}" for name in PERPLEXITY_PRESETS]
CATALOG_IDS = ["google/gemini-3.6-flash", "anthropic/claude-opus-5", "openai/gpt-5"]
SORTED_CATALOG = sorted(CATALOG_IDS)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "PERPLEXITY_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "DATACOMMONS_MCP_URL",
        "DC_API_KEY",
        "POPHIVE_MCP_URL",
        "PERPLEXITY_CONNECTOR_IDS",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    # assemble_model_factories installs a client factory on success paths;
    # clear it so no other suite inherits a test key.
    import perplexity_operations

    perplexity_operations.configure(None)


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, ids: list[str]) -> None:
    async def fake_fetch(api_key: str) -> list[str]:
        return sorted(set(ids))

    monkeypatch.setattr(run_worker, "fetch_model_ids", fake_fetch)


# --- factory union and order ---------------------------------------------------


@pytest.mark.asyncio
async def test_factory_union_order_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test")
    _patch_catalog(monkeypatch, CATALOG_IDS)

    factories, default_model = await run_worker.assemble_model_factories()

    assert list(factories) == [*PRESET_IDS, *SORTED_CATALOG, *GEMINI_MODEL_IDS]
    assert default_model == DEFAULT_MODEL_ID == "preset:high"

    # Mapping values: PerplexityModel for presets + catalog, GeminiModel for
    # Gemini ids.
    assert isinstance(factories["preset:high"](), PerplexityModel)
    assert isinstance(factories[SORTED_CATALOG[0]](), PerplexityModel)
    assert isinstance(factories[GEMINI_MODEL_IDS[0]](), GeminiModel)


@pytest.mark.asyncio
async def test_perplexity_client_pins_base_url_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test")
    # PERPLEXITY_BASE_URL in the environment must not leak into the client.
    monkeypatch.setenv("PERPLEXITY_BASE_URL", "https://api.perplexity.ai/router")
    _patch_catalog(monkeypatch, CATALOG_IDS)

    factories, _ = await run_worker.assemble_model_factories()
    model = factories["preset:high"]()
    assert str(model.client.base_url).rstrip("/") == "https://api.perplexity.ai"
    assert model.client.max_retries == 0


# --- model params ---------------------------------------------------------------


def test_preset_params_have_no_max_output_tokens() -> None:
    tools = run_worker.native_tools()
    for preset_id in PRESET_IDS:
        params = run_worker.model_params(preset_id, tools)
        assert "max_output_tokens" not in params
        assert params["background"] is True
        assert params["store"] is True
        assert params["max_steps"] == 100
        assert params["skills"] == [dict(skill) for skill in BUILTIN_SKILLS]
        assert params["tools"] == tools


def test_catalog_params_carry_provider_output_ceilings() -> None:
    tools = run_worker.native_tools()
    google = run_worker.model_params("google/gemini-3.6-flash", tools)
    other = run_worker.model_params("anthropic/claude-opus-5", tools)
    assert google["max_output_tokens"] == 65_536
    assert other["max_output_tokens"] == 128_000
    for params in (google, other):
        assert params["background"] is True
        assert params["store"] is True
        assert params["max_steps"] == 100
        assert params["skills"] == [dict(skill) for skill in BUILTIN_SKILLS]


def test_max_output_tokens_for_provider_prefixes() -> None:
    assert run_worker.max_output_tokens_for("google/gemini-3.6-flash") == 65_536
    assert run_worker.max_output_tokens_for("openai/gpt-5") == 128_000


# --- native tools ---------------------------------------------------------------


def test_native_tool_types_are_the_documented_union_members() -> None:
    assert [tool["type"] for tool in run_worker.NATIVE_TOOLS] == [
        "web_search",
        "fetch_url",
        "people_search",
        "finance_search",
        "sandbox",
    ]


def test_mcp_tools_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_worker.mcp_tools() == []

    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://dc.example/mcp")
    monkeypatch.setenv("DC_API_KEY", "dc-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://pophive.example/mcp")

    tools = {tool["server_label"]: tool for tool in run_worker.mcp_tools()}
    assert tools["datacommons"]["server_url"] == "https://dc.example/mcp"
    assert tools["datacommons"]["headers"] == {"X-API-Key": "dc-key"}
    assert tools["datacommons"]["allowed_tools"] == [
        "search_indicators",
        "search_child_indicators",
        "get_variable_metadata",
        "get_observations",
        "get_child_observations",
    ]
    assert tools["pophive"]["server_url"] == "https://pophive.example/mcp"
    assert "headers" not in tools["pophive"]
    assert "allowed_tools" not in tools["pophive"]


# --- connectors -----------------------------------------------------------------


def test_native_tools_include_both_dashboard_connectors() -> None:
    connectors = [
        tool for tool in run_worker.native_tools() if tool["type"] == "connector"
    ]
    assert [(c["id"], c["server_label"]) for c in connectors] == [
        ("connector_googledrive", "google_drive"),
        ("connector_github", "github"),
    ]
    # Descriptions come from config.CONNECTORS verbatim.
    for connector in connectors:
        assert connector["server_description"]


def test_connector_tools_env_override_replaces_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PERPLEXITY_CONNECTOR_IDS",
        "drive_alt=connector_drivealt, gh_alt=connector_ghalt,malformed",
    )
    connectors = run_worker.connector_tools()
    assert connectors == [
        {"type": "connector", "id": "connector_drivealt", "server_label": "drive_alt"},
        {"type": "connector", "id": "connector_ghalt", "server_label": "gh_alt"},
    ]


@pytest.mark.parametrize(
    "model_id", ["preset:high", "anthropic/claude-opus-5"]
)
def test_model_params_tools_include_connectors(model_id: str) -> None:
    """Both preset and catalog params carry the connector entries."""
    tools = run_worker.native_tools()
    params = run_worker.model_params(model_id, tools)
    connector_ids = [
        tool["id"] for tool in params["tools"] if tool.get("type") == "connector"
    ]
    assert connector_ids == ["connector_googledrive", "connector_github"]


# --- catalog fetch --------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_model_ids_sends_bearer_and_sorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {
                "data": [
                    {"id": "openai/gpt-5"},
                    {"id": "anthropic/claude-opus-5"},
                    {"id": "openai/gpt-5"},
                    {"id": ""},
                ]
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            calls.append({"url": url, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr(run_worker.httpx, "AsyncClient", FakeAsyncClient)

    ids = await run_worker.fetch_model_ids("pplx-test")
    assert ids == ["anthropic/claude-opus-5", "openai/gpt-5"]
    assert calls == [
        {
            "url": "https://api.perplexity.ai/v1/models",
            "headers": {"Authorization": "Bearer pplx-test"},
        }
    ]


# --- graceful degradation -------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_perplexity_key_registers_gemini_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test")

    factories, default_model = await run_worker.assemble_model_factories()
    assert list(factories) == list(GEMINI_MODEL_IDS)
    assert default_model == GEMINI_MODEL_IDS[0]


@pytest.mark.asyncio
async def test_missing_gemini_key_registers_perplexity_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    _patch_catalog(monkeypatch, CATALOG_IDS)

    factories, default_model = await run_worker.assemble_model_factories()
    assert list(factories) == [*PRESET_IDS, *SORTED_CATALOG]
    assert default_model == "preset:high"


@pytest.mark.asyncio
async def test_catalog_failure_degrades_to_presets_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")

    async def broken_fetch(api_key: str) -> list[str]:
        raise RuntimeError("503 from the catalog")

    monkeypatch.setattr(run_worker, "fetch_model_ids", broken_fetch)

    factories, default_model = await run_worker.assemble_model_factories()
    assert list(factories) == PRESET_IDS
    assert default_model == "preset:high"


@pytest.mark.asyncio
async def test_no_keys_at_all_raises_system_exit() -> None:
    with pytest.raises(SystemExit):
        await run_worker.assemble_model_factories()


# --- readiness ------------------------------------------------------------------


def test_write_readiness_includes_models_order_and_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    readiness = tmp_path / "worker-readiness.json"
    monkeypatch.setattr(run_worker, "READINESS_PATH", readiness)

    model_ids = [*PRESET_IDS, *SORTED_CATALOG, *GEMINI_MODEL_IDS]
    run_worker.write_readiness(model_ids, "Gwen", "preset:high")

    record = json.loads(readiness.read_text())
    assert record["models"] == model_ids
    assert record["default_model"] == "preset:high"
    assert record["agent"] == "Gwen"
    assert record["task_queue"] == "perplexity-orchestrator"
