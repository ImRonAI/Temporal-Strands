"""Agent Skills catalog and load_tool bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

import skills_config


@pytest.fixture(autouse=True)
def _clear_skill_cache() -> None:
    skills_config.discovered_skills.cache_clear()
    yield
    skills_config.discovered_skills.cache_clear()


def test_discover_fixture_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures = (
        Path(__file__).resolve().parents[2]
        / ".."
        / "strands-tools"
        / "tests"
        / "fixtures_skills"
    ).resolve()
    monkeypatch.setenv("SKILLS_DIR", str(fixtures))
    skills = skills_config.discovered_skills()
    names = {skill.name for skill in skills}
    assert "wf-skill" in names
    assert skills_config.list_skills_text("wf").startswith("1 skill")


def test_skills_loader_exports_tools() -> None:
    import skills_loader

    assert skills_loader.list_skills.tool_name == "list_skills"
    assert skills_loader.skill.tool_name == "skill"


def test_augmented_prompt_mentions_loader() -> None:
    text = skills_config.augmented_system_prompt("Base.")
    assert "list_skills" in text
    assert "use_skill" in text
