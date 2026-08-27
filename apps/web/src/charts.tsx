/**
 * A small chart kit, hand-built so every mark obeys the same spec.
 *
 * Conventions that hold everywhere in here:
 *  - one y-axis, never two
 *  - 2px lines, 4px rounded data-ends, >= 8px hit targets
 *  - a legend whenever there is more than one series, plus direct labels
 *  - grid and axes recede; the data does not
 *  - colour comes from the entity tokens, so a chiller is the same blue here
 *    as it is on the mimic panel
 */

import { useId, useMemo, useRef, useState } from 'react'

export const SERIES_COLOR: Record<string, string> = {
  chillers: 'var(--chw)',
  chillerPowerKw: 'var(--chw)',
  coolingDeliveredKw: 'var(--chw)',
  coolingLoadKw: 'var(--chw)',
  towerFans: 'var(--cw)',
  towerPowerKw: 'var(--cw)',
  towerHeatKw: 'var(--cw)',
  boilers: 'var(--hhw)',
  boilerHeatKw: 'var(--hhw)',
  heatingLoadKw: 'var(--hhw)',
  heatPumps: 'var(--hhw)',
  heatPumpHeatKw: 'var(--hhw)',
  pumps: 'var(--pump)',
  pumpPowerKw: 'var(--pump)',
  plantPowerKw: 'var(--ink-2)',
  chwSupplyC: 'var(--chw)',
  cwSupplyC: 'var(--cw)',
  hhwSupplyC: 'var(--hhw)',
  oaWetbulbC: 'var(--cw)',
  chillerPlr: 'var(--chw)',
  oaDrybulbC: 'var(--ink-2)',
  other: 'var(--other)',
  boilerAncillary: 'var(--other)',
}

export const color = (key: string) => SERIES_COLOR[key] ?? 'var(--other)'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Hour-of-year to a readable date, non-leap. */
export function hourLabel(h: number): string {
  const cum = [0, 744, 1416, 2160, 2880, 3624, 4344, 5088, 5832, 6552, 7296, 8016]
  let m = 11
  for (let i = 0; i < 12; i++) if (h >= cum[i]) m = i
  const day = Math.floor((h - cum[m]) / 24) + 1
  const hr = h % 24
  return `${day} ${MONTHS[m]} · ${String(hr).padStart(2, '0')}:00`
}

export const fmt = (v: number, digits = 0) =>
  v.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })

export function compact(v: number): string {
  const a = Math.abs(v)
  if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`
  if (a >= 1e3) return `${(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`
  if (a >= 10) return v.toFixed(0)
  if (a >= 1) return v.toFixed(1)
  return v.toFixed(2)
}

/**
 * An upper bound that ignores rare excursions.
 *
 * A handful of unmet hours can send a loop temperature to 180 °C and squash
 * the entire useful range into the bottom of the plot. Scale to the bulk of
 * the data and clip the outliers, which the summary reports numerically.
 */
export function robustMax(series: number[][], quantile = 0.99): number {
  const all = series.flat().filter(Number.isFinite)
  if (!all.length) return 1
  const sorted = [...all].sort((a, b) => a - b)
  const q = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * quantile))]
  return q * 1.12
}

/** "Nice" axis ticks — 1/2/5 x 10^n. */
function ticks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return [min]
  const span = max - min
  const raw = span / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const norm = raw / mag
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag
  const out: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) {
    out.push(+v.toFixed(10))
  }
  return out
}

/* ==========================================================================
   Legend
   ========================================================================== */

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  if (items.length < 2) return null
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 14px',
      marginTop: 8, paddingLeft: 2,
    }}>
      {items.map((it) => (
        <span key={it.label} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          fontSize: 'var(--t-xs)', color: 'var(--ink-2)',
        }}>
          <span style={{
            width: 9, height: 9, borderRadius: 2, background: it.color, flex: 'none',
          }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

/* ==========================================================================
   Line / area chart with crosshair
   ========================================================================== */

export interface Series {
  key: string
  label: string
  values: number[]
  color?: string
  fill?: boolean
  dashed?: boolean
}

interface LineChartProps {
  series: Series[]
  height?: number
  unit?: string
  xLabel?: (i: number) => string
  xTickCount?: number
  yMin?: number
  yMax?: number
  band?: { from: number; to: number; label?: string }
  marker?: number | null
  onScrub?: (i: number | null) => void
}

export function LineChart({
  series, height = 190, unit = '', xLabel = String, xTickCount = 6,
  yMin, yMax, band, marker, onScrub,
}: LineChartProps) {
  const uid = useId().replace(/:/g, '')
  const wrapRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number | null>(null)
  const [width, setWidth] = useState(680)

  // Track the container so the chart is responsive without a resize library.
  useMemo(() => {
    if (!wrapRef.current || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(wrapRef.current)
    return () => ro.disconnect()
  }, [wrapRef.current])

  const pad = { t: 10, r: 12, b: 22, l: 46 }
  const w = Math.max(260, width)
  const iw = w - pad.l - pad.r
  const ih = height - pad.t - pad.b
  const n = Math.max(...series.map((s) => s.values.length), 1)

  const lo = yMin ?? Math.min(0, ...series.flatMap((s) => s.values))
  const hi = yMax ?? Math.max(1e-9, ...series.flatMap((s) => s.values))
  const yt = ticks(lo, hi, 4)
  const top = Math.max(hi, yt[yt.length - 1] ?? hi)

  const X = (i: number) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * iw)
  const Y = (v: number) => pad.t + ih - ((v - lo) / (top - lo || 1)) * ih

  const path = (vals: number[]) =>
    vals.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('')

  const areaPath = (vals: number[]) =>
    `${path(vals)}L${X(vals.length - 1).toFixed(1)},${(pad.t + ih).toFixed(1)}` +
    `L${X(0).toFixed(1)},${(pad.t + ih).toFixed(1)}Z`

  const idx = hover ?? marker ?? null

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const rel = (e.clientX - r.left - pad.l) / (iw || 1)
    const i = Math.round(Math.min(1, Math.max(0, rel)) * (n - 1))
    setHover(i)
    onScrub?.(i)
  }
  const onLeave = () => { setHover(null); onScrub?.(null) }

  const xTicks = Array.from({ length: xTickCount }, (_, k) =>
    Math.round((k / (xTickCount - 1)) * (n - 1)))

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <svg
        width="100%" height={height} viewBox={`0 0 ${w} ${height}`}
        onMouseMove={onMove} onMouseLeave={onLeave}
        role="img"
        aria-label={series.map((s) => s.label).join(', ')}
        style={{ display: 'block', overflow: 'visible' }}
      >
        <defs>
          <clipPath id={`clip-${uid}`}>
            <rect x={pad.l} y={pad.t - 2} width={iw} height={ih + 4} />
          </clipPath>
          {series.filter((s) => s.fill).map((s) => (
            <linearGradient key={s.key} id={`g-${uid}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={s.color ?? color(s.key)} stopOpacity="0.26" />
              <stop offset="100%" stopColor={s.color ?? color(s.key)} stopOpacity="0.02" />
            </linearGradient>
          ))}
        </defs>

        {band && (
          <rect
            x={X(band.from)} y={pad.t}
            width={Math.max(1, X(band.to) - X(band.from))} height={ih}
            fill="var(--live)" opacity="0.09"
          />
        )}

        {yt.map((v) => (
          <g key={v}>
            <line
              x1={pad.l} x2={pad.l + iw} y1={Y(v)} y2={Y(v)}
              stroke="var(--line)" strokeWidth="1"
              strokeDasharray={v === 0 ? undefined : '2 4'}
            />
            <text
              x={pad.l - 7} y={Y(v)} textAnchor="end" dominantBaseline="middle"
              fontSize="10" fill="var(--faint)" fontFamily="var(--data)"
            >
              {compact(v)}
            </text>
          </g>
        ))}

        {xTicks.map((i) => (
          <text
            key={i} x={X(i)} y={height - 6}
            textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}
            fontSize="10" fill="var(--faint)" fontFamily="var(--data)"
          >
            {xLabel(i)}
          </text>
        ))}

        <g clipPath={`url(#clip-${uid})`}>
          {series.filter((s) => s.fill).map((s) => (
            <path key={`a-${s.key}`} d={areaPath(s.values)}
              fill={`url(#g-${uid}-${s.key})`} />
          ))}

          {series.map((s) => (
            <path
              key={s.key} d={path(s.values)} fill="none"
              stroke={s.color ?? color(s.key)} strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round"
              strokeDasharray={s.dashed ? '5 4' : undefined}
            />
          ))}
        </g>

        {idx !== null && idx >= 0 && (
          <g pointerEvents="none">
            <line
              x1={X(idx)} x2={X(idx)} y1={pad.t} y2={pad.t + ih}
              stroke="var(--ink)" strokeWidth="1" opacity="0.35"
            />
            {series.map((s) => s.values[idx] === undefined ? null : (
              <circle
                key={s.key} cx={X(idx)} cy={Y(s.values[idx])} r="4"
                fill={s.color ?? color(s.key)}
                stroke="var(--panel)" strokeWidth="2"
              />
            ))}
          </g>
        )}
      </svg>

      {idx !== null && idx >= 0 && (
        <div
          role="tooltip"
          style={{
            position: 'absolute', pointerEvents: 'none', zIndex: 5,
            left: Math.min(Math.max(X(idx) + 10, 4), w - 168),
            top: 4, width: 158,
            background: 'var(--panel)', border: '1px solid var(--line)',
            borderRadius: 'var(--r-sm)', boxShadow: 'var(--shadow-2)',
            padding: '7px 9px', fontSize: 'var(--t-xs)',
          }}
        >
          <div className="tag" style={{ marginBottom: 4 }}>{xLabel(idx)}</div>
          {series.map((s) => (
            <div key={s.key} style={{
              display: 'flex', alignItems: 'center', gap: 6, marginTop: 2,
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: 2, flex: 'none',
                background: s.color ?? color(s.key),
              }} />
              <span style={{ color: 'var(--muted)', flex: 1 }}>{s.label}</span>
              <span className="num" style={{ fontWeight: 500 }}>
                {compact(s.values[idx] ?? 0)}{unit}
              </span>
            </div>
          ))}
        </div>
      )}

      <Legend items={series.map((s) => ({
        label: s.label, color: s.color ?? color(s.key),
      }))} />
    </div>
  )
}

/* ==========================================================================
   Stacked bar — part-to-whole over months
   ========================================================================== */

export interface StackSeries { key: string; label: string; values: number[]; color?: string }

export function StackedBars({
  series, labels, height = 176, unit = '',
}: {
  series: StackSeries[]; labels: string[]; height?: number; unit?: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const pad = { t: 10, r: 8, b: 20, l: 44 }
  const w = 680
  const iw = w - pad.l - pad.r
  const ih = height - pad.t - pad.b
  const n = labels.length

  const totals = labels.map((_, i) =>
    series.reduce((a, s) => a + (s.values[i] ?? 0), 0))
  const hi = Math.max(1e-9, ...totals)
  const yt = ticks(0, hi, 4)
  const top = Math.max(hi, yt[yt.length - 1] ?? hi)

  const bw = Math.max(4, (iw / n) * 0.62)
  const X = (i: number) => pad.l + (i + 0.5) * (iw / n) - bw / 2
  const H = (v: number) => (v / (top || 1)) * ih

  return (
    <div style={{ width: '100%' }}>
      <svg
        width="100%" height={height} viewBox={`0 0 ${w} ${height}`}
        style={{ display: 'block', overflow: 'visible' }}
        onMouseLeave={() => setHover(null)}
        role="img" aria-label={series.map((s) => s.label).join(', ')}
      >
        {yt.map((v) => (
          <g key={v}>
            <line
              x1={pad.l} x2={pad.l + iw}
              y1={pad.t + ih - H(v)} y2={pad.t + ih - H(v)}
              stroke="var(--line)" strokeWidth="1"
              strokeDasharray={v === 0 ? undefined : '2 4'}
            />
            <text
              x={pad.l - 7} y={pad.t + ih - H(v)} textAnchor="end"
              dominantBaseline="middle" fontSize="10"
              fill="var(--faint)" fontFamily="var(--data)"
            >{compact(v)}</text>
          </g>
        ))}

        {labels.map((lb, i) => {
          let acc = 0
          return (
            <g key={lb}
              onMouseEnter={() => setHover(i)}
              opacity={hover === null || hover === i ? 1 : 0.45}
              style={{ transition: 'opacity 120ms ease' }}
            >
              {/* generous hit target, independent of bar height */}
              <rect x={X(i) - 4} y={pad.t} width={bw + 8} height={ih} fill="transparent" />
              {series.map((s) => {
                const v = s.values[i] ?? 0
                const h = H(v)
                const y = pad.t + ih - acc - h
                acc += h
                if (h <= 0.4) return null
                return (
                  <rect
                    key={s.key} x={X(i)} y={y} width={bw}
                    height={Math.max(0.5, h - 2 /* 2px surface gap */)}
                    rx="2" fill={s.color ?? color(s.key)}
                  />
                )
              })}
              <text
                x={X(i) + bw / 2} y={height - 6} textAnchor="middle"
                fontSize="10" fill="var(--faint)" fontFamily="var(--data)"
              >{lb}</text>
            </g>
          )
        })}
      </svg>

      {hover !== null && (
        <div style={{
          marginTop: 6, padding: '7px 9px',
          background: 'var(--panel-sunk)', border: '1px solid var(--line)',
          borderRadius: 'var(--r-sm)', fontSize: 'var(--t-xs)',
        }}>
          <span className="tag">{labels[hover]}</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', marginTop: 3 }}>
            {series.map((s) => (
              <span key={s.key} style={{ display: 'inline-flex', gap: 5, alignItems: 'center' }}>
                <span style={{
                  width: 8, height: 8, borderRadius: 2,
                  background: s.color ?? color(s.key), flex: 'none',
                }} />
                {s.label}
                <b className="num">{compact(s.values[hover] ?? 0)}{unit}</b>
              </span>
            ))}
          </div>
        </div>
      )}

      <Legend items={series.map((s) => ({
        label: s.label, color: s.color ?? color(s.key),
      }))} />
    </div>
  )
}

/* ==========================================================================
   Share bar — one horizontal part-to-whole, always directly labelled
   ========================================================================== */

export function ShareBar({
  parts, unit = 'MWh',
}: {
  parts: { key: string; label: string; value: number; color?: string }[]
  unit?: string
}) {
  const shown = parts.filter((p) => p.value > 0)
  const total = shown.reduce((a, p) => a + p.value, 0)
  if (!total) {
    return <p className="hint">Nothing recorded for this breakdown.</p>
  }
  return (
    <div>
      <div style={{
        display: 'flex', height: 24, borderRadius: 3, overflow: 'hidden', gap: 2,
      }}>
        {shown.map((p) => (
          <div
            key={p.key}
            title={`${p.label}: ${fmt(p.value, 1)} ${unit}`}
            style={{
              width: `${(p.value / total) * 100}%`,
              background: p.color ?? color(p.key),
              minWidth: 3,
            }}
          />
        ))}
      </div>
      <div style={{ marginTop: 9, display: 'grid', gap: 5 }}>
        {shown.map((p) => (
          <div key={p.key} style={{
            display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--t-sm)',
          }}>
            <span style={{
              width: 9, height: 9, borderRadius: 2, flex: 'none',
              background: p.color ?? color(p.key),
            }} />
            <span style={{ flex: 1, color: 'var(--ink-2)' }}>{p.label}</span>
            <span className="num" style={{ fontWeight: 500 }}>{fmt(p.value, 1)}</span>
            <span className="num" style={{ color: 'var(--muted)', width: 46, textAlign: 'right' }}>
              {((p.value / total) * 100).toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ==========================================================================
   Carpet plot — 365 x 24 hours, the shape of a whole year at a glance
   ========================================================================== */

export function CarpetPlot({
  values, height = 168, unit = 'kW', hue = 'var(--chw)',
}: {
  values: number[]; height?: number; unit?: string; hue?: string
}) {
  const [hover, setHover] = useState<{ d: number; h: number; v: number } | null>(null)
  const days = Math.floor(values.length / 24)
  const w = 730
  const pad = { t: 8, r: 8, b: 16, l: 26 }
  const cw = (w - pad.l - pad.r) / days
  const ch = (height - pad.t - pad.b) / 24
  const hi = Math.max(1e-9, ...values)

  const canvas = useMemo(() => {
    // Sequential ramp: one hue, light to dark, by opacity over the surface.
    const cells: { x: number; y: number; o: number }[] = []
    for (let d = 0; d < days; d++) {
      for (let h = 0; h < 24; h++) {
        const v = values[d * 24 + h] ?? 0
        if (v <= 0) continue
        cells.push({ x: d, y: h, o: Math.min(1, (v / hi) ** 0.72) })
      }
    }
    return cells
  }, [values, days, hi])

  const monthStarts = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

  return (
    <div>
      <svg
        width="100%" height={height} viewBox={`0 0 ${w} ${height}`}
        style={{ display: 'block' }} onMouseLeave={() => setHover(null)}
        role="img" aria-label={`Hourly heat map, peak ${compact(hi)} ${unit}`}
      >
        {[0, 6, 12, 18].map((h) => (
          <text key={h} x={pad.l - 5} y={pad.t + h * ch + ch / 2}
            textAnchor="end" dominantBaseline="middle"
            fontSize="9" fill="var(--faint)" fontFamily="var(--data)">
            {String(h).padStart(2, '0')}
          </text>
        ))}
        {monthStarts.map((d, i) => (
          <text key={d} x={pad.l + d * cw} y={height - 4}
            fontSize="9" fill="var(--faint)" fontFamily="var(--data)">
            {MONTHS[i]}
          </text>
        ))}
        <rect x={pad.l} y={pad.t} width={days * cw} height={24 * ch}
          fill="var(--panel-sunk)" />
        {canvas.map((c, i) => (
          <rect
            key={i}
            x={pad.l + c.x * cw} y={pad.t + c.y * ch}
            width={Math.max(cw, 0.8)} height={Math.max(ch, 0.8)}
            fill={hue} opacity={c.o}
          />
        ))}
        {/* one transparent column per day gives a usable hit target */}
        {Array.from({ length: days }, (_, d) => (
          <rect
            key={d} x={pad.l + d * cw} y={pad.t} width={Math.max(cw, 1)} height={24 * ch}
            fill="transparent"
            onMouseMove={(e) => {
              const r = (e.target as SVGRectElement).getBoundingClientRect()
              const h = Math.min(23, Math.max(0,
                Math.floor(((e.clientY - r.top) / r.height) * 24)))
              setHover({ d, h, v: values[d * 24 + h] ?? 0 })
            }}
          />
        ))}
        {hover && (
          <rect
            x={pad.l + hover.d * cw - 0.5} y={pad.t}
            width={Math.max(cw, 1) + 1} height={24 * ch}
            fill="none" stroke="var(--ink)" strokeWidth="1" opacity="0.5"
            pointerEvents="none"
          />
        )}
      </svg>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, marginTop: 6,
        fontSize: 'var(--t-xs)', color: 'var(--muted)',
      }}>
        <span className="tag">0</span>
        <span style={{
          flex: 1, height: 7, borderRadius: 2,
          background: `linear-gradient(90deg, var(--panel-sunk), ${hue})`,
        }} />
        <span className="tag">{compact(hi)} {unit}</span>
        <span style={{ minWidth: 150, textAlign: 'right' }} className="num">
          {hover
            ? `${hourLabel(hover.d * 24 + hover.h)} · ${compact(hover.v)} ${unit}`
            : 'Hover for any hour'}
        </span>
      </div>
    </div>
  )
}

/* ==========================================================================
   Load-duration curve — the classic plant-sizing view
   ========================================================================== */

export function DurationCurve({
  values, capacities = [], unit = 'kW', height = 190,
}: {
  values: number[]
  capacities?: { label: string; value: number; color?: string }[]
  unit?: string
  height?: number
}) {
  const sorted = useMemo(
    () => [...values].sort((a, b) => b - a), [values])
  const hi = Math.max(1e-9, sorted[0] ?? 0)

  return (
    <div style={{ position: 'relative' }}>
      <LineChart
        height={height}
        unit={` ${unit}`}
        series={[
          {
            key: 'duration', label: 'Load exceeded for N hours',
            values: sorted, fill: true, color: 'var(--chw)',
          },
          // A flat series is the simplest honest way to draw the reference
          // line on the same single axis as the data.
          ...capacities.map((c) => ({
            key: `cap-${c.label}`, label: c.label,
            values: sorted.map(() => c.value),
            color: c.color ?? 'var(--live)', dashed: true,
          })),
        ]}
        xLabel={(i) => `${compact(i)} h`}
        yMax={Math.max(hi, ...capacities.map((c) => c.value)) * 1.05}
      />
      {capacities.length > 0 && (
        <div style={{ marginTop: 4, display: 'grid', gap: 3 }}>
          {capacities.map((c) => {
            const hours = sorted.filter((v) => v > c.value).length
            return (
              <div key={c.label} style={{
                display: 'flex', gap: 8, alignItems: 'baseline',
                fontSize: 'var(--t-xs)', color: 'var(--muted)',
              }}>
                <span style={{ flex: 1 }}>{c.label}</span>
                <span className="num">
                  {hours > 0
                    ? `exceeded ${fmt(hours)} h/yr`
                    : 'never exceeded'}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/* ==========================================================================
   Gauge — a single reading against a limit
   ========================================================================== */

export function Gauge({
  value, limit, label, unit, invert = false,
}: {
  value: number | null; limit: number; label: string
  unit: string; invert?: boolean
}) {
  if (value === null) {
    return <p className="hint">Not enough output to compute {label}.</p>
  }
  const pass = invert ? value >= limit : value <= limit
  const span = Math.max(value, limit) * 1.35
  const pct = (v: number) => Math.min(100, (v / span) * 100)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="num" style={{
          fontSize: 'var(--t-2xl)', fontWeight: 500, letterSpacing: '-0.02em',
          color: pass ? 'var(--ok)' : 'var(--bad)',
        }}>{value.toFixed(3)}</span>
        <span style={{ color: 'var(--muted)', fontSize: 'var(--t-sm)' }}>{unit}</span>
        <span className={`badge ${pass ? 'badge-ok' : 'badge-bad'}`}
          style={{ marginLeft: 'auto' }}>
          <span className="dot" />{pass ? 'Compliant' : 'Above limit'}
        </span>
      </div>

      <div style={{
        position: 'relative', height: 10, marginTop: 12,
        background: 'var(--panel-sunk)', border: '1px solid var(--line)',
        borderRadius: 99,
      }}>
        <div style={{
          position: 'absolute', inset: '0 auto 0 0', borderRadius: 99,
          width: `${pct(value)}%`,
          background: pass ? 'var(--ok)' : 'var(--bad)',
        }} />
        <div style={{
          position: 'absolute', top: -4, bottom: -4, left: `${pct(limit)}%`,
          width: 2, background: 'var(--ink)', borderRadius: 1,
        }} />
      </div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', marginTop: 5,
      }}>
        <span className="tag">{label}</span>
        <span className="tag">Limit {limit} {unit}</span>
      </div>
    </div>
  )
}
