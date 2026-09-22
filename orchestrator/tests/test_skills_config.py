"""Agent Skills catalog and load_tool bridge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import skills_config


def test_duplicate_skills_prefer_canonical_path_without_warning(caplog) -> None:
    copies = [SimpleNamespace(name="example", path=path) for path in [
        "/skills/skills_example/SKILL.md", "/skills/example/SKILL.md", "/skills/example_upstream/SKILL.md",
    ]]
    for entries in [copies, list(reversed(copies))]:
        result = skills_config._dedupe_skills(entries)
        assert [skill.path for skill in result] == ["/skills/example/SKILL.md"]
    assert not caplog.records


def test_duplicate_skills_without_canonical_path_have_stable_winner():
    copies = [SimpleNamespace(name="example", path=path) for path in [
        "/skills/z_copy/SKILL.md", "/skills/a_copy/SKILL.md",
    ]]
    assert skills_config._dedupe_skills(copies)[0].path == "/skills/a_copy/SKILL.md"
    assert skills_config._dedupe_skills(list(reversed(copies)))[0].path == "/skills/a_copy/SKILL.md"


def test_flow_yaml_discovery_and_instruction_loading(monkeypatch, tmp_path, caplog) -> None:
    directory = tmp_path / "flow"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        '---\nname: flow\ndescription: Flow YAML\ntags: [one, category:reasoning]\n'
        'metadata:\n  packages: {pandas: "2.2"}\nempty: []\n---\n# Instructions\nKeep this body.\n'
    )
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills = skills_config.discovered_skills()
    assert [skill.name for skill in skills] == ["flow"]
    from agentskills.parser import load_instructions
    assert load_instructions(skills[0].path) == "# Instructions\nKeep this body."
    assert not [record for record in caplog.records if record.levelname in {"WARNING", "ERROR"}]


def test_missing_required_skill_description_still_warns(monkeypatch, tmp_path, caplog) -> None:
    directory = tmp_path / "invalid"
    directory.mkdir()
    (directory / "SKILL.md").write_text('---\nname: invalid\ntags: [unfinished\n---\nBody\n')
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    assert skills_config.discovered_skills() == ()
    assert "Skipping invalid skill" in caplog.text


def test_prose_header_and_vendor_metadata_do_not_discard_skill(monkeypatch, tmp_path, caplog):
    directory = tmp_path / "prose"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        '---\nname: prose\ndescription: Run checks: startup, docs, tests.\n'
        'version: 1\nversion: 2\nmetadata:\n  benchmarks:\n    - Success: >95%\n---\n'
        '# Instructions\n\n---\n\nKeep **all** Markdown.\n'
    )
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills = skills_config.discovered_skills()
    assert len(skills) == 1
    assert skills[0].description == "Run checks: startup, docs, tests."
    from agentskills.parser import load_instructions
    assert load_instructions(skills[0].path) == '# Instructions\n\n---\n\nKeep **all** Markdown.'
    assert not [record for record in caplog.records if record.levelname in {"WARNING", "ERROR"}]


def test_discovery_still_rejects_paths_outside_catalog(monkeypatch, tmp_path, caplog):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: outside\ndescription: External\n---\nInstructions\n")
    (catalog / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("SKILLS_DIR", str(catalog))
    assert skills_config.discovered_skills() == ()
    assert "Skipping unsafe path" in caplog.text


@pytest.fixture(autouse=True)
def _clear_skill_cache() -> None:
    skills_config.discovered_skills.cache_clear()
    yield
    skills_config.discovered_skills.cache_clear()


def test_default_skills_dir_is_the_user_supplied_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SKILLS_DIR", raising=False)
    collection = (
        Path(__file__).resolve().parents[2].parent / "strands-tools" / "src" / "skills"
    ).resolve()
    assert skills_config.skills_dir() == collection


def test_skills_dir_env_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    assert skills_config.skills_dir() == tmp_path.resolve()


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


def test_skills_loader_exports_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    import skills_loader

    assert skills_loader.list_skills.tool_name == "list_skills"
    assert skills_loader.skill.tool_name == "skill"


def test_agent_skills_plugin_is_the_official_strands_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Official ``strands.AgentSkills`` for scoped nested agents.

    Skills are loaded on the worker as ``Skill`` instances so the plugin never
    touches the filesystem inside workflow code. The plugin registers the
    ``skills`` tool and injects only name/description/location per skill;
    SKILL.md bodies stay out of the prompt until the model calls ``skills``.
    """
    from strands import AgentSkills

    fixtures = (
        Path(__file__).resolve().parents[2]
        / ".."
        / "strands-tools"
        / "tests"
        / "fixtures_skills"
    ).resolve()
    monkeypatch.setenv("SKILLS_DIR", str(fixtures))
    skills_config.catalog_skills.cache_clear()

    plugin = skills_config.agent_skills_plugin()
    assert isinstance(plugin, AgentSkills)
    assert [tool.tool_name for tool in plugin.tools] == ["skills"]
    names = {skill.name for skill in plugin.get_available_skills()}
    assert "wf-skill" in names

    xml = plugin._generate_skills_xml()
    assert xml.startswith("<available_skills>")
    assert "<name>wf-skill</name>" in xml
    assert "<skills_instructions>" not in xml
    wf_skill = next(s for s in plugin.get_available_skills() if s.name == "wf-skill")
    assert wf_skill.instructions
    assert wf_skill.instructions not in xml


def test_agent_skills_plugin_without_catalog_has_no_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills_config.catalog_skills.cache_clear()
    plugin = skills_config.agent_skills_plugin()
    assert plugin.get_available_skills() == []
    assert "No skills are currently available" in plugin._generate_skills_xml()


def test_augmented_system_prompt_is_gone() -> None:
    """The 527-skill catalog dump must never be baked into a system prompt again."""
    assert not hasattr(skills_config, "augmented_system_prompt")


def _write_skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Skill {name}\n---\n# {name} instructions\n"
    )


def test_scoped_inline_skill_tool_only_loads_assigned_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """create_inline_skill_tool(names) scopes Pattern 2 to the assigned subset:
    assigned skills load, unassigned catalog skills are rejected by the
    reference validate_skill_name inside the tool."""
    for name in ("alpha", "beta", "gamma"):
        _write_skill(tmp_path, name)
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))

    tool = skills_config.create_inline_skill_tool(["alpha", "beta"])
    assert "# alpha instructions" in tool("alpha")

    from agentskills.errors import SkillActivationError, SkillNotFoundError

    with pytest.raises((SkillNotFoundError, SkillActivationError)):
        tool("gamma")


def test_resolve_skills_rejects_unknown_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_skill(tmp_path, "alpha")
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))

    from agentskills.errors import SkillNotFoundError

    with pytest.raises(SkillNotFoundError, match="nope"):
        skills_config.resolve_skills(["nope"])


def test_scoped_skills_prompt_lists_only_assigned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("alpha", "beta"):
        _write_skill(tmp_path, name)
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))

    text = skills_config.skills_prompt(["alpha"])
    assert "<name>alpha</name>" in text
    assert "<name>beta</name>" not in text


def test_scoped_agent_skills_plugin_lists_only_assigned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("alpha", "beta"):
        _write_skill(tmp_path, name)
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills_config.catalog_skills.cache_clear()

    plugin = skills_config.agent_skills_plugin(["alpha"])
    assert [skill.name for skill in plugin.get_available_skills()] == ["alpha"]
    xml = plugin._generate_skills_xml()
    assert "<name>alpha</name>" in xml
    assert "<name>beta</name>" not in xml

    with pytest.raises(LookupError, match="nope"):
        skills_config.agent_skills_plugin(["nope"])
