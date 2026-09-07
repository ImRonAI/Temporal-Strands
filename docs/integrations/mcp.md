# User-Supplied MCP Servers — Integration Record

Date: 2026-09-07. Scope: `/Users/tims-stuff/Desktop/strands-tools/src/mcp` (excluding `openapi-mcp`, owned elsewhere) plus the ronbrowser `electron-playwright-mcp` config. Everything here lives under `orchestrator/integrations/mcp/`; the app's shared `.venv`, `mcp.json`, `requirements.txt`, `run_worker.py`, `workflow.py`, and `load_tool.py` were **not** touched.

## Deliverables

| Artifact | Path |
| --- | --- |
| Config fragment for parent (`mcpServers`, all `disabled: true` = discoverable, not eager) | `orchestrator/integrations/mcp/config/mcpServers.fragment.json` |
| Inventory / classification manifest | `orchestrator/integrations/mcp/manifests/inventory.json` |
| Probe client (test harness only — not a wrapper/adapter) | `orchestrator/integrations/mcp/runtime/probe.py` |
| Per-server probe transcripts (JSONL) + server/stderr logs | `orchestrator/integrations/mcp/runtime/logs/` |
| Isolated per-server venv lanes | `orchestrator/integrations/mcp/venvs/{probe,workshop,strands-tools-mcp}` |
| Strands `MCPClient` official-integration evidence | `runtime/logs/strands_mcpclient_integration.log` |

Every "verified" claim below is backed by a real MCP `ClientSession.initialize()` → `list_tools()` → `call_tool()` sequence over the server's declared transport (stdio or streamable-http), logged in `runtime/logs/<name>.probe.jsonl`. The official Strands integration path was proven separately: `strands.tools.mcp.MCPClient` (`list_tools_sync` + `call_tool_sync`) against the calculator server returned `success [{'text': '42.0'}]`. At runtime the worker consumes servers through the same `MCPClient` factories (Temporal `StrandsPlugin(mcp_clients=...)`), so the fragment entries are directly consumable.

## Inventory: runnable servers vs. libraries

**Runnable MCP servers (14 candidates → 10 fully tool-tested, 4 partial with documented blockers):** workshop calculator / weather / calendar / email-history / static / task-manager / strands-agent, healthcare-mcp (Node), cms-coverage (spec + `uvx awslabs.openapi-mcp-server`), `strands-agents-mcp-server` (docs), `strands_tools_mcp` (module server), `strands-mcp-server` (cagatay), `@anaisbetts/mcp-installer`, `electron-playwright-mcp` (ronbrowser).

**Not servers (no integration attempted):**
- `strands_mcp_server/` — byte-identical duplicate source dump of `cagatay_mcp_server/strands_mcp_server` (verified with `diff`). Library code; the PyPI package `strands-mcp-server` is the runnable form.
- `utcp/strands_utcp` — UTCP→Strands tool-adapter *library* (`utcp_tool_adapter.py`); exposes no MCP server entry point.
- `ronbrowser_mcp_servers.py` — a FastAPI router (MCP server *registry* CRUD) lifted from another app; imports `agent.api.core.config` which doesn't exist here. Its useful payload is the accompanying `ronbrowser_mcp.json`, which is integrated (see electron-playwright-mcp row).
- `cms-coverage-mcp-server/cms_coverage_client.py` — hand-rolled JSON-RPC subprocess helper referencing a nonexistent `uvx --from openapi-mcp-server openapi_mcp_server` invocation; superseded by the real AWS Labs server + real MCP client.

## Test matrix

Status legend — **PASS**: initialize + list_tools + ≥1 safe read-only tool invoked with correct result. **LIST**: initialize + list_tools OK, invocation withheld or blocked (reason given). **N/A**: not invoked by safety policy.

### workshop-calculator (`mcp_servers/calculator_server.py`, stdio, venvs/workshop)
| Tool | Status | Evidence |
| --- | --- | --- |
| add | PASS | `add(2,3) → 5.0` |
| multiply | PASS | `multiply(6,7) → 42.0` (also via Strands MCPClient: `add(40,2) → 42.0`) |
| subtract, divide | LIST | same trivial arithmetic surface; listed, not individually invoked |

### workshop-weather (`mcp_servers/weather/weather_server.py`, stdio, live NWS API)
| Tool | Status | Evidence |
| --- | --- | --- |
| get_alerts | PASS | `get_alerts("CA")` → live High Surf Advisory text |
| get_current_weather | PASS | `(37.77,-122.42)` → 15.3°C observation |
| get_forecast | LIST | same NWS GET surface as the two above |

### workshop-calendar (`mcp_servers/calendar/calendar_server.py`, stdio, JSON store in scratch cwd)
| Tool | Status | Evidence |
| --- | --- | --- |
| get_current_datetime | PASS | correct local datetime |
| list_events | PASS | empty store read |
| weekday_to_date | PASS | `friday → 2026-09-11` |
| check_conflicts, find_available_slots | LIST | read-only but same store; listed |
| add_event, delete_event | N/A | mutating — skipped by policy |

### workshop-email-history (`mcp_servers/email_history/email_history_server.py`, stdio)
| Tool | Status | Evidence |
| --- | --- | --- |
| get_emails | PASS | empty store (no `email_history.json` shipped — server handles gracefully) |
| get_email_by_id | PASS | correct not-found JSON for id "1" |

### workshop-static (`mcp_servers/static/static.py`, stdio)
| Tool | Status | Evidence |
| --- | --- | --- |
| read_some_value | PASS | canned read response |
| write_value | N/A | mutating by name/contract; skipped |

### workshop-task-manager (`mcp_servers/task_manager_server.py`, **streamable-http**, port 8000)
| Tool | Status | Evidence |
| --- | --- | --- |
| list_tasks | PASS | over `http://localhost:8000/mcp/` → "No tasks found." |
| add_task, complete_task, delete_task | N/A | mutating; skipped |

Packaging note (config-only fix, upstream source untouched): the file's `mcp.run(transport="streamable-http", port=8001)` uses a `port=` kwarg the current `mcp` SDK removed → `TypeError` on direct launch. Launched instead via the SDK's own CLI `mcp run <file> --transport streamable-http` (binds default 8000; `FASTMCP_PORT` is not honored by the pinned pydantic-settings). Port 8000 is contested on this machine — run on demand only.

### healthcare (`healthcare-mcp-public/server/index.js`, Node stdio) — 11 tools listed
| Tool | Status | Evidence |
| --- | --- | --- |
| calculate_bmi | PASS | `1.8m/75kg → 23.15` |
| fda_drug_lookup | PASS | aspirin → OpenFDA products |
| pubmed_search | PASS | 18,591 results for "aspirin cardiovascular" |
| lookup_icd_code | PASS | "hypertension" → 3 ICD-10 codes |
| health_topics | PASS | diabetes → 11 Health.gov topics |
| clinical_trials_search | PASS | asthma/recruiting → 2 trials |
| ncbi_bookshelf_search | PASS | genetics → 53,675 books |
| medrxiv_search | PASS | valid empty result (upstream medRxiv API returned 0 — tool behaved correctly) |
| get_usage_stats | PASS | session stats JSON |
| get_all_usage_stats | LIST | same local SQLite read surface as get_usage_stats |
| extract_dicom_metadata | LIST | needs a local DICOM file path; no sample DICOM available — invocation would only prove file-not-found |

Packaging notes: the **published** `npx -y healthcare-mcp` package dies at `initialize` ("Connection closed", see `runtime/logs/healthcare.stderr.log` first section) — upstream packaging bug. The repo's own README Option 4 (run from source after `npm install` in `server/`) works and is what the fragment uses. No API keys required for any tested tool; `PUBMED_API_KEY`/`FDA_API_KEY` are optional rate-limit raisers referenced as `${ENV}` in the fragment. The keys sitting in the repo's `.env.example` were **not** used.

### cms-coverage (`coverageapi.json` via `uvx awslabs.openapi-mcp-server@latest`, stdio) — 93 tools generated
| Tool | Status | Evidence |
| --- | --- | --- |
| getMetadataStateId | PASS | 200 OK, live states list |
| getMetadataContractType | PASS | 200 OK, contract types |
| getDataArticle (and all `/v1/data/*`, ~80 tools) | LIST | **blocked: 401 Unauthorized** — CMS Coverage `/v1/data/*` requires a license token (MCD license agreement / API token from CMS). Env scope needed: a bearer/token per CMS onboarding, passed via the server's `--auth-type`/`--auth-token` flags referencing an env var. |
| getReports* (~10 tools) | LIST | public per spec; representative metadata GETs already prove transport+dispatch |

Config-only fix: repo README says base URL `https://api.cms.gov/mcd` — that hostname **does not resolve** (DNS failure, evidence in probe log `ConnectError: nodename nor servname provided`). Correct host verified live: `https://api.coverage.cms.gov`.

### strands-docs (`uvx strands-agents-mcp-server`, stdio)
| Tool | Status | Evidence |
| --- | --- | --- |
| search_docs | PASS | "MCP tools agent" → ranked strandsagents.com results |
| fetch_doc | LIST | same read-only index surface |

Upstream archived this repo (moved into `strands-agents/harness-sdk`) but the PyPI artifact runs fine.

### strands-tools-mcp (`tools_mcp/strands_tools_mcp`, `python -m` stdio, venvs/strands-tools-mcp)
| Tool | Status | Evidence |
| --- | --- | --- |
| current_time | PASS | ISO timestamp |
| calculator | PASS | `6*7 → Result: 42` |

Unpackaged module (no pyproject) — run with `PYTHONPATH=<tools_mcp dir>`, tools chosen via `STRANDS_TOOLS`. It force-sets `BYPASS_TOOL_CONSENT=true`, so only whitelist read-only tool modules in `STRANDS_TOOLS` (the fragment ships `current_time,calculator`).

### strands-mcp-server-cagatay (`uvx strands-mcp-server --no-agent-invocation --cwd <repo>`, stdio)
| Tool | Status | Evidence |
| --- | --- | --- |
| greet (hot-reloaded from `tools/greet.py`) | LIST → **blocked by upstream bug** | initialize + list_tools OK (`mcp_client`, `greet`). Every call (including after settle delay) returns strands `ConcurrencyException: Direct tool call cannot be made while the agent is in the middle of an invocation` — the package keeps its internal Agent mid-invocation while serving MCP; the fix (`record_direct_tool_call=False`) belongs upstream. No local rewrite attempted per scope. |
| mcp_client | N/A | connects out to arbitrary servers (mutating side effects); skipped |

### strands-agent-realestate (`mcp_servers/strands/strands_agent.py`, stdio)
| Tool | Status | Evidence |
| --- | --- | --- |
| real_estate_expert | LIST — **blocked: credentials** | initialize + list_tools PASS. Invocation instantiates a Strands `Agent` on Bedrock `us.amazon.nova-pro-v1:0`. Required to unblock: AWS credentials with `bedrock:InvokeModel` on that model (env: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION` or `AWS_PROFILE`), and acceptance of model spend. Not invented/faked. |

### electron-playwright-mcp (ronbrowser, `/Users/tims-stuff/RonBrowserRebuild/venv/bin/electron-playwright-mcp`, stdio)
| Tool group | Status | Evidence |
| --- | --- | --- |
| all 37 (`cdp_*`, `electron_*`, `playwright_*`, `request_approval`) | LIST — **blocked: backend not running** | initialize + list_tools PASS. Read-only probes failed live: `electron_Check_if_the_Electron_app_is_ready` → ReadTimeout against `localhost:3000/electron` (port 3000 is currently an unrelated Next dev server, returns 404); `cdp_Get_browser_version…` → ConnectError (`:9222` closed); `:8931` closed. Required to unblock: launch the RonBrowser Electron app with `import "electron-playwright-mcp/register"` in its main process, exposing CDP `:9222`, Electron HTTP `:3000/electron`, Playwright `:8931/mcp` (README at `/Users/tims-stuff/electron-playwright-mcp/README.md`). |

### mcp-installer (`npx -y @anaisbetts/mcp-installer`, stdio)
| Tool | Status | Evidence |
| --- | --- | --- |
| install_repo_mcp_server, install_local_mcp_server | LIST / N/A | initialize + list_tools PASS. Both tools install packages / emit config — inherently mutating, **no read-only tool exists**, so none invoked by policy. |

## Summary counts

- 14 runnable candidates; **10 servers with real tool invocations passing** (24 individual tool calls PASS across live public APIs and local logic), 4 LIST-only with concrete blockers (upstream bug ×1, missing backend ×1, credentials ×1, mutating-only surface ×1).
- 3 config/packaging fixes applied per upstream instructions, zero source edits: healthcare run-from-source instead of broken npm artifact; CMS base-URL corrected to the resolving host; task-manager launched via SDK CLI to bypass a removed kwarg.
- All test processes stopped; nothing left listening (task-manager killed post-probe; stdio servers die with their sessions).

## Re-running

```bash
cd orchestrator/integrations/mcp
venvs/probe/bin/python runtime/probe.py --stdio --list-only -- <command from fragment entry>
```

Parent integration: merge desired entries from `config/mcpServers.fragment.json` into the app's `mcp.json` (flip `disabled` off per server). Entries carry `continue_on_error: true` where the server depends on external backends/credentials so a missing dependency never blocks worker startup.
