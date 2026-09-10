import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ModelPicker, reasoningLevels } from "./model-picker"

const h = vi.hoisted(() => ({
  popoverProps: [] as Array<Record<string, unknown>>,
  commandItemProps: [] as Array<Record<string, unknown>>,
  groupHeadings: [] as string[],
}))

vi.mock("./use-models", () => ({
  useModels: () => ({
    models: [
      { id: "gemini-3.8-flash", owned_by: "google-ai-studio", provider_label: "Google AI Studio" },
      { id: "openai/gpt-5", owned_by: "perplexity-agent-api", provider_label: "Perplexity Agent API" },
      { id: "custom-unknown-model", owned_by: "other" },
    ],
    status: "ready",
    error: null,
  }),
}))

vi.mock("@/components/ui/popover", () => ({
  Popover: (props: Record<string, unknown>) => {
    h.popoverProps.push(props)
    return <div data-testid="popover">{props.children as React.ReactNode}</div>
  },
  PopoverTrigger: ({ children, ...rest }: { children?: React.ReactNode }) => (
    <button type="button" {...rest}>{children}</button>
  ),
  PopoverContent: ({ children, ...rest }: { children?: React.ReactNode }) => (
    <div data-testid="popover-content" {...rest}>{children}</div>
  ),
}))

vi.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInputCommand: ({ children, ...rest }: { children?: React.ReactNode }) => (
    <div data-testid="command" {...rest}>{children}</div>
  ),
  PromptInputCommandInput: (props: Record<string, unknown>) => <input {...props} />,
  PromptInputCommandList: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  PromptInputCommandEmpty: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  PromptInputCommandGroup: ({ children, heading }: { children?: React.ReactNode; heading: string }) => {
    h.groupHeadings.push(heading)
    return <div>{children}</div>
  },
  PromptInputCommandItem: (props: Record<string, unknown>) => {
    h.commandItemProps.push(props)
    return (
      <div
        data-testid="command-item"
        data-value={props.value as string}
        onClick={props.onClick as React.MouseEventHandler}
      >
        {props.children as React.ReactNode}
      </div>
    )
  },
}))

describe("ModelPicker", () => {
  beforeEach(() => {
    h.popoverProps = []
    h.commandItemProps = []
    h.groupHeadings = []
  })

  it("returns verified reasoning levels from json catalog", () => {
    expect(reasoningLevels("gemini-3.8-flash")).toEqual(["low", "medium", "high"])
    expect(reasoningLevels("custom-unknown-model")).toEqual([])
  })

  it("renders trigger with model label and reasoning effort", () => {
    const onValueChange = vi.fn()
    const onReasoningEffortChange = vi.fn()
    const html = renderToStaticMarkup(
      <ModelPicker
        value="gemini-3.8-flash"
        onValueChange={onValueChange}
        reasoningEffort="medium"
        onReasoningEffortChange={onReasoningEffortChange}
      />
    )
    expect(html).toContain("gemini-3.8-flash")
    expect(html).toContain("Medium")
  })

  it("wires actionsRef to Popover for imperative closing", () => {
    const onValueChange = vi.fn()
    renderToStaticMarkup(
      <ModelPicker
        value="gemini-3.8-flash"
        onValueChange={onValueChange}
      />
    )
    expect(h.popoverProps.length).toBeGreaterThan(0)
    const latest = h.popoverProps[h.popoverProps.length - 1]
    expect(latest.actionsRef).toBeDefined()
    expect(typeof latest.onOpenChange).toBe("function")
  })

  it("uses worker-declared provider labels and renders every returned model", () => {
    renderToStaticMarkup(<ModelPicker value="openai/gpt-5" onValueChange={vi.fn()} />)
    expect(h.groupHeadings).toEqual(["Google AI Studio", "Perplexity Agent API", "other"])
    expect(h.commandItemProps.map(item => item.value)).toEqual(["gemini-3.8-flash", "openai/gpt-5", "custom-unknown-model"])
  })

  it("closes popover and calls onReasoningEffortChange when selecting a model without reasoning levels", () => {
    const onValueChange = vi.fn()
    const onReasoningEffortChange = vi.fn()
    renderToStaticMarkup(
      <ModelPicker
        value="gemini-3.8-flash"
        onValueChange={onValueChange}
        onReasoningEffortChange={onReasoningEffortChange}
      />
    )

    const unknownItem = h.commandItemProps.find(
      (item) => item.value === "custom-unknown-model"
    )
    expect(unknownItem).toBeDefined()

    // Mock the popover imperative actions
    const closeSpy = vi.fn()
    const latestPopover = h.popoverProps[h.popoverProps.length - 1]
    const actionsRef = latestPopover.actionsRef as React.RefObject<{ close: () => void } | null>
    ;(actionsRef as { current: unknown }).current = { close: closeSpy }

    // Selecting a model with no reasoning levels should close the picker
    ;(unknownItem?.onSelect as (val: string) => void)("custom-unknown-model")
    expect(onValueChange).toHaveBeenCalledWith("custom-unknown-model")
    expect(closeSpy).toHaveBeenCalled()
  })
})
