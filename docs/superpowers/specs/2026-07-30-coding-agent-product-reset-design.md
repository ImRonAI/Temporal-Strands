# Coding Agent Product Reset Design

## Objective

Replace the inherited app-generator identity with a generic, repository-scoped coding-agent identity. Preserve the working durable chat runtime while establishing accurate names and guidance for future workspace coding capabilities.

## Scope

This milestone is a semantic product reset and foundation cleanup.

### In Scope

- Rename the legacy application composition directory to `components/coding-agent/`.
- Rename `blurple-background.tsx` to `app-background.tsx`.
- Update all imports and source comments that reference the old paths.
- Replace visible legacy names, metadata, and app-generation claims with neutral coding-agent language.
- Replace UI-generation suggestions with repository engineering tasks.
- Update `AGENTS.md` to describe a workspace coding agent.
- Replace the global Project Intelligence technical context and update its navigation entry.
- Preserve current model selection, sessions, approvals, streaming, tool activity, comparison, and attachment behavior.

### Out of Scope

- File editing or command-execution tools.
- File tree, diff, terminal, or preview panels.
- Multi-workspace selection.
- Remote repositories or hosted sandboxes.
- Compatibility aliases for the legacy component path.
- Rewriting historical Git commit messages.

## Product Identity

Use neutral terminology until a brand is selected:

- Product: Coding Agent
- Working boundary: current repository
- Primary interaction: coding session
- User input: repository task
- Runtime visibility: agent activity
- Input surface: composer

The existing purple visual treatment may remain, but neither legacy name is product vocabulary.

## Architecture

Runtime architecture remains unchanged:

1. `app/page.tsx` uses AI SDK `useChat`.
2. `/api/orchestrator` translates FastAPI SSE into AI SDK UI-message parts.
3. FastAPI communicates with durable Temporal workflows.
4. Temporal runs Strands agents through worker-side model factories.
5. `PerplexityModel` maps Strands model events to the Perplexity Agent API.

This migration must not alter HTTP routes, SSE event envelopes, AI SDK part lifecycles, Temporal session behavior, or Perplexity request/stream mappings.

## Component Structure

`components/coding-agent/` is the application composition layer:

- `composer.tsx`: prompt input, attachments, model selection, and stopping.
- `agent-activity.tsx`: reasoning, tools, sub-agents, artifacts, and results.
- `site-header.tsx`: generic Coding Agent identity and session navigation.
- `compare-view.tsx`: coding-agent model comparison.
- `model-picker.tsx`: dynamic model selection.
- `use-models.ts`: `/api/models` data loading.
- `app-background.tsx`: visual background without product-specific naming.

`components/ai-elements/` remains vendored library code. Application surfaces must compose native AI Elements components, props, and animations rather than reimplementing them.

## UX Copy

The empty state should describe repository-aware work rather than interface generation. Suggested tasks should include examples such as:

- Diagnose a failing test.
- Add an API endpoint.
- Refactor a component.
- Explain the repository architecture.
- Review the current changes.

The composer should invite a change, investigation, or new implementation in the current repository. Existing conversation, approvals, streaming activity, and model-switch behavior remain intact.

## Project Guidance

`AGENTS.md` and Project Intelligence must state:

- This is a repository-scoped workspace coding agent.
- TypeScript remains strict and uses `@/*` aliases.
- The Next.js bridge, FastAPI service, Temporal workflow, Strands agent, and Perplexity model adapter form one event pipeline.
- Models come from `/api/models`; a session model is fixed at creation.
- Temporal uses the official Strands integration and worker-side model factories.
- AI Elements surfaces use native subcomponents.
- SDK/API work must use only documented types, parameters, classes, props, and event shapes verified against installed packages or authoritative references.

The replacement `technical-domain.md` and updated `navigation.md` must:

- Start with required HTML frontmatter.
- Use Version 1.0 for replaced files and the date 2026-07-30.
- Assign critical priority to core technical context.
- Stay under 200 lines and be scannable in under 30 seconds.
- Include a `📂 Codebase References` section.
- Preserve unrelated Project Intelligence files.
- Back up replaced files before writing.

## Safety And Error Handling

- Keep API keys server-side and out of browser bundles.
- Validate request and event shapes at trust boundaries.
- Preserve session/model invariants and protected HTTP/SSE routes.
- Avoid logging credentials, prompts, or sensitive response payloads by default.
- Reject malformed content and unsupported model parameters safely.
- Do not invent SDK/API fields or compatibility behavior.

## Verification

The product reset is complete when:

1. A case-insensitive source search finds no legacy product name, repository name, or component-path references outside Git history.
2. No legacy application composition directory remains.
3. All imports resolve from `components/coding-agent/`.
4. Visible metadata and copy consistently describe a coding agent.
5. Existing route and orchestrator tests pass.
6. Frontend lint and production build pass.
7. Project Intelligence files satisfy frontmatter, version, priority, size, navigation, and codebase-reference requirements.

## Testing Strategy

- Run existing Vitest coverage for the orchestrator route.
- Run Python pytest coverage for config, telemetry, and the Perplexity model adapter.
- Run `pnpm lint` and `pnpm build`.
- Update tests only where paths or visible strings are asserted.
- Run repository-wide text searches after renaming.

## Future Milestones

After this identity reset, separately design and implement explicit workspace tools, file/diff/terminal UI, and broader workspace selection. Those additions require their own contracts, permissions, safety model, and tests.
