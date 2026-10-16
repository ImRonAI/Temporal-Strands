"""Print the strands-fun-tools 0.5.0 verification matrix.

Run from the lane dir: ``.venv/bin/python scripts/verify_fun_tools.py``

For every export in ``strands_fun_tools.__all__`` (plus any expected export
that failed its optional-dep import), reports:
- import status (package attribute present / ImportError-suppressed),
- native tool_spec name + required params,
- hot-load status through the SDK ``ToolLoader.load_tools`` (file path) and
  ``load_tool_from_string`` (dotted module path),
- whether the lane executed a safe call (see tests/) or why it is blocked.

Read-only: performs no tool executions itself.
"""

from __future__ import annotations

import json
from pathlib import Path

EXPECTED = [
    "human_typer", "cursor", "clipboard", "dynamic_package", "template",
    "utility", "chess", "bluetooth", "screen_reader", "yolo_vision",
    "face_recognition", "take_photo", "listen", "spinner_generator",
    "npm", "dialog", "asciimatics_ui",
]

# Why direct execution is limited for some tools (import/hot-load still pass).
EXECUTION_NOTES = {
    "human_typer": "executed (empty-text validation path only; real typing controls the user's keyboard)",
    "cursor": "executed read-only `position` (skips if GUI/Accessibility denied); moves/clicks never run",
    "clipboard": "executed read-only `status`; clipboard writes never run",
    "dynamic_package": "executed `list_functions` on stdlib json + missing-package failure path",
    "template": "executed create+render in a temp cwd (import creates cwd()/templates)",
    "utility": "executed base64 round-trip + invalid-input and unknown-action failure paths",
    "chess": "executed `evaluate` (clean error if stockfish binary missing: `brew install stockfish`)",
    "bluetooth": "executed read-only `status`; BLE scans/connects never run",
    "screen_reader": "NOT executed: every action captures the screen (needs macOS Screen Recording permission)",
    "yolo_vision": "executed read-only `status`; detection blocked (ultralytics not installed — torch-sized dep)",
    "face_recognition": "NOT executed: requires camera + AWS Rekognition (boto3) credentials",
    "take_photo": "NOT executed: opens the camera",
    "listen": "executed read-only `status`; recording/transcription blocked (openai-whisper not installed)",
    "spinner_generator": "executed a 0.2s local spinner",
    "npm": "executed missing-package failure path (local node require, no install)",
    "dialog": "NOT executed: interactive prompt_toolkit TUI blocks on user input",
    "asciimatics_ui": "NOT executed: full-screen terminal UI takes over the tty",
}


def main() -> None:
    import strands_fun_tools
    from strands.tools.decorator import DecoratedFunctionTool
    from strands.tools.loader import ToolLoader, load_tool_from_string

    pkg_dir = Path(strands_fun_tools.__file__).parent
    rows = []
    for name in EXPECTED:
        row: dict[str, object] = {"tool": name}
        exported = getattr(strands_fun_tools, name, None)
        if not isinstance(exported, DecoratedFunctionTool):
            row["import"] = "BLOCKED (optional dependency missing)"
            rows.append(row)
            continue
        row["import"] = "ok"
        spec = exported.tool_spec
        schema = spec["inputSchema"]["json"]
        row["spec_name"] = spec["name"]
        row["required"] = schema.get("required", [])
        try:
            loaded = ToolLoader.load_tools(str(pkg_dir / f"{name}.py"), name)
            row["hotload_file"] = "ok" if any(t.tool_name == name for t in loaded) else "MISSING"
        except Exception as exc:  # noqa: BLE001
            row["hotload_file"] = f"FAILED: {exc}"
        try:
            loaded = load_tool_from_string(f"strands_fun_tools.{name}")
            row["hotload_module"] = "ok" if any(t.tool_name == name for t in loaded) else "MISSING"
        except Exception as exc:  # noqa: BLE001
            row["hotload_module"] = f"FAILED: {exc}"
        row["execution"] = EXECUTION_NOTES[name]
        rows.append(row)

    print(json.dumps(rows, indent=2))
    blocked = [r["tool"] for r in rows if r["import"] != "ok"]
    print(f"\n{len(rows) - len(blocked)}/{len(rows)} exports import; blocked: {blocked or 'none'}")


if __name__ == "__main__":
    main()
