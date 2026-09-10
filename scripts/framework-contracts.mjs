import fs from "node:fs"
import path from "node:path"
import ts from "typescript"

const slash = value => value.split(path.sep).join("/")
export const isTypedSource = file => /^(app|components|lib|hooks)\/.+\.[cm]?[jt]sx?$/.test(file)

/** Compiler-backed checks: installed .d.ts and vendored source ARE the contract.
 * One native language service per plugin instance reuses parses/types between
 * edits. Only the changed source and its transitive importers get diagnostics.
 * No application code is executed and no handwritten prop registry is involved.
 */
export function createContractChecker(root) {
  root = path.resolve(root)
  let overrides = new Map()
  let names = []
  let options = {}
  let configErrors = []
  let previousImporters = new Map()
  let projectVersion = 0
  const snapshots = new Map()
  const canonical = file => path.resolve(file)
  const read = file => {
    const key = canonical(file)
    if (overrides.has(key)) return overrides.get(key) ?? undefined
    return ts.sys.readFile(file)
  }
  const exists = file => overrides.has(canonical(file)) ? overrides.get(canonical(file)) !== null : ts.sys.fileExists(file)
  const host = {
    getScriptFileNames: () => names,
    getScriptVersion: file => {
      const key = canonical(file)
      if (overrides.has(key)) return `${projectVersion}:overlay`
      try { const stat = fs.statSync(file); return `${stat.mtimeMs}:${stat.ctimeMs}:${stat.size}` } catch { return "missing" }
    },
    getScriptSnapshot: file => {
      const key = canonical(file)
      const version = host.getScriptVersion(file)
      const previous = snapshots.get(key)
      if (previous?.version === version) return previous.snapshot
      const content = read(file)
      if (content === undefined) return undefined
      const snapshot = ts.ScriptSnapshot.fromString(content)
      snapshots.set(key, { version, snapshot })
      return snapshot
    },
    getCurrentDirectory: () => root,
    getCompilationSettings: () => options,
    getDefaultLibFileName: opts => ts.getDefaultLibFilePath(opts),
    getProjectVersion: () => String(projectVersion),
    fileExists: exists,
    readFile: read,
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
    realpath: ts.sys.realpath,
    useCaseSensitiveFileNames: () => ts.sys.useCaseSensitiveFileNames,
  }
  const service = ts.createLanguageService(host, ts.createDocumentRegistry())

  function configure() {
    const configPath = path.join(root, "tsconfig.json")
    if (!fs.existsSync(configPath)) throw new Error("tsconfig.json missing: compiler contracts were not checked")
    const config = ts.readConfigFile(configPath, read)
    if (config.error) { configErrors = [config.error]; return }
    const parsed = ts.parseJsonConfigFileContent(config.config, {
      ...ts.sys,
      readFile: read,
      fileExists: exists,
      readDirectory: (dir, extensions, excludes, includes, depth) => ts.sys.readDirectory(
        dir, extensions, [...(excludes ?? []), "**/.kilo/**", "**/.worktrees/**", "**/.next/**", "**/node_modules/**", "**/.venv/**"], includes, depth,
      ),
    }, root)
    // Missing-input diagnostic may be resolved by the proposed new source file.
    configErrors = parsed.errors.filter(error => error.code !== 18003)
    options = { ...parsed.options, noEmit: true, incremental: false }
    names = parsed.fileNames.filter(file => isTypedSource(slash(path.relative(root, file))) && exists(file))
    for (const [file, content] of overrides) {
      if (content !== null && isTypedSource(slash(path.relative(root, file))) && !names.includes(file)) names.push(file)
    }
  }

  function diagnostics(changedFiles, proposed = {}) {
    overrides = new Map(Object.entries(proposed).map(([file, value]) => [path.resolve(root, file), value]))
    projectVersion++
    configure()
    const result = [...configErrors]
    if (!configErrors.length) {
      const program = service.getProgram()
      if (!program) throw new Error("TypeScript language service could not create a program")
      const sources = program.getSourceFiles().filter(source => isTypedSource(slash(path.relative(root, source.fileName))))
      const importers = new Map()
      for (const source of sources) {
        for (const reference of ts.preProcessFile(source.text, true, true).importedFiles) {
          const target = ts.resolveModuleName(reference.fileName, source.fileName, options, host).resolvedModule
          if (!target) continue
          const key = canonical(target.resolvedFileName)
          if (!importers.has(key)) importers.set(key, new Set())
          importers.get(key).add(canonical(source.fileName))
        }
      }
      const affected = new Set(changedFiles.map(file => path.resolve(root, file)))
      // The old graph matters when an export/file is deleted or an import can no
      // longer resolve. The new graph alone would lose precisely those callers.
      for (const file of affected) {
        for (const importer of importers.get(file) ?? []) affected.add(importer)
        for (const importer of previousImporters.get(file) ?? []) affected.add(importer)
      }
      previousImporters = importers
      if (changedFiles.some(file => file.endsWith(".d.ts"))) {
        for (const source of sources) affected.add(canonical(source.fileName))
      }
      for (const file of affected) {
        if (!isTypedSource(slash(path.relative(root, file))) || !program.getSourceFile(file)) continue
        result.push(...service.getSyntacticDiagnostics(file), ...service.getSemanticDiagnostics(file))
      }
    }
    return result.filter(item => item.category === ts.DiagnosticCategory.Error).map(item => {
      const position = item.file?.getLineAndCharacterOfPosition(item.start ?? 0)
      return {
        file: item.file ? slash(path.relative(root, item.file.fileName)) : "tsconfig.json",
        line: position ? position.line + 1 : 1,
        rule: `TS${item.code}`,
        severity: "error",
        message: ts.flattenDiagnosticMessageText(item.messageText, " "),
      }
    })
  }
  return { diagnostics, dispose: () => service.dispose() }
}

/** Multiset identity includes the file, not its line. Existing errors that move
 * remain baseline errors; identical new errors in another file are not hidden.
 */
export function newDiagnostics(before, after) {
  const counts = new Map()
  const key = item => `${item.file}\0${item.rule}\0${item.message}`
  for (const item of before) counts.set(key(item), (counts.get(key(item)) ?? 0) + 1)
  return after.filter(item => {
    const remaining = counts.get(key(item)) ?? 0
    if (remaining) { counts.set(key(item), remaining - 1); return false }
    return true
  })
}
