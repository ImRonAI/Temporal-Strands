# Master plan: registry, MCP, discovery, memory, and a live app test

> Implementing agent: the task headings are the spec. The batches below are the assignment. Do not launch a subagent per heading or per batch row. Do not treat a green unit test as finished. The last gate is the running app at `http://localhost:3001`.

**Goal.** The orchestrator starts with a small registry plus every MCP server in `orchestrator/mcp.json`. Community tools, including forge, dicom, and fhir, are found with `discover_tools` and attached with `load_tool`. `load_tool` already turns a loaded tool into an activity. Separately, the session keeps Perplexity Agent API native tools on in-activity models, the four graph formations, Think in one chain of thought, and cross-session memory through the journal, two Telnyx buckets, and a scheduled dream.

---

## How you must execute this

The prompts for these batches are in `docs/superpowers/plans/2026-10-08-prompts/`. `00-coding-agent.md` is the parent. Each other file is one section and is the subagent's whole prompt. Pass the file as written. Do not summarize it.

Use subagents for the batches below. Two agents never edit the same file in the same batch. A community tool is not a new activity. `load_tool_activity` returns the spec, `register_community_tool` registers it, and `run_loaded_tool` runs it. Do not launch `03-starter-activities.md`. A change that is one function, one constant block, or a file you are explicitly told not to rewrite does not get its own subagent. A heading that spans two features gets split even when those features were written as one task.

**Batch 1, in parallel.** Four agents.

| Work | Spec | Why this is one agent | Files |
|---|---|---|---|
| Session registry and load path | "What is registered", "load_tool already converts", "What the system prompt tells the agent", Task C | The tool list, the frozenset, the `graph` and desktop exceptions, the three `register_community_tool` calls, and the prompt are one behavior. | `orchestrator/workflow.py` (the `tools=` list only), `orchestrator/load_tool.py`, `orchestrator/agent.json` |
| MCP clients | "MCP servers" | One factory change and the two tests named there. | `orchestrator/agent_tools.py`, `orchestrator/tests/test_mcp_shell.py` |
| Package audit | "Discovery inventory" | The assertion that `forge`, `dicom`, and `fhir` are listed is a few lines. The work is calling the no-network entry points and recording the rest. That is one test module, not three agents and not a separate "confirm the names" agent. | `orchestrator/tests/test_healthcare_forge_catalog.py` (new) |
| Think chain | Task H, the UI only | The route already emits reasoning parts. This agent changes how one component keeps a chain open. Loading `think` is the registry agent's job, not this one. | `components/v0/agent-activity.tsx`, `components/v0/agent-activity-native.test.tsx` |

**Batch 2, after batch 1, in parallel.** Six agents. The registry agent has finished `workflow.py`.

| Work | Spec | Why this is one agent | Files |
|---|---|---|---|
| In-activity models | Task A, Task B, and the last paragraph of Task C | Task A is a frozenset edit. Task B is a constraint: do not rewrite `graph_tool.py`. The parent-tool paragraph is the same file as Task A. Together they are one agent. Apart, Task A and Task B are not worth launching. | `orchestrator/subagent_support.py` and the tests for `in_process_model` |
| Offload | Task D, plus the `config.py` constants from Task D and Task F | Bucket policy and the three short returns are one feature. The dream constants are a few lines in the same file, so this agent adds them and the dream agent only imports them. | `orchestrator/config.py`, a small upload helper, `orchestrator/graph_activity.py`, `orchestrator/use_agent_activity.py`, `orchestrator/use_skill_activity.py` |
| Dream | Task F, the workflow module and `DREAM_PROMPT` | The prompt and the one-shot agent are the work. The schedule is four lines and waits for the wiring agent. | `orchestrator/dream_workflow.py` (new) |
| Delegation SOP | Task G, except the prompt append in `agent_identity()` | The SOP file and the hook implement the same rules. Installing the package is part of this agent, not its own. | `orchestrator/requirements.txt`, `orchestrator/sops/delegation.sop.md`, the hook module, `orchestrator/mcp.json` |
| Handoff and stream truncate | Task I and Task E | Each is one update plus the server call that starts it. They share `workflow.py` and `server.py`, so one agent does both. Take Over stays on `ChatWorkflow`. Do not add a computer-use agent. | `orchestrator/workflow.py`, `orchestrator/server.py` |
| Settings | Task K, the settings view only | A model allowlist and a default. The tool selector is a different surface and a different contract. | a new settings component, `components/v0/model-picker.tsx`, `components/v0/agent-chat.tsx` |

**Batch 3, after batch 2.** Two agents, in parallel. They do not share files.

| Work | Spec | Why this is one agent | Files |
|---|---|---|---|
| Worker wiring | Create the dream schedule, append the SOP inside `agent_identity()`, call the catalog sync, delete the Gemini `computer_use` tool. `run_loaded_tool` is already registered. Do not register new tool activities. | These are short edits in `run_worker.py`. One agent owns that file. | `orchestrator/run_worker.py` |
| Tool and skill selector | Task K, the composer selector and the enforcement | The selector, the turn body, and the allowlist are one feature. Settings already landed, so this agent may edit `agent-chat.tsx` to pass the selection through. It also adds the one selection sentence in "What the system prompt tells the agent" to `agent.json`. | `components/v0/composer.tsx`, `components/v0/agent-chat.tsx`, `orchestrator/agent.json`, `orchestrator/workflow.py`, and `app/api/orchestrator/route.ts` only far enough to forward the two arrays |

Task J is the catalog sync function. It is part of the wiring agent, not a seventh batch-2 agent: `build_perplexity_factories` and `fetch_model_ids` already live in `run_worker.py`, which that agent owns. The function writes `lib/reasoning-levels.json` for the live Agent API ids and deletes ids the fetch no longer returns. Gemini entries stay as they are.

**Review, after batch 3, four agents in parallel.** Each reviewer reads the diff and writes findings before editing. A defect goes back to the agent that owned that file. That agent fixes it. The reviewers read the fix. Do not start the app test while a finding is open.

Every reviewer also rejects `context_manager="auto"`, a live `Model` in a workflow, `take_snapshot` / `load_snapshot`, a payload store other than the Telnyx buckets, and a timeout that is not from `config.py`. That check is part of each review. It is not a fifth agent.

| Reviewer | Reads |
|---|---|
| Registry and MCP | The session `tools` list, `load_tool` exceptions, one `TemporalMCPClient` per connected server, and the `agent.json` tool map: registered tools, server-side tools, discoverable tools, MCP servers, and the ten chains. |
| Memory | Offload keys, embed-once, dream prompt and `SKIP` schedule, journal retry guard, `params["tools"]` kept. |
| Delegation | The SOP hook's cancel rules, including that a direct `swarm` call is not cancelled, and the package-audit test against the inventory lists. The reviewer checks coverage. The reviewer does not re-call the forge, dicom, and fhir functions. |
| UI | Think stays one chain, settings filters the picker, the composer selector is what the session may call, and handoff returns 204 through `desktop_control`. |

**App test, last, on the main agent.** This is mandatory. Pytest is not a substitute.

1. Restart the worker so it loads this code. `pnpm dev:all` is already running in a terminal; restart only this checkout's stack, then wait until `http://localhost:3001` and `http://localhost:8787/health` answer.
2. Open `http://localhost:3001` in the browser. Use the chat the way a person does: type, send, read the transcript, open the tool trace.
3. Walk the script in "App test script". For each step write one line: worked, or the exact text the UI showed when it failed.
4. Fix every failure. Re-run that step in the browser. Repeat until the script's steps succeed in one pass.
5. The final note lists every step and its result. A step that was not exercised in the browser is a failure.

---

## What is registered when a session starts

`ChatWorkflow.run` builds one `TemporalAgent`. Its `tools` list is only:

| Name | How it is attached | Installed source |
|---|---|---|
| `load_tool` | existing `load_tool_activity` via `activity_as_tool` | `strands_tools.load_tool` |
| `discover_tools` | existing `catalog_tools` helper | lists the catalog; it is how the agent finds everything else |
| `unload_tools` | existing `catalog_tools` helper | removes a tool this session loaded |
| `use_skill` | existing `use_skill_activity` via `activity_as_tool` | skill sub-agent |
| `environment` | `load_tool_activity` then `register_community_tool` | `strands_tools.environment`, executed by `run_loaded_tool` |
| `http_request` | `load_tool_activity` then `register_community_tool` | `strands_tools.http_request`, executed by `run_loaded_tool` |
| `journal` | `load_tool_activity` then `register_community_tool` | `strands_tools.journal`, executed by `run_loaded_tool` |
| one `TemporalMCPClient` per connected server | `temporal_mcp_clients()` | the servers in the next section |

`discover_tools` and `unload_tools` stay registered because they are the discovery mechanism this repo already has. They are not a second catalog.

Take these off that list. They are loaded later, or they are desktop activities reached by name through `load_tool`:

`think`, `graph`, `use_agent`, `python_repl`, `browser`, `mcp_client`, `swarm`, every computer-use action, and `PERMANENT_COMMUNITY_TOOLS` entries other than `load_tool` and `use_skill`.

`_SESSION_TOOLS` in `orchestrator/load_tool.py` currently refuses to register `graph`, `use_agent`, `think`, `browser`, `mcp_client`, and `python_repl`. Those names are blocked even when they are not on the agent. Remove them from that frozenset. Keep `load_tool`, `discover_tools`, `unload_tools`, `use_skill`, `environment`, `http_request`, and `journal` in it so a load cannot replace a starter tool.

`agent.json` is the runtime prompt. Replace the "Permanent tools" section with the tool map in the next section. Delete the sentences that say `graph`, `use_agent`, `think`, `python_repl`, and the computer-use actions are already on the session.

---

## What the system prompt tells the agent

The registry agent writes this into `orchestrator/agent.json`. It is the tool map the model reads. Keep the three groups separate. A name belongs in one group only.

The delegation SOP, appended later from `delegation.sop.md`, stays the rules for `graph`, `use_agent`, `use_skill`, and `swarm`. Do not copy those rules into `agent.json` a second time. The tool selector, in batch 3, adds one sentence: when the turn lists selected tools and skills, those names are the ones this turn may call, and a selected discoverable tool is still loaded with `load_tool` before it is called.

### Already on the session

`load_tool`, `discover_tools`, `unload_tools`, `use_skill`, `environment`, `http_request`, `journal`, and the tools from each MCP server that connected. These are callable now. Do not `load_tool` them.

### Server-side, on the model request

These are Perplexity Agent API request types from `agent_api_tools.py`: `web_search`, `fetch_url`, `image_search`, `people_search`, `finance_search`, `sandbox`, `mcp`, and `connector`. The connector configured in `config.py` is GitHub (`server_label` `github`). They are already on the request. They are not registry names. Do not `discover_tools` for them. Do not put them in a graph node's `tools` list.

### Discoverable

Not on the session until `discover_tools`, then `load_tool`. The prompt names the ones the rest of this plan depends on, and says the catalog contains more:

`think`, `graph` (this name attaches `graph_activity`, not `strands_tools/graph.py`), `use_agent`, `python_repl`, `browser`, the computer-use actions, `swarm`, `forge`, `dicom`, `fhir`.

`pubmed` and `fda` are not in the catalog. The prompt says that, so the model does not go looking for them.

### MCP servers

One short line each: what it is for, and that a server whose env is unset is not mounted. The names are `shell` (`shell`, `read_file`, `write_file`, `list_dir`), `telnyx` (buckets, upload, download, embeddings, similarity search), `telnyx-readonly`, `telnyx-docs`, `datacommons`, `pophive`, `cms-coverage`, `mcp-installer`, `openapi`, `unsloth`. `agent-sops` is mounted with the others. Its SOP text is already in the prompt. Do not call it as a tool.

### Chains

The prompt states these as the sequences to use, and tells the model to stop when an earlier step already answered the question.

1. A tool that is not in the first two groups: `discover_tools`, then `load_tool`, then the call. `unload_tools` when the session is finished with it.
2. A fact on the web: `web_search`, `image_search`, `people_search`, or `finance_search`, then `fetch_url` for the page you will use. Do not also `http_request` or `shell` the same URL.
3. A file or command in the workspace: the `shell` MCP tools. `environment` only when a later call needs a process variable. `http_request` for an HTTP call that is not a page you are reading with `fetch_url`.
4. A catalog skill: `use_skill`. Inside that skill agent, read `references/` with `file_read` and run a script with `python_repl` when it is Python, otherwise `shell`. List paths first. Do not paste the skill files into the prompt.
5. Work that splits into agents: `load_tool` `graph`, then one `agent` node, or several nodes with no edge between them, or a `swarm` node, or a nested `graph`. Omit `tools` to inherit. Omit `model_id`. A swarm that is the whole task is `load_tool` `swarm` and the swarm tool, not a graph. Do not run both for the same task.
6. One hard step: `load_tool` `think`, then think. The nested agent uses the tools already loaded. Do not think before every call.
7. A to-do, a plan, or a short note: `journal` `write` or `add_task` now. Do not write that into `gwen-memory`. Dream promotes it later. To read a memory whose key you know, Telnyx `cloud_storage_list_objects` with the prefix, then `cloud_storage_download_file`. When you do not know the key, similarity search on `gwen-memory`, then download that file. Do not `embed_url`. Do not similarity-search `gwen-offload`.
8. The desktop: `load_tool` `browser` or the computer-use tool, then that tool. The computer is this same session. Take Over and Relinquish are the user's controls, not tools.
9. A new tool the session needs: create it, `load_tool` it, then `use_agent` or `graph` so the child can call it.
10. A package: `load_tool` the package, then the specific function. `forge.health` before a forge invoke. `fhir_terminology_copilot` `health` or `list_bindings` before a terminology action.

---

## load_tool already converts a loaded tool into an activity

Do not write an activity per community tool.

`register_community_tool` registers the schema returned by the official `load_tool` and points the name at `workflow.execute_activity(name, tool_use)`. The worker's `@activity.defn(dynamic=True) run_loaded_tool` receives that call and runs the loaded tool's `stream()`. That is the conversion. `swarm`, forge, dicom, and fhir use it.

Two exceptions, both already activities:

- **In-repo graph.** `load_tool(path="graph")` must attach `graph_activity`, not `strands_tools/graph.py`. `tool_file_path("graph")` currently resolves to the community module. Special-case the name `graph` so the schema and the dispatch are the existing `graph_activity`. The community module stays on disk and is not the tool named `graph`.
- **Desktop tools.** `browser` and the computer-use activities stay on the `desktop-browser` queue. `load_tool` of those names dispatches with the existing desktop activity options (`task_queue=DESKTOP_BROWSER_TASK_QUEUE`). They must not run inside `run_loaded_tool` on the host worker.

`swarm` is `strands_tools.swarm.swarm`. It is not hidden. The agent discovers it and loads it. The dynamic activity runs it. Do not add `swarm_activity.py`.

---

## MCP servers: connect all of them

Today only `shell` is mounted. `_eager_mcp_server_names()` keeps a server only when `continue_on_error` is absent, and every server except `shell` sets `continue_on_error: true`.

Change the factory so every server in `orchestrator/mcp.json` whose required environment variables are set becomes a `TemporalMCPClient`. `continue_on_error` means a failed handshake does not crash the worker. It does not mean "leave this server out of the factory."

`_server_env_missing` still drops a server when a `${VAR}` in its url, command, or env is unset. Log the server name and the missing variable. Do not read `.env*`.

| Server key | Transport | Required env | What the agent gets |
|---|---|---|---|
| `shell` | stdio `.venv/bin/strands-shell --mcp` | none | `shell`, `read_file`, `write_file`, `list_dir` |
| `telnyx` | `https://api.telnyx.com/v2/mcp` | `TELNYX_API_KEY` | storage and embeddings tools used by the memory tasks |
| `datacommons` | `${DATACOMMONS_MCP_URL}` | `DATACOMMONS_MCP_URL`, `DC_API_KEY` | Data Commons tools |
| `pophive` | `${POPHIVE_MCP_URL}` | `POPHIVE_MCP_URL` | PopHIVE tools |
| `cms-coverage` | `uvx` openapi-mcp-server, spec `cms-coverage-mcp-server/coverageapi.json`, base `https://api.cms.gov/mcd` | none in the env block | CMS coverage tools |
| `mcp-installer` | `npx @anaisbetts/mcp-installer` | none | installer tools |
| `openapi` | `npx @ivotoby/openapi-mcp-server` | `API_BASE_URL` and `OPENAPI_SPEC_PATH` are empty strings in `mcp.json` | connect it; if the process exits because the spec path is empty, record that in the app-test note and leave the server configured |
| `telnyx-readonly` | `https://api.telnyx.com/v2/ai/mcp` | none | included because it is in `mcp.json` |
| `telnyx-docs` | `https://developers.telnyx.com/mcp` | none | included because it is in `mcp.json` |
| `unsloth` | `npm --prefix unsloth-mcp-server run start` | none | included because it is in `mcp.json` |

The agent-sops server from Task G is added the same way.

A test builds the factory with the env vars set and asserts the returned names are the full set. A second test unsets `TELNYX_API_KEY` and asserts only `telnyx` is absent, and that its absence is logged.

---

## Discovery inventory

`discover_tools_activity` already appends a child directory when it contains `strands_tools.py` or `{name}_tools.py`. Confirm these three appear as the names `forge`, `dicom`, and `fhir`. Add a test that fails if any of the three is missing from the catalog text.

`load_tool` of a package directory uses `_nested_tool_file`. `dicom/strands_tools.py` and `forge/strands_tools.py` match. `fhir/fhir_tools.py` matches the `{name}_tools.py` rule. Do not rewrite those loaders if the test shows a load returns the tool specs below.

### forge — `strands-tools/forge/strands_tools.py`

`get_forge_strands_tools()` returns these `@tool` names:

- `forge.openapi.load`
- `forge.openapi.validate`
- `forge.openapi.synthesize`
- `forge.openapi.synthesize_resources`
- `forge.openapi.build_resource_endpoints`
- `forge.openapi.splice`
- `forge.openapi.filter`
- `forge.openapi.invoke`
- `forge.openapi.invoke_stream`
- `forge.openapi.invoke_with_context`
- `forge.openapi.invoke_with_context_stream`
- `forge.fhir.synthesize_r4`
- `forge.fhir.validate_profile`
- `forge.fhir.validate_terminology`
- `forge.fhir.validate_resource`
- `forge.mcp.export_tools`
- `forge.context_packs.upsert`
- `forge.context_packs.get`
- `forge.context_packs.list`
- `forge.context_packs.delete`
- `forge.webhooks.verify_signature`
- `forge.webhooks.create_headers`
- `forge.webhooks.create_subscription`
- `forge.webhooks.list_subscriptions`
- `forge.webhooks.delete_subscription`
- `forge.webhooks.enqueue_delivery`
- `forge.health`

Audit each one: import the module, call the function with a fixture spec where it does not need the network, and record the exception where it does. `forge.health` must succeed with no network. Invoke and webhook calls may fail closed without a running endpoint; write that down, do not fake a success.

### dicom — `strands-tools/dicom/strands_tools.py`

`get_dicom_strands_tools()` returns:

- `dicom_read_metadata`
- `dicom_read_pixel_data`
- `dicom_to_png`
- `dicom_query_orthanc`
- `dicom_store_orthanc`
- `dicom_qido_search`
- `dicom_wado_retrieve`
- `dicom_create_fhir_imaging_study`
- `dicom_validate_tags`
- `dicom_anonymize`

File-based calls (`dicom_read_metadata`, `dicom_validate_tags`, `dicom_anonymize`) are audited with a tiny DICOM fixture the test writes, or skipped with the import error if `pydicom` is not installed. Orthanc and DICOMweb calls need a server. The audit records "needs Orthanc" or "needs DICOMweb". It does not start one.

### fhir — `strands-tools/fhir/fhir_tools.py`

One tool, `fhir_terminology_copilot`, with actions:

`list_bindings`, `binding_info`, `search`, `lookup`, `validate`, `suggest`, `record_usage`, `audit_resource_terminology`, `propose_terminology_patches`, `generate_valueset`, `generate_conceptmap`, `generate_ecl`, `snomed_concept_graph`, `health`.

The module says it is not clinical decision support and that SNOMED needs a license. The audit calls `health` and `list_bindings`. Search, lookup, and SNOMED actions are recorded as needing a terminology server. Do not point them at a public server from the test.

### pubmed and fda

- `orchestrator/strands-tools/pubmed` is not on disk. Do not create a pubmed package to make the catalog look complete. `discover_tools` output will not contain `pubmed`. The audit file says so.
- `orchestrator/strands-tools/fda/__init__.py` is the only file. It defines no tool. `discover_tools` will not list `fda` until a `strands_tools.py` or `fda_tools.py` exists. Do not invent FDA tools. The audit file says the package is empty.

Write the audit as `orchestrator/tests/test_healthcare_forge_catalog.py`. It asserts the three packages are discoverable, asserts `pubmed` and `fda` are absent, imports forge, dicom, and fhir, and calls the no-network entry points above.

---

## Task A — Keep Agent API tools on in-activity models

Assigned with the in-activity parent tools in batch 2. Not its own subagent.

**File:** `orchestrator/subagent_support.py`, `in_process_model`.

`_OUTER_MODEL_PARAMS` is `{"background", "store", "tools", "skills"}`. Change it to `{"background", "store"}`.

Installed `strands/models/openai_responses.py` `_format_request` then does:

```python
request["tools"] = [
    *request.get("tools", []),
    *({ "type": "function", "name": ..., "parameters": ... } for tool_spec in tool_specs),
]
```

`store` is set afterward from `self.stateful`. Leave `background` and `store` stripped. Graph nodes, `use_agent`, and `use_skill` all call `in_process_model`. Think's nested `TemporalAgent` does not.

Replace the comment that cites `chat-975a5afb58315ff9`. If a request fails, record the response body. Do not put `tools` back into the strip set.

**Test:** a model whose `params["tools"]` contains `web_search` still has that entry after `in_process_model`. `background` and `store` are gone. `stateful` is false.

The server-side names and the rule against putting them on a node are in "What the system prompt tells the agent". This task does not edit `agent.json`.

---

## Task B — Formations

This is a constraint, not a subagent. Do not rewrite `orchestrator/graph_tool.py`. The registry agent writes the prompt sentences. The in-activity agent checks them.

A node is one of:

1. `agent`. Omitted `tools` means every tool on the in-activity parent registry. The model is the parent model.
2. Several `agent` and `skill_agent` nodes with no edge between them. That is one batch.
3. `swarm`. Members are `agent` or `skill_agent` only.
4. `graph`. Nodes may be `agent`, `skill_agent`, `swarm`, or `graph`.

`skill_agent` uses `build_skill_agent`. It does not call `use_skill_activity`.

The `graph` tool is not on the session until `load_tool` attaches `graph_activity`. The SOP and the prompt say that.

---

## Task C — Journal, environment, and http_request

Do not write an activity. `load_tool_activity` already returns the spec. `register_community_tool` already dispatches `workflow.execute_activity(name, tool_use)`. `run_loaded_tool` already runs `stream()`. The registry section calls that path for `journal`, `environment`, and `http_request` when the session starts. The same path loads every other community tool later.

Journal files stay `cwd()/journal/YYYY-MM-DD.md`. Actions stay `write`, `read`, `list`, `add_task`. There is no delete and no notebook tool. The user deletes an entry by editing the markdown file. The dream does not write the journal.

The in-activity parent used by graph and `use_skill` is already inside an activity, so it calls the installed `journal`, `environment`, and `http_request` functions directly. That is not a new activity file.

---

## Task D — Two Telnyx buckets

Constants in `config.py`:

- `OFFLOAD_BUCKET = "gwen-offload"`
- `MEMORY_BUCKET = "gwen-memory"`
- `OFFLOAD_PAYLOAD_BYTES = 524_288`

The Telnyx MCP tools do the work. Confirm each tool's arguments from that server's tool list at implementation time. Documented operations: `cloud_storage_list_buckets`, `cloud_storage_create_bucket`, `cloud_storage_upload_file`, `cloud_storage_download_file`, `cloud_storage_list_objects`, `cloud_storage_delete_object`, `create_embeddings`, `list_embedded_buckets`, and similarity search. Use the listed schema.

- Reuse the two buckets when they already exist. Create a missing one once.
- Offload keys: `sessions/{session_id}/{tool_name}/{activity_id}.json`
- Memory keys, one file per memory: `index.md`, `skills/{skill_name}.md`, `topics/{topic}/{date}-{slug}.md`, `dream-cursor.md`
- `create_embeddings` once, only when `list_embedded_buckets` does not include `gwen-memory`. Later uploads and deletes update embeddings on their own.
- Do not call `embed_url`. Do not embed `gwen-offload`.

`graph_activity`, `use_agent_activity`, and `use_skill_activity`, before they return, upload a JSON body at or above `OFFLOAD_PAYLOAD_BYTES` through the Telnyx `MCPClient` factory and return a short string: tool name, status, object key, and a preview of a few hundred characters. Smaller results return unchanged. The streaming sample returns `str(result)`. Match that.

---

## Task E — Truncate the stream before continue-as-new

Assigned with handoff in batch 2. Not its own subagent.

`WorkflowStream.get_state()` copies the whole log. `truncate(up_to_offset)` drops entries before that offset. Truncating past the log raises `ApplicationError` type `TruncateOutOfRange`.

```python
@workflow.update
def truncate_stream(self, up_to_offset: int) -> None:
    self._stream.truncate(up_to_offset)
```

`orchestrator/server.py` calls this update with the offset it has already forwarded, and `run` truncates before `get_state()`. Do not change part types in `app/api/orchestrator/route.ts`. A subscriber that reconnects below the new base follows the base offset the stream reports.

---

## Task F — Dream workflow

`orchestrator/dream_workflow.py`. One shot, `model=` required because the worker sets `StrandsPlugin(models=...)`.

```python
@workflow.defn
class DreamWorkflow:
    @workflow.run
    async def run(self, model_id: str) -> str:
        agent = TemporalAgent(
            model=model_id,
            system_prompt=DREAM_PROMPT,
            tools=[journal_tool, *temporal_mcp_clients()],
            start_to_close_timeout=MODEL_START_TO_CLOSE,
            heartbeat_timeout=MODEL_HEARTBEAT,
            retry_policy=MODEL_RETRY_POLICY,
        )
        return str(await agent.invoke_async(DREAM_TASK))
```

`model_id` is a factory already registered on the worker and one of the delegated models that worker already lists: Fable 5.1, GPT 6, DeepSeek V4 Flash, DeepSeek V4 Pro, GLM 5.3, GLM 5.3 Flash, Gemini 3.8 Flash, Grok 4.6, Kimi K3. Do not add a model id literal.

`DREAM_PROMPT`:

1. Read `gwen-memory/index.md`. If the bucket has never been embedded, call `create_embeddings` once.
2. Read journal entries newer than the date in `gwen-memory/dream-cursor.md` with the journal tool.
3. List `gwen-offload/sessions/` for prefixes newer than that cursor and read those objects.
4. Stop without writing when the cursor is newer than `DREAM_MIN_INTERVAL` and the new session count is below `DREAM_MIN_SESSIONS`. Both constants live in `config.py`. Start with 24 hours and 5 sessions.
5. Write or replace separate memory files. A session may produce several, or none. One file is one decision, one preference, one skill reflection, or one verified fact. Each file names the session id and the offload key.
6. Replace a file when the new sessions contradict it.
7. Rewrite `index.md` and `dream-cursor.md`.
8. Do not modify `cwd()/journal` or `gwen-offload`. Do not promote a to-do or a raw tool body.

Schedule in `run_worker.py`:

- `ScheduleActionStartWorkflow` for `DreamWorkflow`
- `ScheduleSpec(intervals=[ScheduleIntervalSpec(every=DREAM_MIN_INTERVAL)])`
- `SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP)`

`SKIP` is the lock. Create the schedule if it is absent.

---

## Task G — Delegation SOP

Install `strands-agents-sops` into `orchestrator/.venv` and add it to `orchestrator/requirements.txt`. Do not copy the GitHub tree into the repo.

Write `orchestrator/sops/delegation.sop.md` in the format of that repo's `rules/agent-sop-format.md`. It tells the orchestrator: call `discover_tools` before naming a tool that is not on the session; `load_tool` it; `graph` is loaded that way and is `graph_activity`; a swarm the agent calls directly is the discovered `swarm` tool; a swarm inside a graph is a node whose members are `agent` or `skill_agent`; omit `model_id`; omit `tools` to inherit; Agent API names are not registry names. Do not put `code-assist` or `pdd` into the orchestrator prompt.

Add the MCP server with no `continue_on_error` exclusion:

```json
"agent-sops": {
  "command": ".venv/bin/strands-agents-sops",
  "args": ["mcp", "--sop-paths", "sops"]
}
```

That server registers prompts with `mcp.prompt()`, and `TemporalMCPClient` only lists tools. Mount it anyway. Also load `delegation.sop.md` once at import, inside `workflow.unsafe.imports_passed_through()`, and append that string to the system prompt `run_worker.agent_identity()` returns. The workflow does not read the file.

`BeforeToolCallEvent` hook, same `HookProvider` pattern as `.agents/skills/strands-temporal/scripts/strands_plugin/hooks/workflow.py`. The callback only reads `event.tool_use` and may set `event.cancel_tool`. Cancel `graph`, `use_agent`, or `use_skill` when:

- `graph` topology is a string, or a node sets `model_id` or `model_settings`
- a node type is `workflow` or `parallel`
- a `skill_agent` has no `skill`
- a graph `swarm` member is not `agent` or `skill_agent`
- a `tools` entry names a tool that is neither on the session nor in the loaded-tool paths

Do not cancel a call to the discovered `swarm` tool. That tool is `strands_tools.swarm`, and its `agents` field is its own schema.

---

## Task H — Think renders as one chain

Think streams on `THINKING_TOPIC`. The route already turns that text into reasoning parts. In `components/v0/agent-activity.tsx` a nested tool frame closes the reasoning block, and `unstreamedThinkOutput` hides the think step once the text has streamed.

Keep the open Think `ChainOfThought` across the text, the tool calls, the tool results, and the native-tool events of that think invocation. Use the existing `ChainOfThought` steps. Do not add a reasoning renderer. Do not change `app/api/orchestrator/route.ts` unless a test shows a thinking-topic frame never becomes a part.

`think` itself is discovered and loaded. Loading it must attach the existing native think tool, not a second implementation. The nested agent stays `TemporalAgent(model=self._model_id)` with the think tool filtered out.

---

## Task I — Take Over and Relinquish, on the same agent

The computer is not a second agent. `ChatWorkflow` is the only session. Browser and computer-use actions are tools of that agent. They run on the `desktop-browser` queue because the screen is in the Linux container, and the workflow id, the messages, and the model stay the chat session. Do not add a computer-use agent, a second `TemporalAgent`, or a Gemini `computer_use` loop as another controller. `run_worker.build_model_factory` currently puts `genai.types.ComputerUse` on every Gemini model. Remove that. Gemini uses the same desktop tools as every other model.

Take Over and Relinquish are broken because the button still posts and the server refuses. `components/v0/agent-chat.tsx` `handoff` POSTs `/api/orchestrator/handoff`. `orchestrator/server.py` `handoff` ignores the body and returns HTTP 410, `"Desktop handoff is not a workflow update"`. `ChatWorkflow` has no handoff update. The activity that already implements the transfer is `browser_activity.desktop_control` (`@activity.defn(name="desktop_control")`), and it only runs on the desktop worker.

Put the update back on `ChatWorkflow`. It calls `desktop_control` with the desktop activity options already in `agent_tools.py` (`task_queue=DESKTOP_BROWSER_TASK_QUEUE`). The server `handoff` endpoint calls that update. One action moves one step. The activity's allowed modes are the order:

`agent` → `stopping` → `human` → `relinquishing` → `instructions` → `resuming` → `agent`

`take` while `agent` enters `stopping`, then `human`. `release` while `human` enters `relinquishing`, then `instructions`. `give` while `instructions` is `prepare_resume` then `resume`, and the steering text is the next turn on this same session (`agent-chat.tsx` `giveControl` already sends that message). A call in the wrong mode returns the activity's existing error. Do not add phases.

The UI in `computer-use-preview.tsx` already has Take control, Relinquish, and the steering composer. After the server returns 204, `refreshControl` must read the mode the activity wrote. The app test for this task is the desktop contract in `orchestrator/desktop/AGENTS.md`: take control during work, change something visible, relinquish, send the steering sentence, and show the same session id afterward.

`app/api/orchestrator/handoff/route.ts` is a protected route. Change it only if a failing test shows it cannot forward `action` and `message` to the server endpoint that already exists. The server endpoint is not a protected file.

---

## Task J — Reasoning levels follow the Agent API catalog

Assigned to the worker-wiring agent in batch 3, because the catalog functions are in `run_worker.py`. Not its own subagent.

`lib/reasoning-levels.json` is a hand-written map. `components/v0/model-picker.tsx` `reasoningLevels` returns `[]` for any id that is not a key, so a model the worker added from `GET /v1/models` has no effort menu. `orchestrator/config.py` `REASONING_EFFORTS` is the Agent API set: `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Presets in the JSON already use that full set. Gemini stays `low`, `medium`, `high` because those are Google thinking levels, not Agent API efforts.

`run_worker.fetch_model_ids` and `build_perplexity_factories` are the tool that adds models. Extend that path:

- Every id in the live catalog, and every preset, gets `REASONING_EFFORTS`.
- An id that was in the previous catalog and is absent from this fetch is not registered and is removed from `reasoning-levels.json`.
- Gemini ids are left as they are.
- A model that appears in a later fetch is written into the JSON in the same function that registers its factory. Do not add a second list.

`server.py` already rejects a `reasoning_effort` that is not in `REASONING_LEVELS` for that id. After this change, a new Agent API model accepts the six efforts, and a removed model is rejected because it is no longer in the factory map.

**Test:** a fake catalog of two ids writes both with the six efforts. A second fetch with one id deletes the other from the JSON and from the factory map. `gemini-3.8-flash` is still `low`, `medium`, `high`.

---

## Task K — Settings, and a tool selector on the composer

Two agents. Settings is batch 2. The composer selector is batch 3, after settings has finished `agent-chat.tsx`.

There is no settings view. Add one the chat can open. It lists the models from `/api/models`. The user checks the ones this browser may use and picks one default. The picker and a new session offer only the checked models. The default is the model a new session starts on. Store the choice in the browser. Do not add a settings service.

The composer is `components/v0/composer.tsx`, built from `PromptInput`. Add a selector on that input for tools and skills. Tools come from `discover_tools` plus the tools already on the session. Skills come from the catalog `use_skill` already uses. The user checks the ones this session may call. The turn sends those names. The session agent may call a checked tool or skill and may not call an unchecked one. `load_tool` still attaches a checked community tool through `run_loaded_tool`. An unchecked name is not loaded.

The turn body today is `model_id` and `reasoning_effort` (`app/api/orchestrator/route.ts`, which is protected). This task is the request to carry the selected tool names and skill names on that body. Change the route only far enough to forward those two arrays. Do not redesign the stream.

The selector uses the existing prompt-input controls. Do not build a second composer.

---

## App test script

Restart the stack first. Then use only the browser at `http://localhost:3001`.

1. The page loads, the composer accepts text, and a short prompt returns an assistant message.
2. Ask the agent which tools it has, which are server-side, which are discoverable, which MCP servers are connected, and which tools it chains. The reply names the three groups and the chains in "What the system prompt tells the agent". The tool trace shows `load_tool`, `discover_tools`, `unload_tools`, `use_skill`, `environment`, `http_request`, `journal`, and the MCP tools from the servers that connected. It does not show `graph`, `swarm`, or `think` until a later step loads them.
3. Ask it to discover tools. The reply lists `forge`, `dicom`, and `fhir`. It does not list `pubmed` or `fda`.
4. Ask it to write a journal note and then read today's journal. The note is in the file `orchestrator/journal/<today>.md` and the chat shows the read-back.
5. Ask it to call `environment` for one variable name and `http_request` against `http://localhost:8787/health`. Both return in the trace.
6. Ask it to `load_tool` `forge` and call `forge.health`. The trace shows the load, then the health result.
7. Ask it to `load_tool` `swarm` and run a two-member swarm on a one-sentence task. The trace shows the swarm tool running. If a member errors because the installed swarm builds `Agent()` with `BedrockModel` when `model` is omitted (`strands/agent/agent.py` assigns `BedrockModel()` when model is `None`), record that as a defect and fix the parent passed into `strands_tools.swarm.swarm` so each member's model is the session model. Do not rewrite swarm.
8. Ask it to `load_tool` `graph` and run one `agent` node with no `tools` key and no `model_id`. The node runs. A second call with a string topology is cancelled by the SOP hook and the chat shows that cancellation.
9. Ask it to `load_tool` `think` and think for one cycle. The chain of thought stays one chain: the thought text and any tool call inside it are in the same component.
10. For each MCP server that connected, the trace or a `discover` of that server's tools shows at least one tool. For each server that did not connect, the note names it and the missing env or the process error. Do not mark a missing-env server as a product bug.
11. Open a desktop turn, click Take control, confirm the badge says the human has the screen, click Relinquish, type a steering sentence, and confirm the same session continues. Record the session id before and after.
12. Open settings, turn off one model, set another as the default, reload the page, and confirm the picker and the new session match. On the composer, select one tool and one skill, send a message, and confirm the trace uses the selected names and does not call an unselected tool.
13. Pick a model that was missing from `reasoning-levels.json` before this change. The effort menu shows minimal, low, medium, high, xhigh, and max.

Fix any step that fails and repeat from step 1 until all thirteen succeed in a single browser session. Write the thirteen results at `docs/superpowers/plans/2026-10-08-app-test.md`.

---

## Unit tests while the batches run

From `orchestrator/`:

```bash
.venv/bin/python -m pytest tests/test_subagent_support.py tests/test_graph_activity.py tests/test_graph_tool_adherence.py tests/test_use_skill_activity.py tests/test_use_agent_activity.py tests/test_think_native.py tests/test_mcp_shell.py tests/test_healthcare_forge_catalog.py -q
```

Add tests for the registry list, the MCP factory name set, `in_process_model`, and the SOP hook.

`pytest tests` will still fail collection where older tests import `TurnInput` and `_ToolResultHook`. Report those. Do not reintroduce the symbols.

Frontend, from the repo root, after the Think, settings, and selector agents have landed:

```bash
npx tsc --noEmit
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' components/v0/agent-activity-native.test.tsx components/v0/graph-run.test.ts
```

---

## Do not

- Edit `app/api/**` route files unless a failing compatibility test proves the backend cannot satisfy the contract.
- Enable `Agent(context_manager="auto")` or a `ContextOffloader` on the workflow agent.
- Call `take_snapshot` or `load_snapshot`.
- Pass a live `Model` into a workflow.
- Add LanceDB, Mem0, a second journal, or a payload store beside the Telnyx buckets.
- Lower stream batch intervals.
- Patch files under site-packages.
- Hide `swarm`, forge, dicom, or fhir from discovery.
- Invent pubmed or FDA tools.
- Claim the work is done before the browser script passes.

**Replay.** Sessions already running keep their old registry until they end. Do not claim replay coverage.

---

## Done when

- A new session's tool list is `load_tool`, `discover_tools`, `unload_tools`, `use_skill`, `environment`, `http_request`, `journal`, and one `TemporalMCPClient` per connected server.
- `agent.json` states the registered tools, the server-side Agent API tools, the discoverable tools, each MCP server, and the ten chains. A turn's tool and skill selection narrows that list.
- `discover_tools` lists `forge`, `dicom`, `fhir`, `swarm`, `graph`, and `think`.
- `load_tool` of `swarm`, `forge`, `dicom`, and `fhir` runs them through `run_loaded_tool`.
- `load_tool` of `graph` runs `graph_activity`.
- Every `mcp.json` server with its env set is connected. Missing env is logged by name.
- The forge, dicom, and fhir inventories above were executed or explicitly marked network-bound. Pubmed's absence and FDA's empty package are written down.
- `in_process_model` keeps `params["tools"]`.
- Journal, offload, dream, stream truncate, the delegation SOP, and the Think chain behave as their tasks say.
- Take Over and Relinquish run through `ChatWorkflow` and `desktop_control`. There is no second computer agent. The Gemini `computer_use` tool is not attached.
- Agent API models in the live catalog have the six reasoning efforts. Models the catalog no longer returns are not registered and are not in `reasoning-levels.json`.
- Settings chooses the visible models and the default. The composer chooses the tools and skills for the session.
- `docs/superpowers/plans/2026-10-08-app-test.md` records a single browser pass of the thirteen steps with no open failure.
