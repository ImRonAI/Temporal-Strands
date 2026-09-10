"""run_worker model-factory assembly, params, and catalog registration. No network.

Run from inside orchestrator/ (no conftest.py):

    .venv/bin/python -m pytest tests/test_run_worker.py -q
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with patch("dotenv.load_dotenv", return_value=False):
    import run_worker

import agent_api_tools
from config import (
    BUILTIN_SKILLS,
    CONNECTORS,
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


@pytest.fixture
def explicit_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise explicit operator configuration, not inferred authorization."""
    monkeypatch.setenv(
        "PERPLEXITY_CONNECTOR_IDS",
        "google_drive=connector_googledrive,github=connector_github",
    )


# --- provider-declaring catalog --------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_declares_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every registered id carries a declared provider; Gemini ids are google-ai-studio."""
    from config import (
        PROVIDER_GOOGLE_AI_STUDIO,
        PROVIDER_PERPLEXITY_AGENT_API,
        RegisteredModel,
    )

    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test")
    _patch_catalog(monkeypatch, CATALOG_IDS)

    factories, catalog, default_model = await run_worker.assemble_model_factories()

    assert default_model == DEFAULT_MODEL_ID
    # 6 presets + 3 catalog ids + 3 Gemini ids, in registration order.
    assert len(catalog) == len(PRESET_IDS) + len(SORTED_CATALOG) + len(GEMINI_MODEL_IDS)
    assert [entry.id for entry in catalog] == list(factories)

    known_providers = {PROVIDER_PERPLEXITY_AGENT_API, PROVIDER_GOOGLE_AI_STUDIO}
    for entry in catalog:
        assert isinstance(entry, RegisteredModel)
        assert entry.provider in known_providers
        assert entry.label

    by_id = {entry.id: entry for entry in catalog}
    for preset_id in PRESET_IDS:
        assert by_id[preset_id].provider == PROVIDER_PERPLEXITY_AGENT_API
        assert by_id[preset_id].label.endswith("(preset)")
    for catalog_id in SORTED_CATALOG:
        assert by_id[catalog_id].provider == PROVIDER_PERPLEXITY_AGENT_API
        assert by_id[catalog_id].label == catalog_id
    for gemini_id in GEMINI_MODEL_IDS:
        assert by_id[gemini_id].provider == PROVIDER_GOOGLE_AI_STUDIO


# --- factory union and order ---------------------------------------------------


@pytest.mark.asyncio
async def test_factory_union_order_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test")
    _patch_catalog(monkeypatch, CATALOG_IDS)

    factories, _catalog, default_model = await run_worker.assemble_model_factories()

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

    factories, _catalog, _default = await run_worker.assemble_model_factories()
    model = factories["preset:high"]()
    # PerplexityModel resolves its own client args per request; the pinned base
    # URL and max_retries=0 live there, not in a preconstructed SDK client.
    client_args = model._resolve_client_args()
    assert client_args["base_url"] == "https://api.perplexity.ai/v1"
    assert client_args["max_retries"] == 0
    assert client_args["api_key"] == "pplx-test"
    assert "api_key" not in model.get_config()


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


def test_native_tools_preserve_explicit_connector_labels_and_descriptions(explicit_connectors) -> None:
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
        "drive_alt=connector_drivealt, gh_alt=connector_ghalt",
    )
    connectors = run_worker.connector_tools()
    assert connectors == [
        {"type": "connector", "id": "connector_drivealt", "server_label": "drive_alt"},
        {"type": "connector", "id": "connector_ghalt", "server_label": "gh_alt"},
    ]


@pytest.mark.parametrize("override", [None, "", "   "])
def test_missing_connector_config_keeps_other_tools(monkeypatch, override, caplog) -> None:
    if override is not None:
        monkeypatch.setenv("PERPLEXITY_CONNECTOR_IDS", override)
    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://dc.example/mcp")
    monkeypatch.setenv("DC_API_KEY", "private-mcp-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://pophive.example/mcp")
    factories, _ = run_worker.build_perplexity_factories("private-api-key", CATALOG_IDS)
    tools = run_worker.native_tools()
    assert tools == [*run_worker.NATIVE_TOOLS, *run_worker.mcp_tools(), *run_worker.connector_tools()]
    assert [tool["type"] for tool in tools] == [
        "web_search", "fetch_url", "people_search", "finance_search", "sandbox", "mcp", "mcp", "connector", "connector", "connector",
    ]
    for factory in factories.values():
        assert factory().get_config()["params"]["tools"] == tools
    message = caplog.text
    assert "authorization status unverified" in message
    assert "google_drive" in message and "github" in message
    assert "https://console.perplexity.ai/group/connectors" in message
    assert "private-api-key" not in message and "private-mcp-key" not in message


@pytest.mark.parametrize("override", [
    "malformed",
    "google_drive=connector_googledrive,malformed",
    "google_drive=",
    "=connector_googledrive",
    "bad label=connector_googledrive",
    f"{'x' * 65}=connector_googledrive",
    "github=connector_github,github=another-id",
    "github=connector_github,",
])
def test_invalid_connector_selection_never_silently_drops_entries(monkeypatch, override) -> None:
    monkeypatch.setenv("PERPLEXITY_CONNECTOR_IDS", override)
    with pytest.raises(ValueError, match="No entries were skipped") as caught:
        run_worker.build_perplexity_factories("not-a-real-key", CATALOG_IDS)
    assert override not in str(caught.value)
    assert "not-a-real-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_startup_accepts_missing_connector_config(monkeypatch, caplog) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-google-key")
    _patch_catalog(monkeypatch, CATALOG_IDS)

    factories, catalog, default = await run_worker.assemble_model_factories()
    assert list(factories) == [*PRESET_IDS, *SORTED_CATALOG, *GEMINI_MODEL_IDS]
    assert [entry.id for entry in catalog] == list(factories)
    assert default == DEFAULT_MODEL_ID
    assert "authorization status unverified" in caplog.text


@pytest.mark.asyncio
async def test_startup_rejects_malformed_explicit_connectors_before_catalog_io(monkeypatch) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-google-key")
    monkeypatch.setenv("PERPLEXITY_CONNECTOR_IDS", "google_drive=")

    async def unexpected_fetch(api_key):
        pytest.fail("Connector setup must be checked before catalog I/O")

    monkeypatch.setattr(run_worker, "fetch_model_ids", unexpected_fetch)
    with pytest.raises(SystemExit, match="Invalid PERPLEXITY_CONNECTOR_IDS"):
        await run_worker.assemble_model_factories()


def test_all_factories_share_explicit_tools_without_claiming_authorization(
    monkeypatch, explicit_connectors, caplog,
) -> None:
    monkeypatch.setenv("DATACOMMONS_MCP_URL", "https://dc.example/mcp")
    monkeypatch.setenv("DC_API_KEY", "private-mcp-key")
    monkeypatch.setenv("POPHIVE_MCP_URL", "https://pophive.example/mcp")
    tools = run_worker.native_tools()
    factories, _ = run_worker.build_perplexity_factories("private-api-key", CATALOG_IDS)
    # Factories capture startup configuration, not a per-inference fallback.
    monkeypatch.delenv("PERPLEXITY_CONNECTOR_IDS")
    for factory in factories.values():
        assert factory().get_config()["params"]["tools"] == tools
    assert [tool["type"] for tool in tools] == [
        "web_search", "fetch_url", "people_search", "finance_search", "sandbox",
        "mcp", "mcp", "connector", "connector",
    ]
    assert "authorization status unverified" in caplog.text
    assert "google_drive" in caplog.text and "github" in caplog.text
    assert "private-api-key" not in caplog.text and "private-mcp-key" not in caplog.text


def test_explicit_selection_reports_omitted_labels(monkeypatch, caplog) -> None:
    monkeypatch.setenv("PERPLEXITY_CONNECTOR_IDS", "github=opaque-portal-id")
    factories, _ = run_worker.build_perplexity_factories("private-api-key", [])
    connectors = [
        tool for tool in factories["preset:high"]().get_config()["params"]["tools"]
        if tool["type"] == "connector"
    ]
    assert connectors == [{"type": "connector", **CONNECTORS[1], "id": "opaque-portal-id"}]
    assert "configuration missing" in caplog.text and "google_drive" in caplog.text
    assert "opaque-portal-id" not in caplog.text
    assert agent_api_tools.CONNECTORS == CONNECTORS


@pytest.mark.parametrize(
    "model_id", ["preset:high", "anthropic/claude-opus-5"]
)
def test_model_params_tools_include_connectors(model_id: str, explicit_connectors) -> None:
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

    factories, _catalog, default_model = await run_worker.assemble_model_factories()
    assert list(factories) == list(GEMINI_MODEL_IDS)
    assert default_model == GEMINI_MODEL_IDS[0]


@pytest.mark.asyncio
async def test_missing_gemini_key_registers_perplexity_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    _patch_catalog(monkeypatch, CATALOG_IDS)

    factories, _catalog, default_model = await run_worker.assemble_model_factories()
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

    factories, _catalog, default_model = await run_worker.assemble_model_factories()
    assert list(factories) == PRESET_IDS
    assert default_model == "preset:high"


@pytest.mark.asyncio
async def test_no_keys_at_all_raises_system_exit() -> None:
    with pytest.raises(SystemExit):
        await run_worker.assemble_model_factories()


# --- outbound tool spec validation ----------------------------------------------


def test_workflow_tool_specs_include_all_registered_tools() -> None:
    names = [spec["name"] for spec in run_worker.workflow_tool_specs()]
    for expected in ("graph", "use_agent", "use_skill", "mcp_client", "browser", "think"):
        assert expected in names, f"missing {expected}"


def test_validate_outbound_tools_accepts_current_registry(monkeypatch) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    run_worker.validate_outbound_tools()  # must not raise


def test_validate_outbound_tools_accepts_gemini_only_without_connectors(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test")
    run_worker.validate_outbound_tools()


# --- installed strands_tools package (no symlink farm) --------------------------


def test_worker_has_no_symlink_farm_bootstrap() -> None:
    """The ``orchestrator/tools/`` symlink farm and its bootstrap are gone.

    ``strands_tools`` is consumed as the installed package; ``load_tool``
    resolves module names inside it (todo 8).
    """
    assert not hasattr(run_worker, "ensure_strands_tools_dir")
    assert not hasattr(run_worker, "_SKIP_COMMUNITY_FILES")
    assert not (run_worker._ROOT / "tools").exists()
    source = (run_worker._ROOT / "run_worker.py").read_text(encoding="utf-8")
    assert "ensure_strands_tools_dir" not in source
    assert "_SKIP_COMMUNITY_FILES" not in source


def test_worker_activities_register_the_public_load_tool_activity() -> None:
    """The worker boots with ``load_tool`` as a registered Temporal activity."""
    from temporalio import activity

    from load_tool import load_tool_activity

    names = {
        defn.name
        for defn in (
            activity._Definition.from_callable(fn) for fn in run_worker.WORKER_ACTIVITIES
        )
        if defn is not None
    }
    assert "load_tool" in names
    assert load_tool_activity in run_worker.WORKER_ACTIVITIES


def test_validate_outbound_tools_rejects_bad_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    good = run_worker.workflow_tool_specs()
    broken = [*good, {"name": "", "description": "", "inputSchema": {"json": {"type": "object"}}}]
    monkeypatch.setattr(run_worker, "workflow_tool_specs", lambda: broken)
    with pytest.raises(SystemExit):
        run_worker.validate_outbound_tools()


def test_validate_outbound_tools_rejects_unserializable(monkeypatch: pytest.MonkeyPatch) -> None:
    good = run_worker.workflow_tool_specs()
    poisoned = [
        *good,
        {
            "name": "bad",
            "description": "d",
            "inputSchema": {"json": {"type": "object", "properties": {"x": {"default": object()}}}},
        },
    ]
    monkeypatch.setattr(run_worker, "workflow_tool_specs", lambda: poisoned)
    with pytest.raises(SystemExit):
        run_worker.validate_outbound_tools()


# --- Temporal-native catalog registration ---------------------------------------
# The custom JSON readiness lease is gone: liveness is DescribeTaskQueue and the
# catalog is served by ModelCatalogWorkflow + the model_catalog activity. These
# checks read run_worker.main's own AST, so no worker/Temporal is started.


def _main_worker_call() -> ast.Call:
    tree = ast.parse(Path(run_worker.__file__).read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "Worker":
            return node
    raise AssertionError("run_worker.main does not construct a Worker")


def _kwarg_elements(call: ast.Call, name: str) -> list[str]:
    for keyword in call.keywords:
        if keyword.arg == name:
            assert isinstance(keyword.value, ast.List), f"{name} must be a list literal"
            return [ast.unparse(element) for element in keyword.value.elts]
    raise AssertionError(f"Worker(...) has no {name}= argument")


def test_worker_registers_model_catalog_workflow_and_activity() -> None:
    call = _main_worker_call()
    assert "ModelCatalogWorkflow" in _kwarg_elements(call, "workflows")
    from temporalio import activity

    from catalog_workflow import model_catalog

    assert model_catalog in run_worker.WORKER_ACTIVITIES
    assert (
        activity._Definition.from_callable(model_catalog).name == "model_catalog"
    )


def test_worker_installs_the_boot_catalog() -> None:
    source = Path(run_worker.__file__).read_text(encoding="utf-8")
    assert "set_catalog(model_catalog" in source or "set_catalog(" in source
    tree = ast.parse(source)
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    calls = [
        ast.unparse(node.func)
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
    ]
    assert any(name.endswith("set_catalog") for name in calls), calls


def test_no_readiness_lease_symbols_remain() -> None:
    """Nothing named for the deleted JSON lease survives in run_worker.

    Catches the whole family at once (the writer, the heartbeat task, the
    ownership-scoped cleanup, and the file path) rather than a fixed list, so a
    partially-reverted lease cannot slip back in under a new name.
    """
    leftovers = [name for name in vars(run_worker) if "readiness" in name.lower()]
    assert leftovers == [], (
        f"run_worker still exposes {leftovers}; the JSON lease is replaced by "
        f"Temporal-native liveness + ModelCatalogWorkflow"
    )
