"use client"

import { useMemo, useState } from "react"
import { ArrowLeftIcon, CheckIcon, ChevronDownIcon, ChevronRightIcon } from "lucide-react"

import {
  PromptInputCommand,
  PromptInputCommandEmpty,
  PromptInputCommandGroup,
  PromptInputCommandInput,
  PromptInputCommandItem,
  PromptInputCommandList,
} from "@/components/ai-elements/prompt-input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import { useModels } from "./use-models"
import levels from "@/lib/reasoning-levels.json"

// Provider-documented levels intersected with the Agent API enum (no "none").
// Sources: platform.claude.com/docs/en/build-with-claude/effort,
// developers.openai.com model pages, docs.x.ai, api-docs.deepseek.com,
// docs.z.ai/guides/capabilities/thinking, and ai.google.dev/gemini-api/docs/thinking.
// Unknown models retain provider defaults instead of inheriting guessed levels.
export function reasoningLevels(model: string): string[] {
  return (levels as Record<string, string[]>)[model] ?? []
}

function effortLabel(value: string) {
  return value === "xhigh" ? "Extra high" : value[0].toUpperCase() + value.slice(1)
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  xai: "xAI",
  perplexity: "Perplexity",
  nvidia: "NVIDIA",
}

function providerLabel(ownedBy: string) {
  return PROVIDER_LABELS[ownedBy] ?? ownedBy
}

// Human-readable preset names for the worker's "preset:<name>" model ids
// (the Perplexity Agent API dynamic presets).
const PRESET_LABELS: Record<string, string> = {
  fast: "Fast",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "XHigh",
  "wide-research": "Wide Research",
}

function modelLabel(id: string) {
  if (id.startsWith("preset:")) {
    const preset = id.slice("preset:".length)
    return `${PRESET_LABELS[preset] ?? preset} (preset)`
  }
  return id.replace(/^[^/]+\//, "")
}

export type ModelPickerProps = {
  value: string
  onValueChange: (value: string) => void
  triggerClassName?: string
  reasoningEffort?: string
  onReasoningEffortChange?: (value: string) => void
}

export function ModelPicker({
  value,
  onValueChange,
  triggerClassName,
  reasoningEffort = "default",
  onReasoningEffortChange,
}: ModelPickerProps) {
  const { models, status } = useModels()
  const [open, setOpen] = useState(false)
  const [choosingEffort, setChoosingEffort] = useState(false)

  // Grouped by provider so the searchable list still reads as organized —
  // CommandList (below) already caps height and scrolls natively.
  const groups = useMemo(() => {
    const map = new Map<string, typeof models>()
    for (const model of models) {
      const key = model.owned_by || "other"
      if (!map.has(key)) map.set(key, [])
      map.get(key)?.push(model)
    }
    return [...map.entries()]
  }, [models])

  const triggerLabel =
    status === "loading" && models.length === 0
      ? "Loading models…"
      : (modelLabel(value) || "Select a model")

  return (
    <Popover onOpenChange={(next) => { setOpen(next); setChoosingEffort(false) }} open={open}>
      <PopoverTrigger
        className={cn(
          "flex items-center gap-1.5 rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm text-muted-foreground transition-colors outline-none select-none hover:bg-accent hover:text-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:bg-accent aria-expanded:text-foreground",
          triggerClassName
        )}
      >
        <span className="max-w-40 truncate">{triggerLabel}</span>
        {onReasoningEffortChange && reasoningEffort !== "default" ? (
          <span className="text-xs">/ {effortLabel(reasoningEffort)}</span>
        ) : null}
        <ChevronDownIcon className="size-3.5 shrink-0 opacity-70" />
      </PopoverTrigger>
      {/* Real glass: the popup needs a translucent bg for backdrop-blur to
          composite, and the inner Command must not repaint it opaque. */}
      <PopoverContent
        align="start"
        className="app-glass-edge border bg-popover/80 p-0 backdrop-blur-xl"
      >
        {choosingEffort && onReasoningEffortChange ? (
          <PromptInputCommand className="bg-transparent" key={`effort-${value}`}>
            <PromptInputCommandList aria-label={`Reasoning effort for ${modelLabel(value)}`}>
              <PromptInputCommandItem onSelect={() => setChoosingEffort(false)}>
                <ArrowLeftIcon className="size-4" /> Back to models
              </PromptInputCommandItem>
              <PromptInputCommandGroup heading={`${modelLabel(value)} / Reasoning effort`}>
                {["default", ...reasoningLevels(value)].map((effort) => (
                  <PromptInputCommandItem key={effort} value={effort} onSelect={() => {
                    onReasoningEffortChange(effort)
                    setOpen(false)
                    setChoosingEffort(false)
                  }}>
                    <CheckIcon className={cn("size-4", reasoningEffort === effort ? "opacity-100" : "opacity-0")} />
                    {effortLabel(effort)}
                  </PromptInputCommandItem>
                ))}
              </PromptInputCommandGroup>
            </PromptInputCommandList>
            {!reasoningLevels(value).length ? (
              <p className="px-3 pb-3 text-xs text-muted-foreground">Only the provider default is verified for this model.</p>
            ) : null}
          </PromptInputCommand>
        ) : <PromptInputCommand
          className="bg-transparent"
          // Open with the CURRENT model highlighted and scrolled into view
          // (native cmdk defaultValue) instead of the first list item. With
          // ~60 models the current one usually sits below the list's fold;
          // starting there keeps the selection visible and clickable without
          // manual scrolling (clicks on clipped items land outside the
          // popover and dismiss it as an outside press).
          defaultValue={value}
          key="models"
        >
          <PromptInputCommandInput placeholder="Search models…" />
          <PromptInputCommandList className="max-h-80">
            <PromptInputCommandEmpty>No models found.</PromptInputCommandEmpty>
            {groups.map(([provider, items]) => (
              <PromptInputCommandGroup
                heading={providerLabel(provider)}
                key={provider}
              >
                {items.map((model) => (
                  <PromptInputCommandItem
                    key={model.id}
                    onSelect={(next) => {
                      onValueChange(next)
                      if (onReasoningEffortChange) setChoosingEffort(true)
                      else setOpen(false)
                    }}
                    value={model.id}
                  >
                    <CheckIcon
                      className={cn(
                        "size-4",
                        value === model.id ? "opacity-100" : "opacity-0"
                      )}
                    />
                    {modelLabel(model.id)}
                    {onReasoningEffortChange ? <ChevronRightIcon className="ml-auto size-3.5 shrink-0" /> : null}
                  </PromptInputCommandItem>
                ))}
              </PromptInputCommandGroup>
            ))}
          </PromptInputCommandList>
        </PromptInputCommand>}
      </PopoverContent>
    </Popover>
  )
}
