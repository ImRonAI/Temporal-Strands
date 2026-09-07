# Browser Handoff

The browser panel reuses the existing Project IDE design: `Artifact`,
`ArtifactHeader`, `ArtifactActions`, `ArtifactContent`, `ide-glass`,
`ide-glass-edge`, the blurple icon tile, and compact monospace status badge.
No new palette, fonts, or visual component library.

## Interaction

Agent -> Stopping -> Human -> Relinquish -> Instructions -> Resuming -> Agent.
Only Human enables CDP input. A backend acknowledgement, not a local button
toggle or cancelled HTTP stream, grants human control. Close is unavailable
while handing off so the prompt/control state cannot be accidentally discarded.
Failures are visible and never optimistically grant browser control.

Native WebPreview owns the iframe and navigation shell. Native PromptInput
owns the relinquish form and submission. The existing CDP viewer renders
Chrome's screencast frames and forwards documented CDP Input commands only
while explicitly enabled by its same-origin parent.

## Framework Contracts

- https://github.com/strands-agents/tools/blob/main/src/strands_tools/stop.py
- https://github.com/strands-agents/tools/blob/main/src/strands_tools/handoff_to_user.py
- https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md
- https://docs.temporal.io/develop/python/workflows/continue-as-new
- https://docs.temporal.io/develop/python/activities/timeouts
- https://chromedevtools.github.io/devtools-protocol/tot/Input/
- https://chromedevtools.github.io/devtools-protocol/tot/Page/

Stop/cancellation is cooperative. The current action is allowed to settle
before ownership is acknowledged. Continue-as-new retains Workflow ID,
conversation and resume instructions but assigns a new Run ID. Live browsers
remain worker-owned; this does not promise browser recovery after worker loss.
The user's instructions describe what changed; no screenshot or browsing
history capture is introduced by the handoff.

## Verification

- Focused Python suites: 66 passing, including real Temporal execution and
  identical native Chromium TargetID across relinquish/continue-as-new.
- Scoped frontend suites: 65 passing; TypeScript check and scoped ESLint pass.
- Live application test with a deterministic chat/handoff HTTP fixture and
  real Chromium/CDP: takeover, click/type into the remote page, relinquish,
  instruction submission, and return to agent mode pass. This is separate
  from the real Temporal test, not one full live-provider end-to-end test.
- Browser measurements: no document horizontal overflow at 375px and 768px;
  no page errors in the tested flow.
- Screenshot aesthetic review remains unverified: the available model and
  independent reviewer cannot consume image input. Screenshots were captured
  but are not claimed as visually approved.

Operational scope is the existing local worker. Continue-as-new keeps the
browser alive on that worker; worker loss or routing subsequent actions to a
different worker is not browser-session recovery.
