# Google tools integration (`strands-google`)

Status: **verified in isolated lane** — pending parent install into shared `orchestrator/.venv`.

## Upstream identity

| Field | Value |
| --- | --- |
| Package | `strands-google` **0.2.0** (PyPI) |
| Repository | <https://github.com/cagataycali/strands-google> (HEAD `0bbe730` at verification time) |
| Official docs | <https://strandsagents.com/docs/community/tools/strands-google/> and the repo README (mirrored at `orchestrator/integrations/google-tools/UPSTREAM_README.md`) |
| Supplied source | The flattened files in `~/Desktop/strands-tools/src/strands_tools/strands_google_*.py` are **byte-identical** to the PyPI 0.2.0 sdist (`__init__.py`, `use_google.py`, `google_auth.py`, `gmail_helpers.py`, `mcp.py`). The flattened copy is unpackageable as-is, so the official pinned release is installed instead — no custom wrappers, no re-packaging. |
| Known quirk | `strands_google.__version__` reports `"0.1.0"` inside the 0.2.0 release (stale upstream literal). Pin decisions use `importlib.metadata.version("strands-google")`, which correctly reports `0.2.0`. |

## Exported tools (all native Strands `@tool` → `AgentTool`)

| Tool | Module | Required params | Purpose |
| --- | --- | --- | --- |
| `use_google` | `strands_google.use_google` | `service`, `version`, `resource`, `method` | Universal Discovery-API access to 200+ Google APIs (`resource.method` reflection) |
| `google_auth` | `strands_google.google_auth` | — | Interactive OAuth 2.0 flow; writes an authorized-user token JSON |
| `gmail_send` | `strands_google.gmail_helpers` | `to`, `subject`, `body` | Sends mail via `use_google` with automatic RFC 2822 + base64url encoding |
| `gmail_reply` | `strands_google.gmail_helpers` | `message_id`, `body` | Threads a reply onto an existing message |

`strands_google.mcp` additionally provides the `strands-google` MCP stdio/HTTP server (`TOOL_GROUPS = use_google / google_auth / gmail`) if the orchestrator ever prefers the MCP route over direct tools.

## Verification results (lane venv, Python 3.13.13)

Suite: `orchestrator/integrations/google-tools/tests/test_strands_google.py` — **23 passed**.

- All four exports import and are `strands.types.tools.AgentTool` instances with valid JSON input schemas; required-parameter sets match upstream signatures.
- `use_google` with **no credentials** returns a structured `{"status": "error"}` dict (no exception, no fake success).
- Mutative-method consent guard verified: `method="send"` with denied consent cancels before any auth/network activity.
- `google_auth` with a missing credentials file fails cleanly with setup instructions and writes no token.
- Local-only helpers exercised: `create_message` (RFC 2822 base64url assembly), `get_default_scopes` (read-only defaults + `GOOGLE_API_SCOPES` override).
- Hot-load: every module file loads through official `strands.tools.loader.load_tools_from_file_path` (the exact pathway `strands_tools.load_tool` uses); loaded `tool_spec` is identical to the direct-import spec.
- **No real Google API calls were made** — no keys/OAuth are configured, and the tests scrub all `GOOGLE_*` env vars so local credentials can never leak in.

## Hot-load pathway (discoverable, not permanent)

The official `strands_tools.load_tool(path, name)` → `load_tools_from_file_path(path)` pathway works against the installed package files:

```python
# Once strands-google is installed in the worker venv:
load_tool(path="<site-packages>/strands_google/use_google.py", name="use_google")
load_tool(path="<site-packages>/strands_google/gmail_helpers.py", name="gmail_send")
```

Notes for the parent's `load_tool`/`run_worker` wiring (parent-owned; not changed here):

- `use_google.py` hot-loads **standalone** — its Google client imports are lazy (inside functions).
- `gmail_helpers.py` performs `from strands_google.use_google import use_google` at call time, so the `strands_google` **package must be importable** in the worker venv; file-copy hot-loading of that one file alone is not sufficient.
- `orchestrator/load_tool.py`'s `tool_file_path()` resolves bare names against its search dirs; to make `use_google` resolvable by bare name, the parent can add the installed `strands_google` package dir to `load_tool_search_dirs()` or pass an absolute path.
- `use_google`'s mutative-consent prompt calls `input()`; in the non-TTY Temporal worker this raises `EOFError` (returned as a tool error). Mutative operations therefore require `BYPASS_TOOL_CONSENT=true` **and** should be gated by the orchestrator's own HITL approval instead.

### Blockers

1. **Not installed in shared `orchestrator/.venv`** — parent handles serial installs; pins below.
2. **No credentials available** — every real API call is blocked until one of the credential env vars (below) is configured. Nothing was faked.
3. Mutative consent prompt vs. non-TTY worker (see note above).

## Dependencies for the parent (shared `orchestrator/.venv`)

Resolved and verified in the lane; add to `orchestrator/requirements.txt`:

```
strands-google==0.2.0
google-api-python-client==2.200.0
google-auth-httplib2==0.4.2
google-auth-oauthlib==1.4.1
strands-mcp-server==0.1.4
```

`strands-agents==1.50.2` is already pinned and satisfies `strands-google`'s requirement. `strands-mcp-server` is a hard metadata dependency of `strands-google` (it pulls `mcp`, `fastapi`/`uvicorn`, and `boto3` — all already present in the shared venv at compatible versions). Full lane resolution: `orchestrator/integrations/google-tools/requirements.lock.txt`.

## Credentials & scopes (setup required before any real call)

Pick one credential mode; the tool auto-detects in this order: service account → OAuth token → API key.

| Env var | Mode | Setup |
| --- | --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | Service account | GCP Console → IAM → Service Accounts → create key (JSON); point the var at the file. Best for server-side/no-user-data APIs and Workspace domain-wide delegation. |
| `GOOGLE_OAUTH_CREDENTIALS` | OAuth 2.0 (user) | 1) [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials) → OAuth 2.0 Client ID (**Desktop app**) → download JSON. 2) Run `python -m strands_google.google_auth <credentials.json> <token.json>` (opens a browser). 3) `export GOOGLE_OAUTH_CREDENTIALS=/abs/path/token.json`. Required for Gmail send/read as a user. |
| `GOOGLE_API_KEY` | API key | For public APIs only (YouTube search, Places, Books…). |
| `GOOGLE_API_SCOPES` | Scope override | Comma-separated scope URLs; defaults are **read-only** (`gmail.readonly`, `drive.readonly`, `calendar.readonly`, `youtube.readonly`, `userinfo.*`, `cloud-platform`). |
| `BYPASS_TOOL_CONSENT` | Safety toggle | `true` skips the interactive y/N confirmation on mutative methods (`create/insert/update/patch/delete/trash/remove/send/stop/cancel/modify`). Leave unset unless the orchestrator's own HITL approval is in front. |

Scopes needed per helper:

- `gmail_send` / `gmail_reply`: `https://www.googleapis.com/auth/gmail.send` (send-only) or `https://mail.google.com/` (full); profile lookup uses `gmail.readonly`.
- Read-only browsing (`users.messages.list`, Drive `files.list`, Calendar `events.list`): covered by the default read-only scope set.

## Sample invocations (once credentials exist)

```python
from strands import Agent
from strands_google import use_google, gmail_send, gmail_reply

agent = Agent(tools=[use_google, gmail_send, gmail_reply])

# Read-only: list 10 most recent Gmail messages
agent.tool.use_google(
    service="gmail", version="v1",
    resource="users.messages", method="list",
    parameters={"userId": "me", "maxResults": 10},
)

# Read-only: today's calendar events
agent.tool.use_google(
    service="calendar", version="v3",
    resource="events", method="list",
    parameters={"calendarId": "primary"},
)

# Public API with key: YouTube search
agent.tool.use_google(
    service="youtube", version="v3",
    resource="search", method="list",
    parameters={"part": "snippet", "q": "temporal workflows", "maxResults": 5},
    credential_type="api_key",
)

# Mutative (requires consent bypass + gmail.send scope) — NOT exercised in verification
agent.tool.gmail_send(to="someone@example.com", subject="Hi", body="Hello!")
```

## Lane layout

```
orchestrator/integrations/google-tools/
├── .venv/                     # isolated lane venv (uv, Python 3.13) — not the shared venv
├── manifest.json              # machine-readable export/pin/env manifest
├── requirements.lock.txt      # full uv pip freeze of the verified lane
├── UPSTREAM_README.md         # mirror of the official repo README
└── tests/
    └── test_strands_google.py # 23-test verification suite (run: .venv/bin/python -m pytest tests -q)
```
