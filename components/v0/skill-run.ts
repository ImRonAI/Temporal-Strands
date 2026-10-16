import { foldLeafEvent, type GraphNodeSnapshot } from "./graph-run"

type Frame = Record<string, unknown>
export type SkillConfiguration = {
  systemPrompt: string
  userPrompt: string
  model?: string
  tools: Array<{ name: string; description?: string; inputSchema: { json: Record<string, unknown> } }>
  skills: string[]
}
export type SkillRunSnapshot = {
  toolUseId: string
  skillName: string
  attempt: number
  configuration?: SkillConfiguration
  request?: string
  skills?: string[]
  node: GraphNodeSnapshot
  timeline: Array<
    | { kind: "text" | "reasoning"; text: string }
    | { kind: "tool"; id: string }
    | { kind: "native"; event: Frame }
  >
  events: Frame[]
  result?: unknown
}

export function createSkillRunSnapshot(toolUseId: string): SkillRunSnapshot {
  return { toolUseId, skillName: "Skill agent", attempt: 1,
    node: { label: "Skill agent", parentId: null, kind: "skill_agent", status: "running", text: "", tools: [] },
    timeline: [], events: [] }
}

/** Keep the native events inspectable; project text and reconciled calls in order. */
export function foldSkillEvent(run: SkillRunSnapshot, frame: Frame) {
  if (frame.type === "skill_start") {
    Object.assign(run, createSkillRunSnapshot(run.toolUseId))
    run.attempt = typeof frame.attempt === "number" ? frame.attempt : 1
    run.request = typeof frame.request === "string" ? frame.request : undefined
    run.skills = Array.isArray(frame.skills) ? frame.skills as string[] : []
  }
  if (typeof frame.skill_name === "string") run.skillName = frame.skill_name
  if (frame.configuration) run.configuration = frame.configuration as SkillConfiguration
  if (frame.type === "skill_complete") {
    run.node.status = frame.status === "error" ? "failed" : "done"
    run.result ??= frame.result
    return
  }
  const event = frame.event && typeof frame.event === "object" ? frame.event as Frame : undefined
  if (!event && typeof frame.text !== "string") return
  const inner = event ?? { data: frame.text }
  run.events.push(inner)
  foldLeafEvent(run.node, inner)
  const addText = (kind: "text" | "reasoning", text: string) => {
    const last = run.timeline.at(-1)
    if (last?.kind === kind) last.text += text
    else run.timeline.push({ kind, text })
  }
  if (typeof inner.data === "string") addText("text", inner.data)
  // Strands emits ReasoningStreamEvent separately from raw model chunks.
  if (typeof inner.reasoningText === "string") addText("reasoning", inner.reasoningText)
  for (const tool of run.node.tools) {
    if (!run.timeline.some(entry => entry.kind === "tool" && entry.id === tool.id)) {
      run.timeline.push({ kind: "tool", id: tool.id })
    }
  }
  const chunk = inner.event as Frame | undefined
  const native = inner.perplexity ?? inner.gemini ?? chunk?.perplexity ?? chunk?.gemini
  if (native && typeof native === "object") run.timeline.push({ kind: "native", event: native as Frame })
  const result = inner.result as { message?: { content?: Array<{ text?: string }> } } | undefined
  if (result?.message?.content) {
    run.result = result.message.content.map(block => block.text ?? "").join("\n")
  }
}
