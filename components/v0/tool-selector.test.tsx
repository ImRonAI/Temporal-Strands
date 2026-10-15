import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  AGENT_API_TYPE_NAMES,
  ToolSelector,
  type Catalog,
} from "./tool-selector"

const h = vi.hoisted(() => ({
  triggerProps: [] as Array<Record<string, unknown>>,
  commandItemProps: [] as Array<Record<string, unknown>>,
  groupHeadings: [] as string[],
}))

vi.mock("@/components/ui/popover", () => ({
  Popover: (props: Record<string, unknown>) => (
    <div data-testid="popover">{props.children as React.ReactNode}</div>
  ),
  PopoverTrigger: (props: Record<string, unknown>) => {
    h.triggerProps.push(props)
    const { render, ...rest } = props
    // base-ui pattern: render={<PromptInputButton .../>} supplies the element,
    // trigger children are injected into it. Reproduce that here.
    if (React.isValidElement(render)) {
      return React.cloneElement(
        render as React.ReactElement<Record<string, unknown>>,
        {},
        props.children as React.ReactNode
      )
    }
    return (
      <button type="button" {...rest}>
        {props.children as React.ReactNode}
      </button>
    )
  },
  PopoverContent: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="popover-content">{children}</div>
  ),
}))

vi.mock("@/components/ui/command", () => ({
  Command: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="command">{children}</div>
  ),
  CommandInput: (props: Record<string, unknown>) => <input {...props} />,
  CommandList: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  CommandEmpty: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  CommandGroup: ({
    children,
    heading,
  }: {
    children?: React.ReactNode
    heading: string
  }) => {
    h.groupHeadings.push(heading)
    return <div data-heading={heading}>{children}</div>
  },
  CommandItem: (props: Record<string, unknown>) => {
    h.commandItemProps.push(props)
    return (
      <div
        data-testid={props["data-testid"] as string}
        data-value={props.value as string}
        data-checked={props["data-checked"] as boolean | undefined}
        onClick={props.onClick as React.MouseEventHandler}
      >
        {props.children as React.ReactNode}
      </div>
    )
  },
}))

const catalog: Catalog = {
  tools: {
    session: ["shell", "python_repl"],
    discoverable: ["browser_navigate", "http_request"],
  },
  skills: [
    { name: "deep-research", description: "Multi-source research workflow" },
    { name: "tdd", description: "Test-driven development workflow" },
  ],
}

describe("ToolSelector", () => {
  beforeEach(() => {
    h.triggerProps = []
    h.commandItemProps = []
    h.groupHeadings = []
  })

  it("renders the three groups and item names from the catalog", () => {
    const html = renderToStaticMarkup(
      <ToolSelector
        catalog={catalog}
        status="ready"
        selectedTools={[]}
        selectedSkills={[]}
        onSelectionChange={vi.fn()}
      />
    )

    expect(h.groupHeadings).toEqual(["On this session", "Discoverable", "Skills"])
    expect(html).toContain("shell")
    expect(html).toContain("python_repl")
    expect(html).toContain("browser_navigate")
    expect(html).toContain("http_request")
    expect(html).toContain("deep-research")
    expect(html).toContain("tdd")

    const values = h.commandItemProps.map((item) => item.value)
    expect(values).toContain("tool:shell")
    expect(values).toContain("tool:browser_navigate")
    expect(values).toContain("skill:deep-research")
  })

  it("selecting an unselected tool adds it, selecting a selected one removes it, skills toggle independently", () => {
    const onSelectionChange = vi.fn()
    renderToStaticMarkup(
      <ToolSelector
        catalog={catalog}
        status="ready"
        selectedTools={["shell"]}
        selectedSkills={["tdd"]}
        onSelectionChange={onSelectionChange}
      />
    )

    const shellItem = h.commandItemProps.find((item) => item.value === "tool:shell")
    expect(shellItem).toBeDefined()
    ;(shellItem?.onSelect as (value: string) => void)("tool:shell")
    expect(onSelectionChange).toHaveBeenLastCalledWith({ tools: [], skills: ["tdd"] })

    const browserItem = h.commandItemProps.find(
      (item) => item.value === "tool:browser_navigate"
    )
    ;(browserItem?.onSelect as (value: string) => void)("tool:browser_navigate")
    expect(onSelectionChange).toHaveBeenLastCalledWith({
      tools: ["shell", "browser_navigate"],
      skills: ["tdd"],
    })

    const tddItem = h.commandItemProps.find((item) => item.value === "skill:tdd")
    ;(tddItem?.onSelect as (value: string) => void)("skill:tdd")
    expect(onSelectionChange).toHaveBeenLastCalledWith({ tools: ["shell"], skills: [] })

    const researchItem = h.commandItemProps.find(
      (item) => item.value === "skill:deep-research"
    )
    ;(researchItem?.onSelect as (value: string) => void)("skill:deep-research")
    expect(onSelectionChange).toHaveBeenLastCalledWith({
      tools: ["shell"],
      skills: ["tdd", "deep-research"],
    })
  })

  it("trigger badge shows the selected count", () => {
    const html = renderToStaticMarkup(
      <ToolSelector
        catalog={catalog}
        status="ready"
        selectedTools={["shell", "python_repl"]}
        selectedSkills={["tdd"]}
        onSelectionChange={vi.fn()}
      />
    )
    expect(html).toContain('data-testid="tool-selector-count"')
    expect(html).toContain(">3<")
  })

  it("never renders tools reserved for the Agent API (web_search et al.)", () => {
    const dirty: Catalog = {
      tools: {
        session: ["shell"],
        discoverable: ["web_search", "fetch_url", "http_request"],
      },
      skills: [],
    }
    const html = renderToStaticMarkup(
      <ToolSelector
        catalog={dirty}
        status="ready"
        selectedTools={[]}
        selectedSkills={[]}
        onSelectionChange={vi.fn()}
      />
    )
    expect(html).not.toContain("web_search")
    expect(html).not.toContain("fetch_url")
    expect(html).toContain("http_request")

    const values = h.commandItemProps.map((item) => item.value)
    expect(values.some((v) => v === "tool:web_search")).toBe(false)
    expect(values.some((v) => v === "tool:fetch_url")).toBe(false)

    for (const reserved of AGENT_API_TYPE_NAMES) {
      expect(values.some((v) => v === `tool:${reserved}`)).toBe(false)
    }
  })

  it("loading status disables the trigger", () => {
    renderToStaticMarkup(
      <ToolSelector
        catalog={null}
        status="loading"
        selectedTools={[]}
        selectedSkills={[]}
        onSelectionChange={vi.fn()}
      />
    )
    const trigger = h.triggerProps[h.triggerProps.length - 1]
    expect(trigger).toBeDefined()
    const rendered = trigger.render as React.ReactElement<Record<string, unknown>>
    expect(rendered.props.disabled).toBe(true)
    expect(rendered.props.title).toBe("Loading catalog…")
  })

  it("error status and null catalog disable the trigger", () => {
    renderToStaticMarkup(
      <ToolSelector
        catalog={null}
        status="error"
        selectedTools={[]}
        selectedSkills={[]}
        onSelectionChange={vi.fn()}
      />
    )
    const trigger = h.triggerProps[h.triggerProps.length - 1]
    const rendered = trigger.render as React.ReactElement<Record<string, unknown>>
    expect(rendered.props.disabled).toBe(true)

    renderToStaticMarkup(
      <ToolSelector
        catalog={null}
        status="idle"
        selectedTools={[]}
        selectedSkills={[]}
        onSelectionChange={vi.fn()}
      />
    )
    const idleTrigger = h.triggerProps[h.triggerProps.length - 1]
    const idleRendered = idleTrigger.render as React.ReactElement<Record<string, unknown>>
    expect(idleRendered.props.disabled).toBe(true)
  })
})
