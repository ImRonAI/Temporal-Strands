"use client"

import { motion, useReducedMotion } from "motion/react"
import { useId, useMemo } from "react"
import { BarChart3Icon, ExternalLinkIcon } from "lucide-react"

import { MessageResponse } from "@/components/ai-elements/message"
import {
  formatValue,
  seriesFrom,
  type DataObservation,
} from "@/components/v0/data-observation"
import { cn } from "@/lib/utils"

const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1]

// Chart geometry (viewBox units).
const W = 560
const H = 180
const PAD = 8
const BAR_LABEL_W = 140

/** Bars for cross-sections, an area path for time series — both drawn with
 *  the same staggered entrance and both bounded by seriesFrom (which labels
 *  any truncation and refuses mixed-source charts). SVG only. Gradient ids
 *  come from useId so multiple panels never collide. */
function Chart({ obs, reduce }: { obs: DataObservation; reduce: boolean }) {
  const uid = useId()
  const series = useMemo(() => seriesFrom(obs), [obs])
  if (series.kind === "none") {
    return series.note ? (
      <p className="text-[11px] text-muted-foreground">{series.note}</p>
    ) : null
  }
  const { points } = series

  const max = Math.max(...points.map((p) => p.value), 1e-9)
  const min = Math.min(...points.map((p) => p.value), 0)
  const span = max - min || 1

  if (series.kind === "bars") {
    const barGap = 6
    const barH = Math.max(6, Math.min(14, (H - PAD * 2) / points.length - barGap))
    const gradientId = `${uid}-bar`
    return (
      <figure className="m-0 w-full overflow-hidden">
        <svg
          aria-label={`Bar chart: ${obs.title}`}
          role="img"
          viewBox={`0 0 ${W} ${(barH + barGap) * points.length + PAD * 2}`}
          className="w-full"
        >
          {points.map((p, i) => {
            const w = ((p.value - min) / span) * (W - BAR_LABEL_W - 60) + 4
            const y = PAD + i * (barH + barGap)
            return (
              <g key={`${p.label}-${i}`}>
                <motion.rect
                  height={barH}
                  rx={barH / 2}
                  width={w}
                  x={BAR_LABEL_W}
                  y={y}
                  fill={`url(#${gradientId})`}
                  initial={reduce ? false : { scaleX: 0, opacity: 0 }}
                  animate={{ scaleX: 1, opacity: 1 }}
                  transition={
                    reduce
                      ? { duration: 0 }
                      : { duration: 0.7, delay: 0.15 + i * 0.04, ease: EASE }
                  }
                  style={{ transformOrigin: `${BAR_LABEL_W}px center` }}
                />
                <text
                  x={BAR_LABEL_W - 6}
                  y={y + barH / 2}
                  textAnchor="end"
                  dominantBaseline="central"
                  className="fill-muted-foreground"
                  fontSize={9}
                >
                  {p.label.length > 18 ? `${p.label.slice(0, 17)}…` : p.label}
                </text>
                <text
                  x={BAR_LABEL_W + 6 + w}
                  y={y + barH / 2}
                  dominantBaseline="central"
                  className="fill-foreground"
                  fontSize={9}
                  fontWeight={600}
                >
                  {formatValue(p.value)}
                </text>
              </g>
            )
          })}
          <defs>
            <linearGradient id={gradientId} x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="var(--blurple)" stopOpacity={0.55} />
              <stop offset="100%" stopColor="var(--blurple-bright)" stopOpacity={0.95} />
            </linearGradient>
          </defs>
        </svg>
        {series.note ? (
          <figcaption className="mt-1 text-[10px] text-muted-foreground">
            {series.note}
          </figcaption>
        ) : null}
      </figure>
    )
  }

  // Time series: area + line.
  const gradientId = `${uid}-area`
  const stepX = (W - PAD * 2) / Math.max(points.length - 1, 1)
  const coords = points.map((p, i) => ({
    x: PAD + i * stepX,
    y: PAD + (1 - (p.value - min) / span) * (H - PAD * 2),
  }))
  const line = coords.map((c) => `${c.x},${c.y}`).join(" ")
  const area = `${PAD},${H - PAD} ${line} ${W - PAD},${H - PAD}`

  return (
    <figure className="m-0 w-full overflow-hidden">
      <svg
        aria-label={`Line chart: ${obs.title}`}
        role="img"
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--blurple-bright)" stopOpacity={0.45} />
            <stop offset="100%" stopColor="var(--blurple)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <motion.polygon
          points={area}
          fill={`url(#${gradientId})`}
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={reduce ? { duration: 0 } : { duration: 0.9, delay: 0.5, ease: EASE }}
        />
        <motion.polyline
          points={line}
          fill="none"
          stroke="var(--blurple-bright)"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={reduce ? false : { pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={reduce ? { duration: 0 } : { duration: 1.1, delay: 0.2, ease: EASE }}
        />
        {coords.map((c, i) => (
          <motion.circle
            key={`${points[i].label}-${i}`}
            cx={c.x}
            cy={c.y}
            r={3}
            fill="var(--blurple-bright)"
            stroke="var(--blurple)"
            strokeWidth={1.5}
            initial={reduce ? false : { scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={
              reduce
                ? { duration: 0 }
                : { duration: 0.3, delay: 0.25 + (i / coords.length) * 0.7, ease: EASE }
            }
          />
        ))}
      </svg>
      <figcaption className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span>{points[0]?.label}</span>
        {series.note ? <span>{series.note}</span> : null}
        <span>{points[points.length - 1]?.label}</span>
      </figcaption>
    </figure>
  )
}

function ExternalLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      className="flex shrink-0 items-center gap-1 text-[10px] text-blurple-bright/80 transition-colors hover:text-blurple-bright"
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {label} <ExternalLinkIcon aria-hidden className="size-3" />
    </a>
  )
}

export type DataObservationPanelProps = {
  observation: DataObservation
  className?: string
}

/**
 * Blue-glass data panel for DataCommons / PopHIVE MCP results.
 *
 * Everything the server said survives: the full `answer` markdown renders
 * through the native MessageResponse (Streamdown), every row is reachable in
 * a scrollable accessible table, every caveat and per-source freshness date
 * is listed, ranks are quoted verbatim, and attribution + source links sit in
 * the footer. The chart is a bounded honest projection (seriesFrom) that
 * labels truncation and refuses mixed-source series.
 */
export function DataObservationPanel({ observation, className }: DataObservationPanelProps) {
  const reduce = useReducedMotion() ?? false
  const obs = observation
  const hasTable = obs.columns.length > 0 && obs.rows.length > 0

  return (
    <motion.div
      className={cn("ide-glass not-prose w-full overflow-hidden rounded-xl", className)}
      initial={reduce ? false : { opacity: 0, y: 14, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={reduce ? { duration: 0 } : { duration: 0.5, ease: EASE }}
    >
      {/* Header */}
      <div className="ide-glass-edge flex items-center gap-2.5 border-b bg-white/[0.03] px-4 py-3">
        <span className="flex size-7 items-center justify-center rounded-lg bg-blurple/30 shadow-[0_0_16px_-2px_oklch(0.62_0.17_250/0.6),inset_0_1px_0_0_oklch(0.85_0.1_235/0.3)]">
          <BarChart3Icon aria-hidden className="size-4 text-blurple-bright" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-sm tracking-tight">{obs.title}</p>
          <p className="truncate text-[11px] text-muted-foreground">
            {obs.server} · {obs.tool}
            {obs.unit ? ` · ${obs.unit}` : ""}
          </p>
        </div>
        {obs.headline ? (
          <div className="shrink-0 text-right">
            <motion.p
              className="font-semibold text-blurple-bright text-lg leading-none"
              initial={reduce ? false : { opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={
                reduce ? { duration: 0 } : { duration: 0.5, delay: 0.35, ease: EASE }
              }
            >
              {formatValue(obs.headline.value)}
              {obs.unit === "%" ? "%" : ""}
            </motion.p>
            <p className="mt-0.5 text-[10px] text-muted-foreground">{obs.headline.date}</p>
          </div>
        ) : null}
      </div>

      <div className="space-y-4 p-4">
        {/* The server's own prose, verbatim markdown. */}
        {obs.answer ? (
          <div className="max-h-72 overflow-y-auto text-sm">
            <MessageResponse>{obs.answer}</MessageResponse>
          </div>
        ) : null}

        {/* Server-quoted ranks, verbatim — never recomputed. */}
        {obs.ranks.length > 0 ? (
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {obs.ranks.map((r) => (
              <li key={r.label} className="text-[11px] text-foreground/85">
                {r.label}: rank {r.rank}
                {r.of !== null ? ` of ${r.of}` : ""}
              </li>
            ))}
          </ul>
        ) : null}

        <Chart obs={obs} reduce={reduce} />

        {hasTable ? (
          <div className="ide-glass-inset overflow-hidden rounded-lg border">
            {/* Every row, scrollable — nothing is hidden. */}
            <div className="max-h-72 overflow-auto" tabIndex={0} aria-label="Observation data table">
              <table className="w-full text-left text-xs">
                <caption className="sr-only">
                  {obs.title} — {obs.rows.length} rows from {obs.server}
                </caption>
                <thead className="sticky top-0">
                  <tr className="border-white/10 border-b bg-white/[0.03]">
                    {obs.columns.map((col) => (
                      <th
                        key={col}
                        scope="col"
                        className="bg-background/60 px-3 py-2 font-medium text-[10px] text-muted-foreground uppercase tracking-wider backdrop-blur-sm"
                      >
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {obs.rows.map((row, i) => (
                    <motion.tr
                      key={i}
                      className="border-white/5 border-b last:border-0"
                      initial={reduce || i > 11 ? false : { opacity: 0, x: -6 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={
                        reduce
                          ? { duration: 0 }
                          : { duration: 0.35, delay: 0.5 + Math.min(i, 11) * 0.05, ease: EASE }
                      }
                    >
                      {row.map((cell, j) => {
                        const key = obs.columns[j] ?? j
                        const display =
                          typeof cell === "number"
                            ? (Number.isFinite(cell) ? new Intl.NumberFormat("en", { maximumSignificantDigits: 15 }).format(cell) : "—")
                            : (obs.entityNames[String(cell)] ?? (cell === null || cell === "" ? "—" : typeof cell === "object" ? JSON.stringify(cell) : String(cell)))
                        return (
                          <td key={key} className="px-3 py-1.5 text-foreground/85 tabular-nums">
                            {display}
                          </td>
                        )
                      })}
                    </motion.tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="border-white/5 border-t px-3 py-1.5 text-[10px] text-muted-foreground">
              {obs.rows.length} row{obs.rows.length === 1 ? "" : "s"}
              {obs.rowsMatched !== null && obs.rowsMatched !== obs.rows.length
                ? ` · ${formatValue(obs.rowsMatched)} matched upstream`
                : ""}
            </p>
          </div>
        ) : null}

        {/* Metadata-only DataCommons facets. */}
        {obs.facets.length > 0 ? (
          <div className="ide-glass-inset max-h-80 overflow-auto rounded-lg border" tabIndex={0} aria-label="Variable metadata table">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">
                {obs.title} — {obs.facets.length} data facets
              </caption>
              <thead>
                <tr className="border-white/10 border-b bg-white/[0.03]">
                  {["Variable", "Method", "Unit", "Source", "Date range", "Observations"].map(
                    (col) => (
                      <th
                        key={col}
                        scope="col"
                        className="px-3 py-2 font-medium text-[10px] text-muted-foreground uppercase tracking-wider"
                      >
                        {col}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody>
                {obs.facets.map((facet, i) => (
                  <tr key={facet.id || i} className="border-white/5 border-b last:border-0">
                    <td className="px-3 py-1.5 text-foreground/85">{facet.variable ?? ""}</td>
                    {/* measurementMethod is the core distinction between
                        facets of one variable (e.g. AgeAdjustedPrevalence vs
                        CrudePrevalence) — always its own column. */}
                    <td className="px-3 py-1.5 text-foreground/85">{facet.method ?? ""}</td>
                    <td className="px-3 py-1.5 text-foreground/85">{facet.unit ?? ""}</td>
                    <td className="px-3 py-1.5 text-foreground/85">
                      {facet.provenanceUrl ? (
                        <a
                          className="text-blurple-bright/90 underline decoration-blurple-bright/40 underline-offset-2 transition-colors hover:text-blurple-bright"
                          href={facet.provenanceUrl}
                          rel="noreferrer"
                          target="_blank"
                          title={facet.provenance}
                        >
                          {facet.provenanceName || facet.provenance}
                        </a>
                      ) : (
                        <span title={facet.provenance}>
                          {facet.provenanceName || facet.provenance}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-foreground/85 tabular-nums">
                      {facet.dateRange}
                    </td>
                    <td className="px-3 py-1.5 text-foreground/85 tabular-nums">
                      {facet.observations !== null ? formatValue(facet.observations) : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {/* Every caveat, verbatim. */}
        {obs.caveats.length > 0 ? (
          <ul className="space-y-1">
            {obs.caveats.map((c, i) => (
              <li key={i} className="text-[11px] text-muted-foreground leading-snug">
                · {c}
              </li>
            ))}
          </ul>
        ) : null}

        {/* Freshness: dataset-level and per-source dates are different facts
            (data_through can trail or lead an individual source). */}
        {obs.dataThrough || obs.dataThroughBySource.length > 0 ? (
          <div className="space-y-0.5 text-[10px] text-muted-foreground">
            {obs.dataThrough ? <p>Data through {obs.dataThrough}</p> : null}
            {obs.dataThroughBySource.map(({ source, date }) => (
              <p key={source}>
                {source}: through {date}
              </p>
            ))}
          </div>
        ) : null}
      </div>

      {/* Provenance footer: verbatim attribution + validated links. */}
      <div className="ide-glass-edge flex items-start justify-between gap-3 border-t bg-black/20 px-4 py-2">
        <p className="min-w-0 text-[10px] text-muted-foreground leading-snug">
          {obs.attribution}
          {obs.sources.length > 0 ? (
            <span className="mt-0.5 block">Sources: {obs.sources.join(", ")}</span>
          ) : null}
        </p>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {obs.sourceUrl ? <ExternalLink href={obs.sourceUrl} label="Data" /> : null}
          {obs.deeplink && obs.deeplink !== obs.sourceUrl ? (
            <ExternalLink href={obs.deeplink} label="View source" />
          ) : null}
        </div>
      </div>
    </motion.div>
  )
}
