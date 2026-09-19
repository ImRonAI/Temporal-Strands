import { existsSync } from "node:fs"
import { join } from "node:path"
import { Chat } from "@ai-sdk/react"
import { DefaultChatTransport, type UIMessage } from "ai"
import { afterEach, describe, expect, it, vi } from "vitest"

import { POST } from "../orchestrator/route"

// Framework reset V10: CompareView already composes AgentChat per pane.
// Keep its existing page/broadcast tests and exercise the actual SDK transport
// here, mocking only the HTTP boundary to Python, not the SDK or Next bridge.
afterEach(() => vi.unstubAllGlobals())

function sessionId(chat: Chat<UIMessage>) {
  const part = chat.messages.flatMap((message) => message.parts)
    .find((part) => part.type === "data-session")
  expect(part).toMatchObject({ data: { sessionId: expect.any(String) } })
  return (part as { data: { sessionId: string } }).data.sessionId
}

function setup(failModel?: string) {
  const sessions = new Map<string, string>()
  const upstream = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    const body = JSON.parse(String(init?.body))
    if (url.endsWith("/sessions")) {
      const id = `session-${sessions.size}`
      sessions.set(id, body.model_id)
      return Response.json({ session_id: id })
    }
    const id = /\/sessions\/([^/]+)\/turns\/stream$/.exec(url)?.[1]
    if (!id || !sessions.has(id)) throw new Error(`Unexpected upstream URL: ${url}`)
    if (body.model_id === failModel) return new Response("Worker unavailable", { status: 503 })
    const reply = `${id}: ${body.model_id}: ${body.prompt}`
    const frames = [
      { topic: "events", contentBlockDelta: { delta: { text: reply } } },
      { done: true, reply },
    ]
    return new Response(frames.map((frame) => `data: ${JSON.stringify(frame)}\r\n\r\n`).join(""), {
      headers: { "Content-Type": "text/event-stream" },
    })
  })
  vi.stubGlobal("fetch", upstream)
  const frontend = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    expect(String(input)).toBe("/api/orchestrator")
    expect(init?.method).toBe("POST")
    const response = await POST(new Request(`http://localhost${input}`, init))
    expect(response.headers.get("x-vercel-ai-ui-message-stream")).toBe("v1")
    return response
  })
  const chats = [0, 1].map((index) => new Chat({
    id: `pane-${index}`,
    transport: new DefaultChatTransport({ api: "/api/orchestrator", fetch: frontend }),
  }))
  return { chats, frontend, upstream, sessions }
}

describe("compare native streaming compatibility", () => {
  it("retires the unused hand-encoded route rather than maintaining a second protocol", () => {
    expect(existsSync(join(process.cwd(), "app/api/compare/route.ts"))).toBe(false)
  })

  it.each([
    ["model-a", "model-b"],
    ["model-a", "model-a"],
  ])("streams independent pane sessions for %s and %s", async (first, second) => {
    const { chats, frontend, sessions } = setup()
    await Promise.all(chats.map((chat, index) => chat.sendMessage(
      { text: "Compare this" }, { body: { model: [first, second][index] } },
    )))
    expect(frontend).toHaveBeenCalledTimes(2)
    expect(sessions.size).toBe(2)
    expect(sessionId(chats[0])).not.toBe(sessionId(chats[1]))
    for (const [index, chat] of chats.entries()) {
      expect(chat.status).toBe("ready")
      expect(chat.error).toBeUndefined()
      expect(chat.messages.at(-1)?.parts).toContainEqual({
        type: "text", text: `session-${index}: ${[first, second][index]}: Compare this`, state: "done",
      })
    }
  })

  it("retains each session and forwards a model switch on the next turn", async () => {
    const { chats, frontend, upstream, sessions } = setup()
    await Promise.all(chats.map((chat) => chat.sendMessage({ text: "First" }, { body: { model: "model-a" } })))
    const ids = chats.map(sessionId)
    await Promise.all(chats.map((chat, index) => chat.sendMessage(
      { text: "Next" }, { body: { sessionId: ids[index], model: "model-b" } },
    )))
    expect(frontend).toHaveBeenCalledTimes(4)
    expect(sessions.size).toBe(2)
    expect(chats.map(sessionId)).toEqual(ids)
    for (const id of ids) {
      expect(upstream).toHaveBeenCalledWith(expect.stringContaining(`/sessions/${id}/turns/stream`),
        expect.objectContaining({ body: expect.stringContaining('"model_id":"model-b"') }))
    }
  })

  it("surfaces one pane's upstream failure without failing the other pane", async () => {
    const { chats } = setup("model-failed")
    await Promise.all(chats.map((chat, index) => chat.sendMessage(
      { text: "Compare" }, { body: { model: index === 0 ? "model-failed" : "model-ok" } },
    )))
    expect(chats[0].status).toBe("error")
    expect(chats[0].error?.message).toContain("Worker unavailable")
    expect(chats[1].status).toBe("ready")
    expect(chats[1].error).toBeUndefined()
  })
})