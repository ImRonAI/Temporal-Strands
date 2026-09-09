# Desktop implementation handoff

Updated: 2026-09-08, approximately 21:15 UTC.
Workspace: `/Users/tims-stuff/Desktop/v0-clone-blurple`.

## Start here

### Follow-on implementation: authenticated registry/service increment

The continuation added `orchestrator/workspace_registry.py`, `workspace_api.py`,
and `workspace_service.py`, with matching test suites. `server.py` now mounts
the workspace router and its disabled-by-default lifespan; protected Next
routes, Think, workers, and graph behavior were not changed by this increment.

- Single-owner workspace registration; server-issued hashed, expiring/revocable
  session tokens; scoped project reservation/list/open and persisted selection.
- Control API revalidates the OIDC cookie through OAuth2 Proxy's private
  `/oauth2/auth` endpoint on each request, requiring native 202 plus the exact
  configured `X-Auth-Request-User` subject. Client identity headers are ignored.
  Exact HTTPS Origin, same-origin JSON/custom-header CSRF checks, bounded request
  bodies, sanitized errors and private/no-store responses apply to workspace APIs.
- Filesystem list/read/search runs in the separate workspace-side FastAPI app;
  control API uses a fixed Unix-socket client, verifies workspace identity, and
  never passes browser cookies or provider credentials to that service.
- Project creation has prepare/dispatch phases with server-issued IDs, controller
  fences, transactional session-expiry checks, and session admission budgets.
  Lost replies become unknown, never automatic redispatch. Project/journal
  completion is atomic, including late replies after session revocation.
- No public control-acquisition endpoint exists. A newly registered workspace
  stays recovery-required; tests use an explicitly synthetic native-revocation
  receipt. Real mutations remain blocked until native VNC/runtime gates pass.
- Workspace sessions are not authenticated Temporal chat sessions. Existing chat
  endpoints are unchanged, and MUST NOT be advertised as protected by this API.

Enablement requires `GWEN_WORKSPACE_API_ENABLED=1` plus explicit
`GWEN_WORKSPACE_OWNER_ID`, `GWEN_WORKSPACE_ORIGIN`,
`GWEN_WORKSPACE_STATE_PATH`, `GWEN_WORKSPACE_AUTH_SOCKET`, and
`GWEN_WORKSPACE_SERVICE_SOCKET`. Missing configuration fails enabled startup.
The control DB must already exist from explicit trusted bootstrap; startup adds
registry tables but never replaces a missing database. No real values were set.

Workspace-side entrypoint:
`uvicorn workspace_service:configured_app --factory --uds <private-socket>`;
it requires `GWEN_WORKSPACE_PROJECTS_ROOT` (existing mounted projects directory)
and `GWEN_WORKSPACE_ID` matching registration. Run separately as desktop UID,
not inside the credential-bearing worker. Socket directories, mount namespaces,
and service paths MUST be inaccessible to project programs, including same-UID
processes. These deployment protections and actual OAuth2 Proxy behavior are
not established by mocked auth/ASGI tests. No container runtime or cloud rollout
is enabled by this increment.

Verification at this increment: full backend **700 passed**, frontend **328
passed**, Next typegen, `npx tsc --noEmit`, and `pnpm lint` passed. Colima was
already running. Refreshed Linux/amd64 fixture ran API/registry/service/filesystem
suites as UID1000, no network, all capabilities dropped, no-new-privileges:
**123 passed**. Only explicit source/test files were copied, no repo/home mount.
Retained local test image `gwen-workspace-service-test:local`, digest
`sha256:12b83c7a92cf8704c2602970045aa6143702c7abb021e3c8fca5d643b3377cf6`.
Its FastAPI/httpx/Pydantic/pytest-asyncio match requested pins; transitive
Starlette/AnyIO resolved to 1.6.0/4.15.1 rather than local 1.3.1/4.14.2. This is
test evidence, not a production lock or deployment pin. The initial run reused
an old filesystem timestamp fixture, failed once, then passed after refreshing
the current fixture. The stopped dependency container was removed; Colima and
the test image remain. Full desktop, native revocation, chat binding, jobs,
artifacts, OpenCode, UI and rollout gates remain unfinished.

The user wants implementation continued in a NEW conversation/instance, not another planning exercise. They asked for full continuity context. No new Agent Manager session or cloud VM was created for this handoff.

Read in order:

1. `.kilo/plans/1788845228632-gce-desktop-native-infrastructure-spec.md` (primary implementation specification, all sections).
2. This handoff, recording actual implementation and subsequent user corrections.
3. Root `AGENTS.md` and scoped `orchestrator/AGENTS.md`, freshly from disk.
4. `.kilo/plans/1788832097810-gce-desktop-agent-implementation.md` for detailed acceptance cases; the native-infrastructure spec supersedes conflicting decisions.
5. Current source and `git status`/diff before edits. Multiple other sessions are actively changing this workspace.

The feature is NOT ready for end-to-end human desktop testing. Tested foundations exist; no authenticated desktop APIs, desktop container, noVNC UI, or production wiring was enabled by this work. The user explicitly asked to finish, and objected to repeatedly stopping at missing tooling. Docker installation and Linux testing are now accomplished. Proceed with connected, testable implementation increments, not prolonged repeated research.

## Critical user correction: Think is required

The user explicitly corrected an earlier assistant assertion that `think` should be excluded:

> The Think tool is supposed to be implemented for the Perplexity Agent API provider because no reasoning is released through that provider. Do not change it, and do not avoid it. Please carry on, and ask questions if faced with a similar situation moving forward.

Preserve Think functionality and include it in the eventual Perplexity desktop path. Do not remove it based on old AGENTS prose or the original spec's stale inventory warning. If actual architecture/policy conflicts arise, ask one focused question rather than silently dropping behavior. Think output is published nested-agent output, not a claim to expose hidden provider reasoning.

This session did not modify Think, workflow, worker, or graph implementation. Other sessions DID subsequently change those files; preserve their work. The latest scoped guidance now says Think is live. Root/scoped guidance and inventory were updated concurrently during this conversation.

## Authorization and safety boundaries

- Implementation and local Docker installation/testing are authorized.
- No GCE resources, IAM changes, public ingress, bucket objects, secrets, or deployment were created/modified by this implementation session.
- Target remains the isolated single-owner Ubuntu 24.04 pilot in the specification, leaving existing `strands` VM untouched. Proposed VM is `gwen-desktop-staging`, project `fair-expanse-493212-h8`, zone `us-central1-a`; data namespace `gs://thisforagent/gwen/staging/v1/`.
- Provisioning still needs explicit approval and real deployment inputs: capacity/names/network, authenticated owner/OIDC/domains, runtime/deployer identities, Secret Manager versions, approved immutable images/release hashes, retention/backup/alerts.
- Never read/edit protected `.env*`; never copy the supplied service-account key into any build, tool input, container, or output.
- Protected six existing Next routes and `orchestrator/graph_tool.py` remain protected. Route changes need failing compatibility proof. A graph_tool.py file NOW EXISTS from concurrent work: do not remove or edit it merely because older notes say absent.
- Do not rename `components/v0`, modify vendored AI Elements, implement replacement agent loops, or silently introduce provider-protocol facades.
- No commits, pushes, PRs, or Jira mutations were made by this session.

## Actual deliverables from this session

### Credential/release safeguards

Modified `.gitignore`: exact basename exclusion for the supplied bootstrap credential, not blanket `*.json`.

Added `.dockerignore`: excludes credential basename at any depth, `.env*`, local tooling/runtime/build-cache material, and `compute.json` from root Docker build contexts.

Added `.gitattributes`: `export-ignore` for credential basename at root/nested paths, `.env*`, compute export, `.kilo`, `.omo`, `.worktrees`, and orchestrator readiness state. `git check-attr export-ignore` verified root/nested matches. This is NOT a complete project export/backup implementation.

Added `scripts/validate_desktop_release.py` and `orchestrator/tests/test_desktop_release.py` (135 tests last scoped run):

- Stdlib local tar.gz validation CLI, no extraction or execution.
- Requires an externally supplied SHA256. Opens once with `O_NOFOLLOW|O_NONBLOCK`, fstats regular file/size, hashes and parses using the SAME file descriptor, rechecks descriptor identity afterward.
- Rejects path traversal/absolute paths/backslashes/dot components/control characters, duplicate names, links/devices/FIFOs, setuid/setgid, unsafe layout, credential and environment path components, runtime caches, and excessive count/size.
- Does not read member contents if member/layout safety fails.
- Requires sibling `app/` and `strands-tools/src/` markers plus `release-manifest.json`.
- Manifest v1 includes release ID/time, Linux amd64 platform, provenance declarations, artifact paths/SHA256/sizes. Artifact bytes are hash-verified.
- CLI: `python scripts/validate_desktop_release.py --archive <bundle.tar.gz> --sha256 <approved-hash> [--json]`.
- Provenance is a declaration authenticated only by the approved outer hash, NOT independently verified signatures/SLSA. Pins/digests are publisher supplied, not invented.
- A deployer-only staging directory is still essential against hostile in-place writers. Name-based exclusions do not detect renamed secrets/content. Validator does not prove a runnable/prebuilt complete release.
- Original untracked `scripts/gce-startup.sh` was reviewed but left UNCHANGED. Its moving dependency downloads, on-VM build with runtime environment, shared identities, and rollback assumptions still need replacement per specification. The new validator is NOT wired into it.

### Durable workspace state foundation

Added `orchestrator/workspace_state.py` and `orchestrator/tests/test_workspace_state.py` (24 tests at last individual count):

- Pydantic `WorkspaceBinding`, `ControllerLease`, `OperationRecord`, strict extra-field rejection and schema v1.
- Direct SQLite functions, no manager/facade; explicit bootstrap creates mode 0600 database with WAL, FULL synchronization, schema version checks. Subsequent opens use mode=rw so missing storage cannot create a replacement database.
- `create_workspace`, `bind_session`, `get_lease`, `begin_control_transition`, `complete_control_transition`, `renew_control_lease`.
- `accept_operation`, `start_operation`, `get_operation`, `finish_operation`, `recover_workspace`, `reconcile_operation`.
- Owner/session scope checks; epoch fences; 5-minute renewable controller lease; transitions immediately fence admissions and cancel accepted/not-dispatched operations.
- Completion requires trusted native revocation receipt, no unresolved running/unknown operations, and supports a registered target session distinct from initiator. Handoffs invalidate observations by increasing desktop epoch.
- Operation IDs are expected to be server-issued upstream. Stable request hash includes actor/action/session/project/epochs. Duplicate IDs with different requests conflict. Duplicate saved results never authorize redispatch.
- Accepted-to-running is exclusive transactionally across workspace; running/unknown blocks subsequent mutations. Terminal states immutable.
- Recovery requires expected epochs/current owning session when one exists, marks dispatched work unknown, cancels undispatched work, fences both epochs, leaves control recovery_required.
- Explicit evidence-backed reconciliation retains original `state='outcome_unknown'`, adds immutable disposition/cleanup/observation evidence, unblocks NEW work only. Original operation cannot restart. Transition initiator can reconcile an older session's unknown operation during human takeover.
- Fresh epoch/time evidence enforced, future evidence rejected, capture operation must differ from reconciled mutation. Reconciliation requires fenced transition/recovery even if unknown was reported during active control.
- Readers use deferred transactions, not writer locks. Tests include restore via SQLite backup, concurrent admission/dispatch/takeover, cross-scope denial, expiry, renewal, stale transitions, handoff target sessions, recovery reconciliation.

Important limitations: no routes/tools expose these trusted functions; receipts/observation provenance are NOT self-authenticating. Caller must verify catalog ownership, actual process cleanup and native VNC revocation. Role authorization/session revocation, registered project storage, jobs, active transport revocation, budget enforcement, and service lifecycle remain unfinished. SQLite journaling cannot make external GUI effects exactly-once. Fresh bootstrap schema was edited during this unshipped increment; no migration was built for deployed state because none was deployed. Incomplete bootstrap fails closed and needs deliberate operator recovery, never automatic overwrite.

### Observation foundation

Added `orchestrator/desktop_observation.py` and `orchestrator/tests/test_desktop_observation.py` (25 tests):

- `ObservationRef`: schema v1; artifact ID, generation STRING, lowercase SHA256, allowed image MIME/size, dimensions, timezone-aware capture time, desktop epoch, operation ID, display dimensions, bounded crop and scale.
- No image bytes, storage keys, arbitrary URLs, or credentials in descriptors.
- `physical_coordinates`: validate epoch, physical display geometry, capture invalidation time, integer coordinate bounds, crop/scale transform. Reject bool/floats for pixel inputs and timezone-naive invalidation time.
- Pydantic Temporal payload converter roundtrip proves compact descriptor-only payload and lossless large generation string.
- This is metadata/coordinate validation only. Catalog, capture/input, artifact persistence and model image hydration NOT implemented. `desktop_observation` imports `StateConflict` from workspace_state, which uses a function-local reverse import for reconciliation; works, but avoid growing this coupling.

### Native SDK compatibility findings encoded as tests

Added `orchestrator/tests/test_desktop_native_contracts.py`: 40 offline tests against installed versions:

- `temporalio==1.31.0`, `strands-agents==1.50.2`, `strands-agents-tools==0.8.5`, `pydantic==2.13.4`.
- Native `activity_as_tool` schemas from signature/docstring; Pydantic action unions/constraints survive outbound validation through existing `_validate_tools` and `_ensure_object_properties`.
- Arguments bind POSITIONALLY in signature order. Trusted ownership/operation context is NOT injected automatically. Model args cannot be trusted owner IDs.
- `ToolContext` pitfall: postponed annotations can leak it as model-fillable schema; do not use it as a hidden activity context mechanism. `agent` and positional-only parameters are unbindable from model input.
- `Annotated[int, Field(...)]` unsupported in installed Strands tool generator. Put coordinate bounds in Pydantic models.
- Native results are one JSON-text content block for returned dicts. Raw BaseModel return falls back to str unless converter already decoded to dict; return JSON-native mappings. Bytes are unsuitable and may fallback to repr/reject conversion.
- Preserve existing decoders: `_tool_result_payload` in workflow.py and `unwrapToolOutput` in components/v0/computer-use.ts. Do not add another envelope.
- `BeforeToolCallEvent`, `AfterToolCallEvent`, `BeforeModelCallEvent` present. `BeforeToolsEvent`, `AfterToolsEvent`, `InterruptEvent` absent. Use native writable event fields.
- `SequentialToolExecutor` opt-in; default concurrent executor unsafe for frame-dependent mutations.
- CRITICAL: `Agent.cancel()` in BeforeToolCallEvent DOES NOT block the CURRENT tool dispatch; it blocks subsequent tools/model turns. Use `event.cancel_tool`/interrupt plus server fencing for current mutation.
- `AfterToolCallEvent.retry=True` redispatches tool activity; never do this for mutations.
- Native interrupt/resume roundtrip tested. `limits={turns: 1}` can still execute several tools in one turn; implement separate mutation budget.
- `activity_as_hook` comes from temporalio.contrib.strands.workflow, discards returned values, cannot rewrite hook event through return.
- Real `TemporalModel.stream` JSON-filters invocation_state: drops live agent/model/bytes, preserves serializable keys. Executor adds messages/tool config. Python json.dumps accepts NaN, so filter alone is not strict JSON validation.
- `StrandsPlugin` belongs on Client; factories, Pydantic payload converter and Strands failure converter. No live model in workflow initialization. TemporalAgent rejects retry_strategy.
- Desktop finite timeout/retry options pass directly; mutation one attempt, WAIT_CANCELLATION_COMPLETED where cleanup acknowledgement required. ActivityEnvironment cancellation/heartbeat tests are simulated, not proof of remote cleanup.
- Fake model factory caching: preload module-level script queues BEFORE worker startup.

### Workspace filesystem foundation (most recent implementation)

Added `orchestrator/workspace_files.py` and `orchestrator/tests/test_workspace_files.py`: 64 tests.

Public functions:

- `list_project_files(root_fd, path='')`: one directory, bounded before sorting, excludes dangerous paths and link/special entries.
- `read_project_file(root_fd, path, expected_sha256=None)`: bounded stable UTF-8 single-link regular files; strict hash validation/case normalization; inode/size/time checks before/after/named entry.
- `search_project_files(root_fd, query, path='')`: bounded literal search (not regex), line/path/hash results, actual bytes consumed, attempted vs scanned counts, explicit truncation/incomplete/skipped state.
- `create_project_file(root_fd, staging_fd, path, text)`: private same-filesystem stage, write/fsync, atomic link publication ONLY IF ABSENT, unlink stage/fsync directories, verify destination and parent identity. Never overwrite existing content; never delete published destination after failure.

Filesystem boundary details:

- Trusted project root fd must come from authenticated server registration. These are service-thread functions, not exposed tool parameters or workflow I/O.
- Fresh `os.open('.', O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW, dir_fd=root_fd)` gives independent root directory descriptions. `dup` was wrong because concurrent scandir calls shared offsets. All path components opened descriptor-relative/no-follow.
- ExitStack ensures fd cleanup including failed fdopen/directory reads. `_directory` only maps acquisition errors, does not mislabel arbitrary caller-body OSError.
- Distinguish FileUnavailable (404), FilePreconditionFailed (412), FileLimitExceeded (413), FileServiceUnavailable (503), FileOutcomeUnknown (reconcile, not blind retry). Causes retained for trusted diagnosis; do not serialize traceback/host paths.
- Reject traversal, absolute paths, empty/dot components, controls, backslashes/colon, excessive depth/length, surrogates. Exclude exact credential basename, .env* at any depth, .ssh/.gnupg/.git/node_modules etc. No actual protected file reads in tests.
- FIFO nonblocking, hardlink and symlink denial, bounded read bytes. Search charges bytes actually read even if later validation fails; oversized pre-read rejects consume zero bytes. Separate directory/file/queue/query/time/match limits. Lines split on '\n', not Python splitlines Unicode separators.
- Private staging requires same filesystem, current UID, restrictive permissions. Files published 0600 for desktop UID. Same-UID process isolation/mount protections remain runtime prerequisites, not provided by mode bits.
- Directory fd prevents symlink redirection, NOT hostile move-out-of-root containment. Runtime must restrict mount namespace/filesystem access. Parent/destination race after publication produces unknown, never false success.
- Metadata checks are best-effort concurrency detection, not snapshots: timestamps can coalesce. Linux found one test that assumed every edit changes timestamp; fixture now explicitly advances mtime to test change-detection deterministically. Do not claim all directory races are detectable or exact snapshot semantics.
- NO overwrite/patch/move/trash/export/upload API implementation yet. Create-only was deliberate: check-hash-then-os.replace is not CAS against concurrent writers.
- NO project registry, ownership or controller/journal enforcement inside these functions. Must connect upstream trusted admission AND runtime isolation before enabling tools/routes.

Appended desktop/filesystem constants to config.py without changing existing policies: desktop 100 mutation/30m task defaults, 30s observation/mutation deadlines, 3/1 retry attempts, 10s/30s job heartbeat interval/timeout, 5m lease, 64KiB records, 10MiB observations, 16384 max dimension; 1MiB file, 2000 directory entries, 4096-byte path/query, depth32, 200 file/directory/pending-directory limits, 8MiB search reads, 100 matches, 5s search budget. Existing global uncapped model policies unchanged by this work. Budget constants are not runtime enforcement where no activities exist yet.

## Local Docker installation and Linux validation

User explicitly said to download/install the missing Docker runtime rather than stop at absence.

- Apple Silicon macOS host (`arm64`). Existing Homebrew docker CLI had no daemon.
- `/Applications/Docker.dmg` already existed. Mounted read-only, installed `/Applications/Docker.app` with ditto, launched `open -a Docker`, unmounted image afterward. Docker Desktop readiness did not respond; no license acceptance or GUI setup was automated.
- Installed Homebrew `colima 0.10.3` and dependency `lima 2.2.0`. Homebrew updated tap metadata, no broad dependency upgrades were requested.
- Colima first 332MB image download timed out; retry succeeded. Ubuntu 24.04 minimal ARM64 image downloaded from official colima-core release.
- Docker Engine `29.5.2 linux/arm64` verified AGAIN immediately before this handoff. Context `colima` is active. Emulated linux/amd64 available.
- Running tracked background process: `bgp_082d17cf3001StZ5bzP264fPib`.
- Command: `colima start --foreground --cpu 2 --memory 4 --disk 20 --vm-type vz --mount-type virtiofs --mount /var/folders/c8/y41r0sp91172dqm_0jk3wcww0000gn/T/kilo:w`.
- IMPORTANT LIFETIME: session-bound background process, NOT persistent and NOT configured as login service. New session may stop it; first run `docker --context colima version`. If unavailable, restart Colima via tracked background_process with the same command. Never use shell '&'/nohup.
- No whole repository/home mounted into test container. Only three explicit files copied: workspace_files.py, config.py, tests/test_workspace_files.py.
- Pulled `python:3.13-slim` linux/amd64, resolved digest `sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285`. Python in image is 3.13.15 (mac local previously 3.13.13). This is local test evidence, NOT an approved production pin.
- Installed pytest==9.1.1 and temporalio==1.31.0 inside disposable test container (config imports RetryPolicy).
- Local test snapshot image `gwen-filesystem-linux-test:local`, image ID `sha256:8797ef2859df5f653bd4b0847efa6996db100309735e797451566a579859ae53`, retained. Stopped staging container `gwen-filesystem-linux-check` removed. Snapshot contains old copied source/tests; refresh if source changes.
- Final Linux run: x86_64, Python 3.13.15, UID1000, network disabled, capabilities dropped, no-new-privileges: 64 passed. macOS same suite 64 passed afterward.
- Pull initially failed because Docker configuration selected `docker-credential-desktop` absent from PATH. It worked with `PATH=/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin`. Do not read/print Docker credential configuration.

Re-run retained snapshot:

```bash
docker --context colima run --rm --platform linux/amd64 --user 1000:1000 --network none --cap-drop ALL --security-opt no-new-privileges --entrypoint sh gwen-filesystem-linux-test:local -c 'uname -m && python --version && python -m pytest /tmp/test_workspace_files.py -q -p no:cacheprovider'
```

Security operational note: a broad `ps -axo pid,ppid,comm` unexpectedly surfaced unrelated process titles containing credential-bearing CLI arguments in tool output (macOS process titles can override comm). Do not repeat broad process dumps or copy that output. No credential values are included here. If auditing that exposure, coordinate any rotation with the user, do not independently revoke credentials.

## Verification timeline and honest evidence

- Initial baseline: route typegen, tsc, lint passed; 328 frontend tests; 333 Python tests.
- Foundation increment grew full Python suite through 500, 558 passing.
- Latest full backend run after filesystem review fixes: `.venv/bin/python -m pytest tests -q` from orchestrator/: **634 passed, 18 warnings** (~23s).
- Last full frontend gate: **16 files / 328 tests passed**; `npx tsc --noEmit`, `pnpm lint`, `git diff --check` passed.
- Then Docker/Linux run exposed only timestamp-dependent test fixture; fixed fixture, Linux/amd64 **64 filesystem tests passed**; macOS **64 filesystem tests passed**. Full suite was NOT rerun after this final fixture-only change or subsequent concurrent graph edits.
- Warnings: installed Strands tool-loader deprecations; post-summary OTel export retries to telemetry test's `collector.example` fixture, non-failing. Do not treat these as production endpoint failures.
- All tests use synthetic local fixtures; no actual cloud credentials, provider inference, VNC server, or GCS persistence verified.
- Linux run covers filesystem suite ONLY, not full backend, replay, browser/desktop, or runtime isolation gates.

Standard verification commands:

```bash
pnpm exec next typegen
npx tsc --noEmit
pnpm lint
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**'
# workdir=orchestrator, not shell cd chains:
.venv/bin/python -m pytest tests -q
```

## Current dirty tree and ownership

Our source edits: `.gitignore`, new `.dockerignore`, `.gitattributes`, desktop/filesystem constants appended to `orchestrator/config.py`, new workspace_state.py, desktop_observation.py, workspace_files.py, their three test suites, test_desktop_native_contracts.py, test_desktop_release.py, scripts/validate_desktop_release.py, and this handoff.

Other/pre-existing work to PRESERVE: AGENTS.md, orchestrator/AGENTS.md, model picker/popover/test, package.json/pnpm-lock.yaml, agent.json, run_worker.py, think_activity.py, workflow.py, test_config.py/test_think_activity.py/test_workflow.py, graph_activity.py/skills_config.py/test_graph_activity.py, untracked graph_tool.py/test_graph_tool_adherence.py, compute.json, scripts/gce-startup.sh/scripts/start-all.sh, plans/.omo state. Runtime readiness and tracked pycache/typegen artifacts also dirty. Do not blanket stage/revert/clean anything.

Immediately before handoff, NEW graph changes were present relative to the last full test run. Treat current code as fresher than stale architecture inventory. This session did not inspect or modify protected graph_tool.py. Never claim the whole tree is solely these desktop changes.

Supplied key basename was untracked and had no matching reachable filename history when checked at start; exact exclusion was then added. This is only a filename check, not proof against prior renamed/content exposure. Credential file never read by this session.

## Remaining implementation and suggested next increment

Most of the full specification remains. The large tests count is foundation evidence, not a working product.

1. Build authenticated trusted project/session registration and workspace service integration. Resolve roots server-side; role/session revocation and CSRF/Origin policy before routes; tie mutations to controller journal and quotas. Keep service-side filesystem functions out of worker-host tool registry.
2. Build pinned desktop/runtime assets and secure deployment foundation: immutable build artifacts, mount checks, least-privilege identities, auth gateway, network/metadata denial, bounded services. Do not reuse current startup script as production-safe desktop rollout.
3. Implement native TigerVNC/XFCE/noVNC transport and all input/capture operations. PROVE native server view-only, credential rotation plus active disconnect, clipboard/resize denial, forged input rejection. If native controls fail, feature stays disabled; ask before design amendment/custom proxy.
4. Connect official Temporal/Strands activities with trusted server context, finite retry/cancellation, sequential executor and per-call fencing. Keep Think. Audit load_tool/MCP/skills/subagents/provider-native connectors for unscoped access without silently dropping required behavior.
5. Trusted artifact catalog/GCS generation/hash validation, then hydrate only authentic completed observation descriptors into images in existing PerplexityModel.stream (activity-side). Preserve function call/output ordering and existing tool envelope. No blind actions on missing vision/capability.
6. UI: extend existing AgentChat/ComputerUsePreview/ProjectIdePanel/AgentActivity with native AI Elements composition and direct noVNC RFB. Tool names need explicit preview/activity grouping updates; existing computer-use names are browser/Gemini-specific. Preserve browser-only behavior and independent comparison histories.
7. Async jobs/output/project filesystem implementation beyond create-only: bounded supervised process identity/cancellation, authoritative file state and actual stdout instead of reconstructed transcript. Separate workspace output channel from Temporal summaries. Handle overwrite safely under actual concurrency policy.
8. Independently pinned OpenCode 1.18.27 provider V3 package (AI SDK6 ABI, not Gwen AI SDK7), path-preserving credential gateway with response ownership/budgets, disabled sharing/autoupdate, managed policy, live model discovery. No OpenAI wire emulation gateway or fork.
9. GCS artifacts/checkpoints/memory + LanceDB index/tombstones/repair/export. Restore tests and native Temporal replay fixtures, patched behavior, cancellation/approvals/continue-as-new/stream-gap recovery. Cleanup only after verified restore.
10. Provision isolated GCE pilot only after approval/input/security/compatibility gates, then full E2E and restore. No production cutover authorized.

Useful existing code pointers from planning: components/v0/computer-use.ts (ComputerUsePreview, unwrapToolOutput, computerUsePreview), computer-use-preview.tsx (browser CDP reload must not remain for desktop), public/computer-use-live.html (browser-only), agent-chat.tsx (preview derivation/mounts), agent-activity.tsx (tool grouping/evidence), project-ide.ts/project-ide-panel.tsx (tool-output-derived file state and terminal transcript). Existing server SSE must cancel the consumer, never workflow update, and never wait for stream exhaustion.

Native UI reminders: WebPreviewBody always creates iframe; mount RFB into a div inside native WebPreview instead. Terminal is output-only, not PTY. Final Markdown goes in MessageResponse inside AgentContent, not AgentOutput. Preserve native navigation/scrolling/animations and blue/purple glass classes. Base UI uses render, not Radix asChild. noVNC root ESM export/types still need real compatibility validation.

## Prior internal subagents (optional transcript fallback)

No agents remain needed/running for this work. If exact test/review rationale is missing from files, these sessions were used:

- `ses_f7eb1e8f5ffeh9pRqBnOPlipGm`: native contract tests, 40 final.
- `ses_f7eb18d98ffelkzvKHgyOjdtbg`: release validator + read-race hardening, 135 tests.
- `ses_f7eab33d0ffeJ21y4P9SkUFILY`: workspace state review and recovery fixes.
- `ses_f7d6ec977ffeTaEJ1ue3xttCvV`: filesystem review; ALL high/medium findings acted on, regression coverage now 64 tests.
- `ses_f7eb1e8f3ffe5WS5ki9FL5mxx2`: initial baseline verification.

Use current source first; do not spend another turn reconstructing research already captured above.
