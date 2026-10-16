# strands-fun-tools integration report

Verified 2026-09-07 in the isolated lane `orchestrator/integrations/fun-tools/`
(own `.venv`; the app's shared `orchestrator/.venv` was **not** touched — the
parent owns that serial install via `orchestrator/requirements.txt`).

## Authoritative source

| Item | Value |
| --- | --- |
| Upstream repo | <https://github.com/cagataycali/strands-fun-tools> (third-party community package by Cagatay Cali — **not** under the official `strands-agents` org) |
| Release / commit | `v0.5.0`, commit `741c090f952ea6e74c53270aa3bc97bd79637394` (published 2026-07-23) |
| PyPI | <https://pypi.org/project/strands-fun-tools/0.5.0/> (`strands_fun_tools-0.5.0-py3-none-any.whl`) |
| Layout | root layout `strands_fun_tools/` (not `src/`); setuptools; version from `__version__.py` attr |
| Console script | `strands-fun-tools = strands_fun_tools.mcp:main` (MCP server entrypoint) |

The user-supplied flattened collection at
`/Users/tims-stuff/Desktop/strands-tools/src/strands_tools/strands_fun_tools_*.py`
was diffed against the extracted PyPI 0.5.0 wheel: **byte-identical** for every
module checked (`__init__`, `utility`, `template`, `chess`, `human_typer`,
`spinner_generator`, plus clipboard/cursor/dialog/etc. matched via the copies
already in `orchestrator/tools/`). The flattened copies are genuine upstream —
no repackaging was needed and the **real PyPI distribution** was installed
instead of inventing packaging.

Version quirk (upstream bug, not ours): `strands_fun_tools.__version__` reads
`"0.1.0"` (stale hardcode in `__init__.py`); the built distribution version is
`0.5.0` (setuptools reads `strands_fun_tools/__version__.py`). Trust
`importlib.metadata.version("strands-fun-tools")`.

## What the lane installed (isolated `.venv` only)

Pins in `orchestrator/integrations/fun-tools/requirements.txt`:

```
strands-agents[gemini]==1.50.2        # matches app shared venv
strands-agents-tools==0.8.5           # matches app shared venv
strands-fun-tools[all]==0.5.0
sounddevice==0.5.5                    # from upstream [audio] extra
webrtcvad==2.0.10                     # from upstream [audio] extra
```

Deliberately **not** installed (bulk/torch-sized downloads, out of scope):
`ultralytics` (yolo_vision detection), `openai-whisper` (listen
transcription). Both tools import and hot-load fine and degrade gracefully at
call time. Upstream's `[all]` extra itself omits these; they live in the
`[vision]` / `[audio]` extras.

### Recommendation for the parent shared-venv install

Add to `orchestrator/requirements.txt` (parent-owned; serial install later):

```
strands-fun-tools[all]==0.5.0
```

Notes for the parent:
- Base deps are **unpinned upstream** (`strands-agents`, `strands-mcp-server`);
  the app's existing `strands-agents[gemini]==1.50.2` pin satisfies them — the
  lane confirmed 0.5.0 works against 1.50.2 / tools 0.8.5. `strands-mcp-server`
  (0.1.4 resolved in the lane) is pulled in for the package's own MCP
  entrypoint.
- 14 of the 17 tools already exist as loose files in `orchestrator/tools/`
  (12 byte-identical to upstream; `bluetooth`/`npm`/`yolo_vision` carry small
  local edits — the local `npm` adds a `RON_AGENT_SANDBOX_ROOT` cwd, the local
  `yolo_vision` adds `analyze_screen`/`analyze_image` actions). Installing the
  package adds `template`, `utility`, `chess`, `screen_reader`,
  `spinner_generator` and gives dotted-module loading; the parent decides
  precedence (`orchestrator/tools/` is searched first by
  `load_tool.tool_file_path`).

## Verification results — all 17 exported tools

Command: `.venv/bin/python -m pytest tests -q` → **71 passed** (lane suite
`tests/test_fun_tools_lane.py`); matrix script
`scripts/verify_fun_tools.py` → **17/17 exports import, 17/17 hot-load via
both official mechanisms**.

Every export is a native `strands.tools.decorator.DecoratedFunctionTool` with
a well-formed `tool_spec` (name matches, non-empty description,
JSON-serializable object schema). No wrappers or schema rewrites were created.

| Tool | Import | Hot-load (file / module) | Execution in lane |
| --- | --- | --- | --- |
| `human_typer` | ok | ok / ok | tested — empty-text validation path only (real call types into the active window) |
| `cursor` | ok | ok / ok | tested — read-only `position`; moves/clicks never run |
| `clipboard` | ok | ok / ok | tested — read-only `status` |
| `dynamic_package` | ok | ok / ok | tested — `list_functions` on stdlib `json` + missing-package failure |
| `template` | ok | ok / ok | tested — create+render in temp cwd + missing-name failure |
| `utility` | ok | ok / ok | tested — base64 round-trip + invalid-input + unknown-action failures |
| `chess` | ok | ok / ok | blocked at runtime — needs `stockfish` binary on PATH (`brew install stockfish`); clean error verified |
| `bluetooth` | ok | ok / ok | tested — read-only `status`; scans/connects never run |
| `screen_reader` | ok | ok / ok | blocked — every action screenshots (macOS Screen Recording permission) and OCR needs `tesseract` binary (`brew install tesseract`) |
| `yolo_vision` | ok | ok / ok | tested — read-only `status`; detection blocked (needs `ultralytics` + camera) |
| `face_recognition` | ok | ok / ok | blocked — camera + AWS Rekognition; needs `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` (or `AWS_PROFILE`) — standard boto3 chain, `boto3.client("rekognition")` |
| `take_photo` | ok | ok / ok | blocked — opens the camera (macOS Camera permission) |
| `listen` | ok | ok / ok | tested — read-only `status`; record/transcribe blocked (needs `openai-whisper` + mic permission) |
| `spinner_generator` | ok | ok / ok | tested — 0.2 s local spinner |
| `npm` | ok | ok / ok | tested — missing-package failure (local `node -e` require; needs `node` on PATH, present) |
| `dialog` | ok | ok / ok | blocked — interactive prompt_toolkit TUI blocks on user input (optional `DEV=true` env enables its dev mode) |
| `asciimatics_ui` | ok | ok / ok | blocked — full-screen terminal UI takes over the tty |

Failure paths exercised and passing: nonexistent tool file via official
`load_tool` (`status: error`), invalid base64, unknown `utility` action,
missing template name, nonexistent Python package, nonexistent npm package.

### Keys / permissions summary for blocked tools

| Blocker | What to set | Docs |
| --- | --- | --- |
| `face_recognition` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` (or `AWS_PROFILE`) | <https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html>; Rekognition: <https://docs.aws.amazon.com/rekognition/> |
| `chess` | none — install engine: `brew install stockfish` | <https://stockfishchess.org/> |
| `screen_reader` | none — `brew install tesseract` + System Settings → Privacy → Screen Recording | <https://github.com/tesseract-ocr/tesseract> |
| `take_photo` / `yolo_vision` detection | Camera permission; `uv pip install ultralytics` for detection | <https://docs.ultralytics.com/> |
| `listen` record/transcribe | Microphone permission; `uv pip install openai-whisper` | <https://github.com/openai/whisper> |
| consent prompts | `BYPASS_TOOL_CONSENT=true` (or `STRANDS_NON_INTERACTIVE=true`) for non-interactive `load_tool`; `STRANDS_DISABLE_LOAD_TOOL=true` disables it | `strands_tools/load_tool.py` docstring |

Sample startup / invocation once the parent installs the package:

```bash
cd orchestrator
BYPASS_TOOL_CONSENT=true .venv/bin/python - <<'PY'
from strands.tools.loader import load_tool_from_string
[t] = load_tool_from_string("strands_fun_tools.utility")
print(t.tool_spec["name"])  # -> utility
PY
```

## How official hot-loading accepts these files (incl. relative-import caveats)

Two official mechanisms, both verified against every module:

1. **Community `strands_tools.load_tool(path, name)`** (the tool already in
   this app's `PERMANENT_COMMUNITY_TOOLS`, wired through
   `orchestrator/load_tool.py`): expands `~`, requires the file to exist,
   prompts for consent unless `BYPASS_TOOL_CONSENT=true`, then calls the
   SDK's **deprecated** `ToolRegistry.load_tool_from_filepath` →
   `ToolLoader.load_tools` (`DeprecationWarning`; removal slated for SDK 2.0).
   Works today on installed site-packages files, e.g.
   `.venv/lib/python3.13/site-packages/strands_fun_tools/utility.py`.
2. **SDK `strands.tools.loader.load_tool_from_string`** (non-deprecated):
   accepts a file path **or** a dotted module path
   (`strands_fun_tools.utility`, or `module:function`). The dotted form uses
   `importlib.import_module` with full package context.

Relative-import caveats (SDK `_load_tool_module`, loader.py):

- File-path loading executes the file as a **bare top-level module**
  (`spec_from_file_location`, no `__package__`), so `from . import x` inside a
  tool file would raise `ImportError: attempted relative import with no known
  parent package`. **Not an issue here**: none of the 17 fun-tools modules use
  relative imports (only `__init__.py` does, and you never load `__init__.py`
  as a tool file).
- During file-path load the tool's parent directory is temporarily prepended
  to `sys.path`, so bare sibling imports resolve — and any siblings imported
  that way land in `sys.modules` under **unnamespaced** names and persist
  (documented SDK limitation). Loading from site-packages, the "siblings" are
  the already-installed package modules, so this is harmless.
- The loaded tool module itself is registered as `_strands_tool_<name>` in
  `sys.modules`, avoiding stdlib collisions — relevant for `chess` and
  `template`, whose bare names shadow the stdlib `chess`-alike / PyPI names.
  Prefer the dotted form (`strands_fun_tools.chess`) where possible.
- This app's Temporal integration (`orchestrator/load_tool.py`, parent-owned)
  wraps loaded I/O tools with `activity_as_tool`; nothing in this lane
  changes that. The fun tools are all I/O-flavored (GUI/hardware/subprocess)
  and would go through that same parent-owned `activity_as_tool` path — no
  custom wrappers were added by this lane.

## Lane contents (this integration's owned assets)

```
orchestrator/integrations/fun-tools/
├── .venv/                     # isolated; not the app venv
├── .gitignore                 # .venv, __pycache__, .pytest_cache
├── requirements.txt           # pins above
├── scripts/verify_fun_tools.py  # prints the 17-tool matrix (read-only)
└── tests/test_fun_tools_lane.py # 71 tests: imports, schemas, hot-load, safe calls
docs/integrations/fun-tools.md   # this report
```

Not committed to git; no shared files (requirements.txt at orchestrator root,
`run_worker.py`, `load_tool.py`, `agent.json`, `config.py`) were modified —
those remain parent-owned.
