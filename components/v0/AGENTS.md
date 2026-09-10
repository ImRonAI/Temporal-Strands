# Repository Guidelines

## Desktop scope

Read root `AGENTS.md` and `orchestrator/desktop/AGENTS.md` before browser/computer-use changes. The latter owns the full acceptance sequence and known gaps. Apply these rules without refactoring unrelated UI. Relevant files: `computer-use-activity.tsx`, `computer-use-preview.tsx`, `computer-use.ts`, `agent-activity.tsx`, and `agent-chat.tsx`.

## Native AI Elements contracts

Compose exports from `components/ai-elements/` directly. Do not edit vendored primitives, duplicate their animations/scrolling, create renamed facade components, weaken types with casts/`any`, or introduce a replacement event protocol. A feature component that composes these primitives is necessary application UI, not permission to replace them.

Check official documentation against installed source before editing:

- Task: https://elements.ai-sdk.dev/components/task
- ChainOfThought: https://elements.ai-sdk.dev/components/chain-of-thought
- WebPreview: https://elements.ai-sdk.dev/components/web-preview
- Tool: https://elements.ai-sdk.dev/components/tool
- PromptInput: https://elements.ai-sdk.dev/components/prompt-input

Exact contracts: `Task` accepts native Collapsible props; `TaskTrigger` requires string `title`; `TaskContent` uses CollapsibleContent props; `TaskItem`/`TaskItemFile` accept div props. Do not invent `Task.status` or progress APIs. `ChainOfThoughtStep.status` is only `complete | active | pending`; errors/denials use native Tool states and explicit labels. `WebPreviewBody` accepts iframe props. Documentation demos are examples, not authorization for mock-generated workflow events or dependency changes. If docs differ from installed types, reconcile explicitly rather than casting around them.

## Required behavior

- The dedicated browser/computer-use component uses `Task`, `TaskTrigger`, `TaskContent`, `TaskItem` and nested native `ChainOfThought*`, `ToolInput`, `ToolOutput`, `MessageResponse`, and `ChainOfThoughtImage` as appropriate. Preserve chronological emitted updates, Think output, screenshots, and every agent click/scroll/key/tool call with its real arguments/result. Human VNC input is not fabricated as agent tool calls.
- Keep native `UIMessage`/`DynamicToolUIPart` states and call IDs. Do not split a loop on interleaved text, duplicate progress outside it, collapse failures into success, or invent hidden reasoning. An interrupted stream with no final result is not completion.
- WebPreview must iframe native noVNC displaying the headed Linux desktop and browser chrome. Screenshot galleries, CDP image players, and directly iframing the visited page fail acceptance. Viewer connection and chat-stream status are separate facts.
- Take Control becomes interactive only after server acknowledgment. Relinquish awaits native input revocation before showing the native PromptInput steering form. Preserve feedback on error; await successful resume under the same session. Client flags never substitute for server input fencing. Handle reconnect without silently resetting ownership.

## Verification

Run `npx tsc --noEmit`, `pnpm lint`, and scoped vitest from root as specified in the desktop contract. Use the existing `computer-use.test.ts`, `computer-use-preview.test.tsx`, `agent-activity-computer.test.tsx`, `agent-chat.test.tsx`, and `app/api/orchestrator/handoff/route.test.ts` for targeted regressions; update mocks to the real contracts, not vice versa. SSR tests cannot prove clicks, noVNC keyboard input, or async handoff transitions. Complete the desktop contract's real Perplexity UI sequence before claiming success.
