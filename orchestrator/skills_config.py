"""Agent Skills catalog for the orchestrator (agentskills.io / aws-samples).

Patterns from sample-strands-agents-agentskills:
- Pattern 2: ``create_skill_tool`` → inline ``skill(skill_name)`` (progressive disclosure)
- Pattern 3: ``create_skill_agent_tool`` → ``use_skill(skill_name, request)`` (isolated sub-agent)

Skills directory defaults to sibling ``../../strands-tools/skills``; override with ``SKILLS_DIR``.
``configure_skills`` registers names for graph ``skill_agent`` nodes (``strands_graph_tool``).
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from strands_graph_tool import configure_skills

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _ROOT.parent
_DEFAULT_SKILLS_DIR = (_REPO_ROOT.parent / "strands-tools" / "skills").resolve()

# Vendored agentskills from aws-samples/sample-strands-agents-agentskills layout.
_AGENTSKILLS_SRC = (
    _REPO_ROOT.parent
    / "strands-tools"
    / "src"
    / "strands_tools"
    / "sessions_and_skills"
    / "sample_agent_skills"
)


def _ensure_agentskills_path() -> None:
    path = str(_AGENTSKILLS_SRC)
    if path not in sys.path and _AGENTSKILLS_SRC.is_dir():
        sys.path.insert(0, path)


def skills_dir() -> Path:
    raw = os.environ.get("SKILLS_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else _DEFAULT_SKILLS_DIR


def _dedupe_skills(skills: list[Any]) -> list[Any]:
    """Keep one entry per skill name (last discovery wins — log duplicates)."""
    by_name: dict[str, Any] = {}
    for skill in skills:
        prior = by_name.get(skill.name)
        if prior is not None and prior.path != skill.path:
            logger.warning(
                "duplicate skill name %r: keeping %s over %s",
                skill.name,
                skill.path,
                prior.path,
            )
        by_name[skill.name] = skill
    return sorted(by_name.values(), key=lambda s: s.name)


@lru_cache(maxsize=1)
def discovered_skills() -> tuple[Any, ...]:
    """Discover skills once per worker process."""
    _ensure_agentskills_path()
    from agentskills import discover_skills

    directory = skills_dir()
    if not directory.is_dir():
        logger.warning("skills directory missing: %s", directory)
        return ()
    found = _dedupe_skills(discover_skills(directory))
    logger.info("discovered %d skills in %s", len(found), directory)
    return tuple(found)


def skill_subagent_tools() -> list[Any]:
    """Tools isolated skill sub-agents may call (Pattern 3 example parity)."""
    from strands_tools import file_read, file_write

    return [file_read, file_write]


def ensure_skills_configured(model_factory: Any | None = None) -> int:
    """Register catalog for graph ``skill_agent`` nodes; return skill count."""
    skills = list(discovered_skills())
    model = model_factory() if callable(model_factory) else None
    configure_skills(
        skills,
        base_agent_model=model,
        additional_tools=skill_subagent_tools(),
    )
    return len(skills)


def skills_system_prompt_suffix() -> str:
    """Compact catalog header — full list via ``list_skills`` tool."""
    skills = discovered_skills()
    directory = skills_dir()
    if not skills:
        return (
            "\n\n## Agent Skills\n"
            f"No skills discovered at `{directory}`. "
            "Install with `pnpm skills:add owner/repo` or set `SKILLS_DIR`.\n"
        )
    loader = _ROOT / "skills_loader.py"
    return (
        "\n\n## Agent Skills\n"
        f"{len(skills)} skills registered under `{directory}`.\n"
        f"- **Discover**: `load_tool(path=\"{loader}\", name=\"list_skills\")` "
        "then call `list_skills`.\n"
        f"- **Pattern 2 (inline)**: `load_tool(path=\"{loader}\", name=\"skill\")` "
        "then `skill(skill_name=...)` loads full instructions into your context.\n"
        "- **Pattern 3 (meta-tool)**: permanent `use_skill(skill_name, request)` runs an "
        "isolated sub-agent with the skill's SKILL.md as system prompt.\n"
        "- **Graph formations**: `skill_agent` nodes reference skill names from the registry "
        "(same names as `list_skills`).\n"
        "- **Install more**: `pnpm skills:add <owner/repo>` (npx skills CLI).\n"
    )


def augmented_system_prompt(base: str) -> str:
    return base.rstrip() + skills_system_prompt_suffix()


def create_inline_skill_tool() -> Any:
    """Pattern 2: ``skill(skill_name)`` factory product."""
    _ensure_agentskills_path()
    from agentskills import create_skill_tool

    return create_skill_tool(list(discovered_skills()), skills_dir())


def create_use_skill_tool(model: Any) -> Any:
    """Pattern 3: ``use_skill(skill_name, request)`` factory product."""
    _ensure_agentskills_path()
    from agentskills import create_skill_agent_tool

    return create_skill_agent_tool(
        list(discovered_skills()),
        skills_dir(),
        base_agent_model=model,
        additional_tools=skill_subagent_tools(),
    )


def list_skills_text(query: str = "") -> str:
    """Human-readable skill catalog for ``list_skills``."""
    skills = discovered_skills()
    needle = query.strip().lower()
    lines: list[str] = []
    for skill in skills:
        hay = f"{skill.name} {skill.description}".lower()
        if needle and needle not in hay:
            continue
        desc = (skill.description or "").replace("\n", " ").strip()
        if len(desc) > 160:
            desc = desc[:157] + "..."
        lines.append(f"- **{skill.name}**: {desc}")
    header = f"{len(lines)} skill(s)"
    if needle:
        header += f" matching {query!r}"
    header += f" (of {len(skills)} registered)\n"
    return header + "\n".join(lines)


def skills_loader_path() -> Path:
    return _ROOT / "skills_loader.py"
