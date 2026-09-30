# Telnyx remote MCP verification - September 30, 2026

## Scope

Replaced the runtime Telnyx stdio entry with Telnyx's hosted API MCP, read-only AI Connector, and public documentation MCP. All three load on demand. Configured URLs and authentication resolve inside the worker; configured credentials cannot be forwarded by supplying a different URL for the same connection ID. Native Strands MCP transports and the existing Temporal activity binding remain in use.

Configuration and usage: [Telnyx remote MCP](../telnyx-mcp.md).

## Real service checks

- Authenticated API MCP at `https://api.telnyx.com/v2/mcp`: six top-level tools discovered. Real fax endpoint discovery and `get_api_endpoint_schema` for `create_faxes` succeeded.
- Read-only connector at `https://api.telnyx.com/v2/ai/mcp`: five tools discovered; anonymous fax API-reference queries succeeded. Its account-data tools require per-user OAuth, which is not implemented in Gwen.
- Documentation MCP at `https://developers.telnyx.com/mcp`: three tools discovered; real documentation search succeeded. Its separate feedback tool was not called.
- Native `mcp_client_activity` connected all three services. Reconstructed Temporal MCP clients listed their tools successfully.
- A fresh Python process reconstructed the configured API client and retrieved the `create_faxes` schema without a previous in-memory connection, verifying environment-based authentication after process restart.
- No fax was transmitted, write endpoint invoked, feedback submitted, or private account record requested. Fax transmission itself is not an acceptance claim.
- Native SDK connection cycling emitted asynchronous cleanup warnings (`Task was destroyed but it is pending` / asynchronous-generator close), including during the UI run. Successful requests are not a claim that this SDK cleanup behavior is warning-free.

## Actual browser verification

Used the Playwright skill and headed Playwright CLI against Gwen at `http://localhost:3001`. Submitted a real prompt requesting discovery, connection, fax-reference searches, and the send-fax schema through all three configured servers.

- Workflow: `chat-4fe6363e0339402c`.
- Run: `01a0f2fb-7ed9-7338-9c62-0a9a59cecc8c`.
- Startup scheduled only `shell-list-tools` before model inference; there was no eager Telnyx tool-discovery activity.
- The UI displayed successful connection and real returned data from all three remote servers, followed by successful fax-schema responses.
- Browser console recorded five errors in unchanged frontend components: a Base UI uncontrolled Collapsible default-state error and nested-HTML errors during tool-result rendering.
- The agent then delegated schema extraction with `use_agent`. The worker reported `Tool 'mcp_client' not found in parent agent's tool registry`, and that delegated activity stalled. The successful parent-agent MCP calls are verified; completion of the entire chat response is not.
- The end-session API acknowledged the stop request (HTTP 204), but it only sets the session's end flag and did not interrupt the active delegation. Explicitly cancelled this test workflow through Temporal; its final status was `WORKFLOW_EXECUTION_STATUS_CANCELED`. No user session was reset.
- Browser snapshots are under `output/playwright/telnyx-remote-live.yml`. Screenshot capture timed out with both the default five-second timeout and a thirty-second timeout; no screenshot is claimed. The browser snapshot itself also stalled temporarily before returning.

## Repository checks

| Check | Result |
| --- | --- |
| `npx tsc --noEmit` | Passed |
| `pnpm lint` | Exit 0; one existing unused `_isStreaming` warning in `components/v0/computer-use.ts:192` |
| Scoped full Vitest | 583 passed, 2 failed; 26 files passed, 1 failed |
| Full orchestrator `pytest tests -q` | 1215 passed, 2 skipped, 52 warnings |
| Scoped MCP, hot-load, catalog, framework-adherence pytest after client reconstruction change | 33 passed, 30 warnings |
| Final scoped MCP and catalog pytest after optional-value narrowing | 17 passed, 9 warnings |
| Scoped `git diff --check` and Python syntax parsing | Passed |

The two Vitest failures are in untouched `components/v0/computer-use-preview.test.tsx`: `floats approvals and failures over the desktop instead of stacking above it` (line 322, missing expected test ID), and `exposes an in-app expand toggle only when the parent can reclaim the chat width` (line 341, attribute-order-sensitive markup expectation). The full frontend gate is not green.

The Python skips are `test_live_browser_uses_existing_linux_desktop_worker` (requires opt-in `GWEN_DESKTOP_LIVE_TEST=1`) and `test_browser_survives_handoff_rollover` (requires a Linux display). They do not substitute for the completed live Telnyx service calls. Python warnings include installed Strands tool-loading deprecations.

Existing working-tree changes were preserved. Protected environment files were not read or edited. The worker used the existing process environment for Telnyx credentials.
