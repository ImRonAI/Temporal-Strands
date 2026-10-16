# GCE Desktop Agent Implementation Plan

Date: 2026-09-08

## 1. Goal and Scope

Run Gwen's agent, persistent Linux desktop, browser, project files, terminal, and coding tools in Google Cloud. Embed the desktop in Gwen using noVNC and the existing AI Elements preview composition. Implement screenshot-driven computer use through the official Temporal/Strands integration, with asynchronous progress, recoverable execution, downloadable artifacts, and GCS-backed long-term memory.

This is an implementation plan, not authorization to provision resources, change IAM, expose services publicly, delete data, or modify the supplied VM. On 2026-09-08, after the user supplied a service-account key, planning included authenticated read-only bucket metadata, bucket permission checks, and a limited live instance identity/status check. No objects, IAM policies, or cloud resources were modified. Key material and access tokens were not printed or copied into this document.

Initial release scope: one authenticated owner, one persistent desktop, multiple projects, and one exclusive agent-or-human controller. The owner model is an explicit planning default, not a recorded user decision. Multi-user tenancy, concurrently isolated desktops, Windows, GPU workloads, audio/video conferencing, and arbitrary privileged package installation are out of scope. Do not implement a shared desktop as if it were secure multi-tenancy.

Deployment inputs still to collect through the deployment process: approved application and preview hostnames, authorized human Google identity, Secret Manager resource references, approved runtime-credential strategy, and approved capacity. The initial project/zone/instance and bucket are now identified below. Never infer an authorized human identity from SSH metadata or a service-account key. Missing inputs block deployment, not implementation of the tested components.

## 2. Verified Starting Point

| Boundary | Actual local evidence | Implication |
|---|---|---|
| VM export | `compute.json:54-69,88-105,125-151`: instance `strands`, project `fair-expanse-493212-h8`, zone `us-central1-a`, Ubuntu 24.04 license, `e2-standard-2`, 50 GB persistent boot disk, external IPv4, deletion protection | Treat as an exported description, not deployable infrastructure or proof of current connectivity. E2-standard-2 is a 2-vCPU/8-GB class machine; benchmark before committing to all-in-one capacity. |
| Startup metadata | `compute.json:83`: local launcher uses its own script location to find the repository and runs `pnpm dev:all` | Unsuitable as the VM boot contract. Do not depend on the metadata runner's directory or development port cleanup. |
| Deployment | `scripts/gce-startup.sh` exists, untracked; approved URL/SHA256 release bundles, sibling app/tools trees, Secret Manager, systemd, loopback services, health rollback | Extend after review; do not overwrite this existing work. Temporal is currently a persistent development server, not production infrastructure. |
| Durable agent | `orchestrator/workflow.py`, `run_worker.py`, `server.py`: `TemporalAgent`, named model factories, `activity_as_tool`, workflow streaming, approval and handoff | Preserve this execution model. No replacement standalone Python agent loop. |
| Computer use | `computer_use_activity.py`, `browser_activity.py`, `gemini_model.py`: worker-local Chromium and process-local screenshot delivery | Not a full desktop; process-local browser state cannot be the remote-session registry. |
| Model images | `perplexity_model.py:136-208`: images in messages are supported; non-text/JSON tool-result blocks are rejected | A screenshot filename or JSON URL alone is not vision input. Add explicit artifact-to-image hydration inside the model activity. |
| Preview | `components/v0/computer-use-preview.tsx`, `computer-use.ts`, `public/computer-use-live.html` | Reuse native WebPreview composition and handoff UX, not the CDP viewer as a desktop protocol. Current localhost URLs do not work for remote users. |
| Files/terminal | `components/v0/project-ide.ts`, `project-ide-panel.tsx`: stream-derived file state and read-only terminal transcript | Add authoritative workspace APIs, actual async stdout, optional human PTY, project export, and remote dev-server routing. |
| Auth | No application identity/ownership middleware found; existing APIs trust reachable callers | Authentication and authorization are release gates before browser, terminal, or desktop ingress. |
| Memory | `orchestrator/memory.py` absent; LanceDB and embedding generation are already planned | Implement memory deliberately rather than treating chat history as memory. |
| Strands memory source | Sibling `strands-tools/src/strands_tools/strands_tools_memory.py:1-21,102-158` uses Bedrock Knowledge Bases/runtime, not merely S3 | Reuse the store/get/list/retrieve/delete contract, not a mechanical bucket substitution. Local `strands_tools/memory.py` is instead a Hindsight/LlamaIndex module; filenames are not reliable provenance. |
| OpenCode | Root `opencode.json` has no Agent API provider, permissive tool policies, and automatic sharing | Do not copy this config into the desktop. Generate a separate managed runtime config with sharing disabled and a verified provider adapter. |
| Supplied cloud identity | Key file `fair-expanse-493212-h8-138622c839d2.json` authenticated as `964661841583-compute@developer.gserviceaccount.com` in project `fair-expanse-493212-h8` | This is the Compute Engine default service-account identity, not evidence that it is attached to this instance or safe for agent access. Credentials were consumed only by Google's auth library for scoped metadata requests. |
| Existing bucket | Live metadata: `gs://thisforagent`, created `2026-09-08T04:35:58.216Z`, `US` multi-region, `STANDARD`, uniform bucket-level access enabled, public-access prevention enforced, seven-day soft deletion | Use this existing bucket for the pilot with explicit prefixes and scoped cleanup. Do not create replacement buckets or assume regional placement. |
| Permission check | `testIamPermissions` returned bucket get/getIamPolicy/update and object list/get/create/update/delete | Reported permissions support the planned object operations, but no upload/delete was executed. This is not proof of all cloud/Secret Manager/signing/deployment permissions. |
| Live VM identity | A limited Compute API GET returned `strands`, `RUNNING`, `e2-standard-2`, with no `serviceAccounts` field | Treat the instance as lacking an attached runtime identity until deployment confirms otherwise. The key working locally does not configure VM ADC. |
| Key exposure prevention | `git status` reports the key as untracked; `git check-ignore` found no ignore rule | Add credential exclusions before staging, image builds, release packaging, exports, indexing, or backups. Untracked does not mean protected. |

Some AGENTS.md inventory descriptions are stale. Follow its safety constraints, but use inspected source to determine implementation status. Preserve all pre-existing worktree changes.

## 3. Architecture Decisions

```text
Authenticated browser
  | HTTPS: chat, desktop panel, files, artifacts, memory
  | WSS: noVNC and optional human terminal
  v
TLS/auth gateway -> Next.js UI -> FastAPI -> Temporal -> Strands worker
                         |                     | model activities -> Agent API
                         |                     | activity_as_tool
                         v                     v
                 session/ownership broker -> workspace service
                                                |
                                   isolated desktop container
                                   X11 + XFCE + VNC
                                   browser + terminal + OpenCode
                                   persistent /workspace

Trusted storage service -> private GCS records, artifacts, checkpoints
Separate preview origin -> registered workspace dev-server ports only
```

1. Keep the control plane outside the desktop container. The container runs as an unprivileged user with no host filesystem, Docker socket, cloud credentials, or control-plane secrets mounted.
2. Use X11 for v1, with a pinned TigerVNC virtual X server and lightweight XFCE session. Add `dbus`, a file manager, terminal, browser, XTest input support, screenshot capture, and clipboard support. Do not combine redundant Xvfb/x11vnc/TigerVNC stacks without a demonstrated need.
3. noVNC renders the desktop for the human. The workspace service captures screenshots and sends input for the agent. The agent does not automate the noVNC webpage.
4. Desktop browser, file operations, terminal, and OpenCode share the same workspace and visible desktop environment. Provider-native Agent API sandbox remains a separate filesystem, labeled as such; moving its files into a project requires an explicit import operation.
5. Existing browser-only sessions remain available. Desktop-mode tasks receive desktop-scoped tools, not unrestricted worker-host shell/file tools. Dynamic `load_tool`, MCP connections, graph nodes, and delegated agents must enforce the same scope or be unavailable in desktop mode.
6. Use the existing `ChatWorkflow` and `TemporalAgent` for the first computer-use loop. Add an explicit desktop execution mode and durable workspace binding rather than another nested opaque agent loop. All environment actions are Temporal activities exposed through `activity_as_tool`; workflow hooks enforce ordering, limits, approval, and handoff.
7. Do not choose a vision model by name alone. Discover Agent API models, verify image input plus function calling with contract tests, and allow a verified model override. Unsupported models fail with an actionable capability message rather than silently operating blind. Do not hardcode an unverified model list.
8. Continue using approved, pinned release bundles containing both `app/` and `strands-tools/src/`, including skills. No automatic upstream dependency upgrades.

## 4. Identity, Isolation, and Cloud Setup

Definitions:

| Identifier | Meaning |
|---|---|
| `owner_id` | Server-derived authenticated principal; never a trusted model argument |
| `workspace_id` | Persistent desktop home/project namespace, independent of chat and worker lifetime |
| `project_id` | Registered project root inside the workspace |
| `session_id` | Existing chat/Temporal workflow identity |
| `desktop_epoch` | Changes when the desktop is recreated; invalidates old frames and input |
| `lease_epoch` | Monotonically increasing controller fence |
| `operation_id` | Stable side-effect identifier surviving Temporal activity retries |
| `artifact_id` | Authorized immutable object reference with generation and hash |

Implementation requirements:

1. Front the private services with TLS and Google OIDC authentication, using a pinned auth proxy and explicit owner allowlist. FastAPI must receive a verified principal through a trusted boundary; strip client-supplied identity headers. All backend service ports stay private.
2. Apply owner/workspace authorization to every chat, desktop, terminal, artifact, memory, and preview operation. Protect mutating HTTP endpoints against CSRF and validate WebSocket Origin. Authorization at the page route alone is insufficient.
3. Expose HTTPS only for the application. Do not publish VNC, websockify, CDP, workspace API, terminal, Temporal, or dev-server ports. Administrative access uses an approved IAP/SSH path. Verify actual firewall rules; instance tags are not evidence of configured rules.
4. Provide outbound internet for browsing and package registries. Enforce isolation outside the container: block cloud metadata IP/hostname and link-local/private control-plane destinations, except narrow broker endpoints. Handle DNS rebinding, redirects, IPv6 if enabled, and direct-IP bypasses. Browser URL validation alone is not a network security boundary.
5. Use the supplied service-account key for authorized bootstrap/preflight where necessary, without exposing it to the desktop. Prefer an explicitly approved attached runtime identity and ADC on GCE; the live instance check returned no attached identity. Verify IAM and OAuth scopes independently. The key's broad bucket access is an available capability, not a reason to grant every process cloud access. Follow the credential transition in Section 14 before deployment.
6. Container controls: non-root, dropped capabilities, no-new-privileges, tested seccomp policy, resource limits, persistent data volume, bounded temp/cache storage. Preserve the browser sandbox. Containers reduce blast radius but are not equivalent to separate VMs for hostile multi-tenant code.
7. User project programs and development previews run on a separate origin from authenticated Gwen. Never render arbitrary project JavaScript with Gwen's origin/cookies. Use scoped preview tickets and an authenticated reverse proxy, not arbitrary host/port forwarding.
8. Use Secret Manager for provider credentials. A narrowly scoped model gateway holds the upstream key; OpenCode receives only revocable workspace credentials. Do not expose cloud credentials to shell commands or screenshots.

The current machine is a pilot baseline, not a performance promise. Enforce one active desktop/coding workload, build releases outside the interactive VM, and measure OOM/CPU pressure. Resize or split compute only with approval. Keep the 50 GB boot disk; propose a separately budgeted persistent data disk so deployment rollback does not affect projects. Do not silently attach or resize disks.

## 5. Desktop Tools and Control Lease

Implement a small typed API instead of exposing raw X11, arbitrary host commands, or arbitrary paths:

| Tool group | Required operations |
|---|---|
| Observation | Full screenshot, bounded crop, display geometry, active window, cursor; optional accessibility observations where supported |
| Mouse | Move/hover, left/right/middle click, double click, press/release, drag along a bounded path |
| Scroll | Up/down/left/right with explicit amount and pointer position |
| Keyboard | Type text, individual keys, chords, key-down/key-up, select-all, copy, cut, paste |
| Clipboard | Read/write desktop clipboard, explicit clipboard-to-human transfer, bounded text; no automatic host clipboard sync |
| Workspace | Project list/create/open, file list/read/search/write/patch/move/copy/trash, upload/download/export |
| Terminal | Start command, read/reconnect output, stdin, resize PTY where applicable, interrupt, cancel, exit status |
| Preview | Register/unregister a project development server and obtain an authorized preview URL |

Requirements:

1. All actions bind to workspace, desktop epoch, lease epoch, and operation ID. Coordinates reference a particular screenshot and its original width/height. Reject stale epochs, invalid bounds, and changed geometry; reacquire an observation after resize or human intervention.
2. Define coordinates in physical desktop pixels. Crops include origin and scale metadata; map model coordinates back before dispatch. Browser CSS scaling must not change the agent coordinate system.
3. Implement Unicode and multiline text through a tested desktop input/clipboard path; do not assume ASCII key simulation handles every language. Support Ctrl+C/X/V/A in GUI apps and Ctrl+Shift+C/V in terminals. Preserve or explicitly replace clipboard state, and keep sensitive text out of command-line arguments and logs.
4. Drag uses ordered mouse-down, path movement, mouse-up with guaranteed release in cleanup. On interruption, release all tracked keys/buttons. Support desktop file-manager drag-and-drop; local-browser file drops use the upload API and are not falsely described as VNC file transfer.
5. Serialize visible desktop mutations with an exclusive lease. File writes, agent commands, and OpenCode mutations also respect the controller/project write policy to avoid edits racing with human intervention.
6. Take-control first fences new agent operations, cancels/waits for in-flight mutations, pauses delegated coding jobs where safely supported or reports why takeover is pending, then acknowledges human control. Resume revokes old human credentials, acquires a new fence, captures fresh state, and continues with the human's instructions.
7. Enforce noVNC view-only versus control server-side through a tested VNC-server permission mechanism or RFB-aware gateway. CSS pointer disabling and noVNC's client-side `viewOnly` property are UX only. Existing sockets must lose input permission on lease revocation.
8. Unknown input results are not success. If the service crashes after dispatch but before recording completion, mark the operation `outcome_unknown`, capture fresh state, and reconcile. Do not replay a possible payment, deletion, click, or keystroke blindly.

Maintain the operation journal and lease state transactionally on persistent disk, with unique operation IDs and fencing checks. A single VM service can use SQLite with WAL and tested backup; it is not a distributed lock service. Return saved results for duplicates when known. A journal cannot make arbitrary GUI effects exactly-once.

## 6. Durable Async Computer-Use Loop

Use the Strands agent's native tool loop, not `while True` around direct provider calls in a long-running tool activity.

1. At desktop-task entry, resolve authenticated workspace binding, acquire the control lease, retrieve bounded task/project memory, and obtain an observation through activities.
2. `TemporalAgent.invoke_async` performs the model step through a named factory on the worker. Expose only the scoped desktop/workspace tools and approved skills/delegations.
3. Workflow-safe hooks enforce observe-before-act, one visible desktop mutation at a time, approval policy, and configured step/runtime/token limits. Parallel read-only work is permitted where it cannot invalidate observations.
4. Validate proposed tool arguments and fence before execution. Any action requiring approval interrupts through the existing Temporal/Strands approval mechanism, with an expiration and operation-bound approval record.
5. Execute through `activity_as_tool`. Publish action-started metadata; execute; capture resulting state; persist the observation; publish action-completed/error metadata; return compact structured results.
6. The next model step receives the actual latest image and relevant result, not a path string. Repeat until a verified completion, user intervention, explicit failure, budget limit, or cancellation.
7. Completion includes outcome, changes made, evidence references, project/download references, and remaining limitations. Persist a bounded task summary and memory candidates. A reported intention is not completion evidence.

Durability and async rules:

- All network, disk, screenshot, subprocess, storage, and clock-dependent environment work stays in activities/services. Keep workflow hooks deterministic. Do not pass live models/clients into workflow constructors.
- Use async HTTP/subprocess APIs and bounded queues. Run blocking GCS/X11/image operations in a dedicated bounded executor, not on the async event loop.
- Configure timeouts, heartbeat intervals, retry policies, and task limits in `orchestrator/config.py`. Proposed starting limits: 100 desktop mutations and 30 minutes per task; explicit user-approved continuation is possible. These are configurable safety defaults, not performance estimates.
- Read-only activities may retry. Mutations use maximum one automatic attempt until server-side deduplication and reconciliation are proven. Long jobs heartbeat operation IDs/cursors and have remote cancellation plus hard process-group termination as fallback.
- Cancellation stops new side effects, cancels active provider background responses, terminates or interrupts workspace jobs as appropriate, releases held input, and records the final state. Browser disconnect alone does not cancel durable work.
- Continue-as-new carries workspace binding, epochs, bounded messages, operation references, and summaries. Never carry image bytes or unbounded terminal output. Rehydrate by durable identifiers after worker loss; do not depend on global browser objects.
- Version changed workflow behavior and replay recorded histories before rollout. Code rollback does not roll back Temporal history or external side effects.

### Screenshot-to-Model Contract

Add a trusted observation descriptor containing artifact ID, immutable object generation/hash, dimensions, crop transform, capture time, workspace/desktop epoch, and operation ID. Store only descriptors in Temporal history.

Extend the Perplexity model activity's request preparation to resolve authorized observation descriptors from completed desktop tools, fetch the immutable bytes outside workflow execution, and add supported `input_image` message content in valid function-call/output order. Keep function outputs as supported text/JSON. Prefer worker-side data-URI hydration so buckets remain private and expiring URLs are not baked into workflow history.

Only trusted structured desktop results qualify for hydration; text resembling an artifact descriptor in a webpage or shell output must not trigger a fetch. Validate owner/workspace and size/type limits. Hydrate the latest required observation and bounded explicitly requested comparisons, not every screenshot in history. On expiry/missing artifact, produce a recoverable missing-observation result and capture fresh state; do not fabricate an image or silently use a different frame.

Use supplied Agent API schemas under `docs/integrations/openapi.md` and the actual SDK contracts for this work. Test image input, function output ordering, streamed calls, cancellation/reconnect, and model capability before enabling autonomous input. Existing Gemini browser-specific computer-use behavior remains separate until an explicitly tested desktop adapter exists.

## 7. Streaming and UI

Three distinct channels prevent video and terminal logs from overwhelming Temporal:

| Channel | Content | Persistence |
|---|---|---|
| Existing WorkflowStream/SSE | Model output, action summaries, approvals, artifacts, run state | Bounded durable events; existing reconnect semantics |
| Authenticated workspace SSE/WS | Incremental stdout/stderr, PTY, detailed operation progress with sequence/cursor | Bounded disk log plus GCS segments; reconnect by cursor |
| Authenticated noVNC WSS | Live framebuffer and authorized human input | Transient; no continuous video in Temporal/GCS by default |

1. Reuse `Agent`, `Task`, `ChainOfThought`, `MessageResponse`, `WebPreview`, `FileTree`, and `Terminal` native subcomponents. Preserve the current design system and responsive layout.
2. Create a desktop preview variant with connected/reconnecting/paused states, controller badge, take/resume control, fullscreen, clipboard controls, current screenshot, and artifact/project links. Mobile gets a fitted viewport and explicit keyboard/clipboard controls.
3. Publish versioned events with workspace/session/operation IDs, attempt, sequence, timestamp, type, status, and bounded payload. Render task progress and available model output; do not invent or promise hidden chain-of-thought.
4. Batch durable summaries initially at the repository's approximately two-second side-channel cadence, with prompt delivery of terminal states. Keep interactive framebuffer and stdout latency independent of that batch interval.
5. Drop/coalesce obsolete visual updates for slow clients; never drop the durable terminal outcome. Detect cursor gaps, fetch a state snapshot, and deduplicate events across reconnect and retries.
6. Preserve existing protected route contracts. Prefer new desktop/workspace/artifact endpoints and existing compatible tool-result envelopes. Change a protected `app/api/**/route.ts` only after a failing compatibility test demonstrates the backend cannot satisfy the contract.
7. Proxy WebSocket traffic through the deployment gateway, not an assumption that Next.js route handlers provide arbitrary WS upgrades. Remove public localhost/CDP links from cloud desktop output.

## 8. Terminal, Projects, and OpenCode

### Files and Projects

- Persistent POSIX workspace is the live source of truth: `/workspace/projects/<project_id>` and a separate persistent desktop home. GCS is for records/checkpoints/artifacts, not a mounted replacement for active Git repositories, SQLite, node_modules, or browser profiles.
- Resolve project roots server-side. Prevent traversal, symlink escape, unsafe archive extraction, and time-of-check/time-of-use races. Apply file-count/size/time limits to searches, uploads, and exports; stream large content.
- Return authoritative file listings and version/hash metadata. Writes/patches use preconditions and atomic replacement where applicable. Trash destructive local changes by default, with explicit permanent deletion.
- Downloads use authenticated artifact endpoints with validated MIME, safe filenames, `Content-Disposition`, range support where appropriate, and no shared public caching. Short-lived signed URLs are optional capabilities, never persistent identity.
- Export ZIP/tar archives with a manifest and SHA256. Include uncommitted/untracked project work by default but exclude secrets, runtime credentials, node_modules, caches, and virtual environments. Show exclusions and provide scoped explicit inclusion rather than claiming a full backup.
- Uploads land in staging, are verified, then moved into the project. Import native Agent API sandbox files through the existing provider file contract into the same artifact/import flow.
- Register dev servers by workspace job and allowed port; expose them through an isolated preview origin with HTTP and WebSocket/HMR support. Do not allow the agent to proxy arbitrary control-plane addresses.

### Async Terminal

- Implement workspace command start/status/output/input/cancel APIs with persistent operation IDs, cwd, exit codes, bounded output, and process-group cleanup. Supervise jobs independently from the Temporal activity connection.
- Stream stdout/stderr while commands run. Store cursored output segments and return a log artifact when output exceeds display/model budgets. Distinguish `running`, `succeeded`, `failed`, `cancelled`, and `outcome_unknown`.
- The human PTY is a separate authenticated capability under the human lease. It is not the same as a replay-only AI Elements terminal transcript. Sanitize unsafe terminal control sequences and hyperlinks in passive transcript views.
- Python/file/shell tools used by the agent must run in the workspace, not `run_worker.py`'s cwd. Disable host fallbacks and verify nested agents cannot recover them through `load_tool` or MCP.

### OpenCode with Agent API

1. Install a pinned, checksum-verified OpenCode release into the desktop image. Disable automatic sharing and upgrades. Keep per-workspace OpenCode sessions/config outside disposable containers and outside exported project source.
2. Implement a managed Agent API provider adapter against the pinned OpenCode/AI SDK extension contract. The existing repository `opencode.json` does not establish compatibility, and pointing a Chat Completions provider at `/v1/agent` is not sufficient.
3. Route provider traffic through a workspace-scoped model gateway holding the upstream key. Map model discovery, preset/model selection, messages, images, custom functions/results, stream events, usage, errors, cancellation, and resumable background response IDs. Preserve `stream: true`, `background: true`, and supported skills. Do not assume all presets support interactive coding equally.
4. OpenCode's local coding tools operate in the GCE workspace. Do not enable the provider-native sandbox as a substitute for those local project tools. Enforce the same filesystem/network limits even for shell escape and downloaded code.
5. Provide `coding_job` start/status/cancel/result tools via Temporal activities. Start jobs with stable IDs, reattach after worker failure, stream progress to Agent/Task and Terminal, and return final Markdown plus diff/artifact references to the calling agent.
6. OpenCode has its own internal loop. Be explicit: the launch, monitoring, result collection, and cancellation are Temporal-managed; individual internal OpenCode edits are not automatically Temporal activities. Never restart an ambiguous whole coding job and claim exactly-once execution. If per-edit Temporal durability becomes required, that is additional OpenCode integration scope.
7. Acceptance fixture: create/edit a small project, run its tests, display live output, open its preview, reconnect mid-job without duplicate work, then download the resulting project. Check credentials do not appear in logs/config exports.

## 9. Storage and Memory

### Storage Layout and Budgets

Use the existing `gs://thisforagent` bucket for the pilot. Its verified placement is `US` multi-region, not the previously proposed same-region bucket. It already has uniform bucket-level access and public-access prevention. Separate releases, active observations, finalized artifacts, projects, and memory into explicit prefixes as specified in Section 14. Prefixes are organization, not independent bucket-level security/retention boundaries. Split into separate buckets later only if approved isolation, retention, placement, or performance requirements demand it; do not provision additional buckets by default.

Example logical keys:

```text
gs://thisforagent/gwen/v1/active/<owner_id>/<workspace_id>/<task_id>/<artifact_id>
gs://thisforagent/gwen/v1/artifacts/<owner_id>/<workspace_id>/<artifact_id>
gs://thisforagent/gwen/v1/projects/<owner_id>/<project_id>/checkpoints/<checkpoint_id>/manifest.json
gs://thisforagent/gwen/v1/memory/<owner_id>/records/<memory_id>/<revision>.json
gs://thisforagent/gwen/v1/memory/<owner_id>/index-manifests/<generation>.json
```

1. Artifacts are immutable, content-hashed, and referenced by object generation. Publish a manifest only after uploads/checksums succeed, using GCS generation preconditions for conditional updates.
2. Retain observations referenced by active tasks until task completion plus a recovery window. Proposed defaults after completion: ordinary screenshots seven days; logs/temporary exports thirty days; project checkpoints daily for thirty days plus monthly for twelve months. Explicitly saved deliverables and memory persist until deleted. Make policies visible/configurable before enabling cleanup.
3. Never apply lifecycle deletion that can remove active-task evidence. Separate staging/active and finalized namespaces, and start retention timing from finalization. GCS lifecycle evaluation is asynchronous, not an exact deletion timer.
4. Stream and deduplicate screenshots; generate small thumbnails; capture full resolution only for task evidence or precision inspection. No default continuous recording. Keep latest model context to one current frame plus bounded comparisons.
5. Configure disk high-water alerts and cleanup for caches/staging/expired logs. Never delete the only copy of user source. Only evict artifacts after verified durable upload. Bound node/package caches and separate rebuildable dependencies from source checkpoints.
6. Account for soft-deleted/noncurrent objects, API operations, egress, provider image tokens, snapshots, and duplicate retention. Prefer Standard for short-lived artifacts; colder classes only when retention exceeds minimum-duration charges and restore needs permit it. Do not enable indefinite versioning or retention locks by default.
7. Persistent-disk checkpoints protect recently edited files; GCS checkpoints protect VM/disk loss with an explicitly measured recovery-point window. Exporting a live browser profile or copying a live SQLite file is not a consistent backup. Quiesce/checkpoint services or use their supported snapshot APIs.

### Memory as a Product Feature

Implement `orchestrator/memory.py` with store/remember, get, list, recall/retrieve, update/supersede, and forget operations. Preserve useful Strands tool semantics, but remove Bedrock coupling and interactive CLI confirmation. Expose these through `activity_as_tool`; approvals use workflow interrupts where necessary.

Memory layers:

| Layer | Contains | Recovery |
|---|---|---|
| Working context | Current task, recent observations, next steps, unresolved operations | Bounded Temporal state and task checkpoints |
| Episodic | Completed task summaries, outcomes, evidence/project references | GCS records |
| Semantic | Confirmed preferences, project facts, constraints, corrections | Versioned GCS records plus search index |
| Procedural | Verified reusable workflows/skills with prerequisites and versions | Reviewed records/skill artifacts; never silently executable |

Record schema: server-derived owner/project scope; memory ID/revision; type; content; source task/artifact references; creation/update times; confidence/verification status; expiration if applicable; superseded/tombstone state; embedding generation. Keep secrets, clipboard contents, authentication screens, and raw reasoning traces out of automatic memory.

1. GCS is the canonical record store, not a semantic search engine. Use the existing planned versioned embedding configuration and a local rebuildable LanceDB index on persistent disk. Validate the configured embedding endpoint/dimension/encoding before use; do not install a large local embedding model on this VM by default.
2. Durable record write precedes index update. Use idempotent revision IDs and a repair queue. On index failure, report degraded recall and fall back to a bounded metadata/keyword catalog; never silently discard a successful explicit memory write.
3. Query by owner/project before ranking, then freshness/verification and semantic relevance. Inject a small token-budgeted selection with provenance. Memory is contextual evidence, not authority over current user instructions.
4. Automatically produce small task summaries and candidate facts after completion. Store explicit user preferences/corrections directly; mark inferred memories as candidates until supported. Merge duplicates and supersede contradictions without erasing provenance.
5. Provide inspect/search/edit/pin/forget/export controls. Forget removes records from recall immediately via tombstone and index deletion, then purges revisions/artifacts according to policy. Disclose residual copies in soft deletion, backups, Temporal history, or upstream provider retention; do not promise immediate physical erasure everywhere.
6. Project resume restores a verified checkpoint/catalog, recalls scoped memory, reconstructs pending task state, and captures a fresh desktop. Do not store expiring URLs, screenshot coordinates, or browser window IDs as enduring facts.
7. Optional memory outages do not crash ordinary chat. Explicit save failures must remain visible and retryable. Snapshot/index rebuild tests must prove recovery on a replacement VM.

## 10. Security and Failure Policies

| Scenario | Required behavior |
|---|---|
| Worker dies after a click | Inspect journal; never automatically replay unknown mutation; capture/reconcile |
| Desktop restarts | Increment desktop epoch, invalidate lease/frame, reopen workspace, take fresh screenshot |
| Browser closes/reconnects | Durable task continues; resubscribe by cursor, obtain current state and new viewer ticket |
| Human takes control mid-command | Fence mutations; cancel/pause active mutating work before acknowledging takeover |
| GCS unavailable | Preserve bounded local journal/staging; pause actions requiring durable observations when safe recovery cannot be guaranteed |
| Disk nearly full | Stop accepting large jobs/uploads; clean only evictable data; show actionable degraded state |
| Provider stream disconnects | Resume stored background response by ID/sequence where supported; do not create duplicate response blindly |
| Screenshot expires | Report missing observation and recapture; no stale-frame substitution |
| Malicious page/repository | Treat contents as untrusted; enforce network/filesystem boundaries independently of prompts |
| Approval pending | Bind approval to operation/arguments/epoch and expiration; changes require new approval |
| Authentication revoked | Reject new API/WS requests and revoke active control sessions/tickets |

Routine reversible workspace edits may proceed within the requested task. Credentials, external publication, purchases, irreversible deletion, privilege changes, and cloud resource changes require an explicit policy/approval boundary. Generic keyword blocklists cannot secure arbitrary shell or GUI actions. Internet-enabled agents retain prompt-injection/exfiltration risk for any data intentionally placed in their workspace; do not claim perfect detection or isolation.

## 11. Ordered Implementation Work

1. **Compatibility and fixtures.** Capture current tests/contracts and replay histories. Verify pinned Temporal/Strands, Agent API schemas, vision/function-call path, noVNC permission enforcement, OpenCode provider extension API, and memory-source provenance/license. Add failing tests for unsupported boundaries before implementation. Do not deploy until these spikes pass.
2. **Cloud desktop foundation.** Add the pinned desktop image/service, persistent workspace layout, workspace service, scoped identity broker, egress isolation, systemd/container lifecycle, and health checks. Extend reviewed GCE deployment without changing protected environment files or replacing existing work.
3. **Storage and operation journal.** Implement immutable artifacts, signed/authorized descriptors, operation deduplication/fencing, consistent checkpoints, downloads/uploads, quotas, and cleanup safeguards.
4. **Desktop action tools.** Implement and test complete input/clipboard/screenshot coverage and the control lease before model-driven automation. Validate Unicode, drag cleanup, horizontal scrolling, geometry changes, and unknown outcomes.
5. **Temporal/Strands integration.** Add desktop mode/scoped tools to the existing workflow, model-activity artifact hydration, approval/step limits, heartbeats/cancellation, bounded streaming, and recovery/CAN behavior. Validate generated tool specs and full outbound tools array before worker readiness.
6. **Desktop and project UI.** Compose noVNC WebPreview variant, server-enforced handoff, operation timeline, authoritative file browser, incremental terminal, export/upload, and isolated remote project previews. Add new authenticated routes where necessary; preserve existing protected route contracts.
7. **OpenCode coding workflow.** Add pinned installation, managed config, Agent API adapter/gateway, durable coding-job lifecycle, project-aware streams, permissions, and final result/artifact mapping.
8. **Memory.** Implement GCS records, versioned LanceDB index, memory tools, completion summaries, bounded retrieval injection, inspect/edit/forget/export UI, and index repair/rebuild.
9. **Recovery and release.** Run security/E2E/replay/load/restore gates, generate an approved release bundle, deploy to staging, and only then authorize changes to the supplied instance. Enable screenshots/memory cleanup after recovery and deletion behavior are verified.

Likely file boundaries (new names are implementation targets, not claims that files exist):

| Area | Files |
|---|---|
| Desktop runtime | New `desktop/Dockerfile`, service configuration, workspace API/input/session/journal modules |
| Activity adapters | New `orchestrator/desktop_activity.py`, `workspace_activity.py`, `artifact_store.py`, `coding_activity.py`, `memory.py` |
| Durable integration | `orchestrator/workflow.py`, `run_worker.py`, `config.py`, `perplexity_model.py`, `server.py`, `requirements.txt` |
| Scope enforcement | `orchestrator/load_tool.py`, `subagent_support.py`, MCP/tool-resolution boundaries as required by tests |
| UI composition | New `components/v0/desktop-preview.tsx` and state helpers; existing `agent-chat.tsx`, `project-ide.ts`, `project-ide-panel.tsx`, `agent-activity.tsx`; native AI Elements remain library code |
| New transport | New desktop/workspace/artifact/memory routes; auth and gateway configuration; no public arbitrary-proxy route |
| Deployment | Existing `scripts/gce-startup.sh` after review; new pinned desktop/model-gateway release assets and deployment tests |
| Tests | New focused orchestrator, workspace-service, UI, auth, provider, recovery, and cloud E2E suites |

Do not create/edit `orchestrator/graph_tool.py`, perform the deferred product-directory rename, read/edit protected `.env*`, or change the six protected Next routes without their required compatibility proof. Runtime credentials/config are provisioned from deployment inputs/Secret Manager, not committed. If Jira work items are created later, record this feature's decisions before decomposing it and follow GWEN's hierarchy and mandatory fields; no Jira mutations are part of this planning step.

## 12. Validation and Rollout Gates

Run baseline gates before changes and compare against actual results rather than assuming failures described in old documentation still apply:

```bash
pnpm exec next typegen
npx tsc --noEmit
pnpm lint
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
```

From `orchestrator/`:

```bash
.venv/bin/python -m pytest tests -q
```

Additional mandatory evidence:

1. Unit/contract tests cover tool schemas, ownership, path traversal/symlink/archive attacks, artifact hydration provenance, stale frames, coordinate transforms, clipboard limits, input cleanup, idempotency, and terminal cursor ordering.
2. Temporal tests demonstrate worker death between dispatch/result, model-stream reconnect, cancellation, approval expiry, human takeover, replay, and continue-as-new without duplicate mutations or image payload growth.
3. noVNC tests attempt forged input from a view-only connection and from a revoked controlling socket. Both must fail server-side. Verify no public VNC/CDP/terminal/Temporal ports and no metadata access from browser, shell, or OpenCode.
4. Desktop E2E demonstrates left/right/double click, drag a file, scroll all four directions, type Unicode/multiline text, select/copy/cut/paste, terminal shortcuts, and recovery after display resize. Verify visible state rather than only RPC success.
5. Project E2E demonstrates upload, edit, streamed test execution, isolated preview including HMR, export/download with checksum, and restore with uncommitted files intact.
6. OpenCode E2E proves actual Agent API inference and function roundtrips, background streaming, job reconnect/cancel, same-workspace changes, and no automatic sharing or secret leakage.
7. Memory tests prove save/recall across worker and VM restart, owner/project filtering, corrections/supersession, bounded injection, degraded indexing, rebuild, forget, and export.
8. Storage tests prove failed uploads do not publish checkpoints, active screenshots are not collected, source files are not evicted, archive extraction is safe, and backups restore to a clean replacement instance.
9. Measure desktop latency, screenshot/model bytes, stdout lag, peak RAM/CPU, disk growth, object counts, egress, and Temporal history growth on the proposed machine. Gate concurrency/capacity on measured results; do not present unmeasured performance as guaranteed.
10. Readiness verifies real Temporal workflow/activity pollers, desktop display/input/capture, workspace service, authorized storage access, and required model capabilities. Liveness alone must not advertise a usable agent.

Rollout: disabled-by-default desktop feature -> local/container fixtures -> authenticated staging -> approved single-owner pilot. Preserve old browser-only behavior. Use versioned workflow changes, backward-readable durable record schemas, consistent data backups, and approved release URL/SHA256 updates. Rollback disables new starts and restores compatible code; it must not replay unknown side effects or delete workspace/memory state.

The persistent Temporal development server is acceptable only for an explicitly labeled pilot. Production availability requires a separate approved Temporal Cloud or supported self-hosted deployment and backup/restore design. GCS snapshots cannot turn the development server into a production cluster.

## 13. Definition of Done

- An authenticated owner can see and take control of the same persistent cloud desktop the agent uses.
- All requested mouse, scroll, keyboard, clipboard, file, and terminal operations work in the GCE workspace, not on the developer machine or orchestrator host.
- Computer use runs through TemporalAgent and activity_as_tool, streams asynchronously, observes real screenshots, and recovers conservatively from interrupted side effects.
- OpenCode uses the verified Agent API provider and edits the same projects, with observable/reconnectable jobs and downloadable results.
- Files, screenshots, project checkpoints, and curated memory have explicit ownership, retention, export, deletion, and tested recovery behavior.
- Safety/auth/isolation, protected-route compatibility, workflow replay, desktop E2E, model contracts, and clean-VM restore gates pass with evidence.
- Pilot limitations, deployment inputs, capacity measurements, and any deferred production work are documented honestly; nothing is marked implemented or verified merely because this plan specifies it.

## 14. Supplied Credentials and Existing Bucket

This section refines the earlier design using the user's new key and bucket. It takes precedence over earlier suggestions that bucket names were unknown or that new regional buckets were required. The leading `v` in the supplied path was treated as a typing error because the environment's active file and Git status identify the matching file at the repository root.

### 14.1 Credential Lifecycle

1. **Containment before implementation packaging.** Add an exact root `.gitignore` exclusion for the supplied JSON and equivalent `.dockerignore`, release-archive, project-export, and indexing exclusions. Verify that no tracked or staged copy exists; separately check history before concluding it was never committed. Ignore rules alone do not exclude a file from raw tar archives or Docker build contexts. Do not use a blanket `*.json` exclusion.
2. **Bootstrap use.** Load credentials in the trusted deployment/preflight process by explicit file path. Do not run `gcloud auth activate-service-account` merely for discovery, mutate shared CLI credentials, persist access tokens, or print the credential JSON. Restrict local file permissions and move the key outside repository/build roots during approved setup; do not move or delete the user's file during planning.
3. **Preferred VM runtime.** Attach an approved service account and use metadata-based ADC only from trusted control-plane processes. Assess whether the supplied default service account is appropriately scoped or whether a narrower runtime identity is needed. Attaching/changing an identity or scopes can require a VM stop/start; schedule explicitly and preserve workspace state.
4. **Key fallback.** If attachment is deferred, provision the provided key only to a protected host credential path, mounted/readable solely by the storage/model-control service that needs it. Use explicit service configuration, not a project-local `.env` edit. Plan rotation and access audit. Never place the key in image layers, the GCS bucket, desktop home, project tree, OpenCode config, or Temporal inputs/history.
5. **Isolation is enforced outside agent control.** Ordinary browser/shell/OpenCode processes cannot access either the key path or GCE metadata. They receive workspace-scoped broker capabilities, never cloud access tokens. An untrusted container must not be able to remove its own metadata egress restrictions.
6. **No assumption of universal permissions.** The successful bucket test does not establish Secret Manager access, instance mutation, service-account attachment (`iam.serviceAccounts.actAs`), IAM policy changes, or keyless signing (`iam.serviceAccounts.signBlob`). Preflight each required permission and API separately; do not grant Owner/Editor automatically.
7. **Prefer authenticated file streaming.** The initial download path is the authorized artifact proxy, which does not require signing keys or public objects. Add signed URLs only if justified by size/traffic, with explicit signing permission and expiration tests. Never infer that ADC automatically supports signing.
8. **Exposure response.** If a key is found in Git history, an image, a release, logs, or an externally shared archive, treat it as exposed and coordinate revocation/replacement. Removing the file or adding an ignore rule does not invalidate an exposed key. No rotation/revocation is performed as a side effect of planning.

### 14.2 Concrete Bucket Contract

Initial non-secret deployment settings:

```text
GOOGLE_CLOUD_PROJECT=fair-expanse-493212-h8
GWEN_GCS_BUCKET=thisforagent
GWEN_GCS_PREFIX=gwen/v1
```

These are proposed configuration names, not existing settings or an instruction to edit protected environment files. Production code should use ADC and explicit project/bucket configuration, without hardcoding the development key path.

| Prefix below `gwen/v1/` | Purpose | Writers and cleanup |
|---|---|---|
| `releases/` | Approved app/tools release bundles and integrity manifests | Deployment publisher only by policy; runtime reads. Not agent-writable. |
| `active/` | Images and evidence still referenced by running tasks | Trusted artifact service; no unconditional age-based lifecycle deletion |
| `artifacts/` | Finalized screenshots, deliverables, exports, segmented logs | Artifact service; explicit category and expiry; user-pinned items exempt |
| `projects/` | Immutable project checkpoints, manifests and source blobs | Checkpoint service; protect live references before garbage collection |
| `memory/` | Durable records, revisions, tombstones, catalog/index manifests | Memory service; no generic temporary-artifact retention rule |
| `recovery/` | Consistent journal/database backups and restore manifests | Trusted backup service; separate retention and access checks |
| `staging/` | Incomplete uploads and pending checkpoint construction | Bounded janitor after active-upload checks |

Use server-derived owner/workspace/project IDs beneath these prefixes. Reject arbitrary model-supplied bucket names or GCS object paths. A server-side artifact ID resolves to an allowed prefix, generation, expected size, MIME and checksum. Neither object naming nor a memory prompt constitutes authorization.

Bucket metadata has no returned lifecycle, versioning, or retention-policy configuration. Record these as absent in the inspected response, not a promise they cannot change. Read current metadata again before proposing lifecycle changes. The bucket's uniform-access lock time is `2026-12-07T04:35:58.216Z`; do not disable uniform access as part of this design.

The current credential reports bucket-update and all tested object permissions, so it could alter/delete more than the application should. Logical service separation on the same broadly credentialed host is not an IAM boundary. Before security-sensitive deployment, define separately scoped identities/IAM conditions or stronger resource separation. Do not claim a `releases/` prefix is protected from runtime modification merely because application code avoids it. If the pilot retains one broad identity, disclose that residual risk and keep approved release hashes anchored outside the agent-writable data path.

### 14.3 Retention, Memory, and Recovery Refinements

- Keep seven-day soft deletion as observed until an explicit cost/deletion decision changes it. Deleting a screenshot or memory revision does not immediately remove its recoverable storage copy or charges. Memory UI should distinguish removed-from-recall, purge-requested, and policy-retained copies.
- Multi-region placement does not replace application-consistent backups, checkpoint validation, or memory revision control. Measure transfer costs and performance between `us-central1-a` and this bucket; do not assume all transfers are free or that the bucket can be changed to regional in place.
- Commit a memory update as an immutable revision plus a conditional head/catalog update. Use `ifGenerationMatch=0` for new immutable objects and generation-matched updates for mutable pointers. Concurrent corrections must conflict or explicitly merge, not silently overwrite.
- Preserve explicit forget tombstones and catalog revision/watermark during index rebuild and restore. Before exposing a restored index, apply all newer deletions/supersessions. A stale backup must not resurrect forgotten memories into recall.
- GCS does not provide a multi-object transaction for a project snapshot. Upload all referenced data first, verify hashes/generations, then publish the manifest conditionally. Interrupted staging is recoverable garbage, not a valid checkpoint.
- Reference counts and pins must be reconciled before deleting shared content-addressed blobs. Do not equate an old object creation time with an unused object. Begin with simple immutable checkpoints and conservative cleanup; implement incremental cross-checkpoint deduplication only with deletion/recovery tests.
- Restore must verify manifests and archive paths and target an empty/new workspace, not overwrite a live project by default. Restore source, memory catalog and journal separately; report the last acknowledged checkpoint time and any newer work that could be missing.
- Add a context manifest per completed task: summary, source project/checkpoint, verified findings, unresolved operations, memory revision IDs and artifact references. Keep it small enough for fast retrieval, with detail fetched on demand. Never archive credentials or raw clipboard contents to improve recall.

### 14.4 Preflight and Acceptance Evidence

Already verified read-only on 2026-09-08:

- Supplied credential can authenticate and enumerate the project's bucket metadata.
- `thisforagent` is the only bucket returned by that project listing; the response contained no next-page token.
- Bucket permissions test returned all eight requested permissions: `storage.buckets.get`, `storage.buckets.getIamPolicy`, `storage.buckets.update`, `storage.objects.list`, `storage.objects.get`, `storage.objects.create`, `storage.objects.update`, and `storage.objects.delete`.
- Limited live VM metadata reports a running `strands` instance with no attached service account in the response.

Still required before deployment:

1. Credential exclusion/history/build-context checks and approved runtime identity setup.
2. Object roundtrip under a dedicated test prefix: upload immutable bytes, verify hash/generation, reject a conflicting conditional write, download through the authorized proxy, and delete only the fixture. This is a cloud write test and was not executed during planning; account for soft deletion.
3. Exact Secret Manager resource access checks without printing secret values, plus required API and deployment/IAM checks.
4. Negative tests proving desktop/OpenCode cannot obtain the key, metadata tokens, arbitrary bucket objects, or release-write capabilities.
5. Lifecycle dry-run/report before enabling cleanup, including active-task references, memory tombstones, source checkpoints, retained objects, and estimated recoverable-deletion storage.
6. Replacement-workspace restore from this bucket and memory index rebuild without stale/deleted memory resurrection.

Only the implementation plan was amended for this planning extension. Credential contents, Git exclusions, cloud IAM, bucket objects/policies, VM identity, and running services remain unchanged.
