"""Load via ``load_tool`` to expose Agent Skills Pattern 2 tools.

``load_tool(path=<this file>, name="list_skills")`` — catalog discovery.
``load_tool(path=<this file>, name="skill")`` — Pattern 2 inline activation.

Pattern 3 ``use_skill`` is a permanent Temporal activity (``use_skill_activity``).
Importing this module calls ``configure_skills`` for graph ``skill_agent`` nodes.
"""

from __future__ import annotations

from strands import tool

from skills_config import (
    create_inline_skill_tool,
    ensure_skills_configured,
    list_skills_text,
)

# Side effect: graph skill_agent nodes resolve names after this module loads.
ensure_skills_configured()

# Pattern 2 — progressive disclosure inline tool (factory assigns @tool).
skill = create_inline_skill_tool()


@tool
def list_skills(query: str = "") -> str:
    """List registered Agent Skills (name + description). Optional substring filter."""
    return list_skills_text(query)
