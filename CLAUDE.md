# v0-clone-blurple

A v0.dev-style AI product — a full-screen chat UI where a user describes an interface in plain language and streams back an AI response (with a live "chain of thought" trace) that would ship as production-ready React. Dark, "blurple" glass aesthetic (OKLCH color tokens, serif display headline, animated background glow).

## Tech Stack

- **Next.js 16** (App Router), **React 19**, **TypeScript**
- **AI SDK v6** (`ai`) + **`@ai-sdk/react`** (`useChat`) — the client-side chat state machine and UI Message Stream protocol. `useChat`/`DefaultChatTransport` drive `app/page.tsx` against `/api/orchestrator`; the *server* side of that route builds its own `createUIMessageStream` and writes `text-start` / `text-delta` / `text-end` / `reasoning-*` / `tool-*` parts by hand from the orchestrator's SSE stream rather than using `streamText`/an AI Gateway model string.
- **Perplexity Agent API** (`https://api.perplexity.ai/v1`) — the actual model backend for this app. OpenAI Responses-API-compatible, called via the official `openai` npm package with `baseURL` overridden (see `lib/perplexity.ts`). Requires `PERPLEXITY_API_KEY` in `.env.local`. Model discovery is **fully dynamic** — `GET /v1/models` is unauthenticated and is what powers every model picker in the app; no model list is hardcoded in the frontend.
- **AI Elements** (`components/ai-elements/*`) — vendored (copied-in, shadcn-style) chat UI primitives built on the AI SDK. See the **hard rule** below — this is the most important thing in this file.
- **shadcn/ui** (`components/ui/*`) — the primitive layer AI Elements is built on (Button, InputGroup, Select, Command, HoverCard, DropdownMenu, Collapsible, ScrollArea, Tooltip, Badge, ButtonGroup...). Note: this project's `Button` and `Select` are `@base-ui/react` primitives, not Radix — `Button` takes a `render={<Element/>}` prop instead of `asChild`, and `Select.Root` is headless (no `className`).
- **Streamdown** (+ `@streamdown/cjk`, `@streamdown/code`, `@streamdown/math`, `@streamdown/mermaid`) — the markdown/streaming renderer that powers `MessageResponse`.
- **Strands Agents + Temporal** (`orchestrator/`, Python) — the main orchestrator: a durable Strands Agent workflow using Perplexity's Agent API as its model backend, built on the integration's public `TemporalAgent` API. It **is** the default request path: `app/page.tsx` → `/api/orchestrator` → the FastAPI bridge → `ChatWorkflow`. See [Orchestrator](#orchestrator-python-strands--temporal) below.
- **Tailwind CSS v4**, `tw-animate-css`, `class-variance-authority`, `tailwind-merge`.
- Package manager: **pnpm** (`pnpm-lock.yaml` is present — always use pnpm, not npm/yarn).

## Project Structure

```
app/
  page.tsx              — the single chat screen (empty state + conversation view)
  compare/page.tsx      — up-to-4-model side-by-side comparison view
  api/orchestrator/route.ts          — POST: starts/reuses a durable orchestrator session and
                            converts its SSE stream into AI SDK UI-message parts
  api/orchestrator/end/route.ts      — POST: ends a session (sendBeacon target on pagehide)
  api/orchestrator/approval/route.ts — GET/POST: the human-in-the-loop query + signal
  api/models/route.ts    — GET proxy for Perplexity's `GET /v1/models` (cached 5 min)
  api/compare/route.ts   — POST: fans a prompt out to up to 4 models in parallel, multiplexed
                            over one SSE stream tagged per-model
  layout.tsx              — fonts, TooltipProvider, Analytics
  globals.css            — Tailwind v4 theme tokens, OKLCH palette, custom keyframes
lib/perplexity.ts        — Perplexity Agent API client (openai SDK + baseURL override),
                            DEFAULT_MODEL, DEFAULT_MAX_OUTPUT_TOKENS, listPerplexityModels()
orchestrator/             — Python: Strands Agent + Temporal durable workflow (see below)
components/
  ai-elements/           — AI Elements primitives (see hard rule below). DO NOT treat as
                            app-owned code to freely refactor.
    conversation.tsx, message.tsx, prompt-input.tsx, attachments.tsx,
    chain-of-thought.tsx, image.tsx, suggestion.tsx, agent.tsx,
    artifact.tsx, code-block.tsx, sandbox.tsx, shimmer.tsx, task.tsx,
    tool.tsx, web-preview.tsx
    (reasoning.tsx is deliberately NOT vendored: ChainOfThought is this
    app's one and only reasoning/thinking surface — do not add it back)
  ui/                     — shadcn/ui primitives that ai-elements composes over
  v0/                     — app-specific components that COMPOSE ai-elements
    composer.tsx          — wraps PromptInput + Attachments into the chat input bar
    agent-activity.tsx    — wraps ChainOfThought into the reasoning-trace UI
                            (thinking cycles, native reasoning, tool calls,
                            sub-agent cards) for assistant messages
    model-picker.tsx       — dynamic, provider-grouped, searchable model picker
                            (composes ai-elements PromptInputCommand* in a Popover)
    use-models.ts          — client hook: fetches /api/models, exposes {models, status}
    compare-view.tsx       — the up-to-4-model comparison UI (composes Message/
                            MessageContent/MessageResponse + PromptInput per pane)
    blurple-background.tsx, site-header.tsx
lib/utils.ts              — `cn()` (clsx + tailwind-merge)
```

`app/page.tsx` is the only place `useChat` is called. It uses `DefaultChatTransport({ api: "/api/orchestrator" })` and renders either the empty-state hero (with `Suggestions`) or the `Conversation` timeline (with `Message` / `MessageContent` / `MessageResponse`), plus `Composer` pinned to the bottom.

`app/api/orchestrator/route.ts` converts the orchestrator's three SSE topics into UI-message parts: model `events` become `text-*` / `reasoning-*` / `tool-input-*` parts, `thinking` cycles become custom `data-thinking-cycle` parts, and `tool_results` become `tool-output-available` / `tool-output-error`. `components/v0/agent-activity.tsx` is what renders all of it as `ChainOfThought` steps. There is **no simulated chain of thought** anywhere — every step shown comes from a real event the agent emitted.

---

## ⚠️ HARD RULE — AI Elements Native Behavior Is Not Optional

**Every frontend surface in this project that uses an AI Elements component (`components/ai-elements/*`) MUST use that component's native subcomponents, props, and built-in animations exactly as designed. Do not strip out, reimplement, bypass, or hand-roll behavior that an AI Elements component already provides natively.**

This is a hard implementation requirement, not a style preference. Concretely:

- **Never reimplement scroll-to-bottom, streaming markdown rendering, collapsible/expand animations, branch navigation, attachment previews, or submit-state icon logic from scratch.** These are native, tested behaviors already wired into the components below (via `use-stick-to-bottom`, `streamdown`, Radix `Collapsible`, etc.). Reimplementing them with raw `useState`/`useEffect`/manual DOM scroll code is a correctness and maintenance regression — use the component.
- **Never delete or short-circuit the built-in animation classes/keyframes** these components ship with (`fade-in-0 slide-in-from-top-2 animate-in` on `ChainOfThoughtStep`, `data-[state=open]:animate-in` / `data-[state=closed]:animate-out` on `ChainOfThoughtContent`, the `StickToBottom` `initial="smooth" resize="smooth"` behavior on `Conversation`, `ConversationScrollButton`'s auto-hide-when-at-bottom logic, etc.). If a design needs different motion, change it through the documented className/props surface, not by ripping out the mechanism.
- **Extend only via the documented customization points**: `className` (merged with `cn()`, so Tailwind utilities layer on top safely), the component's typed props, composition (nesting/reordering the exported subcomponents), and context hooks (`usePromptInputAttachments`, `useProviderAttachments`, etc.). Do not reach into internals, do not copy-paste a component's JSX into `components/v0/*` and modify it there instead of using the real export.
- **If a new AI Elements component is needed**, source it the same way the existing ones were (AI Elements' shadcn-style registry / `elements.ai-sdk.dev`), keep it in `components/ai-elements/`, and document it in this file the same way — don't build a bespoke equivalent in `components/v0/` or `components/ui/`.
- **`components/v0/*` files exist to compose and skin AI Elements, never to replace them.** `composer.tsx` and `agent-activity.tsx` are the reference pattern: they import the real `PromptInput*`/`ChainOfThought*` subcomponents and only add app-specific layout, copy, and Tailwind classes around them.

If you think a native behavior is wrong for this product, that's a discussion to raise — not a license to quietly work around it in application code.

---

## AI Elements Component Reference (as used in this project)

Fifteen AI Elements components are vendored under `components/ai-elements/` (the seven below in detail, plus `agent`, `artifact`, `code-block`, `sandbox`, `shimmer`, `task`, `tool`, `web-preview` used/available for the agent-activity trace UI). `reasoning.tsx` is deliberately absent — ChainOfThought is this app's only reasoning surface. For each: what it's for, its exported subcomponents, and the native features that must be preserved.

### `conversation.tsx` — message list / auto-scroll

Wraps [`use-stick-to-bottom`](https://www.npmjs.com/package/use-stick-to-bottom).

- **`<Conversation>`** — root; sets `role="log"`, `initial="smooth"`, `resize="smooth"`. **Native feature: automatically sticks to and smooth-scrolls to the bottom as new content streams in**, and stops auto-following if the user scrolls up.
- **`<ConversationContent>`** — flex column wrapper for messages (`gap-8 p-4` by default).
- **`<ConversationEmptyState>`** — placeholder (`title`/`description`/`icon`) for zero messages. (Not currently used — the app builds its own hero instead — but available.)
- **`<ConversationScrollButton>`** — floating "scroll to bottom" button. **Native feature: uses `useStickToBottomContext().isAtBottom` to auto-hide/show itself** — never manually toggle its visibility.
- **`<ConversationDownload>`** + **`messagesToMarkdown()`** — exports the transcript as a `.md` file client-side (blob URL). Not currently wired in `page.tsx`; use it as-is (with an optional `formatMessage`) if a "download conversation" affordance is added rather than writing a new exporter.

Used in `app/page.tsx`: `Conversation` → `ConversationContent` (maps `messages`) → `ConversationScrollButton`.

### `message.tsx` — message bubbles, actions, branching, markdown response

- **`<Message from={role}>`** — root; **native feature: automatically right-aligns/styles user messages vs. full-width assistant messages** via the `is-user`/`is-assistant` group classes — don't hand-roll alignment logic per message.
- **`<MessageContent>`** — bubble body; user variant gets the secondary-background pill styling via `group-[.is-user]:*` classes automatically.
- **`<MessageActions>` / `<MessageAction>`** — action-button row (retry/copy/like/etc.) with built-in `Tooltip` wiring when a `tooltip` prop is passed. Not yet used in `page.tsx`; when adding message actions, use these rather than bare `<Button>`s.
- **`<MessageBranch>` / `<MessageBranchContent>` / `<MessageBranchSelector>` / `<MessageBranchPrevious>` / `<MessageBranchNext>` / `<MessageBranchPage>`** — **native feature: full response-branching state machine** (current branch index, prev/next, "N of M" label, auto-hides the selector when there's only one branch) driven by React context (`MessageBranchContext`). If multi-response branching is added, use this system — do not build parallel branch-switching state in `components/v0/`.
- **`<MessageResponse>`** — the markdown renderer, a thin `memo`-wrapped `Streamdown` with `cjk`/`code`/`math`/`mermaid` plugins pre-registered. **Native features: GFM, math (KaTeX), Mermaid diagrams, syntax-highlighted code blocks, and "smart" incomplete-markdown parsing during streaming** (safe partial-markdown rendering while tokens are still arriving). Never swap this for a plain `<div>{text}</div>` or a different markdown library for assistant output.
- **`<MessageToolbar>`** — flex row (`justify-between`) for pairing actions with a branch selector under a message.

Used in `app/page.tsx`: `Message` → `MessageContent` → `MessageResponse` per text part.

### `prompt-input.tsx` — the composer (largest component; many subcomponents)

- **`<PromptInput onSubmit accept multiple globalDrop maxFiles maxFileSize onError>`** — root `<form>`. **Native features: drag-and-drop file handling (scoped or `globalDrop` document-wide), file-count/size validation with an `onError` callback, and automatic form reset + attachment clearing after a successful submit** (with retry-safe "don't clear on error" behavior). It supports two state modes — self-managed, or lifted via `<PromptInputProvider>` — do not add separate `useState` for text/attachments alongside it; use its context (`usePromptInputAttachments`, or `usePromptInputController` under a provider).
- **`<PromptInputBody>`**, **`<PromptInputHeader>`**, **`<PromptInputFooter>`**, **`<PromptInputTools>`** — layout slots (header = attachment previews area, footer = toolbar + submit).
- **`<PromptInputTextarea>`** — **native features: auto-resizing textarea (`field-sizing-content`), Enter-to-submit / Shift+Enter-for-newline, IME composition-safe Enter handling, paste-to-attach for pasted images/files, and Backspace-to-remove-last-attachment when the field is empty.** Do not attach a competing `onKeyDown` submit handler — pass through this component's `onKeyDown` prop if you need to add behavior; it already chains yours before its own.
- **`<PromptInputActionMenu>` / `Trigger` / `Content`** + **`<PromptInputActionAddAttachments>`** / **`<PromptInputActionAddScreenshot>`** — the "+" dropdown for adding files or a live screen-capture screenshot (via `getDisplayMedia`). Reuse these actions rather than writing new file-picker/screenshot code.
- **`<PromptInputButton>`** — toolbar button with optional `tooltip` (string or `{content, shortcut, side}`).
- **`<PromptInputSubmit status onStop>`** — **native feature: automatically swaps its icon/aria-label based on chat `status`** (idle → send arrow, `submitted` → spinner, `streaming` → stop square that calls `onStop`, `error` → X). Never hardcode a static submit icon once `status` is available — pass it through.
- **`<PromptInputSelect>` family** — thin wrapper over the shadcn `Select`. (Available; the model picker uses the `PromptInputCommand` family instead, for search over a 35-model catalog.)
- **`<PromptInputHoverCard>` family**, **`<PromptInputTabsList>`/`Tab`/`TabLabel`/`TabBody`/`TabItem`**, **`<PromptInputCommand>` family** — advanced composition primitives (referenced-source hover previews, tabbed menus, command palettes) available for richer composer UIs; not all are wired into `composer.tsx` today, but if similar UI is needed, use these instead of building bespoke popovers/menus.
- Hooks: **`usePromptInputAttachments`** (files/add/remove/clear/openFileDialog), **`usePromptInputController`** + **`PromptInputProvider`** (lift text+attachment state above the form), **`usePromptInputReferencedSources`**.

Used in `components/v0/composer.tsx`, which is the canonical example of composing this component correctly — follow its pattern for any new composer variant.

### `attachments.tsx` — file/source-document chips

- **`<Attachments variant="grid" | "inline" | "list">`** — layout container; sets context so children know their variant.
- **`<Attachment data onRemove>`** — item wrapper; **native feature: automatic media-category detection** (`getMediaCategory`) driving preview/icon choice.
- **`<AttachmentPreview fallbackIcon>`** — renders image/video inline or a category icon (image, video, audio, document, source, unknown).
- **`<AttachmentInfo showMediaType>`** — filename/media-type label (hidden in `grid` variant by design — don't force it to show there).
- **`<AttachmentRemove label>`** — **native feature: hover-reveal remove button** (`opacity-0` → `group-hover:opacity-100`) — keep it inside the same hover group (`Attachment`'s `group` class), don't move it outside.
- **`<AttachmentHoverCard>` / `Trigger` / `Content`** — hover preview wrapper (`openDelay`/`closeDelay` default to `0`).
- **`<AttachmentEmpty>`** — empty state.
- Utilities: **`getMediaCategory()`**, **`getAttachmentLabel()`**.

Used in `components/v0/composer.tsx` (`variant="inline"`, inside `PromptInputHeader`) to show in-flight attachments before send.

### `chain-of-thought.tsx` — collapsible reasoning trace

- **`<ChainOfThought open defaultOpen onOpenChange>`** — root; controllable-or-uncontrolled open state via `@radix-ui/react-use-controllable-state`.
- **`<ChainOfThoughtHeader>`** — **native feature: Radix `Collapsible` trigger with an animated chevron** (`rotate-180` when open) — don't replace with a manual `onClick`/`useState` toggle.
- **`<ChainOfThoughtStep icon label description status>`** — a single step; **native feature: entrance animation** (`fade-in-0 slide-in-from-top-2 animate-in`) and status-driven dimming (`active` full-opacity, `complete` muted, `pending` more muted) — don't override step opacity/animation with ad hoc classes that fight these.
- **`<ChainOfThoughtSearchResults>` / `<ChainOfThoughtSearchResult>`** — badge row for cited sources.
- **`<ChainOfThoughtContent>`** — **native feature: Radix `Collapsible` content with enter/exit animation** (`data-[state=open]:animate-in` / `data-[state=closed]:animate-out`, slide+fade) driven by the same open state as the header.
- **`<ChainOfThoughtImage caption>`** — bounded-height image slot with optional caption.

Used in `components/v0/agent-activity.tsx`, which is the canonical composition example (real steps come from the model's own reasoning events, the `think` tool's per-cycle `data-thinking-cycle` parts, and dynamic tool parts — all streamed by `app/api/orchestrator/route.ts`). When adding new step kinds, compose more `ChainOfThoughtStep`s in `agent-activity.tsx` — don't fork the component.

### `image.tsx` — AI-generated image renderer

- **`<Image {...Experimental_GeneratedImage} alt className>`** — **native feature: builds the `data:${mediaType};base64,...` URL automatically** from the AI SDK's `experimental_generateImage` output, with responsive `max-w-full h-auto` styling baked in. Not currently used in this app (no image-generation route yet), but if one is added, render results through this component rather than hand-building a `data:` URL / `<img>`.

### `suggestion.tsx` — prompt chips

- **`<Suggestions>`** — horizontal scroll container (shadcn `ScrollArea`, horizontal `ScrollBar` hidden by default here).
- **`<Suggestion suggestion onClick>`** — pill button; fires `onClick(suggestion)`; renders `suggestion` as its label unless `children` is passed.

Used in `app/page.tsx`'s empty-state hero to seed `sendMessage`.

---

## Perplexity Agent API Integration

`lib/perplexity.ts` is the single source of truth for talking to Perplexity:

- **`perplexityClient()`** — returns an `openai` npm SDK client with `baseURL: "https://api.perplexity.ai/v1"` and `apiKey: process.env.PERPLEXITY_API_KEY`. This is Perplexity's documented OpenAI-compatibility pattern (`client.responses.create(...)` → `POST /v1/agent`, aliased as `/v1/responses`) — do not hand-roll `fetch` calls against the Agent API when the `openai` SDK already covers it.
- **`listPerplexityModels()`** — calls the *unauthenticated* `GET /v1/models` (no API key needed) with a 5-minute `revalidate`. This is the only source of model IDs anywhere in the app — **never hardcode a model list**. `app/api/models/route.ts` is a thin proxy over this for the client.
- **`DEFAULT_MODEL`** (`"openai/gpt-5.6-sol"`) — the pre-fetch placeholder used before a model picker's live `GET /v1/models` call resolves, and the orchestrator's fallback. It's a real, currently-listed model (see `docs/agent-api/models`), not a guess — if Perplexity's catalog changes, update this constant.
- **`DEFAULT_MAX_OUTPUT_TOKENS`** — Anthropic models on the Agent API return HTTP 400 without `max_output_tokens` set; every server-side call in this app sets it unconditionally (harmless for other providers) rather than special-casing `anthropic/*`.

### Model picker (`components/v0/model-picker.tsx` + `use-models.ts`)

`useModels()` fetches `/api/models` client-side once on mount and exposes `{ models, status }`. `<ModelPicker value onValueChange />` composes AI Elements' `PromptInputCommand` family (inside a shadcn `Popover`) to give the 35-model catalog a searchable, provider-grouped list — composition over the vendored primitives, not a bespoke dropdown, so it satisfies the AI Elements hard rule above. **Both the main composer (`composer.tsx`) and the comparison view use this same component** — there is exactly one model-picker implementation in the app.

A chat session's model is fixed once the session starts (`TemporalModel.update_config` is a documented no-op), so `app/page.tsx` treats picking a different model as starting a new conversation: it ends the current session and clears `messages`. The picker never points at a model the running session isn't actually using.

### Comparison view (`app/compare/page.tsx` + `components/v0/compare-view.tsx`)

Sends one prompt to up to 4 independently-selected models (min 2, max 4) and streams all of them side by side:

- `app/api/compare/route.ts` — `POST { prompt, models[] }`. Runs up to 4 `client.responses.create({ stream: true })` calls in parallel and multiplexes their `response.output_text.delta` events onto **one** SSE stream, each event tagged `{ model, type, ... }`. This is a hand-rolled protocol (not the AI SDK's UI Message Stream) because it needs to interleave N independent streams under one HTTP response — `compare-view.tsx`'s client-side reader demuxes by `event.model` into per-pane React state.
- Each pane renders through the same `Message` / `MessageContent` / `MessageResponse` AI Elements components used on the main chat page — do not introduce a second markdown renderer or a plain `<div>` for pane output.
- The pane grid, add/remove-model controls, and shared bottom composer are `compare-view.tsx`-owned layout; the composer there is a minimal `PromptInput` (text only, no attachments) — if attachments are ever needed per-comparison, reuse `PromptInputProvider`/`usePromptInputAttachments`, don't hand-roll file state.

### `app/api/orchestrator/route.ts`

The one chat route. It starts (or reuses) a durable orchestrator session, then converts the bridge's SSE stream into the AI SDK's UI Message Stream protocol via `writer.write(...)` inside `createUIMessageStream`'s `execute()` — the officially documented low-level way to feed `useChat` from a non-AI-SDK model call, not a workaround. Every block it opens (`text-start`, `reasoning-start`) is closed exactly once in a `finally`, on both the success and error paths, so a mid-turn failure cannot leave a duplicated or permanently-pending block on the client.

Image attachments ride along with the prompt: the route splits each `file` part's data URL into the `{format, data}` pair `ChatWorkflow.turn` accepts, and the composer's file picker is restricted to the formats the Agent API's `input_image` part actually accepts (`png`/`jpeg`/`gif`/`webp`) rather than accepting files that would be silently discarded downstream.

There is deliberately **no** second, direct-to-Perplexity chat route. One previously existed and streamed a hardcoded, `sleep()`-driven fake chain of thought (invented search sources and a static wireframe PNG) before calling the model; nothing pointed at it, and it has been deleted. Do not reintroduce a simulated trace — `agent-activity.tsx` renders only real agent events.

## Orchestrator (Python, Strands + Temporal)

`orchestrator/` is **the main orchestrator** referenced in this project's brief: a [Strands Agent](https://strandsagents.com/) running as a durable [Temporal](https://temporal.io/) workflow, built to **Temporal's official Strands Agents integration guide** — https://docs.temporal.io/develop/python/integrations/strands-agents — followed exactly. (The `/Applications/strands-temporal` local skill guide describes a different, non-working architecture — passing a live `Model` object to `TemporalAgent(model=...)` inside `__init__` — that fails at runtime with `PydanticSerializationError` because Temporal must serialize activity inputs and a live `Model` instance isn't serializable. Don't follow that guide for this integration; the official docs above are correct and this code matches them, verified by reading the installed `temporalio.contrib.strands` source and running it end-to-end against a live worker.)

- **`run_worker.py`** fetches Perplexity's live `GET /v1/models` once at startup (a plain `httpx` GET — the unauthenticated endpoint isn't wrapped by Perplexity's own SDK) and builds `models: dict[str, Callable[[], Model]]` — one `PerplexityModel` factory per model id — passed to `StrandsPlugin(models=...)`. This is the officially documented model-registration mechanism, just populated dynamically instead of hand-typed.
- **`workflow.py`**'s `ChatWorkflow` is the official docs' "Handle long-running chat sessions" pattern, verbatim in structure: `@workflow.update turn(prompt)` handles one message and returns the reply from the same call; `@workflow.signal end_chat` ends the session; `@workflow.query messages` inspects history. `TemporalAgent` is built once, inside `@workflow.run`, from `ChatInput.model_id` — a **name** the Worker resolves against its factory dict, never a live object (that's what makes dynamic model selection possible at all; passing the live object directly, as an earlier version did, fails with `PydanticSerializationError`). Model is chosen once per session — there's no supported way to change a `TemporalAgent`'s model mid-session (`TemporalModel.update_config()` is an explicit no-op in the SDK source).
- **Long horizon, no context loss**: one workflow execution *is* the session, for as long as it runs. When Temporal's own `workflow.info().is_continue_as_new_suggested()` signals that history is getting large, the workflow drains in-flight updates (`workflow.all_handlers_finished`) and calls `continue_as_new`, carrying `agent.messages` forward into a fresh execution — conversation memory survives indefinitely; only workflow *history* resets. There is no length cap on a session.
- **No hanging**: every model-call activity gets an explicit `RetryPolicy` (6 attempts, exponential backoff capped at 30s — rides out rate limits/blips without retrying forever), `heartbeat_timeout=30s` (this specifically *activates* the SDK's built-in `auto_heartbeater` on the model activities — confirmed in `_model_activity.py` — without it, no heartbeating happens and a dead worker isn't detected until the full timeout), `start_to_close_timeout=10min` per attempt, and `schedule_to_close_timeout=30min` as a hard ceiling across all retries, so a truly-broken upstream still surfaces as an error within a bounded time. These are real, tunable constants at the top of `workflow.py`, not silent defaults. On top of that, every `invoke_async` passes Strands' own `limits={"turns": 100}` (`TURN_LIMITS` in `workflow.py`) — 100 being the same ceiling the Agent API allows for `max_steps`, so the agent gets the platform maximum rather than a client-chosen budget. It is purely a runaway backstop: live testing once found a malformed tool call making the agent repeat the identical mistake 315+ times in one turn, and Temporal's timeouts don't catch that because every individual model call succeeds. `Limits` is the framework-native mechanism (`strands/types/agent.py`), checked at loop boundaries so the turn ends with `stop_reason="limit_turns"` rather than raising; an earlier hand-written `BeforeModelCallEvent` hook that did the same job predated it and is gone.
- **`server.py`** — the FastAPI bridge the Next.js app actually talks to: `POST /sessions` (start), `POST /sessions/{id}/turns` (send a message, get the reply), `POST /sessions/{id}/end` (signal `end_chat`).
- **Wired into the real chat UI.** `app/api/orchestrator/route.ts` is what `app/page.tsx` actually calls (`DefaultChatTransport({ api: "/api/orchestrator" })`) — it starts a session on the first message, gets a `session_id` back as a custom `data-session` UI-message part, and the frontend threads that same id through every subsequent turn (`useEffect` in `page.tsx` picks it out of `messages` into React state). It is the only chat route; there is no direct-to-Perplexity fallback.
- **Sessions are ended, not abandoned.** A `ChatWorkflow` execution runs until it is signalled, so `page.tsx` fires `/api/orchestrator/end` from `pagehide` via `navigator.sendBeacon`, and again when the model picker changes. Without this every page load left a workflow running in Temporal forever.
- **Verified live, in the actual product UI, not just the CLI**: sent "My favorite number is 42. Remember it." then, as a second message, "What is my favorite number?" — got "Your favorite number is 42." back. The FastAPI access log confirms one `POST /sessions` followed by two `POST /sessions/{same-id}/turns` — the second turn genuinely reused the same durable session; this isn't the model coincidentally guessing.
- **Running it requires 4 processes**: `temporal server start-dev`, `python run_worker.py`, `uvicorn server:app --port 8787` (all three in `orchestrator/`, venv activated, `PERPLEXITY_API_KEY` exported), then `pnpm run dev` in the app root (or just `pnpm dev:all`, which starts all four). See `orchestrator/README.md` for the exact sequence. If any of the first three aren't running, `/api/orchestrator` reports a clear error in the chat UI rather than hanging.

### Model provider (`orchestrator/perplexity_model.py`)

`PerplexityModel` is a native Strands `Model` subclass built directly on Perplexity's own official SDK (`perplexityai` on PyPI, `import perplexity`), not the generic `openai` package with a `baseURL` override that earlier versions used. Perplexity's own docs are explicit about this: *"We recommend using the Perplexity SDK for the best experience... Use OpenAI SDKs if you're already integrated and need drop-in compatibility."* We weren't. Follows Strands' documented custom-model-provider pattern (subclass `Model`, implement `stream()`/`update_config()`/`get_config()`); request-building is adapted from `OpenAIResponsesModel` since the wire format is the same Responses-API shape either way, but the response side uses Perplexity's own richer typed event stream (`perplexity.types.ResponseStreamChunk`), which exposes real events the OpenAI-compatible endpoint doesn't — notably `response.skill.loaded` (this is what `"skill_loaded": "pplx_sdk"` actually is: a first-class, documented event type in the SDK's OpenAPI-generated types, not an undocumented quirk) and `response.reasoning.*` events, both now surfaced as real chain-of-thought instead of the earlier simulated one.

`DEFAULT_MAX_STEPS = 20` is set explicitly on every request, because the Agent API reference confirms `max_steps` has no documented default when `preset` is also omitted (only bounds, 1-100) — and leaving it unset was the actual root cause of an extended debugging saga (see `_default_native_tools()` below).

### Agent tooling (`orchestrator/perplexity_tools.py` and `run_worker.py`)

- **Default native tools, attached to every turn**: `sandbox` + `finance_search` (`_default_native_tools()` in `run_worker.py`). `sandbox`'s preinstalled SDK already covers `web_search`, `fetch_url`, and `people_search` internally (confirmed in Perplexity's own sandbox docs: *"The container ships with a preinstalled Perplexity SDK, so code the model runs inside the sandbox can call Web Search, Fetch URL Content, and People Search directly"*) — `finance_search` is the one native capability it doesn't cover, so it's declared alongside it.
- **This only works because of the `max_steps` fix above.** The apparent "sandbox conflicts with web_search" bug from earlier testing wasn't a real conflict — Perplexity's own docs show `tools=[{"type": "sandbox"}, {"type": "web_search"}]` as a valid, intended combination. The real cause: the sandbox's internal search path goes through a `load_skill` tool first (`skill_loaded` output item; per its own docstring, *"the skill body itself lives in the function_call_output input item the model consumes on its next turn"* — i.e. loading the skill and using it are two separate steps), and with no `max_steps` set, the model would give up before completing the second step. Verified directly: identical requests with `max_steps=10` or `20` completed correctly every time; the default (unset) failed every time. No `instructions` wording or `tool_choice` setting fixed it without the step budget — that was a real dead end, not a workaround worth keeping.
- **Function tools** (`AGENT_API_FUNCTION_TOOLS` in `perplexity_tools.py`, wired into `TemporalAgent(tools=...)` in `workflow.py`) — the rest of the Agent API as Temporal-activity-backed tools the agent can call on itself: `create_agent_response` (background jobs, skills, presets, wide-research, model fallback chains, one-off native tool selection via `tool_names_json`), `retrieve_agent_response`, `cancel_agent_response`, `list_agent_response_files`, `download_agent_response_file`. These do real HTTP I/O, so per the strands-temporal determinism rules they're `@activity.defn` functions wrapped in `activity_as_tool`, never plain `@tool` functions — and `httpx` is imported locally inside each one, not at module level, because the module is pulled into the workflow-sandboxed `workflow.py` and a module-level `httpx` import trips the sandbox (`RestrictedWorkflowAccessError` on `urllib.request.Request.__mro_entries__`).
- **`skills_json` is a JSON-encoded string, not a nested object/array parameter.** Perplexity's backend rejects function-tool JSON Schemas with `object`-typed properties that don't fully specify nested `properties` — confirmed by direct testing, including an empty `{"type": "object"}` with no declared properties. `create_agent_response` accepts `skills_json: Optional[str]` and does `json.loads()` on it internally rather than declaring `skills` as `list[dict]`.
- **`create_agent_response`'s prompt parameter is named `task`, not `input`**, even though it maps straight through to Perplexity's own `input` request field — naming the Python parameter `input` to match caused the live agent to reliably omit it when calling this tool (a separate, real bug from the max_steps one — fixed by the rename, not by the step budget).
- **The `think` tool** (`orchestrator/think.py` + `thinking_activity.py` + the wrapper in `workflow.py`): `think.py` is the strands-agents-tools `think` tool vendored **verbatim** (byte-identical to `src/strands_tools/think.py` on GitHub) with ONLY async streaming added — `async def` + `asyncio.to_thread` around the unchanged `process_cycle` call, and an `on_cycle` callback that publishes each cycle live to the `"thinking"` WorkflowStream topic (rendered as ChainOfThought steps in `agent-activity.tsx`). It is **agent-callable, never forced**: the model decides when to think and controls depth itself — `cycle_count` (1-10), it writes `system_prompt`/`thinking_system_prompt` with its own prompt engineering, `tools` filters which of its tools the thinking agent may use (`[]` = none), and `model_settings={"params": {"max_output_tokens": N}}` caps per-cycle length. `thinking_activity.py` is only the Temporal glue (activity boundary, stand-in parent agent reconstructed from `model_id`, periodic heartbeat); the workflow-side `@tool` wrapper only crosses the workflow/activity boundary. Do not reintroduce a hook that runs thinking automatically every turn.
- **`TURN_LIMITS`** (`workflow.py`) is Strands' own `Limits({"turns": 100})`, passed to every `invoke_async` — see the "No hanging" bullet above. A runaway backstop at the Agent API's own `max_steps` ceiling, not a budget the agent works within.
- **Not yet implemented**: contextualized embeddings (`POST /v1/contextualizedembeddings`, models `pplx-embed-context-v1-0.6b`/`pplx-embed-context-v1-4b`) for agent memory, and Strands-community tooling (explicitly deferred).

## Environment Variables

- **`PERPLEXITY_API_KEY`** (`.env.local`, gitignored via `.env*.local`) — required by `lib/perplexity.ts` for every server-side Agent API call. Also required by `orchestrator/` (exported in the shell before running `run_worker.py`/`server.py`).
- **`TEMPORAL_ADDRESS`** (optional, orchestrator only) — defaults to `localhost:7233`.
- **`PERPLEXITY_DEFAULT_MODEL`** (optional, orchestrator only) — overrides `DEFAULT_MODEL` for the worker process.

## Styling Conventions

- Theme tokens (colors, radii, fonts) live in `app/globals.css` under `@theme inline` + `:root`/`.dark` — an OKLCH "blurple" palette (`--blurple`, `--blurple-bright`) plus the standard shadcn token set. Reuse `--color-blurple-bright` / `text-blurple-bright` for accent moments rather than introducing new purple hardcoded values.
- `font-editorial` (Instrument Serif, tight tracking) is the display headline utility — defined once in `globals.css`; use it for hero-scale headings instead of redeclaring `font-serif` + letter-spacing inline.
- `animate-blurple-drift` / `animate-blurple-drift-alt` are the slow background-glow keyframes used by `BlurpleBackground`, already `prefers-reduced-motion`-safe. Reuse these for any other ambient drift effect instead of writing new keyframes.
- `cn()` from `lib/utils.ts` (clsx + tailwind-merge) is the standard way every component in this repo (both `ui/` and `ai-elements/`) merges incoming `className` — follow the same pattern in new components: accept `className`, merge last with `cn()`.
