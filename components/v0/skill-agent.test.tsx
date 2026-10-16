import { renderToStaticMarkup } from "react-dom/server"
import type { DynamicToolUIPart, UIMessage } from "ai"
import { describe, expect, it } from "vitest"
import { AgentActivity } from "./agent-activity"
import { createSkillRunSnapshot, foldSkillEvent } from "./skill-run"

const call = (id = "skill-1"): DynamicToolUIPart => ({
  type: "dynamic-tool", toolName: "use_skill", toolCallId: id, state: "input-available",
  input: { skill_name: "research", request: "Research this", skills: ["sources"] },
})

function liveRun(id = "skill-1") {
  const run = createSkillRunSnapshot(id)
  foldSkillEvent(run, { skill_name: "research", configuration: {
    systemPrompt: "Exact skill system prompt\nPreserve this line.", userPrompt: "Research this with scoped catalog",
    model: "test-model", skills: ["sources"],
    tools: [{ name: "file_read", description: "Read source files", inputSchema: { json: { type: "object" } } }],
  } })
  foldSkillEvent(run, { event: { data: "Before tool." } })
  foldSkillEvent(run, { event: { type: "tool_use_stream", current_tool_use: { toolUseId: "read-1", name: "file_read", input: { path: "notes.md" } } } })
  foldSkillEvent(run, { event: { type: "tool_result", tool_result: { toolUseId: "read-1", status: "success", content: [{ text: "Evidence found" }] } } })
  foldSkillEvent(run, { event: { reasoningText: "Checking the evidence." } })
  foldSkillEvent(run, { event: { data: "After tool." } })
  return run
}

describe("standalone skill agent", () => {
  it("renders one native Agent with prompts, assignments and a bottom Task/ChainOfThought", () => {
    const run = liveRun()
    const parts: UIMessage["parts"] = [call(), { type: "data-skill-run", id: "skill-skill-1", data: run }]
    const html = renderToStaticMarkup(<AgentActivity parts={parts} isThinking />)
    expect(html.match(/data-skill-agent=/g)).toHaveLength(1)
    for (const text of ["System prompt", "Exact skill system prompt", "User prompt", "Research this with scoped catalog",
      "Read source files", "sources", "test-model", "Agent working...", "Parameters", "Evidence found"]) {
      expect(html).toContain(text)
    }
    expect(html.indexOf("Assigned skills")).toBeLessThan(html.indexOf('data-testid="skill-agent-task"'))
    expect(html.indexOf("Before tool.")).toBeLessThan(html.indexOf('data-skill-tool="read-1"'))
    expect(html.indexOf('data-skill-tool="read-1"')).toBeLessThan(html.indexOf("After tool."))
    expect(html).toContain("All emitted events")
  })

  it("renders the Agent while queued and when the stream precedes the tool part", () => {
    const queued = renderToStaticMarkup(<AgentActivity parts={[call()]} isThinking />)
    expect(queued).toContain('data-skill-agent="skill-1"')
    expect(queued).toContain("configuration not received yet")
    const orphan = renderToStaticMarkup(<AgentActivity parts={[{ type: "data-skill-run", data: liveRun() }]} isThinking />)
    expect(orphan).toContain('data-skill-agent="skill-1"')
  })

  it("does not merge concurrent calls of the same skill", () => {
    const parts: UIMessage["parts"] = [call("a"), call("b"),
      { type: "data-skill-run", data: liveRun("a") }, { type: "data-skill-run", data: liveRun("b") }]
    const html = renderToStaticMarkup(<AgentActivity parts={parts} isThinking />)
    expect(html.match(/data-skill-agent=/g)).toHaveLength(2)
  })

  it("shows wrapped activity errors instead of a completed status", () => {
    const part: DynamicToolUIPart = { type: "dynamic-tool", toolName: "use_skill", toolCallId: "skill-1", input: {}, state: "output-available",
      output: JSON.stringify({ status: "error", content: [{ text: "Skill failed to load" }] }) }
    const html = renderToStaticMarkup(<AgentActivity parts={[part]} isThinking={false} />)
    expect(html).toContain("Failed")
    expect(html).toContain("Skill failed to load")
    expect(html).not.toContain("Completed")
  })

  it("retains unknown events, avoids raw chunk text duplication and resets retry state", () => {
    const run = liveRun()
    foldSkillEvent(run, { event: { event: { contentBlockDelta: { delta: { text: "After tool." } } } } })
    foldSkillEvent(run, { event: { custom_event: { detail: "Inspect me" } } })
    expect(run.node.text).toBe("Before tool.After tool.")
    expect(run.events.at(-1)).toEqual({ custom_event: { detail: "Inspect me" } })
    foldSkillEvent(run, { type: "skill_start", attempt: 2, skill_name: "research" })
    expect(run.attempt).toBe(2)
    expect(run.timeline).toEqual([])
    expect(run.node.tools).toEqual([])
  })

  it("uses native AgentResult markdown rather than the SDK's stringified message", () => {
    const run = liveRun()
    foldSkillEvent(run, { event: { result: { message: { content: [{ text: "## Final findings" }] } } } })
    foldSkillEvent(run, { type: "skill_complete", status: "success", result: { content: [{ text: "{'role': 'assistant'}" }] } })
    expect(run.result).toBe("## Final findings")
    const html = renderToStaticMarkup(<AgentActivity parts={[{ type: "data-skill-run", data: run }]} isThinking={false} />)
    expect(html).toContain("Final output")
    expect(html).toContain("Final findings")
    expect(html).toContain("Completed")
  })
})
