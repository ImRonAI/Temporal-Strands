import type { FileUIPart } from "ai"

/** Pastes longer than this leave the textarea and become a text attachment. */
export const PASTED_PROMPT_CHARACTER_LIMIT = 300

/** Sent with a pasted-prompt attachment so the model treats the file as the task. */
export const PASTED_PROMPT_INSTRUCTION =
  "Your task is to address the attached prompt, and complete any tasks within it."

/** Keep the instruction on any turn that includes a pasted-prompt attachment. */
export function messageForPastedPrompt(text: string, includesPastedPrompt: boolean): string {
  if (!includesPastedPrompt) return text
  const trimmed = text.trim()
  if (!trimmed || trimmed === PASTED_PROMPT_INSTRUCTION) return PASTED_PROMPT_INSTRUCTION
  if (trimmed.includes(PASTED_PROMPT_INSTRUCTION)) return trimmed
  return `${PASTED_PROMPT_INSTRUCTION}\n\n${trimmed}`
}

export const PASTED_PROMPT_FRAME = "pasted-prompt"
export const PASTED_PROMPT_HOST = "pasted-prompt-host"

export type PastedPrompt = {
  filename: string
  text: string
}

export type TextMatch = {
  start: number
  end: number
}

export function isLongPaste(text: string): boolean {
  return text.length > PASTED_PROMPT_CHARACTER_LIMIT
}

export function isPastedPromptFilename(filename: string | undefined): boolean {
  return typeof filename === "string" && /^Pasted prompt( \d+)?\.txt$/.test(filename)
}

const TITLE_OPENER =
  /^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+|help\s+me\s+|i\s+(?:want|need)(?:\s+you)?\s+to\s+|(?:write|create|make|draft|generate|build)\s+(?:me\s+)?(?:(?:a|an|the)\s+)?)/i

/** A short name taken from the paste: a heading or the opening request, not a raw dump. */
export function titleFromPastedText(text: string): string {
  const first = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean)
    ?.replace(/^#{1,6}\s+/, "")
    .replace(/^[-*]\s+/, "")
    .replace(/^["'`“”]+|["'`“”]+$/g, "")
    .trim()
  if (!first) return "Untitled prompt"
  const sentence = first.split(/(?<=[.!?])\s+/)[0]?.replace(/[.!?]+$/g, "").trim() || first
  const stripped = sentence.replace(TITLE_OPENER, "").replace(/[.!?,;:]+$/g, "").trim()
  const source = stripped.length >= 3 ? stripped : sentence
  let title = source.split(/\s+/).slice(0, 6).join(" ")
  if (title.length > 56) title = title.slice(0, 56).replace(/\s+\S*$/, "").trim()
  if (!title) return "Untitled prompt"
  return title.charAt(0).toUpperCase() + title.slice(1)
}

export function pastedPromptFilename(text: string, existing: Iterable<string | undefined>): string {
  const names = new Set<string>()
  for (const name of existing) {
    if (name) names.add(name)
  }
  const base = titleFromPastedText(text)
  if (!names.has(base)) return base
  let index = 2
  while (names.has(`${base} ${index}`)) index += 1
  return `${base} ${index}`
}

/** Literal, case-insensitive matches. The search advances past each hit. */
export function findTextMatches(text: string, query: string): TextMatch[] {
  const needle = query.trim()
  if (!needle) return []
  const haystack = text.toLowerCase()
  const target = needle.toLowerCase()
  const matches: TextMatch[] = []
  let from = 0
  while (from <= haystack.length) {
    const start = haystack.indexOf(target, from)
    if (start === -1) break
    matches.push({ start, end: start + target.length })
    from = start + target.length
  }
  return matches
}

export function wrapMatchIndex(index: number, count: number): number {
  if (count <= 0) return 0
  return ((index % count) + count) % count
}

export function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
}

function utf8Bytes(text: string): Uint8Array {
  const bytes: number[] = []
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0
    if (code <= 0x7f) bytes.push(code)
    else if (code <= 0x7ff) bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f))
    else if (code <= 0xffff) {
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f))
    } else {
      bytes.push(
        0xf0 | (code >> 18),
        0x80 | ((code >> 12) & 0x3f),
        0x80 | ((code >> 6) & 0x3f),
        0x80 | (code & 0x3f),
      )
    }
  }
  return Uint8Array.from(bytes)
}

/** Base64 data URL. The orchestrator route reads the segment after the comma as base64. */
export function textToDataUrl(text: string): string {
  const bytes = utf8Bytes(text)
  let binary = ""
  const chunkSize = 0x8000
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize))
  }
  return `data:text/plain;base64,${btoa(binary)}`
}

/** Recover a pasted prompt from a sent file part. Returns null for every other attachment. */
export function pastedPromptText(part: {
  filename?: string
  mediaType?: string
  url?: string
}): string | null {
  if (part.mediaType && part.mediaType !== "text/plain" && !isPastedPromptFilename(part.filename)) return null
  if (part.mediaType && part.mediaType !== "text/plain") return null
  return textFromDataUrl(part.url)
}

export function textFromDataUrl(url: string | undefined): string | null {
  if (!url) return null
  const comma = url.indexOf(",")
  if (comma < 0) return null
  const meta = url.slice(0, comma)
  const payload = url.slice(comma + 1)
  try {
    if (/;base64/i.test(meta)) {
      const binary = atob(payload)
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
      return new TextDecoder().decode(bytes)
    }
    return decodeURIComponent(payload)
  } catch {
    return null
  }
}

export function applyPastedEdits(files: FileUIPart[], pasted: readonly PastedPrompt[]): FileUIPart[] {
  const byName = new Map(pasted.map((item) => [item.filename, item.text]))
  const used = new Set(files.map((file) => file.filename).filter((name): name is string => Boolean(name)))
  for (const item of pasted) used.delete(item.filename)
  return files.map((file) => {
    if (!file.filename) return file
    const text = byName.get(file.filename)
    if (text === undefined) return file
    const filename = pastedPromptFilename(text, used)
    used.add(filename)
    return { ...file, filename, mediaType: "text/plain", url: textToDataUrl(text) }
  })
}

/**
 * Document shown in WebPreviewBody. The textarea is the editor; a backdrop
 * behind it paints search hits. The parent owns match ranges and posts them in.
 */
export function buildPastedPromptDocument(text: string, filename: string): string {
  const safeName = JSON.stringify(filename)
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  :root { color-scheme: dark; }
  html, body {
    height: 100%;
    margin: 0;
    background:
      linear-gradient(180deg, oklch(0.55 0.15 284 / 0.06), transparent 28%),
      oklch(0.2 0.016 285);
    color: oklch(0.94 0.012 285);
  }
  .shell { position: relative; height: 100%; }
  textarea, .backdrop {
    box-sizing: border-box;
    margin: 0;
    border: 0;
    padding: 14px 16px 18px;
    font: 13px/1.55 ui-sans-serif, system-ui, sans-serif;
    letter-spacing: -0.011em;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  textarea {
    position: relative;
    z-index: 1;
    display: block;
    width: 100%;
    height: 100%;
    resize: none;
    background: transparent;
    color: oklch(0.94 0.012 285);
    caret-color: oklch(0.78 0.14 284);
    outline: none;
  }
  textarea::selection { background: oklch(0.62 0.16 284 / 0.45); color: transparent; }
  .backdrop {
    position: absolute;
    top: 0;
    left: 0;
    z-index: 0;
    overflow: hidden;
    pointer-events: none;
    color: oklch(0.94 0.012 285);
  }
  mark {
    background: oklch(0.62 0.14 284 / 0.28);
    color: inherit;
    border-radius: 0.2em;
    box-shadow: inset 0 -1px 0 oklch(0.75 0.12 284 / 0.45);
  }
  mark.current {
    background: oklch(0.72 0.15 284 / 0.55);
    color: oklch(0.98 0.01 285);
    box-shadow: 0 0 0 1px oklch(0.78 0.14 284 / 0.8), 0 0 18px oklch(0.62 0.16 284 / 0.35);
  }
</style>
</head>
<body>
<div class="shell">
  <div class="backdrop" id="backdrop"></div>
  <textarea id="editor" aria-label="${escapeHtml(titleFromPastedText(text))}" spellcheck="true">${escapeHtml(text)}</textarea>
</div>
<script>
(() => {
  const filename = ${safeName};
  const editor = document.getElementById("editor");
  const backdrop = document.getElementById("backdrop");
  let lastRanges = [];
  let lastActive = 0;

  const escapeHtml = (value) => value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

  const syncBox = () => {
    backdrop.style.width = editor.clientWidth + "px";
    backdrop.style.height = editor.clientHeight + "px";
    backdrop.scrollTop = editor.scrollTop;
    backdrop.scrollLeft = editor.scrollLeft;
  };

  const paint = (value, ranges, activeIndex) => {
    const safe = [];
    let cursor = 0;
    for (const range of ranges || []) {
      if (!range || range.start < cursor || range.end < range.start || range.end > value.length) continue;
      safe.push(range);
      cursor = range.end;
    }
    let html = "";
    cursor = 0;
    safe.forEach((range, index) => {
      html += escapeHtml(value.slice(cursor, range.start));
      const current = index === activeIndex ? " current" : "";
      html += '<mark class="' + current.trim() + '">' + escapeHtml(value.slice(range.start, range.end)) + "</mark>";
      cursor = range.end;
    });
    html += escapeHtml(value.slice(cursor));
    backdrop.innerHTML = html;
    syncBox();
    const current = backdrop.querySelector("mark.current");
    if (!current) return;
    const top = current.offsetTop;
    const bottom = top + current.offsetHeight;
    if (top < backdrop.scrollTop) backdrop.scrollTop = top;
    else if (bottom > backdrop.scrollTop + backdrop.clientHeight) backdrop.scrollTop = bottom - backdrop.clientHeight;
    editor.scrollTop = backdrop.scrollTop;
  };

  const reveal = (start, end) => {
    editor.focus();
    editor.setSelectionRange(start, end);
    const value = editor.value;
    editor.value = value.slice(0, end);
    editor.scrollTop = editor.scrollHeight;
    editor.value = value;
    editor.setSelectionRange(start, end);
  };

  editor.addEventListener("input", () => {
    parent.postMessage({ source: ${JSON.stringify(PASTED_PROMPT_FRAME)}, filename, type: "edit", text: editor.value }, "*");
    paint(editor.value, lastRanges, lastActive);
  });
  editor.addEventListener("scroll", syncBox);
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      parent.postMessage({ source: ${JSON.stringify(PASTED_PROMPT_FRAME)}, filename, type: "close" }, "*");
    }
  });
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.source !== ${JSON.stringify(PASTED_PROMPT_HOST)} || data.filename !== filename || data.type !== "highlight") return;
    lastRanges = Array.isArray(data.ranges) ? data.ranges : [];
    lastActive = typeof data.activeIndex === "number" ? data.activeIndex : 0;
    paint(editor.value, lastRanges, lastActive);
    const range = lastRanges[lastActive];
    if (data.focus && range) reveal(range.start, range.end);
  });
  if (typeof ResizeObserver === "function") new ResizeObserver(syncBox).observe(editor);
  paint(editor.value, [], 0);
  editor.style.color = "transparent";
  parent.postMessage({ source: ${JSON.stringify(PASTED_PROMPT_FRAME)}, filename, type: "ready" }, "*");
})();
</script>
</body>
</html>`
}
