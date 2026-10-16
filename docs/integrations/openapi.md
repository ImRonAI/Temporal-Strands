# OpenAPI → MCP Integration Lane

Converts the supplied OpenAPI specs in `/Users/tims-stuff/Desktop/strands-tools/src/openapi_specs/` into running MCP servers using the **native converter** shipped alongside them — no custom tool dispatchers, no hand-written HTTP wrappers. Everything this lane owns lives in `orchestrator/integrations/openapi/` (isolated `node_modules` + `.venv`) plus this doc. The shared `orchestrator/mcp.json`, `requirements.txt`, `run_worker.py`, and `workflow.py` were **not** touched; the parent integrates by merging `orchestrator/integrations/openapi/mcp-fragment.json`.

Verified: 2026-09-07. Machine verification report: `orchestrator/integrations/openapi/manifests/verification-report.json`.

## Converter (source pin)

| Item | Value |
| --- | --- |
| Package | `@ivotoby/openapi-mcp-server` |
| Version installed | **1.16.1** (pinned exactly in `orchestrator/integrations/openapi/package.json` + lockfile) |
| Integrity | `sha512-PCgNRTSNQ5rf4dUsAgkB0slVJ4TkV3zLRrblT47Sp7EVjtNsbSfuY79cIl4h+hjVBMVs/1AyXEcTJ2rOtllrJQ==` |
| Upstream | <https://github.com/ivo-toby/mcp-openapi-server> |
| Local supplied copy | `/Users/tims-stuff/Desktop/strands-tools/src/mcp/openapi-mcp` (source tree at 0.1.0-dev; its `npm run build` fails — `src/config.ts` imports `yargs/helpers.js`, a subpath yargs 17.7.2 does not export). The published 1.16.1 npm release of the same project is used instead; it ships a prebuilt `dist/bundle.js` and needs no build. |

The converter parses each spec natively (`$ref` resolution, parameter/body merging, 64-char tool-name abbreviation) and speaks MCP protocol `2025-06-18` over stdio. FastMCP's `from_openapi` was not needed — the task supplied this converter and it works as-is.

Verification client: official `mcp` Python SDK **1.16.0** in the lane's own venv (`orchestrator/integrations/openapi/.venv`, Python 3.14). MCP spec reference: <https://modelcontextprotocol.io/specification/2025-06-18>.

## Install (exact commands)

```bash
cd orchestrator/integrations/openapi
npm install --no-audit --no-fund          # installs @ivotoby/openapi-mcp-server@1.16.1 into ./node_modules
uv venv .venv
uv pip install --python .venv/bin/python "mcp==1.16.0"
```

## Run / restart a server

Every server is a plain stdio process; it exits when the client closes the pipe, so nothing lingers. Example (CMS Coverage):

```bash
orchestrator/integrations/openapi/node_modules/.bin/openapi-mcp-server \
  --transport stdio \
  --api-base-url https://api.coverage.cms.gov \
  --openapi-spec /Users/tims-stuff/Desktop/strands-tools/src/openapi_specs/coverageapi.json
```

Re-verify everything at any time:

```bash
cd orchestrator/integrations/openapi
.venv/bin/python verify_servers.py                # all servers
.venv/bin/python verify_servers.py cms-coverage   # one server
```

## Spec inventory (all 18 supplied files)

| Spec file | OpenAPI ver | API | Base URL | Ops (GET) | Security | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `coverageapi.json` | 3.0.3 | CMS Coverage API v1.6 | `https://api.coverage.cms.gov` (spec has empty `servers`; base URL confirmed live) | 93 (93) | Bearer (license-agreement token only for some endpoints) | ✅ converted + **live-tested** |
| `Claim_Status_V2.json` | 3.0.1 | Optum Medical Network Claim Status V2 | `https://sandbox-apigw.optum.com` | 4 (1) | bearer-key | ✅ converted, list_tools verified; live call blocked (credentials) |
| `Eligibility.json` | 3.0.1 | Optum Medical Network Eligibility v3 | `https://sandbox-apigw.optum.com` | 3 (1) | bearer-key | ✅ converted, list_tools verified; live call blocked |
| `Enhanced_Eligibility_API_v1.json` | 3.0.1 | Optum Enhanced Eligibility | `https://sandbox-apigw.optum.com`, `https://apigw.optum.com` | 7 (5) | OAuth2 client-credentials | ✅ converted, list_tools verified; live call blocked |
| `InstitutionalClaims.json` | 3.0.1 | Optum Institutional Claims V1 | `https://sandbox-apigw.optum.com` | 5 (1) | bearer-key | ✅ converted, list_tools verified; live call blocked |
| `MedicalNetworkClaims.json` | 3.0.1 | Optum Professional Claims V3 | `https://sandbox.apigw.optum.com` | 5 (1) | bearer-key | ✅ converted, list_tools verified; live call blocked |
| `priorauthorizationv1.json` | 3.0.3 | Optum Prior Authorization v1 | `https://sandbox-apigw.optum.com` | 11 (1) | bearer (global) | ✅ converted, list_tools verified; live call blocked |
| `Telnyx.json` | 3.1.0 | Telnyx API | `https://api.telnyx.com/v2` | 838 (397) | bearer / OAuth2 (global) | ✅ converted, list_tools verified (838 tools); live call blocked |
| `Telnyx.yml` | 3.1.0 | Telnyx API (duplicate) | same | 838 (397) | same | ➖ skipped — same API as `Telnyx.json` |
| `zoom_meetings.json` | 3.0.0 | Zoom Meetings API | `https://api.zoom.us/v2` | 183 (92) | OAuth2 / apiKey | ✅ converted, list_tools verified (183 tools); live call blocked |
| `chatCompletions.yml` | 3.1.0 | Perplexity chat completions | `https://api.perplexity.ai` (spec `servers: []`; canonical base per Perplexity docs) | 1 (0) | HTTP Bearer | ✅ converted, list_tools verified; live call skipped (POST-only + spend) |
| `asyncCompletions.yml` | 3.1.0 | Perplexity async create | same | 1 (0) | HTTP Bearer | ✅ converted, list_tools verified; live call skipped (POST-only + spend) |
| `getasyncCompletions.yml` | 3.1.0 | Perplexity async get | same | 1 (1) | HTTP Bearer | ✅ converted, list_tools verified; live call blocked (credentials) |
| `listasyncCompletions.yml` | 3.1.0 | Perplexity async list | same | 1 (1) | HTTP Bearer | ✅ converted, list_tools verified; live call blocked (credentials) |
| `agenticResearch.yml` | 3.1.0 | Perplexity `/v1/responses` (agentic research) | same | 1 (0) | HTTP Bearer | ✅ converted, list_tools verified; live call skipped (POST-only + spend) |
| `example-spec.json` | 3.0.0 | Example API (sample) | `https://api.example.com` (not a live backend) | 3 (2) | none | ✅ converted, list_tools verified; no live backend exists |
| `example-spec.yaml` | 3.0.0 | Example API (duplicate) | same | 3 (2) | none | ➖ skipped — same sample as JSON |
| `Authorization_v2.json` | — | *(broken)* | — | — | — | ❌ **not an OpenAPI document** — file is a saved Optum Developer Portal HTML page (`<!DOCTYPE html>...`). Fix: re-download the raw spec JSON from the Optum portal ("Authorization V2" product page → download OpenAPI JSON) and drop it into the spec dir; the same manifest pattern then applies. |

## Verification results (MCP ClientSession, protocol 2025-06-18)

All 15 configured servers were spawned over stdio and completed `initialize` + `tools/list` via the official `mcp` SDK. Tool counts match the specs' operation counts exactly:

| Server | Tools | Sample tools |
| --- | --- | --- |
| cms-coverage | 93 | `get-data-article`, `get-metadata-state-id`, `get-reports-local-coverage-final-lcd` |
| optum-claim-status | 4 | `claimstatus`, `health-check`, `raw-x12` |
| optum-eligibility | 3 | `medical-eligibility`, `health-check` |
| optum-enhanced-eligibility | 7 | `find-all-eligibility-transactions`, `get-all-discoveries` |
| optum-institutional-claims | 5 | `validate-claim`, `process-claim`, `health-check` |
| optum-professional-claims | 5 | `validate-claim`, `raw-x12-validation` |
| optum-prior-auth | 11 | `json-prior-authorization-inquiry`, `278-x-217-prior-authorization-submission` |
| telnyx | 838 | `lst-access-ip-addresses`, `authorization-server-metadata` |
| zoom-meetings | 183 | `lst-archived-files`, `get-archived-file-statistics` |
| perplexity-chat | 1 | `chat-completions-chat-completions-pst` |
| perplexity-async-create | 1 | `crt-async-chat-completions-async-chat-completions-pst` |
| perplexity-async-get | 1 | `get-async-chat-completion-resp-...` |
| perplexity-async-list | 1 | `lst-async-chat-completions-...` |
| perplexity-agentic-research | 1 | `crt-resp` |
| example | 3 | `get-usrs`, `crt-usr`, `get-usr-by-id` |

### Live end-to-end tool invocation

**cms-coverage / `get-metadata-state-id`** (public, unauthenticated, read-only) returned HTTP 200 through the full MCP path (client → converter → real CMS API):

```json
{"meta": {"status": {"id": 200, "message": "OK"}, "fields": ["state_id", "description"]},
 "data": [{"state_id": 1, "description": "Alaska"}, ...]}
```

`isError: false`. This proves the converter's request pipeline (base URL joining, header handling, response marshalling) works against a real backend, not just schema loading.

No mutating requests were made anywhere; no credentials were read, injected, or exercised.

## Credential / backend blockers (exact instructions, no mock success)

| Server(s) | Blocker | To unblock |
| --- | --- | --- |
| optum-* (6 servers) | Optum sandbox requires an OAuth2 client-credentials token | Get sandbox client id/secret at <https://developer.optum.com>, mint a token (`POST https://sandbox-apigw.optum.com/apip/auth/v2/token` with `grant_type=client_credentials`), export it as `OPTUM_SANDBOX_TOKEN`. Safe first call: each server's `health-check` GET tool. |
| telnyx | All endpoints need `Authorization: Bearer <API key>` | Create a key in the Telnyx portal (<https://portal.telnyx.com> → API Keys), export `TELNYX_API_KEY`. Safe first call: `lst-available-phone-numbers` or `authorization-server-metadata` (GETs). |
| zoom-meetings | Requires an OAuth access token from a Zoom Server-to-Server OAuth app | Create the app at <https://marketplace.zoom.us>, mint a token, export `ZOOM_ACCESS_TOKEN`. Safe first call: `lst-meetings` (GET, read scope). |
| perplexity-* (5 servers) | Requires `PERPLEXITY_API_KEY`; the only GET endpoints (async get/list) still need auth, and the POST endpoints incur spend | Export `PERPLEXITY_API_KEY` (from <https://www.perplexity.ai/settings/api>). Safe first call: `lst-async-chat-completions-...` (GET, lists your own async jobs, no spend). Per the task's no-inherited-credentials rule, the repo's `.env.local` key was **not** read or reused. |
| example | `api.example.com` is a reserved documentation domain with no API backend | None — the server itself starts and lists tools correctly, which is the entire provable surface for a sample spec. |
| Authorization_v2.json | File is HTML, not a spec | Re-download the raw OpenAPI JSON from the Optum portal (see inventory table). |

## Parent integration

Merge `orchestrator/integrations/openapi/mcp-fragment.json` → `mcpServers` into `orchestrator/mcp.json` (owner of that file does the merge). `${VAR}` placeholders in `--headers` args are env references; the parent launcher must expand them from the environment (or move the header into the child `env` as `API_HEADERS` — the converter reads both). Include only the servers whose credentials exist; `cms-coverage` works with none.

## Lane contents

```
orchestrator/integrations/openapi/
├── package.json / package-lock.json      # converter pin @1.16.1
├── node_modules/                          # isolated; .bin/openapi-mcp-server
├── .venv/                                 # isolated; mcp==1.16.0 (Python 3.14)
├── verify_servers.py                      # spawn → initialize → list_tools → (safe live call) → report
├── mcp-fragment.json                      # native MCP config fragment for the parent
└── manifests/
    ├── servers.json                       # per-spec manifest: spec, base URL, auth, env refs, exclusions
    └── verification-report.json           # machine output of the last verify run
```

## Process hygiene

`verify_servers.py` uses `stdio_client` context managers — every spawned converter process is terminated when its session closes. Confirmed post-run: `pgrep -fl "openapi-mcp-server|mcp-server.js"` → no matches. Restart is always available via the commands above or the config fragment.
