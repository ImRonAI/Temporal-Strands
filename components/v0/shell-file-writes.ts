/**
 * Static recognition of file-writing shell syntax.
 *
 * This module inspects a shell command string and reports the file writes it
 * can prove syntactically, WITHOUT executing or interpreting shell, Python,
 * or any other program. Only literally-quoted text is ever recovered:
 *
 * - `cat`/`tee` with a quoted heredoc delimiter (either operand order)
 * - `echo`/`printf` of a single-quoted literal redirected to a file
 * - a quoted Python heredoc (`python <<'EOF'`) whose body contains a
 *   TOP-LEVEL `pathlib.Path('file').write_text('...')` statement or a
 *   TOP-LEVEL `with open('file', 'w') as f:` block whose own statements are
 *   literal `f.write('...')` calls
 *
 * Anything dynamic is refused rather than guessed: an expandable target path
 * (`$VAR`, backticks) drops the write entirely, and an unquoted heredoc,
 * escape sequences, format directives, or non-literal Python arguments keep
 * the write but classify its contents as `null` (unknown body).
 *
 * Python recognition is deliberately statement-anchored: calls inside
 * comments, string literals, conditionals, loops, or function/class bodies
 * are never reported, because their execution or text cannot be proven
 * without running the program. A `with open` block ends at the first dedent;
 * writes after the block are not attributed to it, and any control flow or
 * nesting inside the block downgrades its contents to unknown (`null`).
 */

export type ShellFileWrite = { path: string; contents: string | null; append: boolean }

/** A redirection target: single-quoted (spaces ok), double-quoted without
 *  expansion characters, or a bare word without shell metacharacters. */
const TARGET = /(?:'([^']+)'|"([^"$`\\]+)"|([^\s<>;&|$`\\'"]+))/

const HEREDOC = /<<(-?)\s*(?:'([A-Za-z_][\w]*)'|"([A-Za-z_][\w]*)"|([A-Za-z_][\w]*))/

const ECHO_PRINTF = new RegExp(
  String.raw`^(echo|printf)\s+(?:(-n)\s+)?'([^']*)'\s*(>>?)\s*` + TARGET.source + String.raw`\s*$`
)

const CAT_TEE_TARGET = new RegExp(
  String.raw`(?:^cat\s*(>>?)|^tee\s+(-a)?|\s(>>?))\s*` + TARGET.source
)

/** Only a bare `python`/`python3` (optionally reading stdin via `-`) fed a
 *  heredoc runs the heredoc as a program; `python script.py <<EOF` feeds the
 *  body to the script as data and is never inspected. */
const PYTHON_LINE = /^python3?\s+(?:-\s+)?<</

function targetPath(match: RegExpMatchArray, offset: number): string | undefined {
  return match[offset] ?? match[offset + 1] ?? match[offset + 2]
}

/** Parse a plain Python string literal (no prefix, no backslashes) starting
 *  at `from` in `source`, skipping leading whitespace. Returns the literal
 *  value and the index just past the closing quote, or null when the text is
 *  not a provably literal string (variables, f-strings, escapes, …). */
function pythonStringLiteral(
  source: string,
  from: number
): { value: string; end: number } | null {
  let i = from
  while (i < source.length && /\s/.test(source[i])) i++
  for (const quote of ['"""', "'''", '"', "'"]) {
    if (!source.startsWith(quote, i)) continue
    const close = source.indexOf(quote, i + quote.length)
    if (close === -1) return null
    const value = source.slice(i + quote.length, close)
    // Refuse escape sequences instead of interpreting them.
    if (value.includes("\\")) return null
    // Refuse a single-quote body that itself contains the quote's newline
    // ambiguity: single/double-quoted Python strings cannot span lines.
    if (quote.length === 1 && value.includes("\n")) return null
    return { value, end: close + quote.length }
  }
  return null
}

/** For each line of a Python body, report whether the line START is inside a
 *  string literal, using a minimal static lexer (quotes, triple quotes,
 *  backslash escapes, `#` comments). Unterminated strings conservatively stay
 *  open, marking the rest of the body as string text. Never evaluates code. */
function stringStateAtLineStarts(body: string): boolean[] {
  const states: boolean[] = [false]
  let quote: string | null = null
  let i = 0
  while (i < body.length) {
    const ch = body[i]
    if (ch === "\n") {
      i++
      states.push(quote !== null)
      continue
    }
    if (quote) {
      if (ch === "\\") {
        if (body[i + 1] === "\n") {
          i += 2
          states.push(true)
        } else {
          i += 2
        }
        continue
      }
      if (body.startsWith(quote, i)) {
        i += quote.length
        quote = null
        continue
      }
      i++
      continue
    }
    if (ch === "#") {
      while (i < body.length && body[i] !== "\n") i++
      continue
    }
    if (ch === "'" || ch === '"') {
      const triple = ch.repeat(3)
      if (body.startsWith(triple, i)) {
        quote = triple
        i += 3
      } else {
        quote = ch
        i++
      }
      continue
    }
    i++
  }
  return states
}

/** A top-level `Path('...').write_text(` statement, anchored at column 0. */
const PATH_WRITE_TEXT = /^(?:pathlib\.)?Path\(\s*(['"])((?:(?!\1).)*)\1\s*\)\.write_text\(/

/** A top-level `with open('...', 'mode') as name:` statement, anchored at
 *  column 0. Group 6 captures a one-line trailing body, if any. */
const WITH_OPEN =
  /^with\s+open\(\s*(['"])((?:(?!\1).)*)\1\s*,\s*(['"])([rwaxb+]{1,3})\3\s*\)\s+as\s+([A-Za-z_]\w*)\s*:(.*)$/

/** Prove a with-block scope consists ONLY of literal `name.write('...')`
 *  statements (plus blank lines and full-line comments). Returns the joined
 *  contents, or null when anything else appears — a conditional, loop, call,
 *  nested block, or non-literal argument makes the body unprovable. */
function literalBlockWrites(scope: string, name: string): string | null {
  const prefix = `${name}.write(`
  const parts: string[] = []
  let i = 0
  while (i < scope.length) {
    if (/\s/.test(scope[i])) {
      i++
      continue
    }
    if (scope[i] === "#") {
      while (i < scope.length && scope[i] !== "\n") i++
      continue
    }
    if (!scope.startsWith(prefix, i)) return null
    const literal = pythonStringLiteral(scope, i + prefix.length)
    if (!literal) return null
    i = literal.end
    while (i < scope.length && /[ \t]/.test(scope[i])) i++
    if (scope[i] !== ")") return null
    i++
    parts.push(literal.value)
  }
  return parts.length ? parts.join("") : null
}

/** Collect writes provable from a Python program body. Only TOP-LEVEL
 *  statements are inspected: calls inside comments, string literals,
 *  conditionals, loops, or function/class bodies (any indented line) are
 *  never reported. `trusted` is false when the heredoc delimiter was unquoted
 *  (shell expands the body), in which case contents are never reported. */
function pythonWrites(body: string, trusted: boolean): ShellFileWrite[] {
  const writes: ShellFileWrite[] = []
  const lines = body.split("\n")
  const inString = stringStateAtLineStarts(body)
  const offsets: number[] = []
  {
    let offset = 0
    for (const line of lines) {
      offsets.push(offset)
      offset += line.length + 1
    }
  }
  const safePath = (path: string): string | null => {
    if (!path || path.includes("\\")) return null
    if (!trusted && /[$`]/.test(path)) return null
    return path
  }

  for (let i = 0; i < lines.length; i++) {
    if (inString[i]) continue // continuation of a multi-line string literal
    const line = lines[i]
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith("#")) continue
    if (/^\s/.test(line)) continue // indented: conditional/function/loop body

    const writeText = line.match(PATH_WRITE_TEXT)
    if (writeText) {
      const path = safePath(writeText[2])
      if (!path) continue
      let contents: string | null = null
      if (trusted) {
        // Parse the argument from the full body so triple-quoted literals
        // may span lines; the statement must end right after the `)`.
        const literal = pythonStringLiteral(body, offsets[i] + writeText[0].length)
        if (literal) {
          let end = literal.end
          while (end < body.length && /[ \t]/.test(body[end])) end++
          if (body[end] === ")") {
            end++
            while (end < body.length && /[ \t]/.test(body[end])) end++
            if (end >= body.length || body[end] === "\n") contents = literal.value
          }
        }
      }
      writes.push({ path, contents, append: false })
      continue
    }

    const open = line.match(WITH_OPEN)
    if (open) {
      const mode = open[4]
      if (!/[wax]/.test(mode)) continue // read-only open is not a write
      const path = safePath(open[2])
      if (!path) continue
      const trailing = open[6].trim()
      // Block extent: subsequent lines that are blank, string continuations,
      // or indented. The block ends at the first dedent back to column 0.
      let after = i + 1
      while (
        after < lines.length &&
        (inString[after] || !lines[after].trim() || /^\s/.test(lines[after]))
      ) {
        after++
      }
      const scope = trailing || lines.slice(i + 1, after).join("\n")
      let contents: string | null = null
      if (trusted && !mode.includes("b")) {
        contents = literalBlockWrites(scope, open[5])
      }
      writes.push({ path, contents, append: mode.includes("a") })
      if (!trailing) i = after - 1 // resume at the dedented statement
      continue
    }
  }
  return writes
}

/** Recognize shell file-writing syntax without executing or interpreting a
 * shell program. Only quoted heredocs have literal, safely recoverable text.
 * Dynamic paths and expanded bodies are deliberately not fabricated. */
export function shellFileWrites(command: string): ShellFileWrite[] {
  const writes: ShellFileWrite[] = []
  const lines = command.split("\n")
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()
    if (line.startsWith("#")) continue // shell comment, never a write

    const literal = line.match(ECHO_PRINTF)
    if (literal) {
      const path = targetPath(literal, 5)
      if (path && path !== "/dev/null") {
        writes.push({
          path,
          contents:
            literal[1] === "echo" && !literal[3].includes("\\")
              ? literal[3] + (literal[2] ? "" : "\n")
              : literal[1] === "printf" && !/[\\%]/.test(literal[3])
                ? literal[3]
                : null,
          append: literal[4] === ">>",
        })
      }
      continue
    }

    // Limit recognition to explicit writer invocations at the start of a
    // line — never strings inside arbitrary code, comments, or quoted text.
    const isCatTee = /^(?:cat|tee)\s/.test(line)
    const isPython = PYTHON_LINE.test(line)
    if (!isCatTee && !isPython) continue

    const heredoc = line.match(HEREDOC)
    if (!heredoc) continue
    const delimiter = heredoc[2] || heredoc[3] || heredoc[4]
    const quoted = Boolean(heredoc[2] || heredoc[3])
    const body: string[] = []
    let ended = false
    for (let j = i + 1; j < lines.length; j++) {
      const text = heredoc[1] ? lines[j].replace(/^\t+/, "") : lines[j]
      if (text === delimiter) {
        i = j
        ended = true
        break
      }
      body.push(text)
    }
    if (!ended) continue

    if (isPython) {
      writes.push(...pythonWrites(body.join("\n"), quoted))
      continue
    }

    // cat/tee: the redirection target may precede or follow the heredoc.
    const remainder = line.replace(heredoc[0], "")
    const target = remainder.match(CAT_TEE_TARGET)
    if (!target) continue
    const path = targetPath(target, 4)
    if (!path || path === "/dev/null") continue
    writes.push({
      path,
      contents: quoted ? (body.length ? `${body.join("\n")}\n` : "") : null,
      append: target[1] === ">>" || Boolean(target[2]) || target[3] === ">>",
    })
  }
  return writes
}
