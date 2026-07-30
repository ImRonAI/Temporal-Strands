# Durable Strands Temporal Orchestrator

## Objective

Rebuild the Python orchestrator behind the existing Next.js chat UI as a durable, long-running Strands agent hosted in a Temporal Workflow. The implementation must preserve the current HTTP and SSE contracts, call Perplexity's Agent API directly through a custom Strands `Model`, expose Data Commons and PopHIVE through Temporal's native MCP integration, support human approval and graph activity events, and roll long histories forward with Continue-As-New.

The first implementation uses one durable `ChatWorkflow` per chat session. This is the persistence and concurrency boundary; tools, MCP operations, model calls, and hooks that perform I/O execute as Temporal Activities.

## Runtime Architecture

The runtime consists of four processes started by `pnpm dev:all`:

1. Temporal development server on port 7233.
2. A Python worker polling `perplexity-orchestrator`.
3. A FastAPI bridge on port 8787.
4. Next.js on port 3000.

The worker registers:

- `ChatWorkflow` and the compare workflow.
- A `StrandsPlugin` model factory for every live Perplexity model ID returned by `GET /v1/models` during worker startup.
- A Data Commons MCP factory using its hosted Streamable HTTP endpoint and `DC_API_KEY`.
- A PopHIVE MCP factory launching the locally managed server over stdio.
- Explicit application Activities and their `activity_as_tool()` or `activity_as_hook()` adapters.

Workflow code constructs `TemporalAgent(model=<registered model id>)`. It never constructs a live model, HTTP client, MCP transport, subprocess, or filesystem-backed object.

## Session Workflow

`POST /sessions` validates the requested model against the worker-supported live catalog, generates a session ID, and starts `ChatWorkflow` with that fixed model. A model cannot change within a session.

`ChatWorkflow` owns:

- The `TemporalAgent` and its messages.
- Session termination state.
- Pending approval state.
- A lock serializing turn Updates.
- Stream state needed to carry subscribers across Continue-As-New.

A turn is a Workflow Update. The handler waits for agent initialization, acquires the turn lock, invokes `await agent.invoke_async(...)`, handles any interrupt/approval cycle, and returns the final reply. Concurrent requests therefore cannot interleave mutations to one conversation.

Session termination is an idempotent Signal. The main Workflow method waits for termination or `workflow.info().is_continue_as_new_suggested()`. Before return or rollover it waits for `workflow.all_handlers_finished` so no Update or Signal is lost.

On rollover, the main method carries the model ID, `agent.messages`, and Workflow Stream state into `workflow.continue_as_new(...)`. Strands snapshots and external session managers are not used because Temporal Event History is the durability authority.

## Streaming Contract

`TemporalAgent` publishes native Strands model events to its configured Workflow Stream topic. The FastAPI turn endpoint subscribes with `WorkflowStreamClient.create(...)` before issuing the turn Update, then emits SSE frames until the Update finishes and all relevant stream events have been forwarded.

The bridge preserves these existing frontend event families:

- Raw Strands `messageStart`, `contentBlockStart`, `contentBlockDelta`, `contentBlockStop`, `messageStop`, and `metadata` events.
- Tool results on `topic: "tool_results"`.
- Human approval state on `topic: "approval"`.
- Deterministic thinking or tool progress on `topic: "thinking"`.
- Graph envelopes on the thinking topic, unchanged, so `app/api/orchestrator/route.ts` continues producing `data-graph-event` parts.
- A terminal `{done: true, reply: string}` frame or `{error: string}` frame.

The stream endpoint must detach its subscriber on client disconnect or terminal completion. Cancellation of the HTTP subscriber does not cancel the durable Workflow turn unless the API explicitly requests cancellation.

## Perplexity Model Provider

The provider directly implements `strands.models.Model` and calls `POST https://api.perplexity.ai/v1/agent` with `stream: true`. It implements `update_config`, `get_config`, `stream`, and `structured_output` according to the installed Strands interface. It does not add an application provider abstraction or an internal retry loop; Temporal owns retries.

Each registered model factory supplies Perplexity-native tools through the custom model configuration's `params["tools"]`. The provider merges that list with Strands tool specifications when constructing the request: native entries remain Agent API tool objects, while Strands tools become `{type: "function", name, description, parameters}` entries. The current Agent API tool union is `web_search`, `finance_search`, `people_search`, `fetch_url`, `sandbox`, `mcp`, and `function`. The first five execute inside Perplexity; `mcp` delegates to the remote MCP server described by that request entry; `function` returns a call for this application to execute. These request entries are not Temporal Activity registrations.

The default native-tool set is selected once in worker configuration and attached to every live model factory rather than reconstructed in Workflow code. The implementation must validate every selected tool and field against the current Agent API OpenAPI schema. It must not infer fields from similarly named tools or expose credentials in model configuration recorded in Event History.

Request mapping:

- System prompt to `instructions`.
- Strands messages to Agent API message items.
- Tool specs to custom function definitions.
- Tool results to `function_call_output` records using the original `call_id`.
- Live `provider/model` identifiers without a hardcoded model list.

Response mapping emits only native Strands `StreamEvent` dictionaries. Text deltas become text content block deltas. Function calls become tool-use start, argument delta, and stop events. `call_id` becomes `toolUseId`; `thought_signature` is preserved unchanged as `reasoningSignature` and replayed on the next request. Search and fetch telemetry is not represented as private reasoning text. The authoritative completed response supplies usage, stop reason, and final metadata. HTTP failures and `response.failed` raise provider exceptions for Temporal retry handling.

Conversation state is reconstructed from durable Strands messages rather than `previous_response_id`, avoiding a second persistence authority.

## Agent API Operations

The remaining Agent API response lifecycle and file operations are application-side network I/O, not native model tools. They are implemented as `@activity.defn` functions and exposed to the agent with `activity_as_tool()` where agent self-delegation is intended:

- `create_agent_response`: `POST /v1/agent`, corresponding to `client.responses.create(...)` in the official Python SDK. It supports durable background sub-runs and may stream sub-run progress to the existing Workflow Stream topic.
- `retrieve_agent_response`: `GET /v1/agent/{response_id}`, corresponding to `client.responses.retrieve(response_id)`. Retrieval requires a stored response.
- `cancel_agent_response`: `POST /v1/agent/{response_id}/cancel`, corresponding to `client.responses.cancel(response_id)`. The initial `cancelling` result is non-terminal and must be followed by retrieval when a terminal status is required.
- `list_agent_response_files`: `GET /v1/agent/{response_id}/files`, corresponding to `client.responses.files.list(response_id)`. It lists artifacts shared from the response sandbox.
- `download_agent_response_file`: `GET /v1/agent/{response_id}/files/{file_id}/content`, corresponding to `client.responses.files.content(file_id=..., response_id=...)`. It retrieves one file as raw bytes; the ambiguous name `get_files` is not used.

The public tool names may remain the explicit application names above, but endpoint paths and SDK symbols are the source of truth. Activity results are bounded below Temporal payload limits. Binary files are stored outside Workflow History and represented there by serializable metadata or a stable reference rather than unrestricted base64 payloads. Create operations use stable request identity where the API supports it so an Activity retry cannot silently create duplicate sub-runs.

## Durable Memory

Long-term semantic memory uses a custom Strands memory store backed by LanceDB. `generate_contextualized_embeddings` is the application Activity name; it is not treated as a Strands or LanceDB library symbol. Its implementation calls Perplexity's official `client.contextualized_embeddings.create(...)` method, which maps to `POST /v1/contextualizedembeddings`.

Ingestion passes ordered chunks as `input: list[list[str]]`, with each inner list containing chunks from one source document in source order. It uses one configured model and fixed dimension for a table generation: `pplx-embed-context-v1-0.6b` at up to 1024 dimensions or `pplx-embed-context-v1-4b` at up to 2560 dimensions. Requests enforce the documented limits of 512 documents, 16,000 total chunks, 32K tokens per document, 120,000 tokens per request, and no empty chunks. The default `base64_int8` response is decoded as signed int8 and converted to a LanceDB-compatible fixed-dimension vector; model, dimension, and encoding are stored with every row. `base64_binary` is not mixed into the same table.

Each LanceDB row stores a stable memory ID, tenant or user scope, session ID when applicable, source document ID, source-order chunk index, original chunk text, vector, embedding model, dimension, encoding, content hash, timestamps supplied outside Workflow code, and JSON metadata. Ordinary ingestion is idempotent by stable ID and content hash and never opens the table in overwrite mode.

Query embedding uses the same contextualized model, dimension, and encoding as the target table, wrapping each independent query as a one-chunk document (`[[query]]`). Retrieval performs cosine vector search with an explicit result limit and mandatory tenant or user prefilter. Session filters are optional so durable user memory can cross sessions. Returned `MemoryEntry` values contain bounded text, score, stable identifiers, and metadata; raw vectors do not enter Workflow History.

All Perplexity embedding calls and LanceDB connect, create, add, update, delete, index, and search operations execute as Temporal Activities. The Workflow holds only serializable commands and results. The custom Strands memory adapter implements the installed memory interface, including `search(query, options) -> list[MemoryEntry]` and the ingestion methods actually required by that version. Agent-visible remember/recall operations use `activity_as_tool()`; automatic post-turn persistence uses `activity_as_hook()` with a bounded event projection. Memory writes occur only after a completed turn and use stable IDs, so Activity retries and Workflow replay do not duplicate records.

LanceDB's local persistence directory is worker configuration and is excluded from source control. Production deployments must place it on storage shared by all workers that can serve the task queue, or use LanceDB Cloud; per-process ephemeral storage is not a durable-memory deployment. Schema or embedding-model changes create a versioned table and require explicit re-embedding rather than mixing incompatible vectors.

## Tools And Hooks

Model calls and `TemporalMCPClient` operations are Activity-backed by `StrandsPlugin`. Any application tool that performs network, filesystem, subprocess, database, mutable-state, or long-running work is defined with `@activity.defn` and exposed using `activity_as_tool()`.

Inline Strands tools are limited to async, bounded, deterministic, side-effect-free transformations. Hooks that only mutate deterministic Workflow state may remain callbacks. Hooks that persist data, emit external telemetry, or perform other I/O use `activity_as_hook()` with a JSON-serializable event projection.

Activity retry policies are explicit. Side-effecting tools use stable idempotency keys when the downstream service supports them. Long-running Activities heartbeat and recover progress from heartbeat details. Provider clients do not retry independently, preventing retry multiplication.

## Graph Tool

Graph is a supported agent tool, not a separate session architecture. The graph implementation being repaired independently will be integrated behind an Activity boundary unless its final implementation is proven deterministic and side-effect-free.

The tool adapter must:

- Preserve its public tool name and input schema.
- Execute graph work outside Workflow code when it performs model calls, I/O, subprocesses, or long-running computation.
- Emit the existing graph envelope shape with `run_id`, `event_id`, `sequence`, `formation_kind`, `node_path`, `event_type`, and `payload`.
- Publish envelopes to the thinking stream so the current Next.js reconciliation logic remains unchanged.
- Return a normal serializable Strands tool result at completion.
- Support cancellation and heartbeats for long graph runs.

If the repaired graph tool already defines a documented Strands-native streaming surface that Temporal's Activity adapter cannot forward incrementally, a companion progress mechanism will publish graph envelopes from the Activity while the final result still returns through `activity_as_tool()`. This does not change the UI envelope contract.

## MCP Services

Data Commons uses `TemporalMCPClient(server="datacommons")`; the worker factory creates a normal Strands `MCPClient` around `streamablehttp_client("https://api.datacommons.org/mcp", headers={"X-API-Key": ...})`.

PopHIVE is included as a local stdio server. Project setup clones the configured repository into an ignored local runtime directory at a pinned revision. Project startup runs a small sync step that fetches the configured branch or revision, fast-forwards when possible, installs the server's locked dependencies when its lockfile changes, and then lets the worker's MCP factory launch `node server/index.js` through `stdio_client`.

The sync cadence is startup-based by default: every `pnpm dev:all` invocation synchronizes before the worker starts. A separate explicit sync command supports scheduled production refreshes without mutating a running worker's subprocess. Updating the checkout requires a worker restart so tool schemas and executable code change at a controlled process boundary.

Both clients use `TemporalMCPClient` in Workflow code. Tool listing and invocation therefore remain recorded Temporal Activities, while credentials, transports, and the PopHIVE subprocess remain worker-side.

## Human Approval

A deterministic `BeforeToolCallEvent` hook decides whether a tool call requires approval and invokes the Strands interrupt API. The turn Update observes `AgentResult(stop_reason="interrupt")`, records the pending reason in Workflow state, publishes it on the approval stream, and waits for a response delivered by the existing approval endpoint.

The approval endpoint sends a Workflow message carrying `approve` or `deny`. Approval resumes `agent.invoke_async(...)` with the documented interrupt response. Denial cancels the requested tool and resumes the agent with that result. The pending state is cleared and a final approval event with `reason: null` is published. Human waiting occurs in Workflow state, never inside an Activity.

## Compare Workflow

`POST /compare/stream` accepts up to four unique live model IDs and starts one independent durable agent execution per model. Each model uses the same agent identity, tools, MCP clients, retry policy, and provider mapping as chat. Events are tagged with the model ID and forwarded through the existing compare SSE contract.

Comparison does not share messages or mutable agent state between models. One model's failure is reported independently and does not discard successful replies from the others.

## FastAPI Contract

The rebuilt API preserves:

- `POST /sessions`
- `POST /sessions/{session_id}/turns/stream`
- `POST /sessions/{session_id}/end`
- `POST /sessions/{session_id}/approve`
- `POST /compare/stream`
- `GET /health`

Inputs and outputs remain compatible with the current Next.js routes. API validation rejects missing prompts, unsupported models, malformed image blocks, and unknown sessions with explicit HTTP status codes. Workflow-already-completed races on end remain idempotent.

`GET /health` distinguishes API process health from Temporal connectivity and worker readiness. It does not expose secrets.

## Configuration And Dependencies

`orchestrator/requirements.txt` pins a mutually compatible Python 3.13 stack including `temporalio[strands-agents]`, the official Perplexity SDK, LanceDB, FastAPI/Uvicorn, HTTP/SSE support, MCP, and the required Strands tools extras. The Temporal Strands integration requires `temporalio >= 1.28.0`. Installation must pass `uv pip check`.

Secrets remain in `.env.local` and worker/API process environments:

- `PERPLEXITY_API_KEY`
- `DC_API_KEY`
- `POPHIVE_MCP_URL` and `DATACOMMONS_MCP_URL` when retained as configured endpoint metadata

PopHIVE checkout location, repository URL, and revision are non-secret runtime settings. No secret is placed in Workflow inputs, messages, logs, stream events, or Event History.

## Errors And Operations

Model and Activity timeouts bound every external operation. Retryable provider and transport failures raise through Activity boundaries; validation, authentication, and unsupported-request failures are non-retryable. MCP tool errors returned as tool results are visible to the agent and UI rather than misreported as successful business outcomes.

Worker startup fails clearly if model discovery, required plugin registration, or the configured PopHIVE checkout is unavailable after synchronization. The API can start independently but reports worker readiness accurately.

Workflow definitions remain replay-deterministic across deployments. Changes to Workflow command structure use Temporal versioning or compatible deployment practices; Activity implementations can evolve independently when their serialized contracts remain compatible.

## Testing Strategy

Implementation follows test-driven development.

Provider unit tests cover request conversion, text streaming, multiple function calls, fragmented SSE, `thought_signature` round trips, usage mapping, stop reasons, malformed arguments, and terminal errors.

Agent API operation tests cover exact methods and paths, stored-response retrieval, asynchronous cancellation, file metadata, raw file content, non-retryable 4xx responses, bounded results, and idempotent retry behavior for create operations.

Memory tests cover ordered nested inputs, request limits, int8 decoding and dimension validation, stable-ID upserts, tenant isolation, query embedding with `[[query]]`, cosine ranking, bounded `MemoryEntry` results, retry-safe post-turn persistence, and rejection of mixed model or schema generations. Tests use fake embedding responses and a temporary LanceDB directory; live embedding tests are opt-in.

Workflow tests use `WorkflowEnvironment.start_local` with a Worker and fake registered model/MCP factories. They cover serialized turns, approval and denial, session termination, failed Activities, Continue-As-New message carryover, stream completion, and disconnect cleanup.

Replay tests use `temporalio.worker.Replayer` against captured histories for normal chat, tool use, approval, graph execution, and Continue-As-New.

API tests cover every endpoint and verify exact SSE frames expected by the existing Next.js adapters. Existing Vitest graph-envelope tests remain passing. Integration tests use fake provider and MCP servers; live smoke tests are opt-in and require environment credentials.

Final verification runs Python tests, replay tests, `uv pip check`, `pnpm lint`, targeted Vitest tests, and `pnpm build`.

## Authoritative References

- Perplexity Agent create and native-tool schema: https://docs.perplexity.ai/api-reference/agent-post
- Perplexity Agent retrieval: https://docs.perplexity.ai/api-reference/agent-get
- Perplexity Agent cancellation: https://docs.perplexity.ai/api-reference/agent-cancel-post
- Perplexity response file listing: https://docs.perplexity.ai/api-reference/agent-files-get
- Perplexity response file download: https://docs.perplexity.ai/api-reference/agent-file-content-get
- Perplexity contextualized-embeddings OpenAPI: https://docs.perplexity.ai/api-reference/contextualized-embeddings-post
- Perplexity contextualized-embeddings usage and retrieval guidance: https://docs.perplexity.ai/docs/embeddings/contextualized-embeddings
- Temporal Strands integration: https://docs.temporal.io/develop/python/integrations/strands-agents
- Temporal Python Strands API: https://python.temporal.io/temporalio.contrib.strands.html
- LanceDB embedding quickstart: https://docs.lancedb.com/embedding/quickstart/
- LanceDB vector search: https://docs.lancedb.com/search/vector-search/

## Acceptance Criteria

- A chat survives API and worker restarts without losing completed turns.
- Multiple turns on one session remain ordered and use the session's fixed model.
- Long sessions Continue-As-New without breaking subsequent turns or stream subscribers.
- Perplexity text, function calls, results, signatures, usage, and failures round-trip through native Strands events.
- Perplexity-native tools are sent in each model request through `params["tools"]`; response lifecycle and file operations execute only through bounded Temporal Activities.
- Contextualized embeddings persist in LanceDB with versioned dimensions, tenant-scoped retrieval, and replay-safe idempotent writes.
- Data Commons and the locally cloned PopHIVE server are available as Temporal MCP tools.
- Approval pauses and resumes a durable turn through the existing UI endpoint.
- Graph progress renders through the existing `data-graph-event` contract and returns a tool result.
- Compare runs the same complete agent independently for every selected model.
- Workflow replay passes for representative histories.
- Existing Next.js contracts remain compatible and the full verification suite passes.
