# GCE desktop agent: native-framework infrastructure specification

Date: 2026-09-08. Status: implementation-ready specification with explicit compatibility and deployment gates. No infrastructure provisioning is authorized by this document.

## 1. Scope, decisions, and authority

This specification reviews and refines `.kilo/plans/1788832097810-gce-desktop-agent-implementation.md`. It supersedes conflicting implementation choices in that plan; retain its detailed desktop-operation and recovery acceptance cases. The user-supplied `fair-expanse-493212-h8-138622c839d2.json` is a service-account credential, **not the plan**. Do not copy its contents into documentation, tool inputs, images, source, releases, or runtime workspaces.

Confirmed in this review:

- **Native extension points are allowed; redundant custom wrappers are not.** Implement domain behavior using documented framework decorators, interfaces, hooks, and component composition. Do not create replacement agent loops, framework facade classes, custom tool envelopes, or protocol translation layers when native contracts suffice.
- **Isolated single-owner pilot:** a new Ubuntu 24.04 `e2-standard-4` VM, separate persistent data disk, independent Temporal development-server state, and an isolated prefix in `gs://thisforagent`. Leave the existing `strands` VM untouched.
- One authenticated owner, one persistent desktop, multiple projects, one exclusive agent-or-human controller. This is not multi-tenant isolation or production high availability.
- Keep the official Temporal/Strands integration and the existing chat transport. Use the same cloud desktop filesystem for agent tools, browser, terminal, and OpenCode. Provider-native sandboxes remain separate and explicitly labeled.

Authority order: repository safety rules and approved scope; installed/pinned framework contracts; official version-appropriate documentation; then existing application code. Do not copy newer documentation APIs into older installed packages. A documented interface is a legitimate extension point, not evidence that the proposed implementation already exists.

Out of scope: modifying the existing VM, production Temporal cluster/HA, multiple owners/desktops, GPUs, Windows, Kubernetes, arbitrary privileged installation, standalone browser PTY widget, graph-tool redesign, product-directory renaming, and automatic dependency upgrades.

## 2. Review findings that change the original plan

| Finding | Required correction |
|---|---|
| `WebPreviewBody` always renders an iframe (`components/ai-elements/web-preview.tsx:178–198`) | Mount native noVNC `RFB` into a DOM element inside `WebPreview`; use its navigation subcomponents directly. Do not patch the vendored body or wrap it in another preview abstraction. |
| AI Elements `Terminal` is output-only (`terminal.tsx:190–255`) | Feed real streamed output into `output`; use the desktop's terminal application for human interaction. Do not present a transcript as a PTY. |
| Existing handoff is client-side (`computer-use-preview.tsx:128–146,380`) | Reuse UX only. Establish server-authoritative controller state and revoke existing input connections before acknowledging agent resume. |
| `activity_as_tool` serializes results as text containing JSON | Return ordinary serializable domain results. Retain native `TemporalActivityTool` wrapping; do not create an image-aware tool wrapper. |
| Raw image bytes are unsuitable for the current Temporal converter/history boundary | Carry immutable observation descriptors. Fetch and hydrate supported image content inside the existing Strands `Model.stream` implementation running in a model activity. This is the selected extension point, not a claim that no alternative can exist. |
| OpenCode and Gwen use different AI SDK generations | OpenCode 1.18.27 consumes `LanguageModelV3`; Gwen uses `ai@7`. Build the provider as an independently pinned package, not against the application's implicit ABI. |
| OpenCode loads native provider packages | Implement the Agent API at that provider extension point; use a path-preserving, credential-injecting gateway. Do not emulate OpenAI Responses or Chat Completions as an extra intermediary protocol. |
| Existing startup script is not fully reproducible | `scripts/gce-startup.sh:43–60` resolves moving Node/uv/Temporal releases; `:230–262` installs/tests/builds on the VM and exposes the runtime environment during build. Replace those deployment behaviors with reviewed pinned build artifacts before desktop rollout. |
| Current timeout configuration uses `None` plus one-day fallback shims | Specify real native activity options for desktop paths, with explicit cancellation and retry semantics; do not inherit unbounded/default retries for GUI mutations. Avoid unrelated global behavior changes. |
| Repository inventory is stale | Backend inspection reports `think` wiring despite AGENTS.md's prohibition, plus native hooks unavailable in the installed release. Verify these concrete conflicts in the implementation baseline and remove forbidden desktop exposure; do not assume prose inventory is current. |
| The original plan lacks replay fixtures and tested native VNC revocation | Make both release gates. Do not claim crash-safe clicks, secure view-only access, or safe rollback before the tests pass. |

Read-only inspection and documentation research informed this spec. No end-to-end deployment, authenticated cloud preflight, resource creation, full test suite, or restore was performed in this review. Prior plan cloud observations are dated evidence, not current verification. Git cleanliness/tracking was not independently established here.

## 3. Dependency and native API inventory

### 3.1 Version baseline

| Boundary | Inspected version / disposition |
|---|---|
| Python | 3.13; installed interpreter reported 3.13.13 |
| Temporal Python | `temporalio[strands-agents,pydantic]==1.31.0` |
| Strands | `strands-agents[gemini]==1.50.2`; tools `0.8.5` |
| Provider SDK | `perplexityai==0.42.0`; `google-genai==2.19.0` |
| Validation/MCP | Pydantic `2.13.4`; MCP `1.29.0` |
| Gwen UI | Next `16.2.12`, React `19.2.8`, `ai@7.0.41`, `@ai-sdk/react@4.0.44` |
| UI internals | Base UI `1.6.0`, Streamdown `2.5.0`, `ansi-to-react@6.2.6`; AI Elements is vendored source, not a versioned npm dependency |
| OpenCode target | `1.18.27`; inspected upstream tag commit `4b7e19e315cca414121ba1d61523fef74bb3ae8b` |
| OpenCode provider ABI | `ai@6.0.168`, `@ai-sdk/provider@3.0.8`, `@ai-sdk/provider-utils@4.0.23` |
| noVNC candidate | `@novnc/novnc@1.7.0`, not installed; ESM root export maps to `core/rfb.js` |
| noVNC typings | `@types/novnc__novnc@1.6.0` describes old import paths; must reconcile type-only declarations against the selected release |
| GCS/LanceDB/desktop binaries | Freeze exact resolved versions and checksums in the release manifest during compatibility work. Do not invent pins or import unverified latest APIs. |

No dependency bump is implied by this inventory. Record lockfiles, source/license provenance, platform, native dependencies, and image digests in every approved release.

### 3.2 Backend classes, functions, and types to use directly

Installed source references below are relative to `orchestrator/.venv/lib/python3.13/site-packages/`.

| Native API | Contract and feature use |
|---|---|
| `temporalio.contrib.strands.StrandsPlugin` | `models: dict[str, Callable[[], Model]]`, optional `mcp_clients`, `mcp_connection_idle_timeout`. Install on `Client`, as the repository requires. Factories live in worker setup; no live model/client in workflow constructors. `_plugin.py:44–123`. |
| `TemporalAgent` | Constructor accepts named `model`, `task_queue`, native activity timeout/retry/cancellation options, `streaming_topic`, `streaming_batch_interval`, and Strands agent kwargs. Do not supply `retry_strategy`; integration disables it. `_temporal_agent.py:35–103`. |
| `Agent.invoke_async` inherited by `TemporalAgent` | `prompt`, keyword `invocation_state`, structured-output arguments, `idempotency_token`, `limits`. Installed `Limits` supports `turns`, `output_tokens`, `total_tokens`. A turn limit is **not** a desktop-mutation counter; enforce the latter in domain policy. `strands/agent/agent.py:786–796`, `types/agent.py:17–49`. |
| `SequentialToolExecutor` | Pass through `tool_executor` for desktop mode; default concurrent execution is inappropriate for frame-dependent GUI actions. Serializes tools without a custom executor. |
| `@activity.defn` + `activity_as_tool` | Typed activity function/docstring defines native tool metadata; native function accepts activity timeouts, `RetryPolicy`, cancellation type, task queue and activity ID. Returns `AgentTool`; no application wrapper. `temporalio/contrib/strands/workflow.py:22–55`. |
| `ToolSpec`, `ToolUse`, `ToolResult` / `ToolResultContent` | Use framework tool schemas and content contracts. `TemporalActivityTool` produces `status="success", content=[{"text": json.dumps(result)}]` for returned results, even though Strands more generally supports images. Represent domain outcome explicitly inside the result; raise typed failures only where appropriate. `_temporal_activity_tool.py:28–95`. |
| `BeforeToolCallEvent`, `BeforeModelCallEvent`, `AfterToolCallEvent` | Native per-call policy hooks. `cancel_tool`, `cancel`, and result handling are available; keep workflow hooks deterministic. Do not use missing `BeforeToolsEvent`, `AfterToolsEvent`, or `InterruptEvent` APIs from newer docs. |
| Native interrupts | `BeforeToolCallEvent.interrupt(...)`, `Interrupt`, `InterruptResponseContent` resume blocks; retain existing approval bridge. Bind confirmation to operation, argument hash, owner, and epoch. No parallel approval subsystem. |
| `activity_as_hook` | Native hook bridge for activity side effects; callback does not feed a return value back into the event. Do not use it where event mutation requires a returned payload. |
| `Agent.cancel()` | Stop native agent execution; explicitly handle `stop_reason="cancelled"`. It does not by itself stop a remote process or revoke VNC input. Retire redundant `strands_tools.stop` usage in the desktop branch. |
| `WorkflowStream`, `WorkflowStreamState` | Create in workflow initialization; native `topic`, `get_state`, `truncate`, `detach_pollers`, `continue_as_new`. Preserve existing topics. No new event-bus facade. `contrib/workflow_streams/_stream.py:127–364`. |
| `WorkflowStreamClient` | `create` / `from_within_activity`, `publish(..., force_flush=False)`, `subscribe(..., from_offset=...)`. Handle truncated cursors with a state snapshot, not invented events. Batching retry duration must remain below publisher TTL. |
| `RetryPolicy`, `ActivityCancellationType` | Desktop mutation attempts initially `1`; use `WAIT_CANCELLATION_COMPLETED` when cleanup acknowledgment is required, with finite timeouts and remote-job reconciliation. Observation reads may retry within bounded budgets. |
| `activity.heartbeat`, `activity.info` | Carry stable operation/job IDs and cursors; `attempt` is diagnostic, not the business operation ID. Native model activity heartbeating requires a configured heartbeat timeout. |
| `workflow.patched`, `Replayer`, `WorkflowHistory` | Version desktop behavior and replay histories with the matching plugin/converter. No custom workflow-version router. |
| Existing `PerplexityModel(Model)` | Extend the existing official Strands model interface. Resolve trusted observation descriptors in the activity-side stream/request preparation. Do not create `DesktopModel`, `DurableAgent`, or another model facade. |
| FastAPI/Pydantic | Native route handlers, dependencies, request models, `HTTPException`, `StreamingResponse`, and lifespan-managed clients. Domain schemas are allowed; generic repository/service-manager wrappers are not. |
| Google Cloud Storage client | Direct `Client`/`Bucket`/`Blob` use in trusted activity/service code; immutable generations and conditional writes. Bound synchronous SDK work outside the event loop. No `CloudStorageManager` abstraction. |
| LanceDB | Use the pinned SDK's native connection/table/query APIs directly. Confirm async signatures against the resolved version. Owner/project/tombstone predicates precede ranking. No custom vector-store facade. |

Keep curated long-term memory separate from Temporal conversation state. Do not attach a disk/GCS-writing Strands session manager to a workflow agent or assume newer snapshot APIs are supported by `TemporalAgent`.

### 3.3 Native frontend types, props, and CSS classes

Use exports from `components/ai-elements/` directly inside feature composition in `components/v0/`. Do not create `GwenTerminal`, `DesktopWebPreview`, `AgentCard`, or replacement scrolling/Markdown primitives merely to rename native APIs. Feature-specific React state and DOM lifecycle effects remain necessary application logic.

| Export / type | Relevant props and correct use |
|---|---|
| `WebPreviewProps` / `WebPreview` | `ComponentProps<"div"> & { defaultUrl?: string; onUrlChange?: (url: string) => void }`. Desktop content has no web URL; omit `WebPreviewUrl`. Root supplies flex/border/card styling. |
| `WebPreviewNavigation`, `WebPreviewNavigationButton` | Native navigation row; button accepts native `Button` props plus `tooltip`. Add take/resume control, reconnect, fullscreen, and explicit clipboard controls. |
| `WebPreviewBody` | `ComponentProps<"iframe"> & { loading?: ReactNode }`. Use only for project web previews, not direct `RFB` mounting. Set exact isolated origin and sandbox permissions deliberately. |
| `WebPreviewConsole` | `logs?: { level; message; timestamp: Date }[]`; bounded connection diagnostics, not a replacement stream log. Convert serialized timestamps to `Date` at the UI boundary. |
| `Agent`, `AgentHeader`, `AgentContent` | Header requires `name: string`, optional `model: string`. Use for coding-job status and final output composition. |
| `AgentInstructions`, `AgentTool`, `AgentOutput` | Instructions require string children; tool expects AI SDK `Tool`; output expects schema string. Final Markdown belongs in `MessageResponse` inside `AgentContent`, not `AgentOutput`. |
| `Task`, `TaskTrigger`, `TaskContent`, `TaskItem`, `TaskItemFile` | Native Collapsible props including `open/onOpenChange`; trigger `title: string`. Children replace the trigger's default row. |
| `ChainOfThought`, `ChainOfThoughtHeader`, `ChainOfThoughtContent` | `open`, `defaultOpen`, `onOpenChange`. Display published reasoning summaries/tool progress only, never fabricate hidden reasoning. |
| `ChainOfThoughtStep` | `icon?: LucideIcon`, `label: ReactNode`, `description?: ReactNode`, `status?: "complete" | "active" | "pending"`. Domain errors/cancellation need explicit labels; do not pass unsupported status strings. |
| `ChainOfThoughtImage` | Optional string caption; native container has `max-h-[22rem]`. Evidence only, not the agent coordinate plane. |
| `Terminal` / `TerminalProps` | `HTMLAttributes<HTMLDivElement> & { output: string; isStreaming?: boolean; autoScroll?: boolean; onClear?: () => void }`. Children replace its default content. |
| `TerminalHeader/Title/Status/Actions/Content/CopyButton/ClearButton` | Preserve native composition and auto-scroll. `TerminalContent` uses `<pre><Ansi>`. Sanitize unsafe control sequences before display; no stdin semantics. |
| `FileTree` | Controlled `expanded?: Set<string>`, `selectedPath`, `onSelect(path)`, `onExpandedChange(Set)`; `defaultExpanded` optional. Authoritative workspace API supplies data. |
| `FileTreeFolder/File/Actions/Icon/Name` | Folder/file require `path` and `name`; file accepts `icon`. Actions preserve native propagation behavior. |
| `MessageResponse` | `ComponentProps<typeof Streamdown>`; use existing plugins and `isAnimating` contract. Existing memo comparison focuses on children/isAnimating; test dynamic prop changes before relying on them. |
| `Artifact*` | Existing panel shell: `Artifact`, header/title/description/actions/action/content/close. Reuse rather than inventing a new panel system. |
| `UIMessage`, `DynamicToolUIPart` | Keep AI SDK UI-message transport and native dynamic tool states. Decode the existing Temporal JSON-text tool result at the established application boundary. No replacement UI-message protocol. |

Evidence locations: `web-preview.tsx:27–211`, `agent.tsx:20–126`, `task.tsx:14–71`, `chain-of-thought.tsx:41–205`, `terminal.tsx:33–255`, `file-tree.tsx:49–296`, `message.tsx:37–346`, and `artifact.tsx:17–143`.

Styling: reuse `ide-glass`, `ide-glass-inset`, `ide-glass-edge`, `ide-code-surface` in `app/globals.css:277–326`; use native `className` and existing `cn`. Layout utilities include `min-h-0`, `flex-1`, `overflow-hidden`, and `@container/ide`. Preserve blue/purple glass palette. Base UI composition uses `render`, not Radix `asChild`. Do not replace native component animations or scroll behavior.

### 3.4 Native noVNC integration

Use client-only `import RFB from "@novnc/novnc"` for the selected ESM package, then `new RFB(target, urlOrChannel, options)` in the feature's React effect. Remove listeners and call `disconnect()` during cleanup. No RFB subclass, React wrapper library, or custom RFB protocol parser.

- Constructor options: `shared`, `credentials`, `repeaterID`, `wsProtocols`; never put credentials in URLs or logs.
- Properties: `viewOnly`, `scaleViewport`, `clipViewport`, `dragViewport`, `resizeSession`, `focusOnClick`, `qualityLevel`, `compressionLevel`.
- Events: `connect`, `disconnect` (`detail.clean`), `credentialsrequired`, `securityfailure`, `clipboard`, `desktopname`, `capabilities`; validate additional events against the chosen release/types.
- Methods: `disconnect`, `sendCredentials`, `focus/blur`, `sendKey`, `sendCtrlAltDel`, `clipboardPasteFrom`, `getImageData`, `toBlob`, `toDataURL`.
- Start with `scaleViewport=true`, `resizeSession=false`; viewport fitting does not change physical desktop coordinates. Clipboard transfer is explicit, never automatic synchronization with the human's OS.
- `viewOnly` is client UX only. Server permissions and connection revocation remain mandatory.
- Prefer maintained compatible typings; otherwise add a minimal accurate **type-only** declaration for the root module, tested against actual exports. Do not use `any` or misrepresent older typings as complete coverage.

## 4. Infrastructure resource specification

All resource names below are proposed except the project, zone, bucket, and existing VM recorded by the prior plan. Check availability and policy before provisioning. Deployment input validation must fail closed rather than guess identities, domains, or secrets.

| Resource | Pilot target |
|---|---|
| Project / location | `fair-expanse-493212-h8`, `us-central1-a` |
| New VM | Proposed `gwen-desktop-staging`; Ubuntu 24.04 x86_64 pinned image; `e2-standard-4`, standard provisioning, automatic restart, deletion protection |
| Boot disk | Proposed 50 GiB `pd-balanced`, OS and immutable release/image cache; never workspace source |
| Data disk | Proposed 100 GiB `pd-balanced`, ext4, auto-delete false, mounted by UUID at `/var/lib/gwen`; approval required for size/cost before creation |
| Network | Dedicated staging subnet/rules in an approved VPC; no permissive inheritance from existing VM tags. IPv4-only pilot unless equivalent IPv6 rules are tested |
| Public ingress | Reserved static address to Nginx HTTPS gateway; TCP 443 only for application/preview. Certificate issuance via approved DNS challenge avoids general HTTP ingress |
| Administration | IAP TCP forwarding + OS Login; TCP 22 restricted to IAP source range and approved admin IAM; no public SSH password login |
| Runtime identity | Dedicated user-managed staging service account attached with `cloud-platform` scope; IAM grants restrict actual privileges. No downloaded JSON key in runtime |
| Storage | Existing private `gs://thisforagent`; new `gwen/staging/v1/` namespace, separate from existing `gwen/v1/` data |
| Temporal | Independent localhost server and database; no existing-state import or connection to existing task queues. Explicitly pilot-only |
| Domains | Required deployment inputs: trusted Gwen hostname and isolated project-preview hostname; host-only cookies. No wildcard session cookie shared with project previews |
| Secrets | Approved Secret Manager version references for provider credential, OIDC client secret, auth cookie secret; separate delivery per service |
| Build | Trusted Linux build environment outside the interactive VM; pinned OCI images and approved app/tools release bundle with SHA256 |

Initial budgets are design defaults, not measured capacity: desktop/coding cgroup maximum 8 GiB RAM, 3 vCPUs, 512 processes; reserve remaining capacity for control services and OS. One active mutating/coding job. Limit persistent logs/caches and uploads separately; load tests may require revising capacity before deployment. No swap-based promise that OOM cannot occur.

### 4.1 Processes, listeners, and filesystem boundaries

Use native systemd units for host services and a reviewed container runtime for the desktop. Do not build a generic service orchestrator. Unit names below are proposed. Each unit runs with a distinct least-privilege OS identity where practical; existing single-user/shared-environment startup configuration is not the target security model.

| Service | Listener / dependency | Persistent state / authority |
|---|---|---|
| Nginx gateway | Public `443`; auth subrequest and explicit WS upgrade | TLS private keys host-protected; no provider key; strict upstream allowlist |
| OAuth2 Proxy | `127.0.0.1:4180` | OIDC/cookie secrets via service credentials; exact owner allowlist, no provider bearer token forwarded to projects |
| Next.js | `127.0.0.1:3000` | Immutable release; no workspace mount, no cloud/provider credential |
| FastAPI control API | `127.0.0.1:8787` | Native ownership/dependency checks; workflow client; control journal access as needed |
| Temporal dev server | `127.0.0.1:7233`; UI `8233` private/admin only | `/var/lib/gwen/temporal/history.db`; separate from existing instance |
| Strands worker | No public listener; polls staging Temporal | Named factories; permitted activity clients only; no unscoped desktop-mode host tools |
| Workspace/control handlers | Proposed loopback `8790` or Unix socket, trusted callers only | `/var/lib/gwen/control/`; operation journal, controller state, manifests; no generic arbitrary-host RPC |
| Provider access gateway | Proposed `8791` on restricted bridge/loopback path | Upstream key held here; accepts revocable workspace capability; fixed upstream/path/method allowlist |
| websockify | Proposed loopback `6080` | Native WS-to-RFB transport; no public direct access; process/connection revocation controlled by trusted host |
| TigerVNC/X11 + XFCE | RFB Unix socket preferred, or private container `5901`; X11 TCP disabled | Protected server/auth configuration; desktop user gets display access, not permission to edit server auth configuration |
| Desktop command service | Private workspace-side listener reachable only by trusted control service | Runs actions/jobs as desktop user; no host/cloud key; immutable executable/config inaccessible to project writes |
| Browser/terminal/OpenCode/dev servers | Desktop container only; approved dev ports registered per job | Same persistent home and `/workspace/projects/<project_id>`; no host network or Docker socket |

Data layout below `/var/lib/gwen/`: `control/` (journal/leases), `temporal/`, `workspaces/<workspace_id>/home/`, `workspaces/<workspace_id>/projects/`, `artifacts/staging/`, `logs/`, `memory/index/`, `recovery/`. Mount only the selected workspace home/projects into the desktop. Never mount the parent directory containing Temporal, journal, credentials, or other workspaces.

Systemd: explicit `RequiresMountsFor`, dependency ordering, bounded restart backoff, start-rate limits, stop timeouts, `NoNewPrivileges`, restrictive umask and filesystem permissions. Missing data disk must prevent startup; never silently create an empty workspace on the boot disk. Restrictive sandbox options must be tested against each service rather than copied blindly onto the browser/container runtime.

### 4.2 Authentication, networking, and IAM

1. Nginx uses OAuth2 Proxy's native `auth_request` integration. Authenticate HTML, APIs, downloads, and WS handshakes. API failures return 401/403, not login HTML. Strip user-supplied identity headers and replace them only from verified auth results. Internal listeners are inaccessible to untrusted container processes.
2. FastAPI dependencies resolve server-side owner/workspace permissions. Never accept model-supplied `owner_id`. Protect mutation requests against CSRF and validate exact WebSocket `Origin`. OIDC allowlisting is not per-operation authorization.
3. Nginx explicitly forwards `Upgrade`/`Connection`; disable SSE response buffering; configure bounded WS lifetimes and heartbeat/idle behavior. Do not depend on Next route handlers for arbitrary WS upgrades. Re-authentication at handshake alone does not revoke an already-open socket.
4. Host-enforced container firewall rules block metadata (`169.254.169.254` and metadata hostname), link-local, control-plane/private ranges and direct-IP bypasses, with narrow exceptions for authorized broker/gateway endpoints and DNS. Cover Docker forwarding **and** host INPUT paths; `DOCKER-USER` alone does not secure every destination. Do not rely on browser URL filtering, UFW alone, or container-controlled rules.
5. Allow internet HTTPS/HTTP and required DNS for browsing/package registries under policy. This permits residual data-exfiltration risk for data intentionally placed in the workspace; do not claim prompt injection is solved. Block access to the VM's own public ingress from the workspace when it could bypass private service isolation.
6. Container: non-root, no host networking/privileged mode/Docker socket, capabilities dropped, no-new-privileges, tested seccomp and resource limits, browser sandbox enabled. Keep desktop server configuration/control credentials outside the writable project/home. X11 clients share a trust domain; this pilot does not isolate malicious programs from each other inside the desktop.
7. Runtime SA: object get/create/delete/update only for the required staging data prefixes, secret access only to named secrets, optional logging/metrics writer as explicitly configured. No Owner/Editor, bucket policy/update permissions, compute mutation, or IAM administration. Runtime has no release-write capability.
8. IAM object-prefix conditions do not restrict ordinary bucket-level `storage.objects.list` into a confidential prefix view. Prefer exact-key catalogs without list privilege; if recovery requires bucket listing, document the scope or provision approved managed-folder/downscoped access using native GCP mechanisms. Negative tests must prove data/release write boundaries.
9. Publisher/deployer identity is separate from runtime. Approved hashes/config remain outside agent-writable storage. GCE attached credentials can be obtained by processes that reach metadata; OS users alone are not an IAM boundary. Enforce metadata denial for desktop and unrelated host services.
10. App and project previews use separate origins. Preview proxy strips Gwen cookies/auth headers and accepts only registered workspace job/port bindings, not arbitrary URLs. Authentication and ticket bootstrap must be tested with iframe cookie restrictions and HMR WebSockets. An inability to secure embedded preview is a release blocker, not grounds to serve arbitrary project JavaScript at the Gwen origin.

### 4.3 Native VNC control and revocation gate

Prefer TigerVNC's native view-only authentication and native connection-management commands over an RFB-aware custom gateway. Official `vncpasswd` supports a separate view-only password; `vncconfig -disconnect` disconnects all viewers; `PasswordFile` is read on incoming connections. `AcceptKeyEvents`, `AcceptPointerEvents`, clipboard and desktop-resize settings are native server parameters, but runtime mutability and protection from desktop clients must be tested for the chosen release.

For this single-owner pilot, accept viewer reconnect during control transitions: fence new actions, stop/settle mutating jobs, disable new control admission, disconnect existing viewers through the native server/transport lifecycle, rotate control credentials, then reconnect with the permitted role. Do not assume password rotation revokes existing sessions. View credentials must never acquire input rights by flipping `RFB.viewOnly`.

Server/control processes and credential files must be protected from the unprivileged desktop user; do not expose a control password or permissive VNC configuration in the workspace. Human VNC input is not journaled per keystroke. It is governed by the controller lease and invalidates prior observations on handback.

**Gate:** prove forged input, stale control credentials, revoked active sockets, clipboard writes, and resize requests cannot mutate the desktop during agent ownership. If native server/transport configuration cannot meet this, desktop control stays disabled; return for an explicit design amendment instead of implementing a custom RFB proxy silently.

## 5. Feature-owned data contracts (not framework replacements)

Use native Pydantic models for validated API/storage boundaries and TypeScript types generated from the service OpenAPI schema where practical. Plain domain records are allowed; do not wrap SDK handles. Version durable schemas and reject unknown major versions. IDs below are opaque server-issued strings; GCS generation numbers cross JSON as strings to avoid JavaScript integer precision loss.

| Domain type | Required fields / invariants |
|---|---|
| `WorkspaceBinding` | `schema_version: 1`, `owner_id`, `workspace_id`, `project_id?`, `session_id`; binding obtained by authenticated control plane, not accepted from model input |
| `ControllerLease` | `workspace_id`, `desktop_epoch`, monotonically increasing `lease_epoch`, `controller: agent|human|none`, `state: active|transitioning|recovery_required`, owning session/controller reference, expiry; transactionally persisted |
| `ObservationRef` | `artifact_id`, `generation: string`, `sha256`, `mime_type`, byte size, physical `width/height`, crop origin/scale if present, capture time, `desktop_epoch`, `operation_id`; includes immutable owner/workspace provenance in trusted catalog |
| `DesktopAction` | Discriminated action payload; frame/artifact reference, epochs, operation ID, bounded coordinates/text/buttons/keys/duration; internal owner binding is separate from model-visible arguments |
| `OperationRecord` | Unique `(workspace_id, operation_id)`, request hash, epochs, actor, action type, state, timestamps, result/error/evidence refs; duplicate ID with different request hash is a conflict |
| `OperationState` | `accepted|running|succeeded|failed|cancelled|outcome_unknown`; terminal uncertainty is never translated into success or silently retried |
| `JobRecord` | Operation/project/cwd binding, process identity plus start identity (not PID alone), status, exit code or signal, stdout/stderr cursor, log artifact, cancellation state; process lifecycle supervised independently from HTTP connection |
| `OutputChunk` | Job ID, monotonic sequence/cursor, stream `stdout|stderr`, bounded text, truncation/gap metadata; reconnect fetches known durable segments |
| `ArtifactRecord` | ID, scope, immutable object key/generation/hash, MIME, size, safe download name, category, pin/reference state, finalized time, expiration; model never controls bucket/key |
| `CheckpointManifest` | Schema, scope, checkpoint ID, source snapshot time, files with relative paths/mode/size/hash/generation, explicit exclusions, journal/catalog watermarks; publish only after all objects verify |
| `MemoryRecord` | ID/revision/type/content, scope, source refs, verified/candidate status, timestamps, supersession/tombstone/expiry, embedding generation; no secrets, raw clipboard, or hidden reasoning |

Use native AI SDK tool states for tool-call rendering; map domain state to clear labels rather than expanding the framework union. Extend `ProjectIdeOperationStatus` at the application layer to represent cancelled/unknown outcomes.

### 5.1 API boundary specification

Proposed endpoints extend FastAPI through native routes/dependencies. Route prefixes can be served through the authenticated gateway; add Next handlers only where necessary. Existing protected six Next routes retain their contracts unless a failing compatibility test proves a change unavoidable.

| Boundary | Operations and response contract |
|---|---|
| `/workspaces/{id}` | Authorized state snapshot; projects and current controller/desktop epoch |
| `/workspaces/{id}/control` | Take/resume/release as explicit commands; idempotency key and expected epochs; return transition state, not premature ownership acknowledgment |
| `/workspaces/{id}/observations` | Capture/metadata for scoped desktop; returns `ObservationRef`; image bytes through artifact endpoint |
| `/workspaces/{id}/operations` | Validated desktop/filesystem operations; journal before dispatch; status by ID; internal agent calls bind trusted session context |
| `/workspaces/{id}/projects` | List/create/open registered roots; file APIs support path and version preconditions, upload staging, trash/export |
| `/workspaces/{id}/jobs` | Start/status/output/cancel, scoped input when policy permits; asynchronous job ID and cursor rather than waiting for completion |
| `/workspaces/{id}/events` | Authenticated bounded progress/output stream; cursor reconnect and authoritative snapshot on gaps |
| `/artifacts/{id}/content` | Authorized streaming download; immutable generation, safe MIME/disposition, bounded/range support when implemented, private/no-store caching policy |
| `/workspaces/{id}/previews` | Register job/allowed port, obtain isolated preview capability, unregister; never arbitrary host forwarding |
| `/memory` | Explicit scoped store/get/list/search/update/forget/export; write preconditions and visible degraded-index status |

Error behavior: 401 unauthenticated, 403 unauthorized, 404 unavailable scoped resource, 409 stale epoch/lease or conflicting operation, 412 failed file/object precondition, 413 oversized payload, 422 invalid action/schema, 429 quota/backpressure, 503 unavailable dependency. No error response includes secrets or arbitrary host paths.

Filesystem safety uses server-resolved roots, descriptor-relative/no-follow operations where appropriate, atomic writes and hashes/preconditions, safe archive extraction, and bounded searches. A preliminary string-prefix path check is insufficient against symlinks and races.

## 6. Data flow, durable execution, and failure policy

1. Authenticated turn resolves workspace binding; desktop mode selects only scoped tools. Audit `load_tool`, MCP, subagents, skills, graph delegation, and provider-native connectors so none restore unscoped worker-host access. Disable unsafe delegation in desktop mode until proven scoped.
2. Acquire controller lease through a native activity. Observe and persist an immutable screenshot descriptor. Serialize desktop tools with `SequentialToolExecutor`; all network/filesystem/input work remains outside workflow code.
3. Invoke the existing `TemporalAgent` with named model factory and native limits. Model discovery uses the live Agent API catalog; validate vision + function calling before enabling actions. Do not hardcode an unverified model fallback.
4. Before each action, validate frame geometry, epochs, approval, remaining mutation budget, and argument bounds. The trusted service binds a stable operation ID; do not derive it from retry attempt or permit the model to spoof another operation.
5. Journal acceptance before dispatch. Known duplicates return saved results; unknown dispatch outcomes require observation/reconciliation. GUI side effects are not exactly-once, even with Temporal and a journal.
6. Activity returns a compact domain result through native `activity_as_tool`. Model preparation resolves only structured, trusted completed observations, checks immutable provenance and size, fetches bytes in the model activity, and appends supported `input_image` message content in valid function-output order. Never hydrate arbitrary JSON/text from a webpage or command output.
7. Keep image bytes, video, unbounded terminal text, and secrets out of workflow messages/history. Missing image or capability pauses action and requests a fresh observation; no silent blind/stale-frame operation.
8. Preserve native WorkflowStream/SSE for model text, tool summaries, approvals, artifacts, and terminal outcomes. Use separate workspace output stream for stdout and native noVNC WSS for framebuffer/input. No video frames in Temporal.
9. Continue-as-new carries bounded messages, workspace/epoch binding, stream state, unresolved operation refs and summaries. Use native stream draining/continuation; reconstruct environment through activities after restart.
10. Completion returns outcome, verified changes, limits, evidence and download refs to the calling agent and displays final Markdown via `MessageResponse`. Task memory extraction runs only after a known outcome and marks inferred facts as candidates.

Proposed configuration constants for desktop mode: maximum 100 mutations per task; 30-minute task deadline; explicit model turn/token limits; observation activity start-to-close 30s with at most three attempts; GUI mutation start-to-close 30s and one attempt; long-job monitor heartbeat every 10s with 30s heartbeat timeout and bounded schedule-to-close within task budget. Validate these starting values with provider latency and cleanup tests; do not conflate model retries with safe side-effect retries.

| Failure | Required response |
|---|---|
| Worker dies after dispatch | Consult journal; unknown result stays unknown; capture/reconcile, no automatic repeated click or job launch |
| Desktop restarts/resizes | Increment/invalidate epoch or frame geometry; release tracked inputs; fresh observation before further action |
| Human takeover | Fence new work, cancel/settle mutating jobs, then grant human access; explicitly show pending takeover if a job cannot stop safely |
| Agent resume | Revoke human input connections/credentials, new lease fence, fresh screenshot, then model continuation |
| Provider stream interruption | Reattach by known background response ID/cursor where supported; ambiguous create without known ID must not blindly POST again |
| Cancellation | Native agent cancel plus provider cancellation and remote process-group/input cleanup; acknowledged status reflects cleanup outcome |
| Browser disconnect | Durable work continues; reconnect to authoritative state and output cursor |
| GCS failure | Preserve bounded local staging; stop actions that require unavailable durable evidence; explicit saves fail visibly |
| Disk pressure | Warn at 80%, reject large jobs/uploads at 90%; clean only rebuildable/verified-durable data, never sole source files |
| Auth revocation | Deny new access and revoke active control/stream capabilities; bounded connection lifetime is fallback, not immediate-revocation proof |

Native input acceptance must include left/right/middle/double click, drag with guaranteed release, four-way scroll, hover, Unicode/multiline entry, key chords/down/up, clipboard policy, crop coordinate transforms and cleanup. Browser CSS scale never changes the physical-coordinate contract.

## 7. OpenCode integration without an extra protocol wrapper

Implement a small independently built ESM package at the native AI SDK provider interface, loaded by OpenCode's documented `provider.<id>.npm` configuration. Prefer a checksum-pinned `file://` entry bundled in the desktop image so runtime installation and automatic upgrades are unnecessary.

Native interface:

- One exported `createPerplexityAgent(options)` factory; inspected OpenCode loader picks the first export beginning with `create`, so avoid ambiguous exports and pin/test that loader behavior.
- Options accept `name`, `baseURL`, workspace `apiKey`, headers, and injected `fetch`. Honor OpenCode's injected fetch and abort/timeouts for every request.
- Return a provider exposing `languageModel(id): LanguageModelV3`; each model supplies `specificationVersion: "v3"`, `provider`, `modelId`, `supportedUrls`, `doGenerate`, and `doStream`.
- `doStream` returns `ReadableStream<LanguageModelV3StreamPart>` plus optional request/response metadata. Map `stream-start`, text/reasoning start-delta-end, tool-input events, tool calls/results, sources/files, errors, and `finish` usage/reason correctly. Distinguish provider-executed tools from OpenCode-local tool execution.
- `LanguageModelV3CallOptions` carries prompt, tools/toolChoice, providerOptions, headers, abortSignal, token limits and supported sampling/response options. Reject unsupported settings or issue native warnings rather than silently claiming support.
- Use a conservative `supportedUrls` policy; do not give a provider uncontrolled access to arbitrary protected artifact URLs. Verify image conversion and private-data exposure explicitly.

Gateway preserves the Agent API wire format and fixed upstream paths. It authenticates workspace capability, injects the upstream credential, enforces model/tool/budget policy and response ownership for retrieval/cancel/files, and streams without translating to another provider protocol. A bearer-token swap alone is insufficient if a caller can retrieve another response ID.

Managed OpenCode configuration: `share: "disabled"`, `autoupdate: false`, provider allowlist, bounded permissions, immutable managed policy outside project source. Live model discovery generates approved model entries; presets are handled explicitly, not pretended to be catalog model IDs. Credentials are revocable and scoped; never expose the upstream key. Project configs/environment must not bypass provider/network/tool restrictions.

Temporal activities start, monitor, reconnect, cancel, and collect a coding job using stable IDs. OpenCode owns its internal coding loop; individual edits are **not** thereby Temporal activities. Unknown job state never triggers an automatic complete rerun. Provider persistence/reconnect must preserve one response stream and avoid duplicate billable creates.

Compatibility gates: exact Agent API schema mapping, local tool roundtrips, usage/finish semantics, background streaming/cancel, reconnect deduplication, private file access, package loader and Linux runtime. No fork of OpenCode and no Chat Completions/Responses emulation gateway.

## 8. Storage, memory, backup, and retention

Use `gs://thisforagent/gwen/staging/v1/{active,artifacts,projects,memory,recovery,staging}/` with server-derived scope underneath. Approved releases use a separately permissioned publisher path; runtime reads but cannot modify them. Do not apply bucket-wide lifecycle changes to the existing shared bucket.

- Live projects/browser home/journal remain on persistent POSIX disk. No GCS FUSE for Git, SQLite, node_modules, or browser profiles.
- Immutable uploads use `ifGenerationMatch=0`; head/catalog changes use the expected generation; checksum and generation are verified before publication. A 412 is conflict evidence, not permission to overwrite.
- Upload checkpoint data first, publish verified manifest last. GCS provides no multi-object transaction. Do not copy a live SQLite file as a consistent backup; use its backup interface or quiesce services. Coordinate Temporal development-server backup separately.
- Proposed retention: ordinary finalized screenshots 7 days; logs and temporary exports 30 days; daily project checkpoints 30 days plus monthly 12 months; pinned deliverables and confirmed memory until explicit deletion. Active references block cleanup. Enable cleanup only after dry-run and restore tests.
- Retain the bucket's previously observed seven-day soft-delete policy unless separately approved; re-read current metadata before rollout. Removed-from-recall does not mean immediate physical deletion from soft delete, backups, workflow history, or provider retention.
- Proposed recovery objective: checkpoint acknowledged project changes at task completion and on a 15-minute schedule when consistency can be obtained; report actual last verified checkpoint and any gap. VM RAM/browser process continuity is not promised. Measure restore time rather than inventing an availability guarantee.
- Restore into a new/empty workspace, verify hashes and archive paths, restore controller journal in recovery-required state, increment desktop epoch, reapply tombstones/catalog watermarks, rebuild index and capture fresh observation before enabling actions. Never resume unknown mutations automatically.

Memory implementation is feature logic at native activity/tool boundaries: immutable GCS revisions + conditional catalog heads, local rebuildable LanceDB index, explicit repair queue and catalog fallback. Use configured `EMBEDDING_GENERATIONS` only after endpoint/dimension/encoding validation. Existing config reports `memory-v1`, `pplx-embed-context-v1-0.6b`, dimension 1024, `base64_int8`; that is configuration evidence, not proof the endpoint is available.

Store/get/list/recall/update/forget/export operate with authenticated scope, provenance and write preconditions. Durable record commit precedes index update. Tombstone immediately excludes a memory from recall; repair/rebuild cannot resurrect superseded/deleted revisions. Inferred candidates are not promoted automatically to authoritative facts. Optional index/memory failure degrades chat gracefully while explicit save failures remain visible.

## 9. Delivery sequence and affected files

1. **Baseline and native-contract tests.** Re-read scoped AGENTS.md and current diff; protect existing work. Confirm the versions above, public signatures and missing APIs. Test native tool schemas, descriptor/image ordering, OpenCode V3 stream roundtrip, noVNC ESM typing, and native VNC revocation before committing to infrastructure rollout. Add replay fixtures.
2. **Secure release foundation.** Review `scripts/gce-startup.sh`; introduce pinned build/deploy assets, data-disk mount checks, per-service credentials/users, authenticated ingress and external egress enforcement. Never run `pnpm dev:all` as boot orchestration. Preserve the existing VM and data.
3. **Domain state and filesystem operations.** Implement native FastAPI/Pydantic routes and SQLite transactions for bindings, leases, journal, artifacts, projects and jobs; direct SDK calls rather than managers/facades. Add path, quota, conflict, authorization and restore tests.
4. **Desktop runtime and native transport.** New `desktop/Dockerfile` and immutable desktop service configuration; TigerVNC/XFCE/browser/terminal; scoped input/capture functions, noVNC/websockify, native credential/disconnect control. Gate autonomous input on negative permission tests.
5. **Temporal/Strands integration.** New `orchestrator/desktop_activity.py`, `workspace_activity.py`, `coding_activity.py`, `artifact_store.py` as functional implementations, not wrapper classes. Extend `workflow.py`, `run_worker.py`, `config.py`, `server.py`, and existing `perplexity_model.py` at native extension points. Audit `load_tool.py`/`subagent_support.py`/MCP/delegation for host escape. Version behavior and handle native cancellation.
6. **UI composition.** Extend existing `components/v0/agent-chat.tsx`, `computer-use-preview.tsx`, `computer-use.ts`, `project-ide.ts`, `project-ide-panel.tsx`, and `agent-activity.tsx`. Mount `RFB` directly and reuse AI Elements exports. Add only necessary type declarations/schema types; leave vendored primitives unchanged. Make file state authoritative and terminal output incremental.
7. **OpenCode provider.** Add independently pinned provider package/release asset implementing V3; managed immutable config, transparent credential gateway policies, and native activity-managed job lifecycle. Prove same-project editing/test/preview/export plus reconnect/cancel.
8. **Memory and recovery.** New `orchestrator/memory.py` implements activity/tool functions with direct GCS/LanceDB use, index repair and tombstones. Add inspect/edit/forget/export composition and clean-VM restore fixtures.
9. **Staging rollout.** Feature disabled by default; pass local/security/replay gates; validate deployment inputs and obtain provisioning approval; deploy approved digest/bundle only to the new staging VM; run acceptance and restore; enable cleanup last. Production cutover requires a separate decision.

Do not edit `orchestrator/graph_tool.py`, protected `.env*`, or the six existing protected Next route files without their required compatibility proof. Do not rename `components/v0`. Do not mutate Jira in this planning work. Credential ignore/build/export/history checks are mandatory implementation tasks; use exact exclusions, not blanket `*.json`. Coordinate rotation only if actual exposure is found; removing a key file is not revocation.

## 10. Verification, observability, and rollback gates

Baseline and final commands (implementation agent only):

```bash
pnpm exec next typegen
npx tsc --noEmit
pnpm lint
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
# From orchestrator/:
.venv/bin/python -m pytest tests -q
```

Record actual failures; do not treat old AGENTS.md expected failures as fresh results. Next production build is not the type gate.

Release acceptance:

- Native schema validation before worker readiness covers every generated `activity_as_tool(...).tool_spec` and complete Agent API outbound tool array. No byte images/unbounded logs enter history.
- Replay tests cover pre-desktop histories, patched desktop execution, cancellation, pending approval, worker death after dispatch, continue-as-new, stream truncation and reconnect. No duplicate mutation/job creation after recovery.
- Security tests cover forged owner headers, cross-scope IDs, CSRF, WS Origin, revoked sockets, metadata/host/private-IP egress, project config/provider bypass, traversal/symlinks/archive attacks, preview cookie isolation/HMR, and accidental key inclusion in release/build/export.
- Desktop E2E verifies the full input matrix and visible outcomes, Unicode/multiline clipboard behavior, cleanup after interrupted drag, stale-frame rejection, human takeover during a running command, and fresh observation on resume.
- Coding E2E creates/edits a fixture project, streams tests, opens isolated preview, reconnects mid-job without duplicate work, cancels safely, and exports with correct hashes/exclusions using actual Agent API inference.
- Memory/storage E2E covers conditional-write conflicts, index outage and rebuild, scoped recall, explicit corrections/forget, active-artifact pinning, failed checkpoint uploads, and replacement-VM restore without memory resurrection.
- Readiness checks real workflow/activity pollers, desktop display/capture/input, controller/journal state, GCS access, and selected model capabilities. Keep basic liveness separate from desktop readiness.
- Monitor CPU/RAM/OOM, disk/inodes, cache/log growth, provider usage/cost, observation sizes, stdout lag, desktop latency, Temporal history bytes/events, retry counts, unknown outcomes, backup age, auth errors and native transport reconnects. Use native structured logs/OTel/Ops Agent where configured, no custom telemetry framework. Redact credentials/clipboard/sensitive screenshots from logs.
- Alert immediately on repeated `outcome_unknown`, missing/old backups, disk high-water marks, absent pollers, policy bypass attempts and crash loops. Measure capacity before raising concurrency.

Rollback: disable new desktop starts, fence control, settle/cancel active jobs conservatively, take consistent backups, and restore only workflow-compatible code. Do not automatically point old workers at histories they cannot replay. Keep data/epochs/tombstones and approved release assets intact; never delete user source during rollback. Existing `strands` remains a fallback, but failover does not imply migration of staging sessions or side effects.

## 11. Required deployment inputs and honest completion criteria

These are explicit deployment blockers, not reasons to invent values or broaden scope during implementation:

- Approved VM/data-disk names and final capacities, VPC/subnet/static address, exact immutable image/package pins and regional quota.
- Authorized human Google identity, OIDC client and redirect URLs, application/preview hostnames, DNS/certificate access and cookie policy.
- Dedicated runtime/deployer identities, scoped IAM bindings, Secret Manager version references, enabled APIs and signed release URL/SHA256.
- Confirmed retention/cost policy, backup destinations/schedule, observability destination and alert recipient.
- Passing native VNC control/revocation and Agent API/OpenCode V3 compatibility fixtures. Failure keeps the relevant feature disabled; it does not authorize a custom wrapper workaround.

Done means an authenticated owner can watch and take over the same persistent cloud desktop the durable agent uses; projects, jobs, artifacts and curated memory survive tested failure/restore paths; native UI and framework contracts remain intact; all mandatory gates have evidence. A written spec, imported package, successful HTTP response, or intended action is not implementation evidence.

## 12. Documentation references

Use these official sources together with the exact pinned source; current online docs may describe newer APIs:

- Temporal Strands integration: https://docs.temporal.io/develop/python/integrations/strands-agents
- Native activity tool/hook API: https://python.temporal.io/temporalio.contrib.strands.workflow.html
- Workflow streams: https://docs.temporal.io/workflow-streams
- Python workflow versioning: https://docs.temporal.io/develop/python/workflows/versioning
- Python testing/replay: https://docs.temporal.io/develop/python/best-practices/testing-suite
- Strands custom tools: https://strandsagents.com/docs/user-guide/concepts/tools/custom-tools/
- Strands hooks: https://strandsagents.com/docs/user-guide/concepts/agents/hooks/
- Strands interrupts: https://strandsagents.com/docs/user-guide/concepts/interrupts/
- Strands session-management distinction: https://strandsagents.com/docs/user-guide/concepts/agents/session-management/
- AI Elements: https://elements.ai-sdk.dev/components/web-preview ; https://elements.ai-sdk.dev/components/agent ; https://elements.ai-sdk.dev/components/task ; https://elements.ai-sdk.dev/components/chain-of-thought ; https://elements.ai-sdk.dev/components/terminal ; https://elements.ai-sdk.dev/components/file-tree ; https://elements.ai-sdk.dev/components/message
- noVNC API/embedding: https://github.com/novnc/noVNC/blob/master/docs/API.md ; https://github.com/novnc/noVNC/blob/master/docs/LIBRARY.md
- TigerVNC native controls: https://tigervnc.org/doc/Xvnc.html ; https://tigervnc.org/doc/vncconfig.html ; https://tigervnc.org/doc/vncpasswd.html
- OpenCode provider/config: https://opencode.ai/docs/providers/ ; https://opencode.ai/docs/config/
- Pinned OpenCode loader: https://github.com/anomalyco/opencode/blob/v1.18.27/packages/opencode/src/provider/provider.ts
- GCE identity: https://cloud.google.com/compute/docs/access/service-accounts
- GCS conditional writes: https://cloud.google.com/storage/docs/request-preconditions
- IAM conditions: https://docs.cloud.google.com/iam/docs/conditions-attribute-reference
- GCS permissions: https://docs.cloud.google.com/storage/docs/access-control/iam-permissions
- LanceDB API/filtering: https://lancedb.github.io/lancedb/python/python/ ; https://docs.lancedb.com/search/filtering
- Native auth proxy integration: https://oauth2-proxy.github.io/oauth2-proxy/configuration/integrations/nginx/
- Nginx WS: https://nginx.org/en/docs/http/websocket.html
- Docker firewall behavior: https://docs.docker.com/engine/network/firewall-iptables/
- Provider wire contract: repository `docs/integrations/openapi.md` and `docs/integrations/agent-api-reference.md`; supplied schemas take precedence over assumptions about OpenAI compatibility.
