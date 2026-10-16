// Regression tests for components/v0/graph-node.tsx.
//
// FormationNode is a custom Canvas node type whose inspector (the vendored
// NodeToolbar wrapper) is NOT the xyflow default (visible only when the node
// is selected): visibility is controlled — hover previews it (on the node or
// the portalled inspector itself), focus mirrors hover for keyboard users,
// and click/Enter pins it open. Escape dismisses outright, including while
// the pointer still hovers the node. Events bubbling from interactive
// descendants (markdown links, inspector content) never toggle the node.
//
// The repo's vitest runs in the node environment with no DOM library:
// rendering uses react-dom/server.renderToStaticMarkup with the vendored AI
// Elements (which need ReactFlow context) mocked, and the dismissal rules
// are covered directly through the exported pure reducer + helpers (SSR
// cannot commit state updates across renders).

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

const h = vi.hoisted(() => ({
  nodeProps: [] as Array<Record<string, unknown>>,
  toolbarProps: [] as Array<Record<string, unknown>>,
  agentHeaderProps: [] as Array<Record<string, unknown>>,
  messageResponses: [] as Array<{ children: string; isAnimating?: boolean }>,
}))

// FormationNode renders real React Flow Handles on group frames; the real
// Handle needs the ReactFlow zustand store, so stub it for SSR rendering.
vi.mock("@xyflow/react", () => ({
  Handle: ({ type, position }: { type: string; position: string }) => (
    <span data-testid="flow-handle" data-type={type} data-position={position} />
  ),
  Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
}))

vi.mock("@/components/ai-elements/node", () => ({
  Node: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    h.nodeProps.push(props)
    const { children } = props
    return <div data-testid="node">{children}</div>
  },
  NodeHeader: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="node-header">{children}</div>
  ),
  NodeTitle: ({ children }: { children?: React.ReactNode }) => (
    <span>{children}</span>
  ),
  NodeDescription: ({ children }: { children?: React.ReactNode }) => (
    <span>{children}</span>
  ),
  NodeAction: ({ children }: { children?: React.ReactNode }) => (
    <span>{children}</span>
  ),
  NodeContent: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="node-content">{children}</div>
  ),
}))

vi.mock("@/components/ai-elements/toolbar", () => ({
  Toolbar: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    h.toolbarProps.push(props)
    return (
      <div data-testid="toolbar" data-visible={String(props.isVisible)}>
        {props.children}
      </div>
    )
  },
}))

vi.mock("@/components/ai-elements/agent", () => ({
  Agent: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="agent">{children}</div>
  ),
  AgentHeader: (props: { name: string; model?: string }) => {
    h.agentHeaderProps.push(props)
    return (
      <div
        data-testid="agent-header"
        data-name={props.name}
        data-model={props.model}
      />
    )
  },
  AgentContent: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="agent-content">{children}</div>
  ),
}))

vi.mock("@/components/ai-elements/message", () => ({
  MessageResponse: ({
    children,
    isAnimating,
  }: {
    children: string
    isAnimating?: boolean
  }) => {
    h.messageResponses.push({ children, isAnimating })
    return <div data-testid="message-response">{children}</div>
  },
}))

vi.mock("@/components/ai-elements/shimmer", () => ({
  Shimmer: ({ children }: { children?: React.ReactNode }) => (
    <p data-testid="shimmer">{children}</p>
  ),
}))

vi.mock("@/components/ai-elements/tool", () => ({
  getStatusBadge: (state: string) => (
    <span data-testid="status-badge" data-state={state} />
  ),
}))

vi.mock("@/components/ai-elements/task", () => ({
  Task: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="task">{children}</div>
  ),
  TaskTrigger: ({
    children,
    title,
  }: {
    children?: React.ReactNode
    title: string
  }) => (
    <div data-testid="task-trigger" data-title={title}>
      {children}
    </div>
  ),
  TaskContent: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="task-content">{children}</div>
  ),
  TaskItem: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="task-item">{children}</div>
  ),
}))

import {
  FormationNode,
  fromInteractiveDescendant,
  inspectionInitial,
  inspectionReducer,
  isInspecting,
  NodeToolActions,
  type FormationNodeData,
  type InspectionAction,
  type InspectionState,
} from "@/components/v0/graph-node"

function data(overrides: Partial<FormationNodeData> = {}): FormationNodeData {
  return {
    pathId: "researcher",
    label: "researcher",
    kind: "agent",
    status: "done",
    text: "## Findings\nAll good.",
    model: "gemini-3.8-flash",
    tools: [],
    isGroup: false,
    handles: { target: true, source: true },
    ...overrides,
  }
}

function render(overrides: Partial<FormationNodeData> = {}) {
  return renderToStaticMarkup(<FormationNode data={data(overrides)} />)
}

function run(actions: InspectionAction[], from: InspectionState = inspectionInitial) {
  return actions.reduce(inspectionReducer, from)
}

beforeEach(() => {
  h.nodeProps.length = 0
  h.toolbarProps.length = 0
  h.agentHeaderProps.length = 0
  h.messageResponses.length = 0
})

describe("FormationNode inspection wiring", () => {
  it("wires hover, focus, click, and keyboard handlers with button semantics", () => {
    render()
    const props = h.nodeProps.at(-1)!
    expect(typeof props.onMouseEnter).toBe("function")
    expect(typeof props.onMouseLeave).toBe("function")
    expect(typeof props.onFocus).toBe("function")
    expect(typeof props.onBlur).toBe("function")
    expect(typeof props.onClick).toBe("function")
    expect(typeof props.onKeyDown).toBe("function")
    expect(props.role).toBe("button")
    expect(props.tabIndex).toBe(0)
    expect(props["aria-expanded"]).toBe(false)
  })

  it("controls Toolbar visibility explicitly instead of relying on node selection", () => {
    render()
    expect(h.toolbarProps.at(-1)!.isVisible).toBe(false)
  })

  it("keeps the portalled inspector hoverable via its own mouse handlers", () => {
    render()
    const toolbar = h.toolbarProps.at(-1)!
    expect(typeof toolbar.onMouseEnter).toBe("function")
    expect(typeof toolbar.onMouseLeave).toBe("function")
  })
})

describe("inspection reducer dismissal rules", () => {
  it("opens on node hover and closes on leave", () => {
    expect(isInspecting(run([{ type: "node-enter" }]))).toBe(true)
    expect(
      isInspecting(run([{ type: "node-enter" }, { type: "node-leave" }]))
    ).toBe(false)
  })

  it("stays open while the pointer crosses onto the portalled inspector", () => {
    const state = run([
      { type: "node-enter" },
      { type: "inspector-enter" },
      { type: "node-leave" },
    ])
    expect(isInspecting(state)).toBe(true)
    expect(isInspecting(inspectionReducer(state, { type: "inspector-leave" }))).toBe(
      false
    )
  })

  it("dismiss (Escape) closes even while hovered, until a genuine re-enter", () => {
    const hoveredAndPinned = run([
      { type: "node-enter" },
      { type: "toggle-pin" },
    ])
    expect(isInspecting(hoveredAndPinned)).toBe(true)

    const dismissed = inspectionReducer(hoveredAndPinned, { type: "dismiss" })
    // Hover state is reset too: nothing keeps the inspector open under the
    // resting pointer.
    expect(dismissed).toEqual(inspectionInitial)
    expect(isInspecting(dismissed)).toBe(false)

    // A genuine re-enter reopens.
    expect(isInspecting(inspectionReducer(dismissed, { type: "node-enter" }))).toBe(
      true
    )
    // Or a re-focus.
    expect(isInspecting(inspectionReducer(dismissed, { type: "focus" }))).toBe(true)
  })

  it("dismiss clears inspector hover and focus as well", () => {
    const state = run([
      { type: "inspector-enter" },
      { type: "focus" },
      { type: "toggle-pin" },
      { type: "dismiss" },
    ])
    expect(state).toEqual(inspectionInitial)
  })

  it("pin toggles on and off", () => {
    expect(isInspecting(run([{ type: "toggle-pin" }]))).toBe(true)
    expect(
      isInspecting(run([{ type: "toggle-pin" }, { type: "toggle-pin" }]))
    ).toBe(false)
  })
})

// Minimal DOM-ish stand-ins: fromInteractiveDescendant distinguishes surface
// events from descendant events via identity, contains(), and closest().
// The EventTarget methods are inert stubs so the fakes typecheck as targets.
type FakeElement = {
  contains: (other: unknown) => boolean
  closest: (selector: string) => FakeElement | null
  addEventListener: () => void
  removeEventListener: () => void
  dispatchEvent: () => boolean
}

function fakeElement(
  overrides: Partial<Pick<FakeElement, "contains" | "closest">> = {}
): FakeElement {
  const el: FakeElement = {
    contains: (other) => other === el,
    closest: () => null,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
    ...overrides,
  }
  return el
}

describe("fromInteractiveDescendant", () => {
  beforeEach(() => {
    // instanceof Element must accept the fakes in the node environment.
    ;(globalThis as Record<string, unknown>).Element = class {
      static [Symbol.hasInstance](value: unknown) {
        return (
          typeof value === "object" &&
          value !== null &&
          "contains" in value &&
          "closest" in value
        )
      }
    }
  })

  it("returns false for events on the node surface itself", () => {
    const el = fakeElement()
    expect(fromInteractiveDescendant({ target: el, currentTarget: el })).toBe(false)
  })

  it("returns true for interactive descendants (markdown links, buttons)", () => {
    const link = fakeElement()
    link.closest = () => link // matches a, button, …
    const surface = fakeElement({ contains: (o) => o === link || false })
    expect(fromInteractiveDescendant({ target: link, currentTarget: surface })).toBe(
      true
    )
  })

  it("returns true for events bubbling from the portalled toolbar subtree", () => {
    const portalled = fakeElement() // closest -> null, but outside surface DOM
    const surface = fakeElement({ contains: () => false })
    expect(
      fromInteractiveDescendant({ target: portalled, currentTarget: surface })
    ).toBe(true)
  })

  it("returns false for non-interactive descendants inside the node", () => {
    const text = fakeElement() // closest -> null
    const surface = fakeElement({ contains: (o) => o === text })
    expect(fromInteractiveDescendant({ target: text, currentTarget: surface })).toBe(
      false
    )
  })
})

describe("FormationNode streamed output rendering", () => {
  it("renders inspector output as MessageResponse markdown, not AgentInstructions", () => {
    const html = render()
    expect(html).toContain('data-testid="message-response"')
    expect(html).not.toContain("Instructions")
    expect(h.messageResponses.map((r) => r.children)).toContain(
      "## Findings\nAll good."
    )
  })

  it("passes isAnimating=true to the inspector body while the node streams", () => {
    render({ status: "streaming" })
    expect(h.messageResponses.at(-1)?.isAnimating).toBe(true)
  })

  it("passes isAnimating=false once the node is done", () => {
    render({ status: "done" })
    expect(h.messageResponses.at(-1)?.isAnimating).toBe(false)
  })

  it("shows a plain empty state instead of markdown when nothing has streamed", () => {
    const html = render({ text: "" })
    expect(html).toContain("No output yet")
    expect(html).toContain("No output streamed yet.")
    expect(h.messageResponses).toHaveLength(0)
  })

  it("keeps the streaming shimmer for in-flight nodes", () => {
    const html = render({ status: "streaming" })
    expect(html).toContain('data-testid="shimmer"')
  })

  it("shows the live text tail on the card WHILE streaming, never shimmer-only", () => {
    // Regression: a streaming node card that hides its accumulated text
    // behind "Streaming…" gives the user no way to know what the agent is
    // doing for the whole run.
    render({ status: "streaming", text: "## Findings\nAll good." })
    expect(h.messageResponses.map((r) => r.children)).toContain(
      "## Findings\nAll good."
    )
  })

  it("streaming card excerpt shows the tail of long output", () => {
    const long = `${"x".repeat(600)}THE-TAIL`
    render({ status: "streaming", text: long })
    const card = h.messageResponses.find((r) => r.children.includes("THE-TAIL"))
    expect(card).toBeDefined()
    expect(card!.children.length).toBeLessThanOrEqual(481)
  })

  it("labels the inspector with the member name, status, and model", () => {
    render()
    const header = h.agentHeaderProps.at(-1)!
    expect(header.name).toBe("researcher · done")
    expect(header.model).toBe("gemini-3.8-flash")
  })

  it("omits the model badge entirely when the stream reports no model", () => {
    render({ model: undefined })
    // The kind is not a model — no invented metadata in the model badge.
    expect(h.agentHeaderProps.at(-1)!.model).toBeUndefined()
  })
})

describe("FormationNode container (group) rendering", () => {
  it("renders formations as a group frame, not an inspectable card", () => {
    const html = render({
      pathId: "team",
      label: "team",
      kind: "swarm",
      status: "running",
      text: "",
      isGroup: true,
    })
    expect(html).toContain('role="group"')
    expect(html).toContain("Swarm")
    expect(html).toContain('data-node-status="running"')
    // No Node card / toolbar inspector for containers.
    expect(h.nodeProps).toHaveLength(0)
    expect(h.toolbarProps).toHaveLength(0)
  })

  // Regression (React Flow error 008): structural edges connect whole
  // formations (e.g. structural:researchers->pipeline,
  // structural:pipeline->fanout, structural:pipeline/transform->
  // pipeline/subgraph), so container frames must render real target/source
  // Handles like leaf nodes. Without them React Flow resolves the edge
  // endpoint handle to null and drops the edge.
  it("renders target and source Handles on group frames that are edge endpoints", () => {
    const html = render({
      pathId: "pipeline",
      label: "pipeline",
      kind: "graph",
      status: "running",
      text: "",
      isGroup: true,
      handles: { target: true, source: true },
    })
    expect(html).toContain('data-testid="flow-handle"')
    expect(html).toContain('data-type="target"')
    expect(html).toContain('data-position="left"')
    expect(html).toContain('data-type="source"')
    expect(html).toContain('data-position="right"')
  })

  it("omits Handles on group frames with no touching edges", () => {
    const html = render({
      pathId: "island",
      label: "island",
      kind: "swarm",
      status: "running",
      text: "",
      isGroup: true,
      handles: { target: false, source: false },
    })
    expect(html).not.toContain('data-testid="flow-handle"')
  })

  it("renders only the needed Handle side (target-only sink container)", () => {
    const html = render({
      pathId: "fanout",
      label: "fanout",
      kind: "parallel",
      status: "pending",
      text: "",
      isGroup: true,
      handles: { target: true, source: false },
    })
    expect(html).toContain('data-type="target"')
    expect(html).not.toContain('data-type="source"')
  })

  it("shows the container's live status badge", () => {
    const html = render({
      pathId: "pipeline",
      label: "pipeline",
      kind: "graph",
      status: "failed",
      text: "",
      isGroup: true,
    })
    expect(html).toContain('data-state="output-error"')
  })
})

describe("FormationNode skill and cancelled status surfaces", () => {
  it("shows the declared skill next to the kind", () => {
    const html = render({ kind: "skill_agent", skill: "wf-skill" })
    expect(html).toContain("Skill agent")
    expect(html).toContain("wf-skill")
  })

  it("maps cancelled nodes onto the error badge with the true word in labels", () => {
    const html = render({ status: "cancelled" })
    expect(html).toContain('data-state="output-error"')
    expect(html).toContain("cancelled")
  })
})

describe("NodeToolActions (native Task variant via composition)", () => {
  const tools = [
    {
      id: "t1",
      name: "file_write",
      status: "output-available" as const,
      input: '{"path":"a.txt"}',
      output: "wrote a.txt",
    },
    {
      id: "t2",
      name: "file_read",
      status: "input-streaming" as const,
      input: "",
      output: "",
    },
  ]

  it("renders one Task per reconciled tool call with status, input, and output", () => {
    const html = renderToStaticMarkup(<NodeToolActions tools={tools} />)
    expect(html).toContain('data-testid="node-tool-actions"')
    // One Task per toolUseId — no duplicate cards.
    expect(html.match(/data-testid="task"/g)).toHaveLength(2)
    expect(html).toContain('data-title="file_write"')
    expect(html).toContain('data-state="output-available"')
    expect(html).toContain("&quot;a.txt&quot;")
    expect(html).toContain("wrote a.txt")
    // The still-pending call shows its empty state instead of invented data.
    expect(html).toContain("No input streamed yet.")
  })

  it("renders nothing when the node made no tool calls", () => {
    expect(renderToStaticMarkup(<NodeToolActions tools={[]} />)).toBe("")
  })

  it("appears inside the inspector when the node has tool actions", () => {
    const html = render({ tools })
    expect(html).toContain('data-testid="node-tool-actions"')
    expect(html).toContain('data-title="file_write"')
  })
})
