/**
 * The mimic panel: a plant schematic that doubles as the live instrument.
 *
 * This is the one element the tool is built around, so it never leaves the
 * screen.  It has three states over the same geometry:
 *
 *   design    — the plant you are assembling; click a tile to edit it
 *   live      — animating from telemetry while EnergyPlus is still solving
 *   playback  — scrubbing back through the finished year
 *
 * Geometry is derived from the equipment list, and the animation speed of
 * each pipe comes from the real load on that circuit, so the drawing cannot
 * drift away from the model that was actually run.
 */

import type { FoldedTelemetry } from './api'
import type { Boiler, Chiller, CoolingTower, HeatPump, PlantConfig } from './types'

export type MimicMode = 'design' | 'live' | 'playback'

interface Props {
  config: PlantConfig
  frame: FoldedTelemetry | null
  mode: MimicMode
  selectedId?: string
  onSelect?: (kind: 'chiller' | 'boiler' | 'tower' | 'heatpump', id: string) => void
}

/* ---- geometry ---------------------------------------------------------- */

const VB = { w: 1000, h: 540 }
const TILE = { w: 192, h: 46, gap: 9 }
const COL = { load: 30, pump: 216, plant: 380, reject: 640 }
const ROW = { chw: 54, cw: 250, hhw: 404 }

const LOAD_W = 168
const PUMP_W = 138

//: Average glyph width as a fraction of font size. Archivo is a good deal
//: narrower than IBM Plex Mono, so one factor for both over-truncates titles.
const GLYPH = { display: 0.52, mono: 0.58 }

/** Clip a label so a nameplate never overflows its tile. */
function fit(
  text: string, boxW: number, fontPx: number, face: keyof typeof GLYPH = 'mono',
): string {
  const max = Math.floor((boxW - 20) / (fontPx * GLYPH[face]))
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`
}

const TOWER_SPEC: Record<string, string> = {
  'CoolingTower:VariableSpeed': 'VS fan',
  'CoolingTower:TwoSpeed': '2-speed',
  'CoolingTower:SingleSpeed': '1-speed',
}

function stack(x: number, y: number, count: number) {
  return Array.from({ length: count }, (_, i) => ({
    x, y: y + i * (TILE.h + TILE.gap), w: TILE.w, h: TILE.h,
  }))
}

/* ---- pieces ------------------------------------------------------------ */

function Pipe({
  d, loop, active, intensity = 0,
}: {
  d: string
  loop: 'chw' | 'cw' | 'hhw'
  active: boolean
  intensity?: number
}) {
  const stroke = `var(--${loop})`
  // Faster dashes mean more flow. Clamped so it never strobes.
  const seconds = 2.4 - Math.min(1, intensity) * 1.8
  return (
    <>
      <path
        d={d} fill="none" stroke={stroke} strokeWidth="4.5"
        strokeLinecap="round" strokeLinejoin="round"
        opacity={active ? 0.24 : 0.5}
      />
      {active && (
        <path
          className="pipe-flow"
          d={d} fill="none" stroke={stroke} strokeWidth="2.4"
          strokeLinecap="round" strokeLinejoin="round"
          style={{ animationDuration: `${seconds.toFixed(2)}s` }}
        />
      )}

    </>
  )
}

function Nameplate({
  x, y, w, h, loop, title, spec, reading, running, off, selected, onClick,
}: {
  x: number; y: number; w: number; h: number
  loop: 'chw' | 'cw' | 'hhw'
  title: string
  spec: string
  reading?: string | null
  running: boolean
  off?: boolean
  selected?: boolean
  onClick?: () => void
}) {
  const READING_W = 56
  const textW = reading != null ? w - READING_W - 4 : w

  const cls = [
    'node-shell',
    running ? 'node-running' : '',
    selected ? 'node-selected' : '',
    off ? 'node-off' : '',
  ].filter(Boolean).join(' ')

  return (
    <g
      className={cls}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={`${title}, ${spec}${running ? ', running' : ''}`}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onClick()
        }
      }}
    >
      <rect
        className="node-body"
        x={x} y={y} width={w} height={h} rx="4"
        fill="var(--panel-raised)"
        stroke={running ? 'var(--live)' : 'var(--line-strong)'}
        strokeWidth={running ? 1.6 : 1}
      />
      {/* the coloured spine says which loop this machine belongs to */}
      <rect x={x} y={y} width="3.5" height={h} rx="1.75" fill={`var(--${loop})`} />

      {/* A live reading claims the right-hand third, so the text column has
          to shrink to match or the two overlap. */}
      <text
        x={x + 11} y={y + 18} fontSize="11.5" fontWeight="600"
        fill="var(--ink)" fontFamily="var(--display)"
      >
        {fit(title, textW, 11.5, 'display')}
      </text>
      <text
        x={x + 11} y={y + 33} fontSize="9.5"
        fill="var(--muted)" fontFamily="var(--data)"
        letterSpacing="0.03em"
      >
        {fit(spec, textW, 9.5)}
      </text>

      {reading != null && (
        <>
          <line
            x1={x + w - READING_W} y1={y + 7} x2={x + w - READING_W} y2={y + h - 7}
            stroke="var(--line)" strokeWidth="1"
          />
          <text
            x={x + w - 9} y={y + h / 2 + 5} textAnchor="end"
            fontSize="13.5" fontWeight="500"
            fill={running ? 'var(--live)' : 'var(--faint)'}
            fontFamily="var(--data)"
          >
            {reading}
          </text>
        </>
      )}

      {running && (
        <circle cx={x + 7.5} cy={y + h - 7} r="2.4" fill="var(--live)">
          <animate attributeName="opacity" values="1;0.25;1"
            dur="1.6s" repeatCount="indefinite" />
        </circle>
      )}
    </g>
  )
}

function GroupLabel({ x, y, text, loop }: {
  x: number; y: number; text: string; loop: 'chw' | 'cw' | 'hhw'
}) {
  return (
    <g>
      <rect x={x} y={y - 9} width="2" height="10" fill={`var(--${loop})`} />
      <text
        x={x + 7} y={y} fontSize="9.5" fontFamily="var(--data)"
        letterSpacing="0.11em" fill="var(--muted)"
      >
        {text.toUpperCase()}
      </text>
    </g>
  )
}

/* ---- main -------------------------------------------------------------- */

export function Mimic({ config, frame, mode, selectedId, onSelect }: Props) {
  const chillers = config.chillers.filter((c) => c.enabled)
  const towers = config.towers.filter((t) => t.enabled)
  const boilers = config.boilers.filter((b) => b.enabled)
  const pumps = config.heat_pumps.filter((h) => h.enabled)
  const heaters: (Boiler | HeatPump)[] = [...pumps, ...boilers]

  const waterCooled = chillers.some((c) => c.condenser === 'WaterCooled')
  const hasHeating = heaters.length > 0 && config.hhw_loop.enabled
  const live = mode !== 'design' && frame !== null

  const chwLoad = live ? Math.abs(frame!.values['load:cooling::rate'] ?? 0) : 0
  const hhwLoad = live ? Math.abs(frame!.values['load:heating::rate'] ?? 0) : 0
  const peakCooling = Math.max(
    1, chillers.reduce((a, c) => a + c.capacity_kw, 0) * 1000)
  const peakHeating = Math.max(
    1, heaters.reduce((a, h) => a + h.capacity_kw, 0) * 1000)

  // Motion means "fluid is moving right now", so a static design never animates.
  const chwActive = live && chwLoad > 0
  const hhwActive = live && hasHeating && hhwLoad > 0
  const cwActive = live && waterCooled && chwLoad > 0

  const chillerSlots = stack(COL.plant, ROW.chw + 22, chillers.length)
  const towerSlots = stack(COL.reject, ROW.cw - 40, towers.length)
  const heaterSlots = stack(COL.plant, ROW.hhw + 22, heaters.length)

  const readingFor = (id: string, suffix: string, unit = 'kW') => {
    if (!live) return null
    const v = frame!.values[`${id}::${suffix}`]
    if (v === undefined) return null
    return `${Math.abs(v / 1000) < 10
      ? (Math.abs(v) / 1000).toFixed(1)
      : Math.round(Math.abs(v) / 1000)} ${unit}`
  }

  const isRunning = (id: string, suffix: string) => {
    if (!live) return false
    const v = frame!.values[`${id}::${suffix}`]
    return v !== undefined && Math.abs(v) > 1
  }

  const bottom = (slots: { y: number; h: number }[], fallback: number) =>
    slots.length ? slots[slots.length - 1].y + slots[slots.length - 1].h : fallback

  /* ---- pipe routing --------------------------------------------------- */

  const CX = COL.plant + TILE.w / 2       // equipment column centre
  const LX = COL.load + LOAD_W / 2        // load column centre

  const chwMid = ROW.chw + 22 + TILE.h / 2
  const chwBottom = bottom(chillerSlots, chwMid)
  const chwReturnY = chwBottom + 26

  // Supply runs chillers -> pumps -> load; return closes the circuit back to
  // the bottom of the chiller stack, so the loop reads as a loop.
  const chwSupply = `M${COL.plant} ${chwMid} H${COL.load + LOAD_W}`
  const chwReturn =
    `M${LX} ${chwMid + 22} V${chwReturnY} H${CX} V${chwBottom}`

  const hhwMid = ROW.hhw + 22 + TILE.h / 2
  const hhwBottom = bottom(heaterSlots, hhwMid)
  const hhwReturnY = hhwBottom + 26
  const hhwSupply = `M${COL.plant} ${hhwMid} H${COL.load + LOAD_W}`
  const hhwReturn =
    `M${LX} ${hhwMid + 22} V${hhwReturnY} H${CX} V${hhwBottom}`

  const TX = COL.reject + TILE.w / 2
  const towerTop = towerSlots.length ? towerSlots[0].y : ROW.cw
  const towerBottom = bottom(towerSlots, ROW.cw)
  const cwOut =
    `M${COL.plant + TILE.w} ${chwMid} H${COL.plant + TILE.w + 34} ` +
    `V${towerTop - 18} H${TX} V${towerTop}`
  const cwBack =
    `M${TX} ${towerBottom} V${towerBottom + 26} H${COL.plant + TILE.w + 62} ` +
    `V${chwBottom - TILE.h / 2} H${COL.plant + TILE.w}`

  const empty = chillers.length === 0 && heaters.length === 0

  return (
    <svg viewBox={`0 0 ${VB.w} ${VB.h}`} preserveAspectRatio="xMidYMid meet">
      <title>Plant mimic diagram</title>

      {empty && (
        <text
          x={VB.w / 2} y={VB.h / 2} textAnchor="middle"
          fontSize="13" fill="var(--muted)" fontFamily="var(--body)"
        >
          Add a chiller or a boiler to start building the plant.
        </text>
      )}

      {/* ---------------- chilled water ---------------- */}
      {chillers.length > 0 && (
        <>
          <GroupLabel x={COL.load} y={ROW.chw} text="Chilled-water loop" loop="chw" />
          <Pipe d={chwSupply} loop="chw" active={chwActive}
            intensity={chwLoad / peakCooling} />
          <Pipe d={chwReturn} loop="chw" active={chwActive}
            intensity={chwLoad / peakCooling} />

          <Nameplate
            x={COL.load} y={chwMid - 22} w={LOAD_W} h={TILE.h} loop="chw"
            title="Cooling load"
            spec={live ? 'district demand' : (config.load?.label ?? 'no profile')}
            reading={live ? `${Math.round(chwLoad / 1000)} kW` : null}
            running={chwLoad > 0}
          />
          <Nameplate
            x={COL.pump} y={chwMid - 22} w={PUMP_W} h={TILE.h} loop="chw"
            title={config.pumps.secondary_chw ? 'CHW pumps' : 'CHW pump'}
            spec={config.chw_loop.arrangement === 'PrimarySecondary'
              ? 'pri + sec' : 'primary'}
            reading={readingFor('pump:chw-primary', 'power')}
            running={isRunning('pump:chw-primary', 'power')}
          />

          {chillers.map((c: Chiller, i) => {
            const s = chillerSlots[i]
            const id = `chiller:${c.id}`
            return (
              <Nameplate
                key={c.id} {...s} loop="chw"
                title={c.name}
                spec={`${Math.round(c.capacity_kw)} kW · COP ${c.reference_cop}`}
                reading={readingFor(id, 'cooling')}
                running={isRunning(id, 'cooling')}
                selected={selectedId === c.id}
                onClick={onSelect ? () => onSelect('chiller', c.id) : undefined}
              />
            )
          })}
        </>
      )}

      {/* ---------------- condenser water ---------------- */}
      {waterCooled && towers.length > 0 && (
        <>
          <GroupLabel
            x={COL.reject} y={towerTop - 38}
            text="Condenser-water loop" loop="cw"
          />
          <Pipe d={cwOut} loop="cw" active={cwActive}
            intensity={chwLoad / peakCooling} />
          <Pipe d={cwBack} loop="cw" active={cwActive}
            intensity={chwLoad / peakCooling} />

          {towers.map((t: CoolingTower, i) => {
            const s = towerSlots[i]
            const id = `tower:${t.id}`
            return (
              <Nameplate
                key={t.id} {...s} loop="cw"
                title={t.name}
                spec={`${TOWER_SPEC[t.model]} · ${t.design_approach_c} K`}
                reading={readingFor(id, 'reject')}
                running={isRunning(id, 'reject')}
                selected={selectedId === t.id}
                onClick={onSelect ? () => onSelect('tower', t.id) : undefined}
              />
            )
          })}
        </>
      )}

      {/* air-cooled chillers reject straight to ambient */}
      {chillers.length > 0 && !waterCooled && (
        <text
          x={COL.plant + TILE.w + 26}
          y={ROW.chw + 40} fontSize="10"
          fill="var(--muted)" fontFamily="var(--data)" letterSpacing="0.06em"
        >
          → AMBIENT AIR (air-cooled)
        </text>
      )}

      {/* ---------------- hot water ---------------- */}
      {hasHeating && (
        <>
          <GroupLabel x={COL.load} y={ROW.hhw} text="Hot-water loop" loop="hhw" />
          <Pipe d={hhwSupply} loop="hhw" active={hhwActive}
            intensity={hhwLoad / peakHeating} />
          <Pipe d={hhwReturn} loop="hhw" active={hhwActive}
            intensity={hhwLoad / peakHeating} />

          <Nameplate
            x={COL.load} y={hhwMid - 22} w={LOAD_W} h={TILE.h} loop="hhw"
            title="Heating load"
            spec="district demand"
            reading={live ? `${Math.round(hhwLoad / 1000)} kW` : null}
            running={hhwLoad > 0}
          />
          <Nameplate
            x={COL.pump} y={hhwMid - 22} w={PUMP_W} h={TILE.h} loop="hhw"
            title="HHW pump"
            spec={config.hhw_loop.primary_secondary ? 'pri + sec' : 'primary'}
            reading={readingFor('pump:hhw-primary', 'power')}
            running={isRunning('pump:hhw-primary', 'power')}
          />

          {heaters.map((h, i) => {
            const s = heaterSlots[i]
            const isHp = 'source' in h
            const id = `${isHp ? 'hp' : 'boiler'}:${h.id}`
            return (
              <Nameplate
                key={h.id} {...s} loop="hhw"
                title={h.name}
                spec={isHp
                  ? `${Math.round(h.capacity_kw)} kW · COP ${(h as HeatPump).reference_cop}`
                  : `${Math.round(h.capacity_kw)} kW · η ${(h as Boiler).efficiency}`}
                reading={readingFor(id, 'heat')}
                running={isRunning(id, 'heat')}
                selected={selectedId === h.id}
                onClick={onSelect
                  ? () => onSelect(isHp ? 'heatpump' : 'boiler', h.id)
                  : undefined}
              />
            )
          })}
        </>
      )}

      {/* ---------------- ambient readout ---------------- */}
      {live && (
        <g>
          <text
            x={VB.w - 14} y={22} textAnchor="end" fontSize="9.5"
            fontFamily="var(--data)" letterSpacing="0.1em" fill="var(--muted)"
          >
            OUTDOOR AIR
          </text>
          <text
            x={VB.w - 14} y={44} textAnchor="end" fontSize="18" fontWeight="500"
            fontFamily="var(--data)" fill="var(--ink)"
          >
            {frame!.oaDrybulb.toFixed(1)}°C
          </text>
          <text
            x={VB.w - 14} y={60} textAnchor="end" fontSize="10"
            fontFamily="var(--data)" fill="var(--muted)"
          >
            {frame!.oaWetbulb.toFixed(1)}°C wet-bulb
          </text>
        </g>
      )}
    </svg>
  )
}
