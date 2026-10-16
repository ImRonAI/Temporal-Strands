import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { PastedPromptPreview } from "@/components/v0/pasted-prompt-attachment"
import {
  PASTED_PROMPT_CHARACTER_LIMIT,
  applyPastedEdits,
  buildPastedPromptDocument,
  escapeHtml,
  findTextMatches,
  PASTED_PROMPT_INSTRUCTION,
  isLongPaste,
  isPastedPromptFilename,
  messageForPastedPrompt,
  pastedPromptFilename,
  pastedPromptText,
  textToDataUrl,
  titleFromPastedText,
  wrapMatchIndex,
} from "@/components/v0/pasted-prompt"

describe("long paste attachments", () => {
  it("posts the task instruction with a pasted prompt attachment", () => {
    expect(messageForPastedPrompt("", true)).toBe(PASTED_PROMPT_INSTRUCTION)
    expect(messageForPastedPrompt("   ", true)).toBe(PASTED_PROMPT_INSTRUCTION)
    expect(messageForPastedPrompt(PASTED_PROMPT_INSTRUCTION, true)).toBe(PASTED_PROMPT_INSTRUCTION)
    expect(messageForPastedPrompt("Also check mobile.", true)).toBe(
      `${PASTED_PROMPT_INSTRUCTION}\n\nAlso check mobile.`,
    )
    expect(messageForPastedPrompt("leave me alone", false)).toBe("leave me alone")
  })

  it("attaches only pastes longer than 300 characters", () => {
    expect(isLongPaste("a".repeat(PASTED_PROMPT_CHARACTER_LIMIT))).toBe(false)
    expect(isLongPaste("a".repeat(PASTED_PROMPT_CHARACTER_LIMIT + 1))).toBe(true)
    expect(isLongPaste("")).toBe(false)
  })

  it("names a paste from its content and avoids collisions", () => {
    const brief = "Please build a pricing page with a yearly toggle and three tiers for a developer tool."
    expect(titleFromPastedText(brief)).toBe("Pricing page with a yearly toggle")
    expect(titleFromPastedText("# Migration plan\n\nMove the worker off the laptop.")).toBe("Migration plan")
    expect(titleFromPastedText("")).toBe("Untitled prompt")
    expect(pastedPromptFilename(brief, [])).toBe("Pricing page with a yearly toggle")
    expect(pastedPromptFilename(brief, ["Pricing page with a yearly toggle"])).toBe("Pricing page with a yearly toggle 2")
    expect(isPastedPromptFilename("Pasted prompt 12.txt")).toBe(true)
    expect(isPastedPromptFilename("notes.txt")).toBe(false)
  })

  it("finds literal case-insensitive matches and wraps the active index", () => {
    expect(findTextMatches("Alpha beta ALPHA", "alpha")).toEqual([
      { start: 0, end: 5 },
      { start: 11, end: 16 },
    ])
    expect(findTextMatches("aaaa", "aa")).toEqual([
      { start: 0, end: 2 },
      { start: 2, end: 4 },
    ])
    expect(findTextMatches("hello", "   ")).toEqual([])
    expect(findTextMatches("hello", "z")).toEqual([])
    expect(wrapMatchIndex(-1, 3)).toBe(2)
    expect(wrapMatchIndex(3, 3)).toBe(0)
    expect(wrapMatchIndex(1, 0)).toBe(0)
  })

  it("rewrites only pasted prompts into base64 text documents", () => {
    const text = "héllo\n<script>alert(1)</script>"
    const [pasted, image] = applyPastedEdits(
      [
        { type: "file", filename: "Pasted prompt.txt", mediaType: "text/plain", url: "blob:original" },
        { type: "file", filename: "photo.png", mediaType: "image/png", url: "data:image/png;base64,aaaa" },
      ],
      [{ filename: "Pasted prompt.txt", text }],
    )
    expect(image).toMatchObject({ url: "data:image/png;base64,aaaa" })
    expect(pasted?.filename).toBe("Héllo")
    expect(pasted?.url.startsWith("data:text/plain;base64,")).toBe(true)
    const encoded = textToDataUrl(text).split(",")[1] ?? ""
    const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0))
    expect(new TextDecoder().decode(bytes)).toBe(text)
    expect(pasted?.url.split(",")[1]).toBe(encoded)
    expect(pastedPromptText({ filename: "Pasted prompt.txt", mediaType: "text/plain", url: textToDataUrl(text) })).toBe(text)
    expect(pastedPromptText({ filename: "shot.png", mediaType: "image/png", url: "data:image/png;base64,QUJD" })).toBeNull()
  })

  it("keeps pasted markup inside the preview textarea", () => {
    const hostile = `</textarea><script>alert(1)</script> & "quotes"`
    const document = buildPastedPromptDocument(hostile, 'Pasted prompt.txt')
    expect(document).toContain(escapeHtml(hostile))
    expect(document).not.toContain("</textarea><script>alert(1)")
    expect(document.match(/<textarea /g)).toHaveLength(1)
    expect(document).toContain('"Pasted prompt.txt"')
  })
})

describe("pasted prompt preview", () => {
  it("composes Artifact around the WebPreview navigation, search field, and body", () => {
    const html = renderToStaticMarkup(
      <PastedPromptPreview
        onClose={() => {}}
        onTextChange={() => {}}
        prompt={{ filename: "Pasted prompt.txt", text: "Searchable pasted prompt body" }}
      />,
    )
    expect(html).toContain("Searchable pasted prompt body")
    expect(html).toContain("Search pasted text")
    expect(html).toContain('title="Searchable pasted prompt body"')
    expect(html).toContain('aria-label="Previous match"')
    expect(html).toContain('aria-label="Next match"')
    expect(html).toContain('sandbox="allow-scripts"')
  })
})
