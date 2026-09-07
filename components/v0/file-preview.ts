// Pure helpers for previewing a selected IDE file. No React, no DOM —
// project-ide-panel.tsx composes the actual preview surfaces (sandboxed
// iframe for HTML, native MessageResponse for Markdown, native JSXPreview
// for JSX/TSX). Vitest covers this module directly.
//
// JSX/TSX limitation (deliberate): JSXPreview wraps react-jsx-parser, a
// static JSX renderer — NOT a JS compiler or module runtime. componentJsx
// below extracts one static component's returned JSX via the TypeScript AST
// without evaluating any code; anything needing imports, props, hooks, or
// module logic is rejected with an explicit message telling the user to run
// the project and use the Preview view (real iframe) instead. We never fake
// module execution in the parent window.

export type FilePreviewKind = "html" | "markdown" | "jsx"

/** Which preview surface a file path supports, or null for code-only files. */
export function filePreviewKind(path: string): FilePreviewKind | null {
  const ext = path.split(".").pop()?.toLowerCase() ?? ""
  if (ext === "html" || ext === "htm" || ext === "xhtml") return "html"
  if (ext === "md" || ext === "markdown" || ext === "mdx") return "markdown"
  if (ext === "jsx" || ext === "tsx") return "jsx"
  return null
}

/** Extract a static component's JSX without running its module. Load the TS
 * parser only when preview is requested, not in the initial chat bundle. */
export async function componentJsx(source: string): Promise<string> {
  const ts = await import("typescript")
  const file = ts.createSourceFile("preview.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const unwrap = (node: import("typescript").Expression): import("typescript").Expression =>
    ts.isParenthesizedExpression(node) ? unwrap(node.expression) : node
  const fromFunction = (node: import("typescript").FunctionDeclaration | import("typescript").ArrowFunction | import("typescript").FunctionExpression) => {
    if (node.parameters.length) throw new Error("Components with props need the running project Preview.")
    if (!node.body) throw new Error("Components with hooks or logic need the running project Preview.")
    if (!ts.isBlock(node.body)) return unwrap(node.body)
    if (node.body.statements.length !== 1 || !ts.isReturnStatement(node.body.statements[0]) || !node.body.statements[0].expression) throw new Error("Components with hooks or logic need the running project Preview.")
    return unwrap(node.body.statements[0].expression)
  }
  const hasModifier = (statement: import("typescript").Statement, kind: import("typescript").SyntaxKind) =>
    ts.canHaveModifiers(statement) &&
    Boolean(ts.getModifiers(statement)?.some((m) => m.kind === kind))
  type Candidate = {
    expression: import("typescript").Expression
    name: string
    isDefault: boolean
    isExported: boolean
  }
  const candidates: Candidate[] = []
  let defaultExportName = ""
  const namedExports = new Set<string>()
  for (const statement of file.statements) {
    const isExported = hasModifier(statement, ts.SyntaxKind.ExportKeyword)
    const isDefault = hasModifier(statement, ts.SyntaxKind.DefaultKeyword)
    if (ts.isImportDeclaration(statement)) {
      // Type-only imports never execute; a value import is allowed for
      // "react" only (JSX pragma habit), everything else needs a bundler.
      if (statement.importClause?.isTypeOnly) continue
      if (!ts.isStringLiteral(statement.moduleSpecifier) || statement.moduleSpecifier.text !== "react") throw new Error("Imported components need the running project Preview.")
    } else if (
      // Type-only declarations are erased at runtime — common in TSX files.
      ts.isInterfaceDeclaration(statement) ||
      ts.isTypeAliasDeclaration(statement)
    ) {
      continue
    } else if (ts.isFunctionDeclaration(statement)) {
      candidates.push({
        expression: fromFunction(statement),
        name: statement.name?.text ?? "",
        isDefault,
        isExported,
      })
    } else if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        const value = declaration.initializer && unwrap(declaration.initializer)
        if (!value || !(ts.isArrowFunction(value) || ts.isFunctionExpression(value))) throw new Error("Module variables need the running project Preview.")
        candidates.push({
          expression: fromFunction(value),
          name: ts.isIdentifier(declaration.name) ? declaration.name.text : "",
          isDefault: false,
          isExported,
        })
      }
    } else if (ts.isExpressionStatement(statement)) {
      candidates.push({
        expression: unwrap(statement.expression),
        name: "",
        isDefault: false,
        isExported: false,
      })
    } else if (ts.isExportAssignment(statement)) {
      const expression = unwrap(statement.expression)
      if (ts.isArrowFunction(expression) || ts.isFunctionExpression(expression)) {
        candidates.push({ expression: fromFunction(expression), name: "", isDefault: true, isExported: true })
      } else if (ts.isIdentifier(expression)) {
        // `export default App` — mark the earlier declaration as the default.
        defaultExportName = expression.text
      } else {
        candidates.push({ expression, name: "", isDefault: true, isExported: true })
      }
    } else if (ts.isExportDeclaration(statement)) {
      // `export { App }` re-export lists are fine WITHOUT a module specifier;
      // `export { X } from "mod"` needs that module and therefore a bundler.
      if (statement.moduleSpecifier) throw new Error("Imported components need the running project Preview.")
      if (statement.exportClause && ts.isNamedExports(statement.exportClause)) {
        for (const element of statement.exportClause.elements) {
          namedExports.add(element.name.text)
        }
      }
    } else if (!ts.isEmptyStatement(statement)) {
      throw new Error("This module needs the running project Preview.")
    }
  }
  for (const candidate of candidates) {
    if (candidate.name && candidate.name === defaultExportName) candidate.isDefault = true
    if (candidate.name && namedExports.has(candidate.name)) candidate.isExported = true
  }
  // Selection: a lone candidate wins; with several, prefer the default
  // export, then a single exported component — never guess between peers.
  let selected: Candidate | undefined
  if (candidates.length === 1) selected = candidates[0]
  else {
    const defaults = candidates.filter((c) => c.isDefault)
    const exported = candidates.filter((c) => c.isExported)
    selected =
      defaults.length === 1
        ? defaults[0]
        : exported.length === 1
          ? exported[0]
          : undefined
  }
  if (!selected) throw new Error("Select a file containing one static React component or JSX expression.")
  const jsx = selected.expression
  if (!ts.isJsxElement(jsx) && !ts.isJsxSelfClosingElement(jsx) && !ts.isJsxFragment(jsx)) throw new Error("No renderable JSX was found.")
  // Literal-only JSX expressions ({"text"}, {42}, {`raw`}) are static data,
  // not code — allow them; anything referencing identifiers/calls is dynamic.
  const isStaticLiteral = (expr: import("typescript").Expression): boolean =>
    ts.isStringLiteral(expr) ||
    ts.isNumericLiteral(expr) ||
    ts.isNoSubstitutionTemplateLiteral(expr) ||
    expr.kind === ts.SyntaxKind.TrueKeyword ||
    expr.kind === ts.SyntaxKind.FalseKeyword ||
    expr.kind === ts.SyntaxKind.NullKeyword
  const check = (node: import("typescript").Node) => {
    if (ts.isJsxSpreadAttribute(node) || (ts.isJsxExpression(node) && node.expression && !isStaticLiteral(node.expression))) throw new Error("Dynamic JSX expressions need the running project Preview.")
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(file)
      if (!/^[a-z][a-z0-9-]*$/.test(tag) || ["script", "iframe", "object", "embed", "style", "link", "meta", "base"].includes(tag)) throw new Error("This element is not supported in the static JSX preview.")
    }
    ts.forEachChild(node, check)
  }
  check(jsx)
  return jsx.getText(file)
}

