// Regression tests for components/v0/composer.tsx tool-selection wiring.
//
// Same SSR harness as agent-chat.test.tsx: react-dom/server.renderToStaticMarkup
// with heavy subcomponents (ModelPicker, ToolSelector) mocked and their received
// props captured. Props flow only through the Composer — behavior assertions live
// in the component tests for ToolSelector itself.

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { Catalog } from "@/components/v0/use-catalog"

const h = vi.hoisted(() => ({
  toolSelectorProps: [] as Array<Record<string, unknown>>,
  modelPickerProps: [] as Array<Record<string, unknown>>,
}))

vi.mock("@/components/v0/tool-selector", () => ({
  ToolSelector: (props: Record<string, unknown>) => {
    h.toolSelectorProps.push(props)
    return <div data-testid="tool-selector" />
  },
}))

vi.mock("@/components/v0/model-picker", () => ({
  ModelPicker: (props: Record<string, unknown>) => {
    h.modelPickerProps.push(props)
    return <div data-testid="model-picker" />
  },
}))

// PromptInput provider wraps the real composer in context; replace it with a
// thin passthrough so SSR renders children directly and the captured
// ToolSelector props above are what the real PromptInput tree would receive.
vi.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInput: ({ children }: { children?: React.ReactNode }) => <form>{children}</form>,
  PromptInputActionAddAttachments: () => null,
  PromptInputActionMenu: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PromptInputActionMenuContent: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PromptInputActionMenuTrigger: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PromptInputBody: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PromptInputFooter: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PromptInputHeader: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PromptInputSubmit: () => null,
  PromptInputTextarea: () => <textarea />,
  PromptInputTools: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  usePromptInputAttachments: () => ({
    files: [],
    add: () => {},
    remove: () => {},
    clear: () => {},
  }),
}))

import { Composer } from "@/components/v0/composer"

const catalog: Catalog = {
  tools: { session: ["shell"], discoverable: ["browser_navigate"] },
  skills: [{ name: "tdd", description: "Test-driven development workflow" }],
}

function baseProps() {
  return {
    text: "",
    onTextChange: () => {},
    onSubmit: () => {},
  }
}

beforeEach(() => {
  h.toolSelectorProps = []
  h.modelPickerProps = []
})

describe("Composer tool selection", () => {
  it("renders ToolSelector with the provided catalog, status, and selection props inside PromptInputTools", () => {
    const onSelectionChange = vi.fn()
    renderToStaticMarkup(
      <Composer
        {...baseProps()}
        catalog={catalog}
        catalogStatus="ready"
        onSelectionChange={onSelectionChange}
        selectedSkills={[]}
        selectedTools={["journal"]}
      />,
    )

    expect(h.toolSelectorProps).toHaveLength(1)
    const props = h.toolSelectorProps[0]
    expect(props.catalog).toBe(catalog)
    expect(props.status).toBe("ready")
    expect(props.selectedTools).toEqual(["journal"])
    expect(props.selectedSkills).toEqual([])
    expect(props.onSelectionChange).toBe(onSelectionChange)
  })

  it("does not render ToolSelector when onSelectionChange is not provided", () => {
    renderToStaticMarkup(
      <Composer
        {...baseProps()}
        catalog={catalog}
        catalogStatus="ready"
        selectedSkills={["tdd"]}
        selectedTools={["journal"]}
      />,
    )

    expect(h.toolSelectorProps).toHaveLength(0)
  })

  it("falls back to empty selection and idle status when optional props are omitted", () => {
    const onSelectionChange = vi.fn()
    renderToStaticMarkup(
      <Composer {...baseProps()} onSelectionChange={onSelectionChange} />,
    )

    expect(h.toolSelectorProps).toHaveLength(1)
    const props = h.toolSelectorProps[0]
    expect(props.catalog).toBeNull()
    expect(props.status).toBe("idle")
    expect(props.selectedTools).toEqual([])
    expect(props.selectedSkills).toEqual([])
  })
})
