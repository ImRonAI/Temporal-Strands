"""Agent Skills catalog for the orchestrator (agentskills.io / aws-samples).

Patterns from sample-strands-agents-agentskills:
- Pattern 2: ``create_skill_tool`` → inline ``skill(skill_name)`` (progressive disclosure)
- Pattern 3: ``create_skill_agent_tool`` → ``use_skill(skill_name, request)`` (isolated sub-agent)

Skills directory defaults to ``../../strands-tools/src/skills``; override with ``SKILLS_DIR``.
``configure_skills`` registers names for graph ``skill_agent`` nodes (``graph_tool``).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from graph_tool import configure_skills

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _ROOT.parent
_STRANDS_TOOLS = _REPO_ROOT.parent / "strands-tools"

# User-supplied catalog: the skills-CLI dump at ``src/skills`` IS the collection
# (supersedes the earlier plan of a curated sibling ``skills`` root, which was
# never created). discover_skills walks it once per process; entries missing
# required metadata are logged and skipped by the agentskills validator.
_DEFAULT_SKILLS_DIRS = ((_STRANDS_TOOLS / "src" / "skills").resolve(),)

# Vendored agentskills (aws-samples/sample-strands-agents-agentskills layout).
# Optional: when neither location is present the skills catalog is simply
# unavailable and everything degrades to "no skills" (telemetry.py pattern).
_AGENTSKILLS_SRCS = (
    _STRANDS_TOOLS / "src" / "strands_tools" / "sessions_and_skills" / "sample_agent_skills",
    _STRANDS_TOOLS / "src" / "sample_agent_skills",
)


class SkillsUnavailable(RuntimeError):
    """Raised by the skill tool factories when ``agentskills`` cannot be imported."""


def _ensure_agentskills_path() -> None:
    for src in _AGENTSKILLS_SRCS:
        path = str(src)
        if src.is_dir() and path not in sys.path:
            sys.path.insert(0, path)


def _import_agentskills() -> Any | None:
    """The ``agentskills`` module, or None (logged once) when it is not installed."""
    _ensure_agentskills_path()
    try:
        import agentskills  # type: ignore[import-not-found]
    except ImportError as error:
        if not getattr(_import_agentskills, "_warned", False):
            logger.warning(
                "agentskills unavailable (%s); Agent Skills catalog disabled. "
                "Install aws-samples/sample-strands-agents-agentskills or vendor it "
                "under %s.",
                error,
                _AGENTSKILLS_SRCS[0],
            )
            _import_agentskills._warned = True  # type: ignore[attr-defined]
        return None
    # Imported SKILL.md headers include flow collections and free-text fields.
    # Adapt the pinned SDK's parser for discovery AND instruction loading;
    # never rewrite the installed Markdown or reject it for optional metadata.
    from agentskills import parser

    if not getattr(parser._parse_skill_md, "_supports_flow", False):
        original = parser._parse_skill_md

        def parse_skill_md(content: str) -> tuple[dict, str]:
            try:
                return original(content)
            except parser.ParseError:
                match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
                if match is None:
                    raise
                try:
                    data = parser.strictyaml.dirty_load(match[1], allow_flow_style=True).data
                except parser.strictyaml.YAMLError:
                    # Only these fields drive the skill loader. Other imported
                    # metadata may be prose, duplicate keys, or vendor-specific.
                    data = {}
                    fields = list(re.finditer(r"^([\w-]+):", match[1], re.MULTILINE))
                    for index, field in enumerate(fields):
                        key = field[1]
                        if key not in {"name", "description", "license", "compatibility", "allowed-tools", "metadata"}:
                            continue
                        end = fields[index + 1].start() if index + 1 < len(fields) else len(match[1])
                        block = match[1][field.start():end]
                        try:
                            data.update(parser.strictyaml.dirty_load(block, allow_flow_style=True).data)
                        except parser.strictyaml.YAMLError:
                            text = block.split(":", 1)[1].strip()
                            if key in {"name", "description"} and text and text[0] not in "[{|>\"'!&*":
                                data[key] = text
                if not isinstance(data, dict):
                    raise parser.ParseError("SKILL.md frontmatter must be a mapping")
                if isinstance(data.get("metadata"), dict):
                    data["metadata"] = {str(k): str(v) for k, v in data["metadata"].items()}
                return data, match[2].strip()

        parse_skill_md._supports_flow = True
        parser._parse_skill_md = parse_skill_md
    return agentskills


def skills_dir() -> Path:
    raw = os.environ.get("SKILLS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    for candidate in _DEFAULT_SKILLS_DIRS:
        if candidate.is_dir():
            return candidate
    return _DEFAULT_SKILLS_DIRS[0]


def _dedupe_skills(skills: list[Any]) -> list[Any]:
    """Prefer canonical directory names, then stable path order, without spam."""
    by_name: dict[str, Any] = {}
    for skill in sorted(skills, key=lambda s: (
        Path(s.path).parent.name != s.name, str(s.path),
    )):
        prior = by_name.get(skill.name)
        if prior is not None and prior.path != skill.path:
            logger.debug(
                "duplicate skill name %r: keeping %s over %s",
                skill.name,
                prior.path,
                skill.path,
            )
        by_name.setdefault(skill.name, skill)
    if len(skills) != len(by_name):
        logger.info("Resolved %d duplicate skill entries to canonical paths", len(skills) - len(by_name))
    return sorted(by_name.values(), key=lambda s: s.name)


@lru_cache(maxsize=1)
def discovered_skills() -> tuple[Any, ...]:
    """Discover skills once per worker process.

    Returns an empty tuple, never raises, when the ``agentskills`` package or
    the skills directory is missing: the catalog is optional infrastructure.
    """
    agentskills = _import_agentskills()
    if agentskills is None:
        return ()

    directory = skills_dir()
    if not directory.is_dir():
        logger.warning("skills directory missing: %s", directory)
        return ()
    try:
        found = _dedupe_skills(agentskills.discover_skills(directory))
    except Exception as error:  # noqa: BLE001 - a broken SKILL.md must not kill startup
        logger.warning("skill discovery failed in %s: %s", directory, error)
        return ()
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


def resolve_skills(skill_names: list[str]) -> list[Any]:
    """The catalog entries for the given names, in the given order.

    Validation is the reference's own: ``agentskills.tool_utils.
    validate_skill_name`` raises ``SkillNotFoundError`` (listing the
    available names) for anything not in the discovered catalog.
    """
    agentskills = _import_agentskills()
    if agentskills is None:
        raise SkillsUnavailable("agentskills is not installed; Agent Skills are disabled")
    from agentskills.tool_utils import validate_skill_name

    skill_map = {skill.name: skill for skill in discovered_skills()}
    return [validate_skill_name(name, skill_map) for name in skill_names]


def skills_prompt(skill_names: list[str] | None = None) -> str:
    """The reference Phase-1 catalog prompt: ``agentskills.generate_skills_prompt``.

    Exact aws-samples/sample-strands-agents-agentskills wiring (examples 2
    and 3): every skill's name, description, and SKILL.md location in an
    ``<available_skills>`` XML block plus the ``<skills_instructions>``
    usage policy. ``skill_names`` scopes the catalog to that subset (same
    reference function over fewer skills). Empty string when the package or
    catalog is unavailable.
    """
    agentskills = _import_agentskills()
    if agentskills is None:
        return ""
    skills = (
        resolve_skills(skill_names)
        if skill_names is not None
        else list(discovered_skills())
    )
    return agentskills.generate_skills_prompt(skills)


def augmented_system_prompt(base: str) -> str:
    """Reference examples 2 and 3: ``f"{base_prompt}\\n\\n{skills_prompt}"``."""
    prompt = skills_prompt()
    if not prompt:
        return base
    return f"{base}\n\n{prompt}"


def create_inline_skill_tool(skill_names: list[str] | None = None) -> Any:
    """Pattern 2: ``skill(skill_name)`` factory product.

    ``skill_names`` scopes the tool to that subset of the catalog — the
    factory's ``skills`` list IS the tool's whole universe (reference
    ``create_skill_tool`` builds its ``skill_map`` from it), so a scoped
    tool can only ever load the assigned skills' instructions.
    """
    agentskills = _import_agentskills()
    if agentskills is None:
        raise SkillsUnavailable("agentskills is not installed; Agent Skills are disabled")

    skills = (
        resolve_skills(skill_names)
        if skill_names is not None
        else list(discovered_skills())
    )
    return agentskills.create_skill_tool(skills, skills_dir())


def create_use_skill_tool(
    model: Any, assigned_skills: list[str] | None = None
) -> Any:
    """Pattern 3: ``use_skill(skill_name, request)`` factory product.

    ``assigned_skills`` names skills the caller assigns to each skill
    sub-agent as **inline tools** (Pattern 2): the sub-agent gets a scoped
    ``skill(skill_name)`` tool plus those skills' catalog metadata, and
    consumes them in its own context — the reference ``additional_tools``
    parameter, filled with the reference ``create_skill_tool`` product.
    Never nested sub-agents: ``use_skill`` itself is not in the tool list.
    """
    agentskills = _import_agentskills()
    if agentskills is None:
        raise SkillsUnavailable("agentskills is not installed; Agent Skills are disabled")

    additional_tools = skill_subagent_tools()
    if assigned_skills:
        additional_tools.append(create_inline_skill_tool(assigned_skills))
    return agentskills.create_skill_agent_tool(
        list(discovered_skills()),
        skills_dir(),
        base_agent_model=model,
        additional_tools=additional_tools,
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
