// Render tests for components/v0/data-observation-panel.tsx.
//
// The repo's vitest runs in the node environment with no DOM library, so
// rendering uses react-dom/server.renderToStaticMarkup with `motion/react`
// and the AI Elements MessageResponse mocked (Streamdown needs a DOM;
// motion's `useReducedMotion` needs matchMedia). The mocks preserve children
// so content assertions exercise the real panel structure.

import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

vi.mock("motion/react", () => {
  const strip = (Tag: string) =>
    function MotionMock(
      props: Record<string, unknown> & { children?: React.ReactNode }
    ) {
      const rest = { ...props }
      delete rest.initial
      delete rest.animate
      delete rest.transition
      const { children, ...attrs } = rest
      return React.createElement(Tag, attrs, children)
    }
  return {
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_target, tag: string) => strip(tag),
    }),
    useReducedMotion: () => true,
  }
})

vi.mock("@/components/ai-elements/message", () => ({
  MessageResponse: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="message-response">{children}</div>
  ),
}))

import { DataObservationPanel } from "./data-observation-panel"
import { parseDataObservation, type DataObservation } from "./data-observation"

const popHive: DataObservation = parseDataObservation("pophive", "get_data", {
  answer:
    "influenza (overall_trend) — Panel summary: Latest: 0.15% of ED visits (week ending 2026-08-29).",
  resolved: { geography: "United States", source: "CDC NSSP" },
  caveats: [
    "Preliminary: NSSP and NHSN values for the most recent 2 weeks are subject to upward revision.",
    "ILINet measures outpatient influenza-like illness, not laboratory-confirmed influenza.",
    "Kinsa readings are near real time; clinical signals lag 1–2 weeks.",
    "NWSS wastewater viral activity is scaled standard deviations above a dynamic baseline.",
  ],
  provenance: {
    data_url:
      "https://raw.githubusercontent.com/PopHIVE/Ingest/main/data/bundle_respiratory/dist/flu_overall_trends.parquet",
    rows_matched: 1449,
    sources: ["CDC NSSP", "Kinsa"],
    attribution:
      "Data obtained via PopHIVE, Yale School of Public Health (DOI 10.5281/zenodo.17345935).",
  },
  data_through: "2026-09-05",
  data_through_by_source: { "Epic Cosmos, ED": "2026-08-22" },
  deeplink: "https://www.pophive.org/infectious-diseases/influenza",
  rows: [
    ["2026-08-15", "United States", "CDC NSSP", 0.1],
    ["2026-08-22", "United States", "CDC NSSP", 0.12],
    ["2026-08-29", "United States", "CDC NSSP", 0.15],
  ],
  columns: ["date", "geography", "source", "value"],
  panel: { latest: { value: 0.15, date: "2026-08-29" } },
})!

const dataCommons: DataObservation = parseDataObservation(
  "datacommons",
  "get_child_observations",
  {
    variable: { dcid: "Count_Person", name: "Total population" },
    sourceMetadata: {
      measurementMethod: "CensusPEPSurvey",
      provenanceUrl: "https://www.census.gov/programs-surveys/popest.html",
    },
    entityMetadata: {
      columns: ["dcid", "name", "typeOf"],
      rows: Array.from({ length: 20 }, (_, i) => [`geoId/${i}`, `State ${i}`, ["State"]]),
    },
    data: {
      columns: ["observationAbout", "date", "value"],
      rows: Array.from({ length: 20 }, (_, i) => [`geoId/${i}`, "2025", (i + 1) * 100000]),
    },
  }
)!

describe("DataObservationPanel", () => {
  it("renders the verbatim answer markdown, all caveats, ranks metadata, and both dates", () => {
    const html = renderToStaticMarkup(<DataObservationPanel observation={popHive} />)
    expect(html).toContain("message-response")
    expect(html).toContain("Panel summary: Latest: 0.15% of ED visits")
    // Every caveat — none sliced away.
    expect(html).toContain("subject to upward revision")
    expect(html).toContain("laboratory-confirmed influenza")
    expect(html).toContain("clinical signals lag")
    expect(html).toContain("scaled standard deviations")
    // Freshness: dataset-level date AND per-source date, kept distinct from
    // the headline observation date.
    expect(html).toContain("Data through 2026-09-05")
    expect(html).toContain("Epic Cosmos, ED: through 2026-08-22")
    expect(html).toContain("2026-08-29")
    // Verbatim attribution + validated links.
    expect(html).toContain("Yale School of Public Health")
    expect(html).toContain(
      "https://raw.githubusercontent.com/PopHIVE/Ingest/main/data/bundle_respiratory/dist/flu_overall_trends.parquet"
    )
    expect(html).toContain("https://www.pophive.org/infectious-diseases/influenza")
  })

  it("renders every table row (no hidden rows) with an accessible table", () => {
    const html = renderToStaticMarkup(<DataObservationPanel observation={dataCommons} />)
    // All 20 entity rows are present in the table.
    for (let i = 0; i < 20; i++) {
      expect(html).toContain(`State ${i}`)
    }
    expect(html).toContain("<caption")
    expect(html).toContain('scope="col"')
    expect(html).toContain("20 rows")
  })

  it("labels chart truncation when more values exist than bars drawn", () => {
    const html = renderToStaticMarkup(<DataObservationPanel observation={dataCommons} />)
    expect(html).toContain("Showing 16 of 20 values")
    // Chart is an accessible figure.
    expect(html).toContain('role="img"')
  })

  it("gives each panel unique SVG gradient ids", () => {
    const html = renderToStaticMarkup(
      <>
        <DataObservationPanel observation={dataCommons} />
        <DataObservationPanel observation={dataCommons} />
      </>
    )
    const ids = [...html.matchAll(/<linearGradient id="([^"]+)"/g)].map((m) => m[1])
    expect(ids).toHaveLength(2)
    expect(new Set(ids).size).toBe(2)
  })

  it("shows no headline for a cross-section and no invented unit", () => {
    const html = renderToStaticMarkup(<DataObservationPanel observation={dataCommons} />)
    // The header shows server · tool with no trailing unit segment.
    expect(html).toContain("datacommons · get_child_observations")
    expect(html).not.toContain("get_child_observations ·  ")
  })

  it("renders a metadata-only result as prose without chart or table", () => {
    const about = parseDataObservation("pophive", "get_data", {
      answer: "# About PopHIVE\n\nHarmonized US public-health surveillance dataset.",
      provenance: {
        data_url: "https://raw.githubusercontent.com/PopHIVE/Ingest/main/data/ABOUT.md",
        rows_matched: 0,
        sources: [],
        attribution: "Data: PopHIVE, Yale School of Public Health.",
      },
      deeplink: "https://www.pophive.org",
      rows: [],
      columns: [],
    })!
    const html = renderToStaticMarkup(<DataObservationPanel observation={about} />)
    expect(html).toContain("About PopHIVE")
    expect(html).not.toContain("<table")
    // Icon SVGs remain; no chart figure renders (charts carry role="img").
    expect(html).not.toContain('role="img"')
    expect(html).not.toContain("<figure")
    expect(html).toContain("https://www.pophive.org")
  })
})
