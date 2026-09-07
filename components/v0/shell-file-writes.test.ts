import { describe, expect, it } from "vitest"

import { shellFileWrites } from "./shell-file-writes"

describe("cat/tee quoted heredocs", () => {
  it("recovers a literal single-quoted heredoc body", () => {
    const command =
      "cat > src/App.tsx <<'EOF'\nexport default function App() { return <h1>Hello</h1> }\nEOF"
    expect(shellFileWrites(command)).toEqual([
      {
        path: "src/App.tsx",
        contents: "export default function App() { return <h1>Hello</h1> }\n",
        append: false,
      },
    ])
  })
  it("recovers a double-quoted delimiter heredoc body", () => {
    expect(shellFileWrites('cat > a.txt <<"END"\nplain\nEND')).toEqual([
      { path: "a.txt", contents: "plain\n", append: false },
    ])
  })
  it("supports heredoc before the redirection target (either order)", () => {
    expect(shellFileWrites("cat <<'EOF' > notes.md\n# Notes\nEOF")).toEqual([
      { path: "notes.md", contents: "# Notes\n", append: false },
    ])
    expect(shellFileWrites("cat <<'EOF' >> notes.md\nmore\nEOF")[0].append).toBe(true)
  })
  it("recognizes tee, tee -a, and cat >> append", () => {
    expect(shellFileWrites("tee docs.md <<'EOF'\n# Docs\nEOF")[0]).toEqual({
      path: "docs.md",
      contents: "# Docs\n",
      append: false,
    })
    expect(shellFileWrites("tee -a docs.md <<'EOF'\nextra\nEOF")[0].append).toBe(true)
    expect(shellFileWrites("cat >> a.ts <<'EOF'\nmore\nEOF")[0]).toEqual({
      path: "a.ts",
      contents: "more\n",
      append: true,
    })
  })
  it("handles quoted paths containing spaces", () => {
    expect(
      shellFileWrites("cat > 'My Docs/read me.md' <<'EOF'\nhello\nEOF")[0].path
    ).toBe("My Docs/read me.md")
    expect(
      shellFileWrites('tee "My Docs/read me.md" <<\'EOF\'\nhello\nEOF')[0].path
    ).toBe("My Docs/read me.md")
  })
  it("handles <<- tab stripping and an empty body", () => {
    expect(shellFileWrites("cat > t.txt <<-'EOF'\n\tindented\n\tEOF")[0].contents).toBe(
      "indented\n"
    )
    expect(shellFileWrites("cat > empty.txt <<'EOF'\nEOF")[0].contents).toBe("")
  })
  it("classifies an unquoted (expanding) heredoc body as unknown, not guessed", () => {
    const [write] = shellFileWrites("cat > output.md <<EOF\n$SECRET\nEOF")
    expect(write).toEqual({ path: "output.md", contents: null, append: false })
  })
  it("drops dynamic paths and /dev/null instead of guessing", () => {
    expect(shellFileWrites("cat > $TARGET <<'EOF'\nhello\nEOF")).toEqual([])
    expect(shellFileWrites("cat > \"$HOME/x.txt\" <<'EOF'\nhello\nEOF")).toEqual([])
    expect(shellFileWrites("cat > /dev/null <<'EOF'\nhello\nEOF")).toEqual([])
  })
  it("ignores an unterminated heredoc", () => {
    expect(shellFileWrites("cat > a.txt <<'EOF'\nnever closed")).toEqual([])
  })
  it("collects multiple sequential writes", () => {
    const command =
      "cat > a.txt <<'EOF'\none\nEOF\necho 'two' > b.txt\ncat >> a.txt <<'EOF'\nthree\nEOF"
    expect(shellFileWrites(command).map((w) => w.path)).toEqual([
      "a.txt",
      "b.txt",
      "a.txt",
    ])
  })
})

describe("echo/printf literal redirection", () => {
  it("captures echo with trailing newline and append mode", () => {
    expect(shellFileWrites("echo 'export const a = 1;' > a.ts")[0]).toEqual({
      path: "a.ts",
      contents: "export const a = 1;\n",
      append: false,
    })
    expect(shellFileWrites("echo 'line' >> log.txt")[0].append).toBe(true)
  })
  it("captures echo -n without trailing newline", () => {
    expect(shellFileWrites("echo -n 'raw' > raw.txt")[0].contents).toBe("raw")
  })
  it("captures a plain printf literal but nulls format directives and escapes", () => {
    expect(shellFileWrites("printf 'plain text' > p.txt")[0].contents).toBe("plain text")
    expect(shellFileWrites("printf 'a%sb' > p.txt")[0].contents).toBeNull()
    expect(shellFileWrites("printf 'a\\nb' > p.txt")[0].contents).toBeNull()
    expect(shellFileWrites("echo 'a\\nb' > p.txt")[0].contents).toBeNull()
  })
  it("supports quoted target paths with spaces", () => {
    expect(shellFileWrites("echo 'hi' > 'out dir/file.txt'")[0].path).toBe(
      "out dir/file.txt"
    )
  })
  it("does not capture double-quoted or unquoted (expandable) echo bodies", () => {
    expect(shellFileWrites('echo "$VAR" > a.txt')).toEqual([])
    expect(shellFileWrites("echo $VAR > a.txt")).toEqual([])
  })
  it("ignores echo to /dev/null and echo without redirection", () => {
    expect(shellFileWrites("echo 'x' > /dev/null")).toEqual([])
    expect(shellFileWrites("echo 'no redirect here'")).toEqual([])
  })
})

describe("python heredoc writes", () => {
  it("recovers Path('file').write_text with a triple-quoted literal", () => {
    const command = [
      "python3 <<'PY'",
      "from pathlib import Path",
      "Path('src/app.py').write_text('''import os",
      "print(os.name)",
      "''')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([
      { path: "src/app.py", contents: "import os\nprint(os.name)\n", append: false },
    ])
  })
  it("recovers pathlib.Path(...).write_text with a single-quoted literal", () => {
    const command = "python <<'PY'\nimport pathlib\npathlib.Path('a.txt').write_text('hi')\nPY"
    expect(shellFileWrites(command)).toEqual([
      { path: "a.txt", contents: "hi", append: false },
    ])
  })
  it("recovers with open(...,'w') as f: f.write literal, including append mode", () => {
    const write = "python3 <<'PY'\nwith open('out.txt', 'w') as f:\n    f.write('hello\\n')\nPY"
    // \n escape inside the literal is refused, so contents are unknown
    expect(shellFileWrites(write)).toEqual([
      { path: "out.txt", contents: null, append: false },
    ])
    const plain = "python3 <<'PY'\nwith open('out.txt', 'w') as f:\n    f.write('hello')\nPY"
    expect(shellFileWrites(plain)).toEqual([
      { path: "out.txt", contents: "hello", append: false },
    ])
    const append = "python3 <<'PY'\nwith open('log.txt', 'a') as f:\n    f.write('entry')\nPY"
    expect(shellFileWrites(append)).toEqual([
      { path: "log.txt", contents: "entry", append: true },
    ])
  })
  it("joins multiple literal f.write calls in one with-block", () => {
    const command =
      "python3 <<'PY'\nwith open('m.txt', 'w') as f:\n    f.write('a')\n    f.write('b')\nPY"
    expect(shellFileWrites(command)[0].contents).toBe("ab")
  })
  it("keeps the path but nulls contents when a write argument is not a plain literal", () => {
    const fstring =
      "python3 <<'PY'\nwith open('d.txt', 'w') as f:\n    f.write(f'{value}')\nPY"
    expect(shellFileWrites(fstring)).toEqual([
      { path: "d.txt", contents: null, append: false },
    ])
    const variable = "python3 <<'PY'\nPath('v.txt').write_text(data)\nPY"
    expect(shellFileWrites(variable)).toEqual([
      { path: "v.txt", contents: null, append: false },
    ])
  })
  it("skips read-mode opens and never evaluates code", () => {
    expect(
      shellFileWrites("python3 <<'PY'\nwith open('in.txt', 'r') as f:\n    data = f.read()\nPY")
    ).toEqual([])
    expect(
      shellFileWrites("python3 <<'PY'\nPath(user_input).write_text('x')\nPY")
    ).toEqual([])
  })
  it("never reports contents from an unquoted (shell-expanding) python heredoc", () => {
    const command = "python3 <<PY\nPath('a.txt').write_text('$SECRET')\nPY"
    expect(shellFileWrites(command)).toEqual([
      { path: "a.txt", contents: null, append: false },
    ])
    expect(shellFileWrites("python3 <<PY\nPath('$D/a.txt').write_text('x')\nPY")).toEqual([])
  })
  it("does not treat a script invocation's heredoc stdin as code", () => {
    expect(
      shellFileWrites("python3 app.py <<'EOF'\nPath('x.txt').write_text('x')\nEOF")
    ).toEqual([])
  })
  it("supports a one-line with-open body on the same line", () => {
    expect(
      shellFileWrites("python3 <<'PY'\nwith open('one.txt', 'w') as f: f.write('inline')\nPY")
    ).toEqual([{ path: "one.txt", contents: "inline", append: false }])
  })
})

describe("python statement anchoring (no false positives)", () => {
  it("ignores Python comments mentioning writers", () => {
    const command = [
      "python3 <<'PY'",
      "# Path('fake.txt').write_text('no')",
      "# with open('fake.txt', 'w') as f:",
      "print('hello')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([])
  })
  it("ignores writer-like text inside Python string literals", () => {
    const single = [
      "python3 <<'PY'",
      "doc = \"Path('fake.txt').write_text('no')\"",
      "PY",
    ].join("\n")
    expect(shellFileWrites(single)).toEqual([])
    const triple = [
      "python3 <<'PY'",
      "doc = '''",
      "Path('fake.txt').write_text('no')",
      "with open('fake.txt', 'w') as f:",
      "    f.write('no')",
      "'''",
      "PY",
    ].join("\n")
    expect(shellFileWrites(triple)).toEqual([])
  })
  it("recovers a real write that follows a decoy string literal", () => {
    const command = [
      "python3 <<'PY'",
      "doc = '''",
      "Path('fake.txt').write_text('no')",
      "'''",
      "Path('real.txt').write_text('yes')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([
      { path: "real.txt", contents: "yes", append: false },
    ])
  })
  it("ignores writes inside conditionals, loops, and functions", () => {
    const conditional = [
      "python3 <<'PY'",
      "if False:",
      "    Path('never.txt').write_text('no')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(conditional)).toEqual([])
    const loop = [
      "python3 <<'PY'",
      "for name in names:",
      "    with open('loop.txt', 'a') as f:",
      "        f.write(name)",
      "PY",
    ].join("\n")
    expect(shellFileWrites(loop)).toEqual([])
    const fn = [
      "python3 <<'PY'",
      "def save():",
      "    Path('maybe.txt').write_text('no')",
      "save()",
      "PY",
    ].join("\n")
    expect(shellFileWrites(fn)).toEqual([])
  })
  it("requires the write_text statement to stand alone (no trailing code)", () => {
    const chained =
      "python3 <<'PY'\nPath('a.txt').write_text('x') if cond else None\nPY"
    expect(shellFileWrites(chained)).toEqual([
      { path: "a.txt", contents: null, append: false },
    ])
  })
})

describe("python with-open block scoping (dedent)", () => {
  it("ends the block at the first dedent and ignores later writes", () => {
    const command = [
      "python3 <<'PY'",
      "with open('block.txt', 'w') as f:",
      "    f.write('inside')",
      "f2 = None",
      "f.write('after dedent — file is closed')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([
      { path: "block.txt", contents: "inside", append: false },
    ])
  })
  it("handles two sequential with-open blocks independently", () => {
    const command = [
      "python3 <<'PY'",
      "with open('a.txt', 'w') as f:",
      "    f.write('A')",
      "with open('b.txt', 'w') as f:",
      "    f.write('B')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([
      { path: "a.txt", contents: "A", append: false },
      { path: "b.txt", contents: "B", append: false },
    ])
  })
  it("downgrades block contents when the block contains non-write statements", () => {
    const command = [
      "python3 <<'PY'",
      "with open('mixed.txt', 'w') as f:",
      "    f.write('a')",
      "    if flag:",
      "        f.write('b')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([
      { path: "mixed.txt", contents: null, append: false },
    ])
  })
  it("allows blank lines and comments between literal writes in a block", () => {
    const command = [
      "python3 <<'PY'",
      "with open('c.txt', 'w') as f:",
      "    f.write('a')",
      "",
      "    # a comment",
      "    f.write('b')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([
      { path: "c.txt", contents: "ab", append: false },
    ])
  })
  it("keeps multi-line string contents inside a block from ending it early", () => {
    const command = [
      "python3 <<'PY'",
      "with open('doc.txt', 'w') as f:",
      "    f.write('''line1",
      "line2",
      "''')",
      "PY",
    ].join("\n")
    expect(shellFileWrites(command)).toEqual([
      { path: "doc.txt", contents: "line1\nline2\n", append: false },
    ])
  })
})

describe("no false positives", () => {
  it("ignores ordinary commands", () => {
    expect(shellFileWrites("python app.py")).toEqual([])
    expect(shellFileWrites("ls -la && pnpm test")).toEqual([])
    expect(shellFileWrites("cat a.txt")).toEqual([])
  })
  it("ignores shell comments mentioning writers", () => {
    expect(shellFileWrites("# cat > a.txt <<'EOF'\n# body\n# EOF")).toEqual([])
    expect(shellFileWrites("# echo 'x' > a.txt")).toEqual([])
  })
  it("does not scan writer-like text inside heredoc bodies", () => {
    const command =
      "cat > script.sh <<'EOF'\necho 'nested' > inner.txt\ncat > deep.txt <<'INNER'\nno\nINNER\nEOF"
    expect(shellFileWrites(command)).toEqual([
      {
        path: "script.sh",
        contents: "echo 'nested' > inner.txt\ncat > deep.txt <<'INNER'\nno\nINNER\n",
        append: false,
      },
    ])
  })
  it("does not match quoted prose that mentions redirection", () => {
    expect(shellFileWrites("git commit -m 'echo hi > a.txt'")).toEqual([])
    expect(shellFileWrites("grep 'cat > x' notes.md")).toEqual([])
  })
  it("returns an empty list for empty input", () => {
    expect(shellFileWrites("")).toEqual([])
  })
})
