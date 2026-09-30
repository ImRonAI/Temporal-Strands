"""load_tool lists, unloads, and stores subagent packages."""

import json
from pathlib import Path

import pytest

import load_tool
from load_tool import STRANDS_TOOLS_DIR, _inventory, load_tool_activity, tool_file_path


def test_nested_ids_resolve_inside_the_package() -> None:
    assert tool_file_path("dicom") == str(STRANDS_TOOLS_DIR / "dicom" / "strands_tools.py")
    assert tool_file_path("fhir") == str(STRANDS_TOOLS_DIR / "fhir" / "fhir_tools.py")
    assert tool_file_path("forge") == str(STRANDS_TOOLS_DIR / "forge" / "strands_tools.py")
    assert tool_file_path("calculator") == str(STRANDS_TOOLS_DIR / "calculator.py")
    assert tool_file_path("/etc/passwd") == "/etc/passwd"


def test_list_tools_includes_the_four_sections(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(load_tool, "_PACKAGES_PATH", tmp_path / "packages.json")
    text = _inventory()
    assert "<tools>" in text and 'name="calculator"' in text
    assert 'name="dicom_read_metadata"' in text
    assert 'name="fhir.terminology.lookup"' in text
    assert 'name="forge.openapi.load"' in text
    assert "<skills>" in text
    assert '<server name="shell" status="connected"' in text
    assert 'name="openapi"' in text and 'status="started"' in text
    assert 'command="npx"' in text
    assert "coverageapi.json" in text
    assert "<openapi_schemas>" in text and 'path="Telnyx.yml"' in text
    assert "<packages>" in text


@pytest.mark.asyncio
async def test_unload_refuses_a_tool_that_was_not_loaded(monkeypatch) -> None:
    async def missing(_name: str):
        return None

    monkeypatch.setattr(load_tool, "_loaded_tool_path", missing)
    result = await load_tool_activity(name="shell", action="unload")
    assert result["status"] == "error"
    assert "shell" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_unload_drops_a_loaded_name(monkeypatch) -> None:
    async def present(_name: str):
        return "/tmp/made_tool.py"

    monkeypatch.setattr(load_tool, "_loaded_tool_path", present)
    agent = load_tool._worker_agent()
    agent.tool_registry.registry["made_tool"] = object()
    result = await load_tool_activity(name="made_tool", action="unload")
    assert result["status"] == "success"
    assert "made_tool" not in agent.tool_registry.registry


@pytest.mark.asyncio
async def test_packages_save_replace_and_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(load_tool, "_PACKAGES_PATH", tmp_path / "packages.json")
    saved = await load_tool_activity(
        name="imaging",
        action="save_package",
        tools=["dicom_read_metadata"],
        skills=["wf-skill"],
        mcp_servers=["openapi"],
    )
    assert saved["status"] == "success"
    replaced = await load_tool_activity(
        name="imaging",
        action="save_package",
        tools=["dicom_anonymize"],
        skills=[],
        mcp_servers=[],
    )
    assert replaced["status"] == "success"
    stored = json.loads((tmp_path / "packages.json").read_text())
    assert stored["imaging"]["tools"] == ["dicom_anonymize"]
    listed = await load_tool_activity(action="list_packages")
    assert "imaging" in listed["content"][0]["text"]
    deleted = await load_tool_activity(name="imaging", action="delete_package")
    assert deleted["status"] == "success"
    assert json.loads((tmp_path / "packages.json").read_text()) == {}
