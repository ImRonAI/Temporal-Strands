"""Verification suite for the official ``strands-google`` package (pinned 0.2.0).

Runs inside the isolated lane venv (``orchestrator/integrations/google-tools/.venv``),
NOT the shared ``orchestrator/.venv``. No custom wrappers are tested — only the
upstream package's exported tools, their Strands tool specs, safe no-credential
call behavior, and official Strands hot-load (``load_tools_from_file_path``)
compatibility.

No test sends mail, creates/modifies real resources, or reads user credential
stores. All GOOGLE_* env vars are cleared per-test so auto-detected local
credentials can never leak into a call.
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

GOOGLE_ENV_VARS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_OAUTH_CREDENTIALS",
    "GOOGLE_API_KEY",
    "GOOGLE_API_SCOPES",
    "BYPASS_TOOL_CONSENT",
)

EXPECTED_EXPORTS = ("use_google", "google_auth", "gmail_send", "gmail_reply")


@pytest.fixture(autouse=True)
def _no_google_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given: a process with no Google credentials or consent bypass."""
    for var in GOOGLE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Package identity and exports
# ---------------------------------------------------------------------------


class TestPackageExports:
    def test_distribution_version_pinned_when_installed(self) -> None:
        from importlib.metadata import version

        assert version("strands-google") == "0.2.0"

    def test_module_version_attr_carries_known_upstream_stale_value(self) -> None:
        """Upstream 0.2.0 ships ``__init__.__version__ = "0.1.0"`` (stale
        literal, confirmed against the PyPI sdist). Pin the quirk so a future
        upstream fix surfaces here instead of silently changing behavior.
        """
        import strands_google

        assert strands_google.__version__ == "0.1.0"

    def test_all_four_tools_exported_when_package_imported(self) -> None:
        import strands_google

        assert tuple(strands_google.__all__) == EXPECTED_EXPORTS
        for name in EXPECTED_EXPORTS:
            assert getattr(strands_google, name) is not None

    def test_mcp_entrypoint_importable_when_package_installed(self) -> None:
        from strands_google.mcp import TOOL_GROUPS, collect_tools

        assert set(TOOL_GROUPS) == {"use_google", "google_auth", "gmail"}
        tools = collect_tools(skip=set(), only=None)
        assert {t.tool_name for t in tools} == set(EXPECTED_EXPORTS)


# ---------------------------------------------------------------------------
# Strands AgentTool contract — every export is a decorated tool with a schema
# ---------------------------------------------------------------------------


class TestToolSpecs:
    @pytest.fixture(params=EXPECTED_EXPORTS)
    def exported_tool(self, request: pytest.FixtureRequest):
        import strands_google

        return getattr(strands_google, request.param)

    def test_export_is_agent_tool_when_inspected(self, exported_tool) -> None:
        from strands.types.tools import AgentTool

        assert isinstance(exported_tool, AgentTool)

    def test_spec_has_json_input_schema_when_inspected(self, exported_tool) -> None:
        spec = exported_tool.tool_spec
        assert spec["name"] == exported_tool.tool_name
        assert spec["description"]
        schema = spec["inputSchema"]
        json_schema = schema.get("json", schema)
        assert json_schema["type"] == "object"
        assert isinstance(json_schema["properties"], dict)

    def test_required_params_match_upstream_when_inspected(self) -> None:
        import strands_google

        expected_required = {
            "use_google": {"service", "version", "resource", "method"},
            "google_auth": set(),
            "gmail_send": {"to", "subject", "body"},
            "gmail_reply": {"message_id", "body"},
        }
        for name, required in expected_required.items():
            tool = getattr(strands_google, name)
            schema = tool.tool_spec["inputSchema"]
            json_schema = schema.get("json", schema)
            assert set(json_schema.get("required") or []) == required, name


# ---------------------------------------------------------------------------
# Safe no-credential call behavior (read-only / local-only)
# ---------------------------------------------------------------------------


class TestSafeCalls:
    def test_use_google_returns_error_when_no_credentials(self) -> None:
        """When: a read-only Gmail list runs with no credentials configured.

        Then: the tool returns a structured error dict — it never raises and
        never fabricates success.
        """
        from strands_google.use_google import use_google

        result = use_google._tool_func(
            service="gmail",
            version="v1",
            resource="users.messages",
            method="list",
            parameters={"userId": "me", "maxResults": 1},
        )
        assert result["status"] == "error"
        assert result["content"][0]["text"]

    def test_use_google_cancels_mutative_when_consent_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When: a mutative method is requested and the consent prompt gets 'n'.

        Then: the operation is canceled before any network or auth activity.
        """
        from strands_google.use_google import use_google

        monkeypatch.setattr("builtins.input", lambda *a: "n")
        result = use_google._tool_func(
            service="gmail",
            version="v1",
            resource="users.messages",
            method="send",
            parameters={"userId": "me"},
        )
        assert result["status"] == "error"
        assert "cancel" in result["content"][0]["text"].lower()

    def test_google_auth_returns_error_when_credentials_file_missing(
        self, tmp_path: Path
    ) -> None:
        """When: google_auth runs with a nonexistent credentials file.

        Then: it fails cleanly with setup instructions — no browser flow starts.
        """
        from strands_google.google_auth import google_auth

        result = google_auth._tool_func(
            credentials_file=str(tmp_path / "missing_credentials.json"),
            token_file=str(tmp_path / "token.json"),
        )
        assert result["status"] == "error"
        assert "Google Cloud Console" in result["content"][0]["text"]
        assert not (tmp_path / "token.json").exists()

    def test_create_message_encodes_rfc2822_when_given_plaintext(self) -> None:
        """Pure local helper: base64url message assembly (no network)."""
        from strands_google.gmail_helpers import create_message

        raw = create_message(
            sender="a@example.com",
            to="b@example.com",
            subject="Subject line",
            body="Body text",
            cc=["c@example.com"],
        )
        decoded = base64.urlsafe_b64decode(raw.encode()).decode()
        assert "To: b@example.com" in decoded
        assert "Subject: Subject line" in decoded
        assert "Cc: c@example.com" in decoded
        assert "Body text" in decoded

    def test_default_scopes_are_read_only_when_env_unset(self) -> None:
        from strands_google.use_google import get_default_scopes

        scopes = get_default_scopes()
        assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
        assert "https://www.googleapis.com/auth/drive.readonly" in scopes

    def test_scopes_env_override_wins_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from strands_google.use_google import get_default_scopes

        monkeypatch.setenv(
            "GOOGLE_API_SCOPES",
            "https://www.googleapis.com/auth/gmail.readonly, https://www.googleapis.com/auth/drive.file",
        )
        assert get_default_scopes() == [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ]


# ---------------------------------------------------------------------------
# Official Strands hot-load compatibility (load_tool pathway)
# ---------------------------------------------------------------------------


def _installed_module_path(module: str) -> Path:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin)


class TestHotLoadCompatibility:
    """Official ``strands_tools.load_tool`` delegates to
    ``strands.tools.loader.load_tools_from_file_path(path)``. Every
    strands-google module must load through that exact pathway when pointed at
    its installed site-packages file.
    """

    @pytest.mark.parametrize(
        ("module", "expected_tools"),
        [
            ("strands_google.use_google", {"use_google"}),
            ("strands_google.google_auth", {"google_auth"}),
            ("strands_google.gmail_helpers", {"gmail_send", "gmail_reply"}),
        ],
    )
    def test_module_file_loads_when_passed_to_official_loader(
        self, module: str, expected_tools: set[str]
    ) -> None:
        from strands.tools.loader import load_tools_from_file_path

        loaded = load_tools_from_file_path(str(_installed_module_path(module)))
        assert expected_tools <= {t.tool_name for t in loaded}

    def test_loaded_spec_matches_direct_import_when_compared(self) -> None:
        from strands.tools.loader import load_tools_from_file_path
        from strands_google import use_google

        loaded = load_tools_from_file_path(
            str(_installed_module_path("strands_google.use_google"))
        )
        by_name = {t.tool_name: t for t in loaded}
        assert by_name["use_google"].tool_spec == use_google.tool_spec
