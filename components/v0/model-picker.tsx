"use client"

import { useMemo, useState } from "react"
import { CheckIcon, ChevronDownIcon } from "lucide-react"

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

function modelLabel(id: string) {
  return id.replace(/^[^/]+\//, "")
}

export type ModelPickerProps = {
  value: string
  onValueChange: (value: string) => void
  triggerClassName?: string
}

export function ModelPicker({
  value,
  onValueChange,
  triggerClassName,
}: ModelPickerProps) {
  const { models, status } = useModels()
  const [open, setOpen] = useState(false)

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
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger
        className={cn(
          "flex items-center gap-1.5 rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm text-muted-foreground transition-colors outline-none select-none hover:bg-accent hover:text-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:bg-accent aria-expanded:text-foreground",
          triggerClassName
        )}
      >
        <span className="max-w-40 truncate">{triggerLabel}</span>
        <ChevronDownIcon className="size-3.5 shrink-0 opacity-70" />
      </PopoverTrigger>
      <PopoverContent align="start" className="p-0">
        <PromptInputCommand>
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
                      setOpen(false)
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
                  </PromptInputCommandItem>
                ))}
              </PromptInputCommandGroup>
            ))}
          </PromptInputCommandList>
        </PromptInputCommand>
      </PopoverContent>
    </Popover>
  )
}
