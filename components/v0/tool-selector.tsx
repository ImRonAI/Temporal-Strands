"use client"

import { useMemo, useState } from "react"
import { WrenchIcon } from "lucide-react"

import { PromptInputButton } from "@/components/ai-elements/prompt-input"
import { Badge } from "@/components/ui/badge"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

import type { Catalog, CatalogSkill } from "@/components/v0/use-catalog"

export type { Catalog, CatalogSkill }

export type ToolSelection = {
  tools: string[]
  skills: string[]
}

export type ToolSelectorProps = {
  catalog: Catalog | null
  status: "idle" | "loading" | "ready" | "error"
  selectedTools: string[]
  selectedSkills: string[]
  onSelectionChange: (next: ToolSelection) => void
}

// These run inside the Agent API sandbox; the session never calls them
// directly, so the selector must never offer them for manual selection.
export const AGENT_API_TYPE_NAMES = new Set([
  "web_search",
  "fetch_url",
  "image_search",
  "people_search",
  "finance_search",
  "sandbox",
])

function toggle(list: string[], name: string) {
  return list.includes(name) ? list.filter((item) => item !== name) : [...list, name]
}

export function ToolSelector({
  catalog,
  status,
  selectedTools,
  selectedSkills,
  onSelectionChange,
}: ToolSelectorProps) {
  const [open, setOpen] = useState(false)

  const sessionTools = useMemo(
    () => (catalog?.tools.session ?? []).filter((name) => !AGENT_API_TYPE_NAMES.has(name)),
    [catalog]
  )
  const discoverableTools = useMemo(
    () =>
      (catalog?.tools.discoverable ?? []).filter(
        (name) => !AGENT_API_TYPE_NAMES.has(name)
      ),
    [catalog]
  )
  const skills = catalog?.skills ?? []

  const disabled = status !== "ready" || catalog === null
  const triggerTitle =
    status === "loading"
      ? "Loading catalog…"
      : status === "error"
        ? "Tool catalog unavailable"
        : catalog === null
          ? "Tool catalog unavailable"
          : "Tools and skills"

  const selectedCount = selectedTools.length + selectedSkills.length

  const toggleTool = (name: string) => {
    onSelectionChange({ tools: toggle(selectedTools, name), skills: selectedSkills })
  }
  const toggleSkill = (name: string) => {
    onSelectionChange({ tools: selectedTools, skills: toggle(selectedSkills, name) })
  }

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger
        render={
          <PromptInputButton
            aria-label="Tools and skills"
            data-testid="tool-selector-trigger"
            disabled={disabled}
            title={triggerTitle}
          />
        }
      >
        <WrenchIcon className="size-4" />
        {selectedCount > 0 ? (
          <Badge data-testid="tool-selector-count" variant="secondary">
            {selectedCount}
          </Badge>
        ) : null}
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="app-elevated rounded-2xl border bg-popover/90 p-0 ring-0 backdrop-blur-xl"
      >
        <Command className="bg-transparent">
          <CommandInput placeholder="Search tools and skills" />
          <CommandList>
            <CommandEmpty>No tools or skills found.</CommandEmpty>
            <CommandGroup heading="On this session">
              {sessionTools.map((name) => {
                const checked = selectedTools.includes(name)
                return (
                  <CommandItem
                    data-checked={checked}
                    data-testid={`tool-selector-item-${name}`}
                    key={name}
                    onSelect={() => toggleTool(name)}
                    value={`tool:${name}`}
                  >
                    {name}
                  </CommandItem>
                )
              })}
            </CommandGroup>
            <CommandGroup heading="Discoverable">
              {discoverableTools.map((name) => {
                const checked = selectedTools.includes(name)
                return (
                  <CommandItem
                    data-checked={checked}
                    data-testid={`tool-selector-item-${name}`}
                    key={name}
                    onSelect={() => toggleTool(name)}
                    value={`tool:${name}`}
                  >
                    {name}
                  </CommandItem>
                )
              })}
            </CommandGroup>
            <CommandGroup heading="Skills">
              {skills.map((skill) => {
                const checked = selectedSkills.includes(skill.name)
                return (
                  <CommandItem
                    data-checked={checked}
                    data-testid={`tool-selector-item-${skill.name}`}
                    key={skill.name}
                    onSelect={() => toggleSkill(skill.name)}
                    value={`skill:${skill.name}`}
                  >
                    <span className="flex min-w-0 flex-col">
                      <span>{skill.name}</span>
                      <span
                        className={cn(
                          "truncate text-xs text-muted-foreground",
                          "max-w-56"
                        )}
                      >
                        {skill.description}
                      </span>
                    </span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
