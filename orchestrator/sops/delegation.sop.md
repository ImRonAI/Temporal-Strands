# Delegation

## Overview

Delegate work using the current session's tools, model, and discovered formations.
Apply this procedure before calling graph, use_agent, use_skill, or a discovered swarm.

## Parameters

- **task** (required): The work to delegate, taken from the current conversation.
- **tools** (optional): Explicit registry names to assign; omit to inherit the session's tools.

## Steps

### 1. Discover and load tools

Check the tools registered on the session before preparing a delegation.

**Constraints:**
- You MUST call `discover_tools` before naming a tool that is not on the session, then call `load_tool` with the discovered tool before delegating it.
- You MUST use names from the session registry or its loaded-tool paths when specifying `tools`.
- You MUST NOT put Agent API names such as `web_search`, `fetch_url`, or `sandbox` in a registry `tools` list because those capabilities execute on the Agent API server, outside the session tool registry.
- You SHOULD omit `tools` to inherit the session's available tools.

### 2. Choose the formation

Use the discovered tool's actual schema to describe the delegation.

**Constraints:**
- You MUST discover and load `graph` through `load_tool`; this binds the existing `graph_activity`.
- You MUST use the discovered `swarm` tool for a direct swarm call. Its `agents` field follows the installed `strands_tools.swarm` schema.
- For a swarm inside a graph, you MUST use a node with `type: "swarm"` whose `agents` members are `agent` or `skill_agent` nodes.
- You MUST provide graph topology as an object containing `nodes`, `edges`, and `entry_points`, not a string, because `graph_activity` consumes the structured topology.
- You MUST NOT use `workflow` or `parallel` graph node types because the graph implementation supports `agent`, `skill_agent`, `swarm`, and `graph` nodes.
- Every `skill_agent` node MUST name its registered `skill`.

### 3. Inherit the model and invoke

Keep delegated agents on the current session's selected model.

**Constraints:**
- You MUST omit `model_id` and `model_settings` from graph nodes and delegation calls so the session's selected model is inherited.
- You MUST apply these graph rules recursively to nested graph nodes and graph swarm members.
- You MUST correct any cancellation using the discovered schema and loaded registry names before retrying.

## Examples

After discovering and loading `graph`, a graph swarm node can be:

```json
{"id": "team", "type": "swarm", "agents": [{"id": "researcher", "type": "agent"}, {"id": "specialist", "type": "skill_agent", "skill": "<registered skill name>"}]}
```

Replace the skill name with a registered skill. Omitting `tools` and model overrides
lets the formation inherit the session configuration. A direct `swarm` tool call
uses its own `agents` schema instead of graph node definitions.
