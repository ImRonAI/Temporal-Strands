# Perplexity Agent API Reference & Tooling Architecture

Official API Reference documentation for the Perplexity Agent API, its native and extensible tools, dynamic presets, and core features as integrated into the Temporal / Strands orchestrator.

Verified against the live API specifications at `https://docs.perplexity.ai` (OpenAPI 3.1.0).

---

## 1. Core Endpoints Overview

The Agent API provides a durable, multi-provider execution engine designed for streaming, background research, tool execution, and code synthesis.

| Method | Endpoint | Description | Streaming Support | Background Mode |
|---|---|---|---|---|
| `POST` | `/v1/agent` | Generate an agent response with tools, reasoning, and search | SSE (`text/event-stream`) via `stream: true` | `background: true` (durable async execution) |
| `GET` | `/v1/agent/{id}` | Retrieve a stored response snapshot by ID | N/A (JSON snapshot) | Polls queued / in-progress / terminal runs |
| `POST` | `/v1/agent/{id}/cancel` | Request cancellation of an active response | N/A | Returns `status: "cancelling"` for background runs |
| `GET` | `/v1/agent/{id}/files` | List files produced by a response in the sandbox | N/A (JSON list) | Enumerate artifacts delivered via `share_file` |
| `GET` | `/v1/agent/{id}/files/{file_id}/content` | Download raw file bytes produced in the sandbox | Binary stream | Returns original `Content-Type` & `Content-Disposition` |
| `GET` | `/v1/models` | List live model identifiers available for Agent API | N/A (JSON list) | Used for dynamic catalog discovery |

---

## 2. Dynamic Presets

Presets bundle an optimized model, search context, reasoning step budget, system prompt, and tool configuration. Presets own their token budgets (`max_output_tokens` should not be overridden on presets).

| Preset | Depth & Budget | Best For | Typical Use Cases |
|---|---|---|---|
| `fast` | Low latency, 1-step search | Direct lookups, definitions, quick facts | Simple factual questions, inline definitions |
| `low` | Light multi-step research | Everyday questions with current data | Product comparisons, quick news lookups |
| `medium` | Multi-hop search & reasoning | Detailed analysis across multiple sources | Multi-source investigation, synthesis |
| `high` | Deep reasoning, high context budget | Exhaustive research, complex analysis | Institutional-grade equity briefs, policy analysis |
| `xhigh` | Open-ended agentic execution | Sandbox code execution, heavy tool loops | Code synthesis, file generation, math verification |
| `wide-research` | Wide discovery + per-item depth | Large evidence-backed collections (WANDR) | Market landscape mapping, list generation |

### Dynamic Preset Invariant in the Orchestrator
- Registered as model IDs `preset:<name>` (e.g. `preset:high`, `preset:wide-research`).
- Always dispatched with `background: true` and `stream: true`.
- Native tools (`web_search`, `fetch_url`, `finance_search`, `sandbox`, `mcp`, `connector`) and `skills` (`BUILTIN_SKILLS`) are attached by default.

---

## 3. Native & Extensible Tools

The Agent API supports three categories of tools within the `tools` array of `POST /v1/agent`:

### 3.1 Web Search (`type: "web_search"`)
Searches the live index for up-to-date information, news, and citations.

- **Configuration Properties**:
  - `search_context_size` (`enum: ["low", "medium", "high"]`): Named context budget.
    - `low`: 300 total tokens / 300 per page.
    - `medium`: 1,000 total tokens / 1,000 per page.
    - `high`: 4,000 total tokens / 4,000 per page.
  - `max_results` (`integer`, 1–50): Maximum citations/results collected.
  - `max_tokens` (`integer`): Explicit total token cap for search snippets.
  - `max_tokens_per_page` (`integer`): Token cap per search result page.
  - `filters` (`object`):
    - `search_domain_filter` (`array[string]`): Domain allowlist (e.g. `["sec.gov", "fda.gov"]`) or denylist (prefixed with `-`, e.g. `["-reddit.com"]`).
    - `search_recency_filter` (`string`): e.g. `"month"`, `"week"`, `"day"`, `"hour"`.
  - `user_location` (`object`): Latitude, longitude, city, region, country for local search relevance.

### 3.2 Sandbox (`type: "sandbox"`)
Executes code in an isolated Linux container during the agentic loop.

- **Container Capabilities**:
  - Languages: Python 3 and bash shell commands.
  - Network: Enabled; code can query external APIs or install runtime dependencies.
  - State: Persistent across steps within the same response. Files written in step 1 are available in step 2.
  - Output Sharing: Files produced in the sandbox are delivered via the internal `share_file` tool and exposed through the files endpoints.
  - Credential Sharing: Authorized connectors (such as GitHub) inject authenticated CLI tokens into the sandbox for `git` and `gh`.

### 3.3 Fetch URL (`type: "fetch_url"`)
Pulls and extracts full content from specific URLs when target pages are already known.

- **Configuration Properties**:
  - `max_urls` (`integer`, 1–10): Maximum number of URLs fetched per tool call.

### 3.4 Finance Search (`type: "finance_search"`)
Queries structured real-time and historical financial market data for equities, ETFs, and indices.

- **Capabilities**:
  - Real-time and historical OHLCV pricing, market cap, P/E, EPS.
  - Financial statements: quarterly/annual income statements, balance sheets, cash flow statements.
  - Earnings transcripts, beat/miss historical tracking, forward consensus estimates.
  - Key performance indicators (segment revenue, KPIs, subscriber counts).
  - Note: For direct models, set `max_steps >= 3` to allow discovery and execution.

### 3.5 People Search (`type: "people_search"`)
Specialized search for professionals, employees, and public profiles.

### 3.6 MCP (`type: "mcp"`)
Connects a remote Streamable HTTP Model Context Protocol (MCP) server directly to the model.

- **Configuration Properties**:
  - `server_label` (`string`, regex `^[A-Za-z0-9_-]{1,64}$`): Unique namespace for the server's tools.
  - `server_url` (`string`): HTTPS Streamable HTTP endpoint (`/mcp`). Note: legacy SSE transport is not supported.
  - `headers` (`object`): Request headers (e.g. `{"X-API-Key": "..."}`).
  - `authorization` (`string`): Raw bearer access token for remote authentication.
  - `allowed_tools` (`array[string]`): Whitelist of tool names to expose.
  - `defer_loading` (`boolean`): When `true`, defers tool definitions out of the initial context, allowing the model to search and load schemas lazily via `tool_search_output`.

### 3.7 Connectors (`type: "connector"`)
Managed organization-level integrations configured in the Perplexity API Portal.

- **Supported Connector Identifiers**:
  - `connector_github`: User's GitHub repositories, issues, PRs, and sandbox `git`/`gh` credentials.
  - `connector_googledrive`: User's Google Drive Docs, Sheets, Slides, and files.
  - `connector_slack`: Slack channels, messages, and threads.
  - `connector_datadog`: Metrics, logs, and dashboards.
- **Configuration Properties**:
  - `id` (`string`): Opaque connector identifier.
  - `server_label` (`string`): Namespace for connector tools (e.g. `"github"`, `"google_drive"`).
  - `server_description` (`string`, optional): Model-facing description.
  - `allowed_tools` (`array[string]`, optional): Tool whitelist.

### 3.8 Custom Functions (`type: "function"`)
Client-side functions executed by the caller's application.

- **Configuration Properties**:
  - `name` (`string`): Function name.
  - `description` (`string`): Model-facing description of what the function does.
  - `parameters` (`object`): JSON Schema defining arguments.
  - `strict` (`boolean`): Enforces exact schema validation.
- **Protocol Loop**:
  1. Client sends request with `tools=[{"type": "function", ...}]`.
  2. Model emits an output item of `type: "function_call"` with `call_id`, `name`, `arguments`, and optional `thought_signature`.
  3. Client executes function locally and replies with `{"type": "function_call_output", "call_id": call_id, "output": json_string}` in the next turn's `input` array.

---

## 4. Features Specification

### 4.1 Output Control
- **Streaming**:
  - Set `stream: true`. Emits Server-Sent Events (`text/event-stream`).
  - Event types:
    - `response.created`: Initial response object with metadata and response ID.
    - `response.in_progress`: Response processing in progress.
    - `response.output_item.added`: New output item initiated (message, search result, tool call).
    - `response.output_text.delta`: Text token deltas.
    - `response.reasoning.delta`: Thought / chain-of-thought tokens.
    - `response.completed`: Terminal event containing the completed response and `usage`.
    - `response.failed`: Terminal error event.
- **Structured Outputs**:
  - Configured via `response_format`:
    ```json
    {
      "type": "json_schema",
      "json_schema": {
        "name": "schema_name",
        "schema": {
          "type": "object",
          "properties": { ... },
          "required": [ ... ],
          "additionalProperties": false
        },
        "strict": true
      }
    }
    ```

### 4.2 Conversation State
- **Input Replay**:
  - Resend the conversation history as an array of messages:
    ```json
    [
      {"type": "message", "role": "user", "content": "..."},
      {"type": "message", "role": "assistant", "content": "..."},
      {"type": "message", "role": "user", "content": "Follow up question"}
    ]
    ```
- **Server-Side Continuation (`previous_response_id`)**:
  - Pass `previous_response_id: "resp_<id>"` pointing to a prior completed response belonging to the same account.
  - Pass only the new turn in `input`.
  - Server automatically rehydrates prior conversation state and tool outputs.

### 4.3 Image Attachments
- Supports multimodal vision input via `input_image` blocks inside message content:
  ```json
  {
    "role": "user",
    "content": [
      {"type": "input_text", "text": "Analyze this chart:"},
      {
        "type": "input_image",
        "image_url": "data:image/png;base64,iVBORw0KGgo..."
      }
    ]
  }
  ```
- **Supported Formats**: PNG (`image/png`), JPEG (`image/jpeg`), WEBP (`image/webp`), and GIF (`image/gif`).
- **Data URIs**: Base64 encoded payload up to 50 MB per image.
- **Public URLs**: Direct public HTTPS URLs (`https://example.com/photo.jpg`).

### 4.4 Background Mode
- Set `background: true`.
- Returns immediately with status `queued` or `in_progress`.
- Survives client disconnects and network drops.
- **Streaming & Reconnect**:
  - Client streams SSE events while tracking `sequence_number`.
  - On disconnection, reconnect via `GET /v1/agent/{id}?stream=true&starting_after={N}`.
- **Cancellation**:
  - Cancel a running background job via `POST /v1/agent/{id}/cancel`.
  - Server transitions status to `cancelling`, then `cancelled`.

### 4.5 Working with Files
- Models running in the `sandbox` produce files and deliver them via the internal `share_file` tool.
- Output includes a `share_file` record with file metadata.
- **List Files**: `GET /v1/agent/{id}/files` returns `{"object": "list", "data": [{"id": ..., "filename": ..., "bytes": ...}]}`.
- **Download File**: `GET /v1/agent/{id}/files/{file_id}/content` streams raw file bytes with proper MIME `Content-Type`.
- **Proxy**: In the orchestrator, files are proxied through `/api/orchestrator/file?path=/v1/agent/{id}/files/{file_id}/content`.

### 4.6 Skills
- Progressive disclosure of specialized capabilities up to **16 skills** per request.
- **Skill Types**:
  - `builtin`:
    - `office`: Master suite for PDF, DOCX, PPTX, XLSX.
    - `office/pdf`: PDF creation with page-by-page visual QA.
    - `office/docx`: OOXML editable Word document authoring.
    - `office/pptx`: Slide presentations with visual layout QA.
    - `office/xlsx`: Excel spreadsheets with validated formulas.
  - `inline`:
    - Defined per-request with `name` (1-64 chars), `description` (routing trigger, <= 1024 bytes), and `instructions` (markdown body, <= 65,536 bytes).
  - `custom`: Organization-managed skills uploaded to Perplexity.

### 4.7 Wide Research
- Powered by the `wide-research` preset.
- Optimized for Wide-And-Deep Research (WANDR benchmark).
- Fanned-out discovery across dozens of entities, deep citation verification for each, and delivery of structured files (e.g. `results.jsonl`, CSV) via `share_file`.
- Runs with `background: true` and `stream: true`.

---

## 5. Temporal & Strands Orchestrator Mapping

| Agent API Surface | Orchestrator Implementation | Execution Pattern |
|---|---|---|
| Dynamic Presets (`preset:<name>`) | `PerplexityModel` factory | Streams tokens into `events` topic; durable session |
| Sub-Agent Preset Research | `create_<preset>_agent_response` | Activity wrapped via `activity_as_tool`, streams into `agent_runs` topic |
| Polling / Rehydration | `retrieve_agent_response` | Activity wrapped via `activity_as_tool` |
| Job Cancellation | `cancel_agent_response` | Activity wrapped via `activity_as_tool`, calls `POST /v1/agent/{id}/cancel` |
| Sandbox File Listing | `list_agent_response_files` | Activity wrapped via `activity_as_tool` |
| File Retrieval | `download_agent_response_file` | Activity streaming bytes to disk, exposes proxy URL |
| Model Discovery | `list_agent_models` | Activity querying `GET /v1/models` |
| Custom Function Calling | Strands `@tool` -> `FunctionTool` | Automatic conversion in `PerplexityModel._format_request()` |
| Multimodal Attachments | `TurnInput.images` -> ContentBlocks | Mapped to `input_image` in `PerplexityModel._image_part()` |
