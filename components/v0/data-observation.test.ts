// Parser + chart-projection tests for components/v0/data-observation.ts.
// Fixtures mirror live captures of the DataCommons and PopHIVE MCP servers
// (shapes verified against the mcp_call outputs recorded in session logs and
// the user-supplied payloads): a DataCommons state cross-section, a PopHIVE
// national time series whose data_through differs from the headline date, a
// PopHIVE cross-section with server ranks, metadata-only results from both
// servers, and MCP envelope / malformed-input handling.

import { describe, expect, it } from "vitest"

import {
  formatValue,
  inferMcpServer,
  parseDataObservation,
  parseMcpOutput,
  safeHttpUrl,
  seriesFrom,
  type DataObservation,
} from "./data-observation"

describe("conservative chart projection", () => {
  const observation = (rows: unknown[], columns = ["date", "value"]) => parseDataObservation("pophive", "get_data", { answer: "Returned observations", rows, columns })!
  it("does not bridge missing values or treat suppression as measured data", () => {
    expect(seriesFrom(observation([["2026-01", 1], ["2026-02", null], ["2026-03", 2]])).kind).toBe("none")
    expect(seriesFrom(observation([["2026-01", 5, 1], ["2026-02", 8, 0]], ["date", "value", "suppressed_flag"])).kind).toBe("none")
  })
  it("keeps mixed units, mixed cadence and signed values in the full table", () => {
    expect(seriesFrom(observation([["2026-01", 1, "%"], ["2026-02", 2, "count"]], ["date", "value", "unit"])).kind).toBe("none")
    expect(seriesFrom(observation([["2025", 1], ["2026-02-01", 2]])).kind).toBe("none")
    expect(seriesFrom(observation([["2026-01", -1], ["2026-02", 2]])).kind).toBe("none")
    expect(formatValue(0.0004)).toBe("0.0004")
  })
  it("retains metadata variable and measurement-method distinctions", () => {
    const parsed = parseDataObservation("datacommons", "get_variable_metadata", { variables: { diabetes: { name: "Diabetes", facets: [{ id: "a", properties: { measurementMethod: "AgeAdjustedPrevalence", unit: "%" } }, { id: "b", properties: { measurementMethod: "CrudePrevalence" } }] } } })!
    expect(parsed.facets.map((facet) => facet.method)).toEqual(["AgeAdjustedPrevalence", "CrudePrevalence"])
    expect(parsed.facets[0].variable).toBe("Diabetes")
  })
})

// --- Fixtures --------------------------------------------------------------

const dataCommonsCrossSection = {
  variable: {
    dcid: "Count_Person",
    name: "Total population",
    typeOf: ["StatisticalVariable"],
  },
  sourceMetadata: {
    sourceId: "8912910856362438925",
    measurementMethod: "CensusPEPSurvey",
    observationPeriod: "P1Y",
    provenanceUrl: "https://www.census.gov/programs-surveys/popest.html",
  },
  entityMetadata: {
    columns: ["dcid", "name", "typeOf"],
    rows: [
      ["geoId/01", "Alabama", ["State"]],
      ["geoId/06", "California", ["State"]],
      ["geoId/12", "Florida", ["State"]],
      ["geoId/36", "New York", ["State"]],
      ["geoId/48", "Texas", ["State"]],
    ],
  },
  data: {
    columns: ["observationAbout", "date", "value"],
    rows: [
      ["geoId/01", "2025", 5193088],
      ["geoId/06", "2025", 39355309],
      ["geoId/12", "2025", 23462518],
      ["geoId/36", "2025", 20002427],
      ["geoId/48", "2025", 31853822],
    ],
  },
}

const dataCommonsSeries = {
  variable: { dcid: "Count_Person", name: "Total population" },
  sourceMetadata: {
    measurementMethod: "CensusPEPSurvey",
    provenanceUrl: "https://www.census.gov/programs-surveys/popest.html",
  },
  data: {
    columns: ["observationAbout", "date", "value"],
    rows: [
      ["geoId/06", "2023", 39198693],
      ["geoId/06", "2024", 39431263],
      ["geoId/06", "2025", 39355309],
    ],
  },
}

// get_variable_metadata: metadata-only, no observations. The live response
// carries per-facet properties (measurementMethod / unit / observationPeriod)
// and a top-level `provenances` map resolving provenanceId → human source
// name + url + license (shape verified against the live capture).
const dataCommonsMetadata = {
  status: "SUCCESS",
  variables: {
    Count_Person: {
      id: "Count_Person",
      name: "Total population",
      description: "The total number of people in a population.",
      facets: [
        {
          id: "10169881228856630405",
          provenanceId: "dc/base/CensusACS5YearSurvey",
          obsCount: 70,
          dateRange: { start: "2011", end: "2024" },
          properties: { measurementMethod: "CensusACS5yrSurvey" },
        },
        {
          id: "12850099660527362240",
          provenanceId: "dc/base/WikidataPopulation",
          obsCount: 91,
          dateRange: { start: "1850", end: "2020-04-01" },
          properties: { measurementMethod: "WikidataPopulation" },
        },
      ],
    },
    LifeExpectancy_Person: {
      id: "LifeExpectancy_Person",
      name: "Life Expectancy",
      facets: [
        {
          id: "17310021153417057128",
          provenanceId: "dc/base/OECDRegionalDemography_LifeExpectancy",
          obsCount: 30,
          dateRange: { start: "1990", end: "2020" },
          properties: {
            unit: "Year",
            measurementMethod: "OECDRegionalStatistics",
            observationPeriod: "P1Y",
          },
        },
      ],
    },
  },
  provenances: {
    "dc/base/CensusACS5YearSurvey": {
      id: "dc/base/CensusACS5YearSurvey",
      properties: {
        source: "U.S. Census Bureau",
        url: "https://www.census.gov/programs-surveys/acs.html",
        license: "https://www.census.gov/about/policies/open-gov/open-data.html",
        licenseType: "PublicDomain",
      },
    },
    "dc/base/OECDRegionalDemography_LifeExpectancy": {
      id: "dc/base/OECDRegionalDemography_LifeExpectancy",
      properties: {
        source: "Organisation for Economic Co-operation and Development (OECD)",
        url: "https://data-explorer.oecd.org/vis?df[id]=DSD_REG_DEMO",
      },
    },
    // WikidataPopulation intentionally absent: unresolved ids must fall back
    // to the raw provenanceId, never an invented name.
  },
}

// PopHIVE national time series. NOTE: data_through (2026-09-05) intentionally
// differs from the panel.latest date (2026-08-29) — they are different facts.
const popHiveSeries = {
  answer:
    "influenza (overall_trend) — bundle_respiratory/dist/flu_overall_trends.parquet. " +
    "Panel summary: Latest: 0.15% of ED visits (week ending 2026-08-29) — also the peak in this slice. " +
    "The most recent 2 weeks are preliminary and may be revised.",
  resolved: { geography: "United States", geo_level: "national", source: "CDC NSSP" },
  caveats: [
    "Preliminary: NSSP and NHSN values for the most recent 2 weeks are subject to upward revision.",
    "ILINet measures outpatient influenza-like illness, not laboratory-confirmed influenza.",
    "Kinsa readings come from smart thermometers in near real time (daily); clinical signals such as ED visits and hospitalizations are reported with a 1–2 week lag.",
    "NWSS wastewater viral activity is scaled standard deviations above a dynamic baseline.",
  ],
  provenance: {
    file: "data/bundle_respiratory/dist/flu_overall_trends.parquet",
    data_url:
      "https://raw.githubusercontent.com/PopHIVE/Ingest/main/data/bundle_respiratory/dist/flu_overall_trends.parquet",
    rows_matched: 1449,
    sources: ["CDC ILINet", "CDC NHSN", "CDC NSSP", "Kinsa"],
    attribution:
      "Data: CDC ILINet, CDC NHSN, CDC NSSP, CDC NWSS, CDC RespNET, Delphi, Epic Cosmos, and Kinsa data, obtained via PopHIVE, Yale School of Public Health (DOI 10.5281/zenodo.17345935). Epic Cosmos data require attribution to Epic Cosmos.",
  },
  data_through: "2026-09-05",
  data_through_by_source: {
    "CDC NSSP": "2026-08-29",
    "Epic Cosmos, ED": "2026-08-22",
  },
  deeplink: "https://www.pophive.org/infectious-diseases/influenza",
  rows: [
    ["2026-08-15", "United States", "CDC NSSP", 0.1],
    ["2026-08-22", "United States", "CDC NSSP", 0.12],
    ["2026-08-29", "United States", "CDC NSSP", 0.15],
  ],
  columns: ["date", "geography", "source", "value"],
  panel: { latest: { value: 0.15, date: "2026-08-29" } },
}

// PopHIVE cross-section with verbatim server ranks.
const popHiveCrossSection = {
  answer:
    "influenza (overall_trend) — latest value per geography (5 geographies, week ending 2026-08-29). " +
    "Panel summary: California ranks 9 of 50 geographies with data.",
  resolved: { source: "CDC NSSP" },
  caveats: ["Preliminary: NSSP values for the most recent 2 weeks are subject to upward revision."],
  provenance: {
    data_url:
      "https://raw.githubusercontent.com/PopHIVE/Ingest/main/data/bundle_respiratory/dist/flu_overall_trends.parquet",
    rows_matched: 33166,
    sources: ["CDC NSSP"],
    attribution: "Data obtained via PopHIVE, Yale School of Public Health.",
  },
  data_through: "2026-09-05",
  rows: [
    ["Florida", "2026-08-29", "CDC NSSP", 0.3],
    ["Texas", "2026-08-29", "CDC NSSP", 0.29],
    ["California", "2026-08-29", "CDC NSSP", 0.2],
    ["Connecticut", "2026-08-29", "CDC NSSP", 0.05],
    ["New York", "2026-08-29", "CDC NSSP", 0.05],
  ],
  columns: ["geography", "date", "source", "value"],
  panel: {
    ranks: { California: 9, Texas: 5, Connecticut: 40 },
    n_geographies: 50,
  },
}

// PopHIVE metadata-only (about): markdown answer, no rows.
const popHiveAbout = {
  answer: "# About PopHIVE\n\nPopHIVE is the Yale School of Public Health's harmonized dataset.",
  provenance: {
    file: "ABOUT.md",
    data_url: "https://raw.githubusercontent.com/PopHIVE/Ingest/main/data/ABOUT.md",
    rows_matched: 0,
    sources: [],
    attribution:
      "Data: PopHIVE, Yale School of Public Health (DOI 10.5281/zenodo.17345935). Upstream sources retain their own terms; Epic Cosmos and Google Health Trends data require attribution to those providers.",
  },
  deeplink: "https://www.pophive.org",
  rows: [],
  columns: [],
}

// --- parseMcpOutput --------------------------------------------------------

describe("parseMcpOutput", () => {
  it("passes a pre-parsed object through", () => {
    expect(parseMcpOutput(popHiveSeries)).toMatchObject({ answer: popHiveSeries.answer })
  })

  it("parses a JSON string", () => {
    const parsed = parseMcpOutput(JSON.stringify(dataCommonsCrossSection))
    expect(parsed?.variable).toMatchObject({ dcid: "Count_Person" })
  })

  it("unwraps MCP text content blocks", () => {
    const parsed = parseMcpOutput([
      { type: "text", text: JSON.stringify(popHiveSeries) },
    ])
    expect(parsed?.data_through).toBe("2026-09-05")
  })

  it("unwraps MCP json content blocks", () => {
    const parsed = parseMcpOutput([{ json: popHiveSeries }])
    expect(parsed?.data_through).toBe("2026-09-05")
  })

  it("unwraps a structuredContent envelope", () => {
    const parsed = parseMcpOutput({ structuredContent: dataCommonsCrossSection })
    expect(parsed?.variable).toMatchObject({ dcid: "Count_Person" })
  })

  it("unwraps a nested mcp_client tool-result envelope", () => {
    // Official mcp_client result: {status, content:[{text: "<MCP result JSON>"}]}
    // whose inner result carries its own content blocks.
    const inner = { content: [{ type: "text", text: JSON.stringify(popHiveSeries) }] }
    const outer = { status: "success", content: [{ text: JSON.stringify(inner) }] }
    const parsed = parseMcpOutput(outer)
    expect(parsed?.data_through).toBe("2026-09-05")
  })

  it("rejects non-JSON prose", () => {
    expect(parseMcpOutput("The population of Texas is 31,853,822.")).toBeNull()
  })

  it("rejects truncated JSON instead of repairing it", () => {
    const broken = JSON.stringify(popHiveSeries).slice(0, 200)
    expect(parseMcpOutput(broken)).toBeNull()
  })

  it("rejects display-mangled JSON with whitespace inside numbers", () => {
    // Pasted logs sometimes break numbers/URLs across lines; strict JSON
    // parsing must reject rather than guess values.
    const mangled = '{"rows": [["Florida", "2026-08-29", 0.\n3]], "columns": ["a","b","c"]}'
    expect(parseMcpOutput(mangled)).toBeNull()
  })
})

// --- safeHttpUrl -----------------------------------------------------------

describe("safeHttpUrl", () => {
  it("accepts http(s) URLs", () => {
    expect(safeHttpUrl("https://www.census.gov/x")).toBe("https://www.census.gov/x")
  })
  it("rejects javascript:, data:, relative, and non-string values", () => {
    expect(safeHttpUrl("javascript:alert(1)")).toBe("")
    expect(safeHttpUrl("data:text/html,hi")).toBe("")
    expect(safeHttpUrl("/relative/path")).toBe("")
    expect(safeHttpUrl(42)).toBe("")
  })
})

// --- DataCommons parsing ---------------------------------------------------

describe("parseDataObservation · DataCommons", () => {
  it("projects a cross-section with entity names, attribution, and source URL", () => {
    const obs = parseDataObservation(
      "datacommons",
      "get_child_observations",
      JSON.stringify(dataCommonsCrossSection)
    )
    expect(obs).not.toBeNull()
    expect(obs!.title).toBe("Total population")
    expect(obs!.columns).toEqual(["observationAbout", "date", "value"])
    expect(obs!.rows).toHaveLength(5)
    expect(obs!.entityNames["geoId/06"]).toBe("California")
    expect(obs!.attribution).toBe("CensusPEPSurvey · DataCommons")
    expect(obs!.sourceUrl).toBe("https://www.census.gov/programs-surveys/popest.html")
    expect(obs!.unit).toBe("") // no unit declared — none invented
  })

  it("never promotes an arbitrary cross-section row to a headline", () => {
    const obs = parseDataObservation(
      "datacommons",
      "get_child_observations",
      dataCommonsCrossSection
    )
    expect(obs!.headline).toBeNull()
  })

  it("headlines the latest observation of a single-entity series", () => {
    const obs = parseDataObservation("datacommons", "get_observations", dataCommonsSeries)
    expect(obs!.headline).toEqual({ value: 39355309, date: "2025" })
  })

  it("projects metadata-only variable facets with method, unit, and resolved provenance", () => {
    const obs = parseDataObservation(
      "datacommons",
      "get_variable_metadata",
      dataCommonsMetadata
    )
    expect(obs).not.toBeNull()
    expect(obs!.title).toBe("Total population · Life Expectancy")
    expect(obs!.answer).toContain("total number of people")
    expect(obs!.rows).toHaveLength(0)
    expect(obs!.facets).toHaveLength(3)
    // Facet measurement method is preserved per facet — the core distinction
    // between facets of one variable.
    expect(obs!.facets[0]).toMatchObject({
      variable: "Total population",
      method: "CensusACS5yrSurvey",
      provenance: "dc/base/CensusACS5YearSurvey",
      provenanceName: "U.S. Census Bureau",
      provenanceUrl: "https://www.census.gov/programs-surveys/acs.html",
      dateRange: "2011 – 2024",
      observations: 70,
    })
    // Unresolved provenance ids fall back to the raw id, never invented names.
    expect(obs!.facets[1]).toMatchObject({
      method: "WikidataPopulation",
      provenance: "dc/base/WikidataPopulation",
      provenanceName: "",
      provenanceUrl: "",
    })
    // Declared unit survives (Life Expectancy: Year).
    expect(obs!.facets[2]).toMatchObject({
      variable: "Life Expectancy",
      method: "OECDRegionalStatistics",
      unit: "Year",
      provenanceName: "Organisation for Economic Co-operation and Development (OECD)",
    })
    expect(obs!.facets[2].provenanceUrl).toContain("https://data-explorer.oecd.org/")
  })

  it("returns null for unrecognized payloads", () => {
    expect(parseDataObservation("datacommons", "get_observations", { hello: 1 })).toBeNull()
  })

  it("ignores non-finite headline candidates", () => {
    const payload = {
      variable: { name: "X" },
      data: {
        columns: ["observationAbout", "date", "value"],
        rows: [["geoId/06", "2025", Number.NaN]],
      },
    }
    const obs = parseDataObservation("datacommons", "get_observations", payload)
    expect(obs!.headline).toBeNull()
  })
})

// --- PopHIVE parsing -------------------------------------------------------

describe("parseDataObservation · PopHIVE", () => {
  it("preserves the full answer, attribution, caveats, and panel metadata", () => {
    const obs = parseDataObservation("pophive", "get_data", JSON.stringify(popHiveSeries))
    expect(obs).not.toBeNull()
    expect(obs!.answer).toBe(popHiveSeries.answer)
    expect(obs!.attribution).toBe(popHiveSeries.provenance.attribution)
    expect(obs!.caveats).toEqual(popHiveSeries.caveats) // ALL caveats, verbatim
    expect(obs!.sources).toEqual(popHiveSeries.provenance.sources)
    expect(obs!.rowsMatched).toBe(1449)
    expect(obs!.deeplink).toBe("https://www.pophive.org/infectious-diseases/influenza")
    expect(obs!.sourceUrl).toBe(popHiveSeries.provenance.data_url)
  })

  it("keeps data_through separate from the panel.latest headline date", () => {
    const obs = parseDataObservation("pophive", "get_data", popHiveSeries)
    expect(obs!.headline).toEqual({ value: 0.15, date: "2026-08-29" })
    expect(obs!.dataThrough).toBe("2026-09-05")
    expect(obs!.dataThroughBySource).toContainEqual({
      source: "Epic Cosmos, ED",
      date: "2026-08-22",
    })
  })

  it("does not fabricate a unit", () => {
    const obs = parseDataObservation("pophive", "get_data", popHiveSeries)
    expect(obs!.unit).toBe("")
  })

  it("quotes server ranks verbatim and derives no headline from rows", () => {
    const obs = parseDataObservation("pophive", "get_data", popHiveCrossSection)
    expect(obs!.headline).toBeNull() // no panel.latest → no headline
    expect(obs!.ranks).toContainEqual({ label: "California", rank: 9, of: 50 })
    expect(obs!.ranks).toContainEqual({ label: "Connecticut", rank: 40, of: 50 })
    expect(obs!.ranks).toHaveLength(3)
  })

  it("projects metadata-only (about) results as a prose card", () => {
    const obs = parseDataObservation("pophive", "get_data", popHiveAbout)
    expect(obs).not.toBeNull()
    expect(obs!.answer).toContain("# About PopHIVE")
    expect(obs!.rows).toHaveLength(0)
    expect(obs!.attribution).toContain("Yale School of Public Health")
  })

  it("returns null for unknown servers", () => {
    expect(parseDataObservation("othermcp", "get_data", popHiveSeries)).toBeNull()
  })
})

// --- inferMcpServer ---------------------------------------------------------

describe("inferMcpServer", () => {
  it("maps DataCommons tool names", () => {
    expect(inferMcpServer("get_observations")).toBe("datacommons")
    expect(inferMcpServer("get_child_observations")).toBe("datacommons")
    expect(inferMcpServer("search_indicators")).toBe("datacommons")
    expect(inferMcpServer("get_variable_metadata")).toBe("datacommons")
  })
  it("maps PopHIVE get_data and nothing else", () => {
    expect(inferMcpServer("get_data")).toBe("pophive")
    expect(inferMcpServer("run_shell")).toBeNull()
    expect(inferMcpServer("graph")).toBeNull()
  })
})

// --- seriesFrom (chart projection) ------------------------------------------

function obsFrom(raw: unknown, server = "datacommons", tool = "get_child_observations") {
  const obs = parseDataObservation(server, tool, raw)
  expect(obs).not.toBeNull()
  return obs as DataObservation
}

describe("seriesFrom", () => {
  it("ranks a cross-section descending with resolved entity names", () => {
    const series = seriesFrom(obsFrom(dataCommonsCrossSection))
    expect(series.kind).toBe("bars")
    expect(series.points[0]).toEqual({ label: "California", value: 39355309 })
    expect(series.points.map((p) => p.label)).toEqual([
      "California",
      "Texas",
      "Florida",
      "New York",
      "Alabama",
    ])
    expect(series.shown).toBe(5)
    expect(series.total).toBe(5)
    expect(series.note).toBe("5 values · largest first")
  })

  it("labels truncation instead of presenting a capped chart as complete", () => {
    const rows = Array.from({ length: 30 }, (_, i) => [`geoId/${i}`, "2025", 1000 - i])
    const payload = {
      variable: { name: "X" },
      data: { columns: ["observationAbout", "date", "value"], rows },
    }
    const series = seriesFrom(obsFrom(payload))
    expect(series.kind).toBe("bars")
    expect(series.shown).toBe(16)
    expect(series.total).toBe(30)
    expect(series.note).toBe("Showing 16 of 30 values · largest first")
  })

  it("draws a single-source PopHIVE slice as a time series", () => {
    const series = seriesFrom(obsFrom(popHiveSeries, "pophive", "get_data"))
    expect(series.kind).toBe("line")
    expect(series.points.map((p) => p.value)).toEqual([0.1, 0.12, 0.15])
  })

  it("refuses to chart mixed sources in one series", () => {
    const mixed = {
      ...popHiveSeries,
      rows: [
        ["2026-08-22", "United States", "CDC NSSP", 0.12],
        ["2026-08-29", "United States", "CDC NSSP", 0.15],
        ["2026-08-29", "United States", "CDC NWSS", 4.1],
      ],
    }
    const series = seriesFrom(obsFrom(mixed, "pophive", "get_data"))
    expect(series.kind).toBe("none")
    expect(series.note).toContain("units differ by source")
  })

  it("skips non-finite values", () => {
    const payload = {
      variable: { name: "X" },
      data: {
        columns: ["observationAbout", "date", "value"],
        rows: [
          ["geoId/01", "2025", Number.POSITIVE_INFINITY],
          ["geoId/02", "2025", Number.NaN],
        ],
      },
    }
    expect(seriesFrom(obsFrom(payload)).kind).toBe("none")
  })

  it("returns no chart for empty or metadata-only observations", () => {
    expect(seriesFrom(obsFrom(popHiveAbout, "pophive", "get_data")).kind).toBe("none")
    expect(seriesFrom(obsFrom(dataCommonsMetadata, "datacommons", "get_variable_metadata")).kind).toBe(
      "none"
    )
  })
})

// --- formatValue -------------------------------------------------------------

describe("formatValue", () => {
  it("compacts large magnitudes and keeps small precision", () => {
    expect(formatValue(39355309)).toBe("39.4M")
    expect(formatValue(693645)).toBe("693,645")
    expect(formatValue(0.15)).toBe("0.15")
    expect(formatValue(1.68)).toBe("1.7")
  })
  it("renders non-finite values as a dash", () => {
    expect(formatValue(Number.NaN)).toBe("—")
    expect(formatValue(Number.POSITIVE_INFINITY)).toBe("—")
  })
})
