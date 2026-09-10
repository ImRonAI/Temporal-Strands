"""Static framework-adherence gate for the orchestrator.

Every check is a pure ``ast`` scan over the top-level ``orchestrator/*.py``
modules (``tests/`` and the venv are excluded) and, where noted, a light
runtime import. A violation fails with the offending ``file:line`` in the
message so it can be fixed without grepping.

The gate encodes the "no custom reimplementation of a framework primitive"
policy from the framework-adherence reset: providers subclass a concrete
Strands provider, hooks never call ``workflow.execute_activity`` directly,
``timedelta`` literals live only in ``config.py``, private tool internals are
never touched, and bare ``except Exception: pass`` is forbidden.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]

# Modules that are not orchestrator source (excluded from the scan).
_EXCLUDED = {"tests"}


def _source_modules() -> list[Path]:
    """Return the top-level ``orchestrator/*.py`` source files to scan."""
    files = []
    for path in sorted(ORCHESTRATOR_DIR.glob("*.py")):
        if path.name in _EXCLUDED:
            continue
        files.append(path)
    return files


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_stem_to_path(stem: str) -> Path:
    return ORCHESTRATOR_DIR / f"{stem}.py"


def _file_label(path: Path) -> str:
    return f"orchestrator/{path.name}"


# ---------------------------------------------------------------------------
# Helpers shared by the per-check assertions.
# ---------------------------------------------------------------------------


def _imported_names(tree: ast.Module) -> set[tuple[str, str]]:
    """Return ``(module, name)`` pairs imported (import/import-from/alias)."""
    names: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # `import a.b` binds the top-level name `a`; `import a.b as c` binds `c`.
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                names.add(("", bound))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                names.add((module, alias.asname or alias.name))
    return names


# ---------------------------------------------------------------------------
# (a) No ClassDef subclasses bare ``Model`` from strands.models
# ---------------------------------------------------------------------------


def test_a_no_bare_strands_model_subclass() -> None:
    for path in _source_modules():
        tree = _parse(path)
        imported = _imported_names(tree)
        imported_model = any(
            (module, name) == ("strands.models", "Model")
            or (module, name) == ("strands.models.model", "Model")
            for module, name in imported
        )
        if not imported_model:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                base_name = ast.unparse(base)
                # Bare ``Model`` only — a qualified ``strands.models.Model``
                # subclass is equally forbidden (same framework primitive).
                if base_name == "Model" or base_name == "strands.models.Model":
                    pytest.fail(
                        f"(a) {_file_label(path)}:{node.lineno} class "
                        f"{node.name} subclasses bare {base_name!r} imported "
                        f"from strands.models; subclass a concrete Strands "
                        f"provider instead (e.g. OpenAIResponsesModel)"
                    )


# ---------------------------------------------------------------------------
# (b) PerplexityModel MRO includes OpenAIResponsesModel
# ---------------------------------------------------------------------------
# Written to the TARGET state: a concurrent lane is rebasing PerplexityModel
# onto ``strands.models.openai_responses.OpenAIResponsesModel``. This check
# may fail until that lane lands — expected, not a defect in the gate.


def test_b_perplexity_model_subclasses_openai_responses_model() -> None:
    path = _module_stem_to_path("perplexity_model")
    spec = importlib.util.spec_from_file_location("perplexity_model", path)
    assert spec is not None and spec.loader is not None
    sys.path.insert(0, str(ORCHESTRATOR_DIR))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if str(ORCHESTRATOR_DIR) in sys.path:
            sys.path.remove(str(ORCHESTRATOR_DIR))

    perplexity_model = getattr(module, "PerplexityModel", None)
    assert perplexity_model is not None, (
        f"(b) {_file_label(path)} does not define PerplexityModel"
    )

    from strands.models.openai_responses import OpenAIResponsesModel

    assert issubclass(perplexity_model, OpenAIResponsesModel), (
        f"(b) {_file_label(path)}: PerplexityModel MRO must include "
        f"OpenAIResponsesModel; got bases "
        f"{[c.__name__ for c in perplexity_model.__mro__]}"
    )


# ---------------------------------------------------------------------------
# (c) GeminiModel has no hand-rolled ``_format_chunk({"chunk_type"`` frame
# ---------------------------------------------------------------------------


def test_c_gemini_model_has_no_format_chunk_literal() -> None:
    path = _module_stem_to_path("gemini_model")
    src = path.read_text(encoding="utf-8")
    assert '_format_chunk({"chunk_type"' not in src, (
        f"(c) {_file_label(path)} reimplements stream chunk formatting; "
        f"delete the custom _format_chunk parser and inherit from the parent"
    )


# ---------------------------------------------------------------------------
# (d) think_activity imports the installed strands_tools.think
# ---------------------------------------------------------------------------


def test_d_think_activity_imports_strands_tools_think() -> None:
    path = _module_stem_to_path("think_activity")
    tree = _parse(path)
    imported = _imported_names(tree)
    ok = any(module == "strands_tools.think" for module, _ in imported)
    assert ok, (
        f"(d) {_file_label(path)} must delegate to the installed "
        f"strands_tools.think (from strands_tools.think import ...); no "
        f"reimplementation allowed"
    )


# ---------------------------------------------------------------------------
# (e) No ``execute_activity`` Call inside any HookProvider subclass
# ---------------------------------------------------------------------------
# Written to the TARGET state: a concurrent lane is re-expressing hooks on
# ``activity_as_hook``. May fail until that lane lands — expected.


def test_e_no_execute_activity_in_hook_provider() -> None:
    for path in _source_modules():
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("HookProvider" in ast.unparse(b) for b in node.bases):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                fn = ast.unparse(sub.func)
                if "execute_activity" in fn:
                    pytest.fail(
                        f"(e) {_file_label(path)}:{sub.lineno} class "
                        f"{node.name} calls {fn!r} inside a HookProvider; "
                        f"hooks must run through activity_as_hook"
                    )


# ---------------------------------------------------------------------------
# (f) No ``timedelta(`` call outside config.py
# ---------------------------------------------------------------------------
# Written to the TARGET state: concurrent lanes are moving the remaining
# inline literals into config.py. May fail until they land — expected.


def test_f_no_timedelta_outside_config() -> None:
    for path in _source_modules():
        if path.name == "config.py":
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = ast.unparse(node.func)
            if fn == "timedelta" or fn == "datetime.timedelta":
                pytest.fail(
                    f"(f) {_file_label(path)}:{node.lineno} timedelta literal "
                    f"outside config.py; move the constant to config.py and "
                    f"reference it"
                )


# ---------------------------------------------------------------------------
# (g) No attribute access ``._tool_func``
# ---------------------------------------------------------------------------
# Written to the TARGET state: a concurrent lane is removing the private
# access and wiring load_tool via the public activity. May fail until then.


def test_g_no_private_tool_func_access() -> None:
    for path in _source_modules():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_tool_func":
                pytest.fail(
                    f"(g) {_file_label(path)}:{node.lineno} private attribute "
                    f"access {ast.unparse(node)!r}; use the public load_tool "
                    f"activity / strands_tools.load_tool instead"
                )


# ---------------------------------------------------------------------------
# (h) No bare ``except ...: pass`` handler
# ---------------------------------------------------------------------------


def test_h_no_empty_except_handler() -> None:
    for path in _source_modules():
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                pytest.fail(
                    f"(h) {_file_label(path)}:{node.lineno} empty except "
                    f"handler (bare `pass`); handle the error or propagate it"
                )