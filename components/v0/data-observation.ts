// Pure parser for DataCommons / PopHIVE MCP tool outputs — no React, no DOM.
// The Agent API emits remote-MCP calls as `mcp_call` native items whose
// `output` is a JSON string; the dynamic `mcp_client` tool and direct MCP
// tools return MCP result envelopes ({content: [...]} / {structuredContent}).
// This module recognizes those payloads and projects them into a render-ready
// shape without inventing anything: answers, attribution, caveats, panel
// metadata, and source-specific dates pass through verbatim. Vitest covers it
// without a DOM (data-observation.test.ts).
//
// Field names are the servers' own, verified against live captures:
// - DataCommons get_observations / get_child_observations:
//   { variable, sourceMetadata{measurementMethod,observationPeriod,
//     provenanceUrl}, entityMetadata{columns,rows}, data{columns,rows} }
// - DataCommons get_variable_metadata: { status, variables{<dcid>:
//     {name, description, facets:[{id, provenanceId, obsCount, dateRange}]}} }
// - PopHIVE get_data: { answer, resolved, caveats[], provenance{file,
//     data_url, rows_matched, sources[], attribution}, data_through,
//     data_through_by_source{}, deeplink, rows[][], columns[],
//     panel{latest{value,date}} | panel{ranks{}, n_geographies} }

export type DataColumn = string

export type DataRow = Array<string | number | null>

/** One DataCommons facet from a metadata-only (get_variable_metadata) result.
 *  `method` (facet properties.measurementMethod) is the core distinction
 *  between facets of one variable — e.g. AgeAdjustedPrevalence vs
 *  CrudePrevalence — and `provenanceName`/`provenanceUrl` are resolved from
 *  the payload's own top-level `provenances` map (source + url properties). */
export type DataFacet = {
  id: string
  variable?: string
  method?: string
  unit?: string
  provenance: string
  /** Human source name (provenances[id].properties.source), or "". */
  provenanceName: string
  /** Validated http(s) source URL (provenances[id].properties.url), or "". */
  provenanceUrl: string
  dateRange: string
  observations: number | null
}

export type DataObservation = {
  /** MCP server label, e.g. "datacommons" | "pophive". */
  server: string
  /** Tool name, e.g. "get_child_observations" | "get_data". */
  tool: string
  /** Human title — the variable name (DataCommons) or the answer's topic. */
  title: string
  /** Unit label ONLY when the payload declares one; never inferred. */
  unit: string
  /** Verbatim server prose: PopHIVE `answer` markdown or a DataCommons
   *  variable description. Never summarized or rewritten. */
  answer: string
  /** Column headers for the primary table. */
  columns: DataColumn[]
  /** Primary table rows — ALL of them; the renderer must not hide any. */
  rows: DataRow[]
  /** Joined entity names by dcid (DataCommons entityMetadata). */
  entityNames: Record<string, string>
  /** Verbatim attribution line (PopHIVE provenance.attribution) or the
   *  DataCommons measurement-method provenance line. */
  attribution: string
  /** Validated http(s) URL naming the upstream dataset, or "". */
  sourceUrl: string
  /** Validated http(s) deeplink to the source's own UI, or "". */
  deeplink: string
  /** Headline strictly from panel.latest (PopHIVE) or the latest finite
   *  observation of a SINGLE-entity series. Never an arbitrary row of a
   *  multi-entity cross-section. */
  headline: { value: number; date: string } | null
  /** Every caveat line, verbatim. */
  caveats: string[]
  /** Server-reported ranks (panel.ranks), quoted verbatim — never computed. */
  ranks: Array<{ label: string; rank: number; of: number | null }>
  /** PopHIVE data_through — dataset freshness, distinct from headline.date. */
  dataThrough: string
  /** PopHIVE data_through_by_source — per-source freshness, preserved. */
  dataThroughBySource: Array<{ source: string; date: string }>
  /** provenance.sources, verbatim. */
  sources: string[]
  /** provenance.rows_matched — upstream match count vs. rows returned. */
  rowsMatched: number | null
  /** DataCommons metadata-only facets. */
  facets: DataFacet[]
}

type Rec = Record<string, unknown>

function asRec(value: unknown): Rec | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Rec
  }
  return null
}

function asStr(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function asStrArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : []
}

function asRows(value: unknown): DataRow[] {
  if (!Array.isArray(value)) return []
  return value.filter(Array.isArray) as DataRow[]
}

/** Accept only absolute http(s) URLs; anything else (javascript:, data:,
 *  relative paths, garbage) renders no link. */
export function safeHttpUrl(value: unknown): string {
  if (typeof value !== "string" || !value) return ""
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : ""
  } catch {
    return ""
  }
}

// Envelope unwrapping is bounded: mcp_client results nest the MCP result JSON
// inside a Strands tool-result content block, which nests the server payload
// inside another content block — each hop costs a string parse plus an array
// walk (2 levels), so the deepest real chain is ~6.
const MAX_ENVELOPE_DEPTH = 8

function looksLikePayload(rec: Rec): boolean {
  return (
    asRec(rec.data) !== null ||
    typeof rec.answer === "string" ||
    asRec(rec.variables) !== null
  )
}

/** Parse the raw output of an MCP-shaped tool call, tolerating a JSON string,
 *  a pre-parsed object, an MCP content-block array ([{type:"text",text}] or
 *  [{json}]), a {structuredContent} envelope, and nested tool-result
 *  envelopes. Parsing is STRICT JSON — malformed or truncated text (e.g.
 *  numbers or URLs broken across lines by display mangling) is rejected, not
 *  repaired, and yields null so callers fall back to raw rendering. */
export function parseMcpOutput(output: unknown, depth = 0): Rec | null {
  if (depth > MAX_ENVELOPE_DEPTH) return null

  const rec = asRec(output)
  if (rec) {
    if (looksLikePayload(rec)) return rec
    const structured = asRec(rec.structuredContent)
    if (structured) {
      return parseMcpOutput(structured, depth + 1) ?? structured
    }
    if (Array.isArray(rec.content)) {
      const unwrapped = parseMcpOutput(rec.content, depth + 1)
      if (unwrapped) return unwrapped
    }
    return rec
  }

  if (typeof output === "string") {
    const trimmed = output.trim()
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null
    try {
      const parsed: unknown = JSON.parse(trimmed)
      if (Array.isArray(parsed)) return parseMcpOutput(parsed, depth + 1)
      const parsedRec = asRec(parsed)
      return parsedRec ? parseMcpOutput(parsedRec, depth + 1) : null
    } catch {
      return null
    }
  }

  if (Array.isArray(output)) {
    // MCP content blocks: [{type:"text", text:"{...}"}] or [{json: {...}}].
    for (const block of output) {
      const blockRec = asRec(block)
      if (!blockRec) continue
      const jsonBlock = asRec(blockRec.json)
      if (jsonBlock) {
        const parsed = parseMcpOutput(jsonBlock, depth + 1)
        if (parsed) return parsed
      }
      const text = asStr(blockRec.text)
      if (text) {
        const parsed = parseMcpOutput(text, depth + 1)
        if (parsed) return parsed
      }
    }
  }

  return null
}

function emptyObservation(server: string, tool: string): DataObservation {
  return {
    server,
    tool,
    title: tool,
    unit: "",
    answer: "",
    columns: [],
    rows: [],
    entityNames: {},
    attribution: "",
    sourceUrl: "",
    deeplink: "",
    headline: null,
    caveats: [],
    ranks: [],
    dataThrough: "",
    dataThroughBySource: [],
    sources: [],
    rowsMatched: null,
    facets: [],
  }
}

/** DataCommons: tabular observations OR metadata-only variables/facets. */
function parseDataCommons(payload: Rec, server: string, tool: string): DataObservation | null {
  const data = asRec(payload.data)
  const variables = asRec(payload.variables)
  if (!data && !variables) return null

  const obs = emptyObservation(server, tool)

  if (data && Array.isArray(data.rows)) {
    const variable = asRec(payload.variable)
    const sourceMetadata = asRec(payload.sourceMetadata)
    const entityMetadata = asRec(payload.entityMetadata)

    for (const row of asRows(entityMetadata?.rows)) {
      const dcid = row[0]
      const name = row[1]
      if (typeof dcid === "string" && typeof name === "string") {
        obs.entityNames[dcid] = name
      }
    }

    obs.columns = asStrArray(data.columns)
    obs.rows = asRows(data.rows)
    obs.title = asStr(variable?.name) || asStr(variable?.dcid) || tool
    // Only an explicitly declared unit; DataCommons observation payloads
    // usually carry none, and none is ever invented.
    obs.unit = asStr(sourceMetadata?.unit)
    const measurementMethod = asStr(sourceMetadata?.measurementMethod)
    obs.attribution = measurementMethod
      ? `${measurementMethod} · DataCommons`
      : "DataCommons"
    obs.sourceUrl = safeHttpUrl(sourceMetadata?.provenanceUrl)

    // Headline ONLY for a single-entity series: promoting the last row of a
    // multi-entity cross-section would headline an arbitrary entity.
    const dateIdx = obs.columns.indexOf("date")
    const valueIdx = obs.columns.indexOf("value")
    const geoIdx = obs.columns.indexOf("observationAbout")
    if (dateIdx >= 0 && valueIdx >= 0) {
      const entities = new Set(
        geoIdx >= 0 ? obs.rows.map((r) => String(r[geoIdx] ?? "")) : []
      )
      if (entities.size <= 1) {
        const sorted = [...obs.rows].sort((a, b) =>
          String(a[dateIdx] ?? "").localeCompare(String(b[dateIdx] ?? ""))
        )
        for (let i = sorted.length - 1; i >= 0; i--) {
          const value = sorted[i][valueIdx]
          const date = sorted[i][dateIdx]
          if (typeof value === "number" && Number.isFinite(value) && typeof date === "string") {
            obs.headline = { value, date }
            break
          }
        }
      }
    }
    return obs
  }

  // Metadata-only: get_variable_metadata facets. No observations — surface
  // the variable identity, description, and every facet's provenance. The
  // payload's top-level `provenances` map resolves provenanceId → human
  // source name + url (live shape: provenances[id].properties.{source,url}).
  if (variables) {
    const provenances = asRec(payload.provenances)
    const provenanceMeta = (id: string): { name: string; url: string } => {
      const props = asRec(asRec(provenances?.[id])?.properties)
      const url = props?.url
      return {
        name: asStr(props?.source),
        url: safeHttpUrl(Array.isArray(url) ? url[0] : url),
      }
    }

    const names: string[] = []
    for (const [dcid, entry] of Object.entries(variables)) {
      const rec = asRec(entry)
      if (!rec) continue
      const name = asStr(rec.name) || dcid
      names.push(name)
      if (!obs.answer) obs.answer = asStr(rec.description)
      const facets = Array.isArray(rec.facets) ? rec.facets : []
      for (const facet of facets) {
        const facetRec = asRec(facet)
        if (!facetRec) continue
        const range = asRec(facetRec.dateRange)
        const start = asStr(range?.start)
        const end = asStr(range?.end)
        const provenanceId = asStr(facetRec.provenanceId)
        const meta = provenanceMeta(provenanceId)
        obs.facets.push({
          id: asStr(facetRec.id),
          variable: name,
          method: asStr(asRec(facetRec.properties)?.measurementMethod),
          unit: asStr(asRec(facetRec.properties)?.unit),
          provenance: provenanceId,
          provenanceName: meta.name,
          provenanceUrl: meta.url,
          dateRange: start || end ? [start, end].filter(Boolean).join(" – ") : "",
          observations:
            typeof facetRec.obsCount === "number" && Number.isFinite(facetRec.obsCount)
              ? facetRec.obsCount
              : null,
        })
      }
    }
    if (names.length === 0 && obs.facets.length === 0) return null
    obs.title = names.join(" · ") || tool
    obs.attribution = "DataCommons"
    return obs
  }

  return null
}

/** PopHIVE get_data: prose answer + provenance, optionally a tabular slice
 *  with panel metadata. Metadata-only calls (about/catalog) carry empty rows
 *  and render as a prose card. */
function parsePopHive(payload: Rec, server: string, tool: string): DataObservation | null {
  const answer = asStr(payload.answer)
  const provenance = asRec(payload.provenance)
  const rows = asRows(payload.rows)
  if (!answer && rows.length === 0 && !provenance) return null

  const obs = emptyObservation(server, tool)
  obs.answer = answer
  obs.columns = asStrArray(payload.columns)
  obs.rows = rows
  obs.caveats = asStrArray(payload.caveats)

  const resolved = asRec(payload.resolved)
  const panel = asRec(payload.panel)

  // Title from the answer's leading "topic (view) —" fragment when present.
  const firstLine = answer.split("\n").find((line) => line.trim()) ?? ""
  const topicMatch = firstLine.match(/^(.+?)\s*\((\w+)\)\s*—/)
  obs.title = topicMatch
    ? topicMatch[1].replace(/\b\w/g, (c) => c.toUpperCase())
    : asStr(resolved?.geography) || server

  // Headline strictly from the server's own panel.latest — never derived
  // from the rows (a cross-section's rows have no single "latest").
  const latest = asRec(panel?.latest)
  if (
    typeof latest?.value === "number" &&
    Number.isFinite(latest.value) &&
    typeof latest?.date === "string"
  ) {
    obs.headline = { value: latest.value, date: latest.date }
  }

  // Server-reported ranks, quoted verbatim.
  const ranksRec = asRec(panel?.ranks)
  const of =
    typeof panel?.n_geographies === "number" && Number.isFinite(panel.n_geographies)
      ? panel.n_geographies
      : null
  if (ranksRec) {
    for (const [label, rank] of Object.entries(ranksRec)) {
      if (typeof rank === "number" && Number.isFinite(rank)) {
        obs.ranks.push({ label, rank, of })
      }
    }
  }

  obs.attribution = asStr(provenance?.attribution) || "PopHIVE"
  obs.sourceUrl = safeHttpUrl(provenance?.data_url)
  obs.deeplink = safeHttpUrl(payload.deeplink)
  obs.sources = asStrArray(provenance?.sources)
  obs.rowsMatched =
    typeof provenance?.rows_matched === "number" && Number.isFinite(provenance.rows_matched)
      ? provenance.rows_matched
      : null
  obs.dataThrough = asStr(payload.data_through)
  const bySource = asRec(payload.data_through_by_source)
  if (bySource) {
    for (const [source, date] of Object.entries(bySource)) {
      if (typeof date === "string") obs.dataThroughBySource.push({ source, date })
    }
  }
  // Units vary by source (stated in the answer prose); never a single
  // fabricated unit label.
  return obs
}

/** Direct MCP tool names → the server they belong to, from the exact
 *  allowlist in orchestrator/agent_api_tools.py. Used when a dynamic tool
 *  part carries only the remote tool's name. */
const DATACOMMONS_TOOL_NAMES: ReadonlySet<string> = new Set([
  "search_indicators",
  "search_child_indicators",
  "get_variable_metadata",
  "get_observations",
  "get_child_observations",
  "get_multi_entity_observations",
])

export function inferMcpServer(toolName: string): string | null {
  if (DATACOMMONS_TOOL_NAMES.has(toolName)) return "datacommons"
  if (toolName === "get_data") return "pophive"
  return null
}

/** Recognize an MCP result as a data observation panel, or null when the
 *  payload is not one of the known shapes (callers fall back to raw
 *  rendering — nothing is hidden). */
export function parseDataObservation(
  serverLabel: string,
  toolName: string,
  output: unknown
): DataObservation | null {
  const payload = parseMcpOutput(output)
  if (!payload) return null
  const server = serverLabel.toLowerCase()
  if (server.includes("datacommons")) {
    return parseDataCommons(payload, serverLabel, toolName)
  }
  if (server.includes("pophive")) {
    return parsePopHive(payload, serverLabel, toolName)
  }
  return null
}

// ---------------------------------------------------------------------------
// Chart projection (pure, tested without a DOM).

export type ChartPoint = { label: string; value: number }

export type ChartSeries = {
  kind: "bars" | "line" | "none"
  points: ChartPoint[]
  /** Points drawn vs. total available — the renderer labels any truncation
   *  so a capped chart is never presented as a full ranking. */
  shown: number
  total: number
  note: string
}

const MAX_BARS = 16
const MAX_POINTS = 24

const NO_CHART: ChartSeries = { kind: "none", points: [], shown: 0, total: 0, note: "" }

/** Project an observation's rows into one honest chart:
 *  - many entities at ≤2 dates → ranked bars (capped, truncation labeled)
 *  - one entity across dates → time series (trailing window, labeled)
 *  - multiple sources mixed in one table → NO chart (units differ by source)
 *  - anything ambiguous → no chart; the full table always carries the data */
export function seriesFrom(obs: DataObservation): ChartSeries {
  const { columns, rows, entityNames } = obs
  if (rows.length === 0) return NO_CHART
  const valueIdx = columns.indexOf("value")
  if (valueIdx < 0) return NO_CHART

  const dateIdx = columns.indexOf("date")
  const geoIdx =
    columns.indexOf("observationAbout") >= 0
      ? columns.indexOf("observationAbout")
      : columns.indexOf("geography")
  const sourceIdx = columns.indexOf("source")

  const numeric = rows.filter(
    (r) => typeof r[valueIdx] === "number" && Number.isFinite(r[valueIdx] as number)
  )
  if (numeric.length === 0) return NO_CHART
  // Never interpolate a missing/suppressed observation or combine units.
  const suppressed = columns.map((name, index) => /^(suppressed|suppressed_flag|flag_suppressed|is_state_estimate)$/.test(name) ? index : -1).filter((index) => index >= 0)
  if (numeric.length !== rows.length || rows.some((row) => suppressed.some((index) => row[index] === 1 || String(row[index]).toLowerCase() === "true"))) {
    return { ...NO_CHART, note: "Missing or flagged observations — see the full table" }
  }
  const unitIdx = columns.indexOf("unit")
  if (unitIdx >= 0 && new Set(rows.map((row) => row[unitIdx])).size > 1) {
    return { ...NO_CHART, note: "Multiple units — see the full table" }
  }
  if (numeric.some((row) => (row[valueIdx] as number) < 0)) {
    return { ...NO_CHART, note: "Signed values — see the full table" }
  }

  // Never chart across sources — their units are not comparable.
  if (sourceIdx >= 0) {
    const sources = new Set(numeric.map((r) => String(r[sourceIdx] ?? "")))
    if (sources.size > 1) {
      return { ...NO_CHART, note: "Multiple sources — see table (units differ by source)" }
    }
  }

  const entities = new Set(
    geoIdx >= 0 ? numeric.map((r) => String(r[geoIdx] ?? "")) : []
  )
  const dates = new Set(
    dateIdx >= 0 ? numeric.map((r) => String(r[dateIdx] ?? "")) : []
  )

  // Cross-section: one value per entity at (essentially) one time.
  if (geoIdx >= 0 && entities.size > 1 && dates.size === 1) {
    if (entities.size !== numeric.length) return NO_CHART
    const latest = new Map<string, number>()
    for (const row of numeric) {
      const key = String(row[geoIdx] ?? "")
      if (!latest.has(key)) latest.set(key, row[valueIdx] as number)
    }
    const all = [...latest.entries()]
      .map(([key, value]) => ({ label: entityNames[key] ?? key, value }))
      .sort((a, b) => b.value - a.value)
    const points = all.slice(0, MAX_BARS)
    const note =
      all.length > points.length
        ? `Showing ${points.length} of ${all.length} values · largest first`
        : `${all.length} values · largest first`
    return { kind: "bars", points, shown: points.length, total: all.length, note }
  }

  // Time series: a single entity (or no entity column) across dates.
  if (entities.size <= 1 && dateIdx >= 0 && dates.size > 1) {
    if (dates.size !== numeric.length || new Set([...dates].map((date) => date.length)).size > 1) return NO_CHART
    const sorted = [...numeric].sort((a, b) =>
      String(a[dateIdx] ?? "").localeCompare(String(b[dateIdx] ?? ""))
    )
    const windowed = sorted.slice(-MAX_POINTS)
    const points = windowed.map((row) => ({
      label: String(row[dateIdx] ?? ""),
      value: row[valueIdx] as number,
    }))
    const note =
      sorted.length > points.length
        ? `Last ${points.length} of ${sorted.length} points`
        : ""
    return { kind: "line", points, shown: points.length, total: sorted.length, note }
  }

  // Mixed panel (many entities × many dates) — the table is the honest view.
  return NO_CHART
}

/** Format a numeric value for display: compact for large magnitudes. */
export function formatValue(value: number): string {
  if (!Number.isFinite(value)) return "—"
  const abs = Math.abs(value)
  if (abs > 0 && abs < 0.01) return new Intl.NumberFormat("en", { maximumSignificantDigits: 3 }).format(value)
  if (abs >= 1_000_000) {
    return new Intl.NumberFormat("en", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value)
  }
  if (abs >= 100) return new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(value)
  if (abs >= 1) return new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(value)
  return new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(value)
}
