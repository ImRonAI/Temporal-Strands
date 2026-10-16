# Section: Think renders as one chain

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.


You are the coding agent for this section. This is the UI only.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task H — Think renders as one chain". The sentences about loading `think` and the nested `TemporalAgent` are the registry section, not this one.
- `AGENTS.md` — native AI Elements, no local `reasoning.tsx`
- `components/v0/AGENTS.md`
- `components/v0/agent-activity.tsx` — `unstreamedThinkOutput` and the reasoning block that closes on a nested tool frame
- `components/v0/agent-activity-native.test.tsx`
- The `ChainOfThought` component this file already imports. Use that component. Do not vendor a second reasoning renderer.

## Requirements

**Code writing.** Keep the open Think `ChainOfThought` across the text, the tool calls, the tool results, and the native-tool events of that think invocation. Use the existing `ChainOfThought` steps. Do not add `components/ai-elements/reasoning.tsx` or a local reasoning renderer. Do not change `app/api/orchestrator/route.ts`. The route already turns `THINKING_TOPIC` text into reasoning parts. Change the route only if a test you run shows a thinking-topic frame never becomes a part, and then stop and report that instead of editing the route yourself. The route is protected. Do not change how `think` is loaded.

**Subagent use.** Do not launch subagents. The change is one component and its test. A separate agent for "tool frames" and "native events" would edit the same function.

**Prompting.** Task H's UI paragraphs are the spec. Do not implement the load-path paragraphs. Do not invent a new part type.

**Scope.** In: `components/v0/agent-activity.tsx`, `components/v0/agent-activity-native.test.tsx`. Out: `app/api/orchestrator/route.ts`, `orchestrator/workflow.py`, `orchestrator/load_tool.py`, `orchestrator/think_activity.py`. If a change requires a file outside this list, stop and name the file.

## Outcome

One think invocation renders as one chain. The thought text stays visible after it has streamed. A tool call, a tool result, or a native-tool event inside that invocation does not close the chain and does not start a second one.

From the repo root:

```bash
pnpm exec vitest run --exclude '**/.worktrees/**' --exclude '**/.kilo/**' components/v0/agent-activity-native.test.tsx
```

The browser pass for this chain is the app test, not this section. Do not restart the stack.

## Ground in these resources

Read these before you change any file. A signature, default, or behavior that is not in one of them is unverified. Say that and stop. Do not guess, and do not invent a stand-in.

- `docs/superpowers/plans/2026-10-08-session-memory-and-formations.md` — "Task H — Think renders as one chain". The sentences about loading `think` and the nested `TemporalAgent` are the registry section, not this one.
- `AGENTS.md` — native AI Elements, no local `reasoning.tsx`
- `components/v0/AGENTS.md`
- `components/v0/agent-activity.tsx` — `unstreamedThinkOutput` and the reasoning block that closes on a nested tool frame
- `components/v0/agent-activity-native.test.tsx`
- The `ChainOfThought` component this file already imports. Use that component. Do not vendor a second reasoning renderer.

## Requirements

**Code writing.** Keep the open Think `ChainOfThought` across the text, the tool calls, the tool results, and the native-tool events of that think invocation. Use the existing `ChainOfThought` steps. Do not add `components/ai-elements/reasoning.tsx` or a local reasoning renderer. Do not change `app/api/orchestrator/route.ts`. The route already turns `THINKING_TOPIC` text into reasoning parts. Change the route only if a test you run shows a thinking-topic frame never becomes a part, and then stop and report that instead of editing the route yourself. The route is protected. Do not change how `think` is loaded.

**Subagent use.** Do not launch subagents. The change is one component and its test. A separate agent for "tool frames" and "native events" would edit the same function.

**Prompting.** Task H's UI paragraphs are the spec. Do not implement the load-path paragraphs. Do not invent a new part type.

**Scope.** In: `components/v0/agent-activity.tsx`, `components/v0/agent-activity-native.test.tsx`. Out: `app/api/orchestrator/route.ts`, `orchestrator/workflow.py`, `orchestrator/load_tool.py`, `orchestrator/think_activity.py`. If a change requires a file outside this list, stop and name the file.

## How a tool becomes an activity

`orchestrator/load_tool.py` already does this. Do not add an activity file for a community tool.

- `load_tool_activity` (line 150) loads the tool and returns its spec.
- `register_community_tool` (line 265) registers that spec.
- The registered function calls `workflow.execute_activity(name, tool_context.tool_use, **_ACTIVITY_OPTIONS)` (line 279).
- `@activity.defn(dynamic=True) run_loaded_tool` (line 511) runs the loaded tool's `stream()` through `_run_loaded_tool` (line 243).

`journal`, `environment`, `http_request`, `swarm`, forge, dicom, and fhir use that path. `graph_activity` and the desktop activities already exist. Leave them. The sample `environment_activity` in `.agents/skills/strands-temporal/scripts/strands_plugin/tools/workflow.py` is not a file to copy.
