# Perplexity Agent API Preset Tools Plan

## Goal
Implement the four supplied Agent API schemas as Temporal-backed Strands tools, with six distinct preset create tools and live nested rendering in the existing Agent activity UI.

Authoritative schemas:
- Confluence `27918338`: `POST /v1/agent`
- Confluence `27656194`: `GET /v1/agent/{id}`
- Confluence `28147713`: `GET /v1/agent/{id}/files`
- Confluence `28246017`: `GET /v1/agent/{id}/files/{file_id}/content`

External behavior verified against:
- `https://docs.perplexity.ai/docs/agent-api/presets`
- `https://docs.perplexity.ai/docs/agent-api/output-control`
- `https://docs.perplexity.ai/docs/agent-api/skills`
- `https://docs.perplexity.ai/api-reference/agent-post`
- Installed `perplexityai` and `temporalio.contrib.strands` source

## Fixed Decisions
- Expose exactly six create tools, one per current dynamic preset: `create_fast_agent_response`, `create_low_agent_response`, `create_medium_agent_response`, `create_high_agent_response`, `create_xhigh_agent_response`, and `create_wide_research_agent_response`.
- Do not expose a generic seventh create tool and do not copy/freeze preset internals. Each tool sends its fixed documented preset name so Perplexity can update the dynamic preset.
- Each preset tool accepts the remaining `ResponsesRequest` fields from the supplied OpenAPI schema, including `model` and ordered `models` fallback overrides, instructions, native tools, skills, reasoning, structured output, storage, and sampling controls. `models` takes precedence over `model`; either overrides the preset model as documented.
- Force `background=true` and `stream=true`; these are implementation invariants, not model-authored arguments.
- Consume the create SSE stream through `response.completed` or `response.failed`, publishing nested progress as it arrives. Return the bounded terminal response through the normal `activity_as_tool` result so the exact calling agent, including `think`, receives the final output.
- Keep `retrieve_agent_response`, `list_agent_response_files`, and `download_agent_response_file` as separate response-ID/file-ID tools implementing their supplied schemas.
- Implement Agent API skills, not a separate Strands `AgentSkills` plugin: built-ins `office`, `office/pdf`, `office/docx`, `office/pptx`, `office/xlsx`, plus inline skills with the schema limits.
- Preserve the full request-side native `Tool` union from the supplied create schema: `web_search`, `finance_search`, `people_search`, `fetch_url`, `function`, `sandbox`, and `mcp`. Preset tool overrides merge according to Perplexity's documented preset behavior.
- Binary file bytes never enter Workflow History or Strands tool results. Download activities persist bytes outside history and return bounded metadata, checksum, and a browser-safe Agent API proxy reference.
- Do not add cancellation: it is not one of the four supplied schemas.

## Implementation

1. **Add operation contracts and validation**
   - Create `orchestrator/perplexity_operations.py` and `orchestrator/tests/test_perplexity_operations.py`.
   - Define JSON-serializable dataclasses/Pydantic-compatible contracts for all supplied request unions: input items/content parts, reasoning, response format, skills, web-search filters/location, all seven tool variants, response/output items, usage/errors, file metadata, and stored-file references.
   - Validate exact OpenAPI constraints before network I/O: model fallback length 1-5, max steps 1-100, skill count/byte limits and name pattern, native-tool ranges, MCP label/HTTPS requirements, response-format name/schema, and Anthropic `max_output_tokens`.
   - Tolerate documented forward-compatible output/status values where the schema says clients must tolerate unknown values; keep top-level and sandbox statuses distinct.
   - Bound response text, output arrays, native payloads, and metadata below Temporal payload limits using constants added to `orchestrator/config.py`.

2. **Implement the six streaming preset activities**
   - Use one private implementation shared by six separately decorated `@activity.defn(name=...)` functions; each public activity has its own name/docstring/tool schema and injects only its preset constant.
   - Build one worker-configured `AsyncPerplexity` client/factory; do not place API keys, clients, or SDK objects in Workflow state.
   - Call `client.responses.create(..., preset=<fixed>, background=True, stream=True)` with all validated optional fields supplied by the tool call.
   - Publish each SDK stream event through `WorkflowStreamClient.from_within_activity()` on a new `agent_runs` topic. Envelope fields: activity name, activity ID, preset, response ID once `response.created` arrives, API `sequence_number`, attempt, and the verbatim JSON event.
   - Heartbeat bounded progress containing response ID and last sequence number. On a retry/reconnect with a known response ID, resume using the documented `GET /v1/agent/{id}?stream=true&starting_after=N`; if the reconnect window has expired, retrieve the final snapshot.
   - Treat `response.failed` and non-completed terminal states with the same retry/non-retryable classification used by `perplexity_model.py`.
   - Project `response.completed.response` into a bounded serializable result containing response ID, actual selected model, status, output text, output items, usage, and error. This result becomes the Strands `toolResult` returned to the caller.
   - Preserve `response.skill.loaded`, reasoning/search/fetch events, output-item events, text deltas, sandbox/MCP/function calls, `skill_loaded`, and `share_file` output items.

3. **Implement retrieve and file activities exactly**
   - `retrieve_agent_response(response_id)` calls `client.responses.retrieve(response_id)` and returns the same bounded response projection. Preserve 404 behavior for unknown, cross-account, or `store:false` responses.
   - `list_agent_response_files(response_id)` calls `client.responses.files.list(response_id)` and returns the supplied `object`/`data` shape with bounded file metadata.
   - `download_agent_response_file(response_id, file_id)` calls `client.responses.files.content(file_id, response_id=...)`, streams bytes to `AGENT_FILE_STORE_DIR/<response_id>/<file_id>` using safe validated path components, computes SHA-256 and byte count, records content type/disposition filename, and returns metadata plus the existing `/api/orchestrator/file?path=...` proxy URL. Never return base64/raw bytes or an internal filesystem path.
   - Classify SDK authentication/permission/bad-request/not-found/validation errors as non-retryable and throttling/network/5xx as retryable.

4. **Register only the intended activity-backed tools**
   - Add operation timeout, heartbeat, retry, stream-batch, payload-bound, and file-store constants to `orchestrator/config.py`.
   - In `orchestrator/run_worker.py`, configure the operation client/factory and register all nine activities: six preset creates plus retrieve/list/download.
   - In `orchestrator/workflow.py`, add the nine `activity_as_tool(...)` wrappers to `ChatWorkflow._build_agent()` beside `think`, using explicit activity options from config.
   - Ensure the plain nested agent inside `think_activity.py` receives the same operation tools through an activity-capable caller path only if Temporal's workflow context supports it; do not place `activity_as_tool` inside the plain activity-local `strands.Agent`. The required caller-return behavior is already satisfied when `think` calls a preset through the outer Temporal agent; do not invent cross-activity workflow execution.
   - Preserve tool names exactly because the frontend uses them for grouping. Update `_clamp_tool_results` coverage so large sub-agent final outputs remain safe across Continue-As-New.

5. **Add a nested sub-agent stream protocol**
   - Add `AGENT_RUNS_TOPIC = "agent_runs"` to the shared workflow/server contract and subscribe in `orchestrator/server.py` without changing per-turn completion semantics.
   - Forward the activity envelope unchanged as SSE. Preserve ordering by API `sequence_number`, deduplicate replayed/reconnected events by `(response_id, sequence_number)`, and distinguish Temporal attempts.
   - Before modifying protected `app/api/orchestrator/route.ts`, add failing compatibility tests proving the backend envelope cannot be represented by the existing `events`/`thinking` mapping.
   - Extend the route event schema and parser with `agent_runs`; emit one reconciled `data-agent-run` UI part per response. Maintain a cumulative snapshot containing preset/tool name, response ID, status, actual model, ordered reasoning/native events, streamed markdown text, output items, files, and errors. Use stable response/call/item IDs rather than append-only random IDs.
   - Associate a run snapshot to its preset tool call by the preset activity name while live, then bind authoritatively by the response ID returned in that tool's terminal result. Keep simultaneous runs separate; add a test for interleaved distinct preset runs and repeated sequence numbers.

6. **Rework the Agent UI using native AI Elements**
   - Update `components/v0/agent-activity.tsx`; do not modify vendored AI Elements or reimplement their primitives.
   - Replace the current snapshot-only `AgentChainCard` assumptions with a chain that recognizes all six preset create names plus retrieve/list/download by response ID.
   - Render each preset run as `Agent > AgentHeader + AgentContent`. Inside `AgentContent`, render a `Task` for the run, and inside `TaskContent` render a nested `ChainOfThought` variant built from the existing native `ChainOfThought*` subcomponents.
   - In arrival order, render reasoning text with markdown `MessageResponse`, search/fetch/people/finance results, sandbox, MCP calls, function-call items, skill-loaded steps, share-file artifacts, retry/reconnect state, and errors using the same native renderers already used by the outer chain.
   - Render the sub-agent's accumulated final output in its Agent card using `MessageResponse` with streaming animation. Put the nested task/timeline/output inside a bounded `AgentContent` container with `max-height` and `overflow-y-auto`; leave page-level sticking/scrolling to the existing native `Conversation`.
   - Keep the outer caller's final answer in its existing `MessageResponse`. The nested result is visible in the Agent card and also reaches the caller as the preset tool's `toolResult`; do not inject sub-agent text directly into the outer answer stream.
   - Continue using the existing `/api/orchestrator/file` proxy for shared/downloaded artifacts and existing `Artifact`, `Image`, `CodeBlock`, `Sandbox`, `Terminal`, and `WebPreview` compositions.

7. **Test failure modes and contracts**
   - Python unit tests: exact SDK calls/arguments for every preset and endpoint; fixed background/stream; model and fallback overrides; all native tool variants; all skill variants/limits; SSE event ordering; terminal return; reconnect/deduplication; retry classification; response bounding; safe file paths; streamed file persistence/checksum; no bytes/secrets/SDK objects in results.
   - Workflow tests: all nine activity tools are registered; preset result returns to the invoking agent; `think` remains named and rendered correctly; `agent_runs` survives disconnect and Continue-As-New boundaries without duplicate events.
   - Route tests: streamed sub-agent deltas become reconciled `data-agent-run` parts; response IDs bind lifecycle calls; native outputs and `response.skill.loaded` are retained; interleaved runs remain distinct; retry/reconnect frames reconcile. Leave the two graph-contract failures untouched.
   - Component/pure-helper tests: six create names group correctly; retrieve/list/download attach by response ID; nested timeline preserves order; final markdown and file metadata are selected correctly. Do not add a DOM test stack solely for this; use pure exported grouping/reducer helpers under Vitest.
   - Live smoke test with one short preset run and one skills/file-producing run, verifying the selected override model, live nested timeline, caller handoff, retrieval, file list, and proxied download.

## Verification
Run from `orchestrator/`:
- `.venv/bin/python -m pytest tests/test_perplexity_operations.py -q`
- `.venv/bin/python -m pytest tests/test_workflow.py tests/test_think_activity.py -q`
- `.venv/bin/python -m pytest tests -q`

Run from repository root:
- `npx tsc --noEmit`
- `pnpm lint`
- `pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' app/api/orchestrator/route.test.ts`
- Any new scoped `agent-activity` test file

Expected known exception: the two existing graph `data-graph-event` tests remain failing until the separately blocked graph work is implemented; no Agent API change may alter or satisfy them opportunistically.

## Risks And Guards
- Dynamic preset internals change over time by design; never hardcode the current model/tool/prompt blocks.
- `activity_as_tool` returns the activity result to the exact Strands caller but does not inject `toolUseId` into activity arguments. Use response ID as the authoritative lifecycle key and activity name/activity ID only as provisional live-stream correlation; verify interleaving tests before accepting the route change.
- A client-side `function` tool definition can produce a `function_call` output, but the four supplied schemas do not define an endpoint for executing arbitrary application functions inside the sub-run. Preserve and return that output exactly; do not invent an execution protocol.
- Download bytes must remain outside Temporal payloads. Reject unsafe identifiers and enforce configured byte limits/storage cleanup policy.
- Protected route changes require the failing compatibility tests described above before implementation.
