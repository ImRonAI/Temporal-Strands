# Coding Agent Product Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inherited app-generator identity with a neutral, repository-scoped Coding Agent identity without changing chat, orchestration, model, or streaming behavior.

**Architecture:** Keep the existing Next.js -> AI SDK bridge -> FastAPI -> Temporal/Strands -> Perplexity event pipeline unchanged. Limit application changes to component paths, visual token names, metadata, and copy; add a source-level regression test that prevents legacy identity from returning. Refresh the global technical context separately, preserving backups and unrelated Project Intelligence files.

**Tech Stack:** Next.js 16, React 19, TypeScript 6, Vitest 4, Tailwind CSS 4, Python pytest, Temporal, Strands Agents, Perplexity Agent API

---

## File Structure

- Create `app/product-identity.test.ts`: guards the application source namespace and required Coding Agent copy.
- Move `components/v0/*.tsx` to `components/coding-agent/*.tsx`: preserves the application composition layer under its product-neutral name.
- Move `components/v0/blurple-background.tsx` to `components/coding-agent/app-background.tsx`: preserves the visual treatment with a neutral component name.
- Modify `app/page.tsx`: uses the renamed components and repository-task empty state.
- Modify `app/compare/page.tsx`: uses the renamed application components.
- Modify `app/layout.tsx`: publishes neutral Coding Agent metadata.
- Modify `app/globals.css`: renames legacy color and animation identifiers without changing values or timing.
- Modify `components/coding-agent/composer.tsx`: uses the new internal import path and repository-task placeholder.
- Modify `components/coding-agent/compare-view.tsx`: uses the new internal import path and coding-task prompt copy.
- Modify `components/coding-agent/site-header.tsx`: presents generic Coding Agent navigation and actions.
- Modify `app/api/orchestrator/route.ts`: updates source comments only; event translation stays unchanged.
- Modify `package.json`: gives the private package a neutral name.
- Modify `AGENTS.md`: documents the repository-scoped coding-agent architecture and SDK/API constraints.
- Replace `/Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md`: records the actual stack, pipeline, invariants, and code references.
- Modify `/Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md`: promotes the refreshed technical context and links code references.
- Create timestamped `.bak` siblings for both Project Intelligence files before modifying them.

### Task 1: Add The Product Identity Regression Guard

**Files:**
- Create: `app/product-identity.test.ts`

- [ ] **Step 1: Write the failing source-identity test**

Create `app/product-identity.test.ts` with this complete content. The forbidden strings are assembled so the guard does not flag its own source.

```ts
import { existsSync, readFileSync, readdirSync } from "node:fs"
import { extname, join, relative } from "node:path"
import { describe, expect, it } from "vitest"

const ROOT = process.cwd()
const SOURCE_EXTENSIONS = new Set([".css", ".md", ".ts", ".tsx"])
const SOURCE_ROOTS = ["app", "components"]
const FORBIDDEN = [
  ["v", "0"].join(""),
  ["blur", "ple"].join(""),
]

function sourceFiles(path: string): string[] {
  return readdirSync(path, { withFileTypes: true }).flatMap((entry) => {
    const child = join(path, entry.name)
    if (entry.isDirectory()) return sourceFiles(child)
    if (!SOURCE_EXTENSIONS.has(extname(entry.name))) return []
    if (entry.name === "product-identity.test.ts") return []
    return [child]
  })
}

describe("Coding Agent product identity", () => {
  it("contains no legacy identity in application source or guidance", () => {
    const files = [
      ...SOURCE_ROOTS.flatMap((path) => sourceFiles(join(ROOT, path))),
      join(ROOT, "AGENTS.md"),
    ]
    const matches = files.flatMap((path) => {
      const content = readFileSync(path, "utf8").toLowerCase()
      return FORBIDDEN.filter((term) => content.includes(term)).map(
        (term) => `${relative(ROOT, path)}: ${term}`,
      )
    })

    expect(matches).toEqual([])
  })

  it("uses the coding-agent composition namespace", () => {
    expect(existsSync(join(ROOT, "components/coding-agent"))).toBe(true)
    expect(existsSync(join(ROOT, "components", ["v", "0"].join("")))).toBe(false)
  })

  it("describes repository-scoped coding work", () => {
    const page = readFileSync(join(ROOT, "app/page.tsx"), "utf8")
    const layout = readFileSync(join(ROOT, "app/layout.tsx"), "utf8")

    expect(page).toContain("What should we work on?")
    expect(page).toContain("Diagnose a failing test")
    expect(page).toContain("Review the current changes")
    expect(layout).toContain("Coding Agent")
    expect(layout).toContain("current repository")
  })
})
```

- [ ] **Step 2: Run the test and verify the current identity fails**

Run: `pnpm exec vitest run app/product-identity.test.ts`

Expected: FAIL in all three tests because legacy source names remain, `components/coding-agent/` does not exist, and the new copy is absent.

- [ ] **Step 3: Commit the red regression guard**

```bash
git add app/product-identity.test.ts
git commit -m "Test coding agent product identity"
```

### Task 2: Rename The Application Composition And Visual Namespace

**Files:**
- Move: `components/v0/agent-activity.tsx` -> `components/coding-agent/agent-activity.tsx`
- Move: `components/v0/composer.tsx` -> `components/coding-agent/composer.tsx`
- Move: `components/v0/compare-view.tsx` -> `components/coding-agent/compare-view.tsx`
- Move: `components/v0/model-picker.tsx` -> `components/coding-agent/model-picker.tsx`
- Move: `components/v0/site-header.tsx` -> `components/coding-agent/site-header.tsx`
- Move: `components/v0/use-models.ts` -> `components/coding-agent/use-models.ts`
- Move: `components/v0/blurple-background.tsx` -> `components/coding-agent/app-background.tsx`
- Modify: `app/page.tsx:24-27,147,238,244`
- Modify: `app/compare/page.tsx:1-9`
- Modify: `app/globals.css:12-13,80-81,175,192-223`
- Modify: `components/coding-agent/composer.tsx:27`
- Modify: `components/coding-agent/compare-view.tsx:7`
- Modify: `app/api/orchestrator/route.ts:212,265`

- [ ] **Step 1: Move the application files without compatibility aliases**

Apply direct file moves for all seven mappings above. Do not leave a `components/v0/` directory, index file, symlink, or re-export.

- [ ] **Step 2: Rename the background export and its neutral comments**

In `components/coding-agent/app-background.tsx`, preserve the JSX and color values while changing the declaration and comments to:

```tsx
/**
 * Editorial ambient background.
 * A pair of slow-drifting radial glows sit behind everything, softened by a
 * heavy blur and a fine grid so the surface reads as "paper, lit from behind"
 * rather than a flat gradient. Purely decorative; hidden from assistive tech.
 */
export function AppBackground() {
```

Change the primary bloom comment to:

```tsx
{/* primary violet bloom, upper-left */}
```

In the three glow elements, replace `animate-blurple-drift` with `animate-ambient-drift` and `animate-blurple-drift-alt` with `animate-ambient-drift-alt`. Leave every gradient, size, blur, and position unchanged.

- [ ] **Step 3: Update component imports and usages**

Use these imports in `app/page.tsx`:

```tsx
import { AgentActivity } from "@/components/coding-agent/agent-activity"
import { AppBackground } from "@/components/coding-agent/app-background"
import { Composer } from "@/components/coding-agent/composer"
import { SiteHeader } from "@/components/coding-agent/site-header"
```

Replace `<BlurpleBackground />` with:

```tsx
<AppBackground />
```

Use these imports and usage in `app/compare/page.tsx`:

```tsx
import { AppBackground } from "@/components/coding-agent/app-background"
import { CompareView } from "@/components/coding-agent/compare-view"
import { SiteHeader } from "@/components/coding-agent/site-header"

// Inside ComparePage:
<AppBackground />
```

Use this import in `components/coding-agent/composer.tsx`:

```tsx
import { ModelPicker } from "@/components/coding-agent/model-picker"
```

Use this import in `components/coding-agent/compare-view.tsx`:

```tsx
import { AgentActivity } from "@/components/coding-agent/agent-activity"
```

- [ ] **Step 4: Rename the CSS identifiers without changing presentation**

Apply these exact identifier substitutions throughout `app/globals.css` and in `app/page.tsx`:

```text
--color-blurple          -> --color-agent-accent
--color-blurple-bright   -> --color-agent-accent-bright
--blurple                -> --agent-accent
--blurple-bright         -> --agent-accent-bright
bg-blurple-bright        -> bg-agent-accent-bright
blurple-drift            -> ambient-drift
blurple-drift-alt        -> ambient-drift-alt
```

The resulting CSS declarations must retain the existing values:

```css
--color-agent-accent: var(--agent-accent);
--color-agent-accent-bright: var(--agent-accent-bright);
--agent-accent: oklch(0.58 0.22 277);
--agent-accent-bright: oklch(0.7 0.2 285);
```

The selection rule must use `var(--agent-accent)`. The keyframes, utility classes, durations, easing, and reduced-motion selectors must be named `ambient-drift` and `ambient-drift-alt` but otherwise remain byte-for-byte equivalent.

- [ ] **Step 5: Update path references in route comments only**

In `app/api/orchestrator/route.ts`, replace both comment references with:

```text
components/coding-agent/agent-activity.tsx
```

Do not modify executable route code.

- [ ] **Step 6: Run the focused identity test**

Run: `pnpm exec vitest run app/product-identity.test.ts`

Expected: the component namespace test PASSES. The source-identity test still FAILS on `AGENTS.md`, and the repository-copy test still FAILS because Task 3 has not run.

- [ ] **Step 7: Commit the namespace migration**

```bash
git add app/page.tsx app/compare/page.tsx app/globals.css app/api/orchestrator/route.ts components/coding-agent components/v0
git commit -m "Rename coding agent application components"
```

### Task 3: Reset Product Metadata And User-Facing Copy

**Files:**
- Modify: `app/page.tsx:29-35,231,237-282`
- Modify: `app/layout.tsx:18-23`
- Modify: `app/compare/page.tsx:11-16`
- Modify: `components/coding-agent/composer.tsx:94`
- Modify: `components/coding-agent/compare-view.tsx:243-245,282`
- Modify: `components/coding-agent/site-header.tsx:7-69`
- Modify: `package.json:2`

- [ ] **Step 1: Replace suggestions and the empty-state message**

Set `SUGGESTIONS` in `app/page.tsx` to:

```tsx
const SUGGESTIONS = [
  "Diagnose a failing test",
  "Add an API endpoint",
  "Refactor a component",
  "Explain the repository architecture",
  "Review the current changes",
]
```

Set both composer prompts to repository-task language:

```tsx
placeholder="Ask for a change, investigation, or implementation…"
```

Pass the same value explicitly to the empty-state `Composer`.

Replace the empty-state badge, heading, description, and footer with:

```tsx
<span className="mb-8 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.22em] text-muted-foreground backdrop-blur-sm">
  <span className="size-1.5 rounded-full bg-agent-accent-bright shadow-[0_0_8px_2px_oklch(0.7_0.2_285/0.7)]" />
  Current repository
</span>

<h1 className="text-balance text-center font-editorial text-5xl leading-[0.95] tracking-tight text-foreground sm:text-7xl">
  What should we{" "}
  <span className="italic text-agent-accent-bright [text-shadow:0_0_40px_oklch(0.7_0.2_285/0.35)]">
    work on?
  </span>
</h1>

<p className="mt-6 max-w-md text-pretty text-center text-base leading-relaxed text-muted-foreground">
  Describe a change, investigation, or implementation for the current
  repository. The agent will reason through the task and stream its progress.
</p>
```

Replace the footer line with:

```tsx
Repository-scoped assistance for implementation, diagnosis, explanation, and review.
```

- [ ] **Step 2: Publish neutral application metadata**

Replace `metadata` in `app/layout.tsx` with:

```tsx
export const metadata: Metadata = {
  title: "Coding Agent",
  description:
    "A repository-scoped coding agent for changes, investigations, implementations, and reviews in the current repository.",
}
```

Do not add a branded generator value.

- [ ] **Step 3: Make header navigation truthful and repository-scoped**

Remove the unused `NAV` constant and its center navigation block from `components/coding-agent/site-header.tsx`. Change the identity label to:

```tsx
<span className="font-mono text-xs uppercase tracking-[0.25em] text-muted-foreground">
  Coding Agent
</span>
```

Keep the existing Compare models link. Remove the nonfunctional `Sign in` and `Start building` buttons, and add a home action beside Compare models:

```tsx
<Button
  render={<Link href="/">New session</Link>}
  nativeButton={false}
  size="sm"
  className="rounded-full bg-foreground px-4 text-background hover:bg-foreground/90"
/>
```

- [ ] **Step 4: Update composer and comparison copy**

In `components/coding-agent/composer.tsx`, set the fallback placeholder to:

```tsx
placeholder={placeholder ?? "Ask for a repository change or investigation…"}
```

In `components/coding-agent/compare-view.tsx`, set the empty pane copy and prompt placeholder to:

```tsx
<ConversationEmptyState
  title="No response yet"
  description="Send a repository task to compare this model."
/>
```

```tsx
placeholder="Ask all selected models the same repository task…"
```

In `app/compare/page.tsx`, use:

```tsx
<h1 className="font-editorial text-3xl text-foreground sm:text-4xl">
  Compare coding agents
</h1>
<p className="mt-2 max-w-2xl text-pretty text-sm leading-relaxed text-muted-foreground">
  Send one repository task to up to four models and compare their streamed responses side by side.
</p>
```

- [ ] **Step 5: Rename the private package**

In `package.json`, change only the package name:

```json
"name": "coding-agent"
```

Run: `pnpm install --lockfile-only`

Expected: exit 0; dependency versions remain unchanged.

- [ ] **Step 6: Run the identity and route tests**

Run: `pnpm exec vitest run app/product-identity.test.ts app/api/orchestrator/route.test.ts`

Expected: identity still FAILS because `AGENTS.md` contains the legacy identity; both orchestrator route tests PASS.

- [ ] **Step 7: Commit the product copy reset**

```bash
git add app/page.tsx app/layout.tsx app/compare/page.tsx components/coding-agent/composer.tsx components/coding-agent/compare-view.tsx components/coding-agent/site-header.tsx package.json pnpm-lock.yaml
git commit -m "Reframe interface as coding agent"
```

### Task 4: Update Repository Guidance

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Replace repository guidance with the coding-agent contract**

Replace `AGENTS.md` with:

```markdown
# Repository Guidelines

Repository-scoped coding agent: a Next.js UI streams durable coding sessions from a Python Temporal/Strands orchestrator backed by Perplexity's Agent API.

## Project Structure & Module Organization

Request path: `app/page.tsx` (`useChat` -> `/api/orchestrator`) converts FastAPI SSE into AI SDK UI-message parts. The bridge is `orchestrator/server.py` (`POST /sessions`, `/turns/stream`, `/end`, `/compare/stream`, `/approve`, `GET /health`) and communicates with Temporal workflow `ChatWorkflow` in `workflow.py`. Workers (`run_worker.py`) register one `PerplexityModel` factory per live `GET /v1/models` id on task queue `perplexity-orchestrator`.

- `components/ai-elements/` - vendored AI Elements primitives; treat as library code.
- `components/coding-agent/` - application UI composing those primitives (`composer`, `agent-activity`, `model-picker`, `compare-view`).
- `components/ui/` - shadcn/base-ui primitives (`Button`/`Select` use `@base-ui/react`, not Radix `asChild`).
- `lib/perplexity.ts` - `DEFAULT_MODEL` plus unauthenticated model listing only; all inference goes through the orchestrator.
- `orchestrator/` - Python stack (`requirements.txt`, local `.venv`). Agent identity lives in `agent.json`.

**Hard rule:** every AI Elements surface must use native subcomponents, props, and animations (`Conversation` scroll, `MessageResponse`/Streamdown, `PromptInput*` submit/attachments, `ChainOfThought*`). Do not reimplement those in `components/coding-agent/`. `reasoning.tsx` is intentionally not vendored; `ChainOfThought` is the only reasoning UI. Model ids are never hardcoded lists; pickers use `/api/models`. A session's model is fixed at creation; switching models ends the session.

## Runtime Contracts

- Preserve the event pipeline across Next.js, FastAPI, Temporal, Strands, and `PerplexityModel`; HTTP routes and SSE/UI-part envelopes are compatibility boundaries.
- Temporal must use the official Strands integration (`TemporalAgent` plus worker-side model factories). Never pass live `Model` instances into workflow constructors.
- Keep API keys server-side. Do not log credentials, prompts, or sensitive response payloads by default.
- Validate request and event shapes at trust boundaries and reject malformed content or unsupported model parameters safely.
- SDK/API work must use only documented types, parameters, classes, props, and event shapes verified against installed packages or authoritative references. Do not invent compatibility behavior.

## Build, Test, and Development Commands

Use **pnpm** (lockfile present). Root `.env.local` needs `PERPLEXITY_API_KEY`, loaded by the worker and FastAPI.

```bash
pnpm install
cd orchestrator && uv venv .venv && uv pip install -r requirements.txt
pnpm dev:all
pnpm dev:clean
pnpm build && pnpm start
pnpm lint
pnpm exec vitest run
cd orchestrator && .venv/bin/python -m pytest
cd orchestrator && .venv/bin/python run_workflow.py "prompt" [model_id]
```

## Coding Style & Naming Conventions

TypeScript is strict (`tsconfig.json`) and uses the `@/*` path alias. Prefer existing composition patterns in `components/coding-agent/*`. Keep changes minimal and preserve native library contracts. Orchestrator changes must remain deterministic in workflows and isolate network or mutable work in Activities.

## Commit & Pull Request Guidelines

Prefer short, imperative subjects describing the change. PRs should distinguish frontend and orchestrator impact and state any environment or Temporal process requirements for reviewers.
```

- [ ] **Step 2: Run the identity test to green**

Run: `pnpm exec vitest run app/product-identity.test.ts`

Expected: 3 tests PASS.

- [ ] **Step 3: Commit repository guidance**

```bash
git add AGENTS.md
git commit -m "Document coding agent repository contracts"
```

### Task 5: Refresh Global Project Intelligence Safely

**Files:**
- Back up: `/Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md`
- Back up: `/Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md`
- Replace: `/Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md`
- Modify: `/Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md`

- [ ] **Step 1: Create non-destructive timestamped backups**

Run:

```bash
cp /Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md /Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md.2026-07-30.bak
cp /Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md /Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md.2026-07-30.bak
```

Expected: both commands exit 0 and the original `.md` files remain present.

- [ ] **Step 2: Replace technical context with the actual architecture**

Replace `technical-domain.md` with:

```markdown
<!-- Context: project-intelligence/technical | Priority: critical | Version: 1.0 | Updated: 2026-07-30 -->

# Technical Domain

> Technical map for the repository-scoped Coding Agent and its durable event pipeline.

## Quick Reference

- **Purpose**: Preserve runtime contracts while extending repository coding capabilities
- **Boundary**: Current local repository; no remote sandbox or multi-workspace support
- **Update When**: Stack, routes, event shapes, model discovery, or session invariants change

## Primary Stack

| Layer | Technology | Responsibility |
|-------|------------|----------------|
| Web | Next.js 16, React 19, TypeScript 6 | Chat, approvals, model comparison, attachments |
| UI streaming | AI SDK 7, AI Elements | UI-message parts and native agent activity surfaces |
| API bridge | Next.js route handlers | FastAPI SSE to AI SDK stream translation |
| Service | FastAPI | Session, turn, approval, comparison, and health endpoints |
| Durability | Temporal Python SDK | Long-lived sessions, updates, signals, retries |
| Agent runtime | Strands Agents | Agent loop and tool/model event production |
| Inference | Perplexity Agent API | Live model discovery and streamed model responses |

## Event Pipeline

```text
app/page.tsx useChat
  -> POST /api/orchestrator
  -> orchestrator/server.py SSE
  -> ChatWorkflow / CompareWorkflow
  -> TemporalAgent
  -> PerplexityModel
  -> Agent API
```

The same event returns through the pipeline as validated SSE envelopes and AI SDK UI-message parts. HTTP routes, SSE topics, UI-part lifecycles, Temporal update behavior, and model event mappings are compatibility boundaries.

## Project Structure

| Path | Responsibility |
|------|----------------|
| `app/` | Next.js pages, metadata, API bridges |
| `components/coding-agent/` | Product composition for composer, activity, models, and comparison |
| `components/ai-elements/` | Vendored native AI Elements primitives |
| `components/ui/` | Base UI and shadcn primitives |
| `orchestrator/server.py` | FastAPI trust boundary and SSE service |
| `orchestrator/workflow.py` | Deterministic durable workflows |
| `orchestrator/run_worker.py` | Worker registration and model factories |
| `orchestrator/perplexity_model.py` | Strands-to-Perplexity model adapter |

## Core Invariants

- Models are discovered through `/api/models`; never maintain a hardcoded model catalog.
- A session model is fixed at creation. Switching models ends the current session.
- Workflows remain deterministic; network and mutable operations belong in Activities.
- Workers register model factories, not live model instances in workflow constructors.
- AI Elements surfaces compose native subcomponents, props, and animations.
- API keys stay server-side; avoid logging prompts, credentials, or sensitive payloads.
- Validate requests and events at trust boundaries.
- Use only SDK/API types and fields verified in installed packages or authoritative docs.

## Development And Verification

```bash
pnpm dev:all
pnpm exec vitest run
pnpm lint
pnpm build
cd orchestrator && .venv/bin/python -m pytest
```

Local services use Temporal `:7233`, Temporal UI `:8233`, FastAPI `:8787`, and Next.js `:3000`. Root `.env.local` supplies `PERPLEXITY_API_KEY` to server-side processes.

## 📂 Codebase References

- `app/page.tsx` - main coding session and AI SDK integration
- `app/api/orchestrator/route.ts` - SSE-to-UI-message bridge
- `components/coding-agent/agent-activity.tsx` - reasoning and tool activity rendering
- `components/coding-agent/composer.tsx` - repository task input
- `orchestrator/server.py` - service endpoints and trust boundaries
- `orchestrator/workflow.py` - durable session behavior
- `orchestrator/run_worker.py` - worker and model registration
- `orchestrator/perplexity_model.py` - inference adapter
- `AGENTS.md` - repository-level engineering rules

## Related Files

- `navigation.md` - Project Intelligence routes
- `business-domain.md` - product and user context
- `business-tech-bridge.md` - business-to-technical mapping
- `decisions-log.md` - decision history
- `living-notes.md` - active issues and debt
```

- [ ] **Step 3: Update Project Intelligence navigation**

Replace `navigation.md` with:

```markdown
<!-- Context: project-intelligence/nav | Priority: critical | Version: 1.0 | Updated: 2026-07-30 -->

# Project Intelligence

> Start here for product and technical context for the repository-scoped Coding Agent.

## Quick Reference

- **Current technical context**: `technical-domain.md`
- **Product context**: `business-domain.md`
- **Active state**: `living-notes.md`

## Files

| File | Description |
|------|-------------|
| `business-domain.md` | Problem, users, and value proposition |
| `technical-domain.md` | Critical stack, event pipeline, invariants, and code references |
| `business-tech-bridge.md` | Business-to-technical mapping |
| `decisions-log.md` | Major decisions and rationale |
| `living-notes.md` | Active issues, debt, and open questions |

## Reading Routes

| Need | Read |
|------|------|
| Implement or debug code | `technical-domain.md`, then `AGENTS.md` in the repository |
| Understand product intent | `business-domain.md`, then `business-tech-bridge.md` |
| Review historical choices | `decisions-log.md` |
| Check current risks | `living-notes.md` |

## Maintenance

- Update `technical-domain.md` when stack, routes, event shapes, or invariants change.
- Keep each file below 200 lines with complete HTML frontmatter.
- Preserve decision history and unrelated context files.
- Back up files before full replacement.

## 📂 Codebase References

- `/Users/tims-stuff/Desktop/v0-clone-blurple/AGENTS.md` - repository engineering guidance
- `/Users/tims-stuff/Desktop/v0-clone-blurple/app/page.tsx` - primary coding session
- `/Users/tims-stuff/Desktop/v0-clone-blurple/app/api/orchestrator/route.ts` - web event bridge
- `/Users/tims-stuff/Desktop/v0-clone-blurple/orchestrator/workflow.py` - durable workflow
- `/Users/tims-stuff/Desktop/v0-clone-blurple/orchestrator/perplexity_model.py` - model adapter

## Related Files

- `/Users/tims-stuff/.config/opencode/context/core/standards/project-intelligence.md`
- `/Users/tims-stuff/.config/opencode/context/core/standards/project-intelligence-management.md`
- `/Users/tims-stuff/.config/opencode/context/core/context-system.md`
```

- [ ] **Step 4: Validate context structure and preservation**

Run:

```bash
test -f /Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md.2026-07-30.bak
test -f /Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md.2026-07-30.bak
test "$(wc -l < /Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md)" -lt 200
test "$(wc -l < /Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md)" -lt 200
rg -n '^<!-- Context: .*Priority: critical \| Version: 1\.0 \| Updated: 2026-07-30 -->$' /Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md /Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md
rg -n '^## 📂 Codebase References$' /Users/tims-stuff/.config/opencode/context/project-intelligence/technical-domain.md /Users/tims-stuff/.config/opencode/context/project-intelligence/navigation.md
```

Expected: every command exits 0; the two `rg` commands each report both current files. Do not commit files under `/Users/tims-stuff/.config/opencode/` to the application repository.

### Task 6: Verify The Semantic Reset End To End

**Files:**
- Verify only

- [ ] **Step 1: Run all frontend tests**

Run: `pnpm exec vitest run`

Expected: all tests PASS, including 3 product identity tests and 2 orchestrator route tests.

- [ ] **Step 2: Run orchestrator tests**

Run: `orchestrator/.venv/bin/python -m pytest orchestrator/tests`

Expected: all config, telemetry, and Perplexity model adapter tests PASS.

- [ ] **Step 3: Run lint**

Run: `pnpm lint`

Expected: exit 0 with no ESLint errors.

- [ ] **Step 4: Run the production build**

Run: `pnpm build`

Expected: exit 0; Next.js compiles `/`, `/compare`, and existing API routes successfully.

- [ ] **Step 5: Verify namespace and runtime boundaries**

Run:

```bash
test ! -d components/v0
test -d components/coding-agent
rg -n -i 'v0|blurple|components/v0|v0-clone-blurple' app components AGENTS.md package.json && exit 1 || true
rg -n '@/components/coding-agent/' app components/coding-agent
git diff 5a6e6af -- orchestrator app/api | rg '^[+-].*(POST /sessions|/turns/stream|/end|/compare/stream|/approve|data-|topic|event_type)' && exit 1 || true
```

Expected: old directory absent; new directory present; legacy application-source scan prints nothing; imports resolve through the new namespace; protected runtime-contract scan prints nothing. The absolute workspace path may remain only in global Project Intelligence code references because it identifies the repository location, not product vocabulary.

- [ ] **Step 6: Inspect final worktree and commit any verification-only fixes**

Run:

```bash
git status --short
git diff --check
git log --oneline -6
```

Expected: no uncommitted application changes and no whitespace errors. If verification required a source correction, stage only that correction and commit it with an imperative message before rerunning all affected checks.
