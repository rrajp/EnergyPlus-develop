/**
 * Step 3: the plant monitor.
 *
 * While a run is in flight this shows the telemetry as it arrives.  Once the
 * run finishes it switches to the full hourly series so any hour of the year
 * can be scrubbed — the mimic panel above reads from whichever frame is
 * selected, so the same diagram serves both.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { LineChart, fmt, hourLabel, robustMax } from './charts'
import {
  Card, Empty, IconPause, IconPlay, Readout, Segmented,
} from './ui'
import { isRunning, useStore } from './store'

const SPEEDS = [
  { value: '1', label: '1×' },
  { value: '4', label: '4×' },
  { value: '16', label: '16×' },
  { value: '64', label: '64×' },
]

export function MonitorView() {
  const {
    jobState, telemetry, results, job, playHour, playing,
    setPlayHour, setPlaying, toast,
  } = useStore()
  const running = isRunning(jobState)
  const [speed, setSpeed] = useState('16')
  const [series, setSeries] = useState<Record<string, number[]>>({})
  const [loading, setLoading] = useState(false)

  const KEYS = [
    'coolingLoadKw', 'coolingDeliveredKw', 'chillerPowerKw',
    'towerPowerKw', 'pumpPowerKw', 'plantPowerKw',
    'oaDrybulbC', 'oaWetbulbC', 'chwSupplyC', 'cwSupplyC',
    'boilerHeatKw', 'heatingLoadKw', 'chillerPlr',
  ]

  // After a run completes, pull the real hourly series for scrubbing.
  useEffect(() => {
    if (!job || jobState !== 'complete') return
    let alive = true
    setLoading(true)
    api.series(job.id, KEYS, 'hourly')
      .then((r) => { if (alive) setSeries(r.series) })
      .catch((e) => {
        if (alive) toast('error', e instanceof Error ? e.message : String(e))
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [job?.id, jobState])

  const hours = series.coolingLoadKw?.length ?? 0
  const canScrub = hours > 0

  /* playback clock */
  const raf = useRef<number>()
  const last = useRef<number>(0)
  useEffect(() => {
    if (!playing || !canScrub) return
    const step = (t: number) => {
      if (!last.current) last.current = t
      const dt = t - last.current
      const perHour = 1000 / Number(speed) / 6
      if (dt >= perHour) {
        last.current = t
        setPlayHour((useStore.getState().playHour + 1) % hours)
      }
      raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current)
      last.current = 0
    }
  }, [playing, canScrub, speed, hours, setPlayHour])

  if (!running && !canScrub && telemetry.length === 0) {
    return (
      <Card title="Plant monitor">
        <Empty title="Nothing to monitor yet">
          Start a run and the plant animates here while EnergyPlus solves it.
          When the run finishes you can scrub back through any hour of the year.
        </Empty>
      </Card>
    )
  }

  /* ---- live view ---- */
  if (running || !canScrub) {
    const recent = telemetry.slice(-320)
    return (
      <div className="stack" style={{ gap: 'var(--gap)' }}>
        <Card
          title="Live plant data"
          aside={
            <span className="badge badge-live" style={{ marginLeft: 'auto' }}>
              <span className="dot dot-pulse" />
              {telemetry.length.toLocaleString()} frames
            </span>
          }
        >
          {recent.length === 0 ? (
            <p className="hint">Waiting for the first telemetry frame…</p>
          ) : (
            <>
              <LineChart
                height={190} unit=" kW"
                xLabel={(i) => recent[i] ? hourLabel(recent[i].hour) : ''}
                xTickCount={4}
                series={[
                  {
                    key: 'coolingDeliveredKw', label: 'Cooling delivered',
                    values: recent.map((f) => f.coolingW / 1000), fill: true,
                  },
                  {
                    key: 'chillerPowerKw', label: 'Chiller power',
                    values: recent.map((f) => f.chillerPowerW / 1000),
                  },
                  {
                    key: 'pumpPowerKw', label: 'Pump power',
                    values: recent.map((f) => f.pumpPowerW / 1000),
                  },
                  {
                    key: 'towerPowerKw', label: 'Tower fans',
                    values: recent.map((f) => f.towerPowerW / 1000),
                  },
                ]}
              />
              <div style={{ marginTop: 14 }}>
                <LineChart
                  height={140} unit=" °C"
                  xLabel={(i) => recent[i] ? hourLabel(recent[i].hour) : ''}
                  xTickCount={4}
                  series={[
                    {
                      key: 'oaDrybulbC', label: 'Outdoor dry-bulb',
                      values: recent.map((f) => f.oaDrybulb),
                    },
                    {
                      key: 'oaWetbulbC', label: 'Outdoor wet-bulb',
                      values: recent.map((f) => f.oaWetbulb),
                      color: 'var(--cw)',
                    },
                  ]}
                />
              </div>
            </>
          )}
          <p className="hint" style={{ marginTop: 12 }}>
            These readings come from EnergyPlus itself, sampled at the end of
            each zone timestep while the simulation is still running.
          </p>
        </Card>
      </div>
    )
  }

  /* ---- playback view ---- */
  const at = (k: string) => series[k]?.[playHour] ?? 0

  // Always show a full week so the chart never collapses to a single point at
  // the start of the year; the marker moves within the window instead.
  const WINDOW = 168
  const span = Math.min(WINDOW, hours)
  const from = Math.max(0, Math.min(playHour - span + 1, hours - span))
  const win = (k: string) => (series[k] ?? []).slice(from, from + span)
  const markerIdx = playHour - from

  return (
    <div className="stack" style={{ gap: 'var(--gap)' }}>
      <Card title="Playback">
        <div className="scrubber">
          <button
            className="btn"
            onClick={() => setPlaying(!playing)}
            aria-label={playing ? 'Pause playback' : 'Play the year'}
          >
            {playing ? <IconPause /> : <IconPlay />}
            {playing ? 'Pause' : 'Play the year'}
          </button>
          <input
            className="scrub-input" type="range"
            min={0} max={Math.max(0, hours - 1)} value={playHour}
            onChange={(e) => setPlayHour(e.target.valueAsNumber)}
            aria-label="Hour of the year"
          />
          <span className="num" style={{
            minWidth: 132, textAlign: 'right', fontSize: 'var(--t-sm)',
          }}>
            {hourLabel(playHour)}
          </span>
          <Segmented value={speed} onChange={setSpeed} options={SPEEDS} />
        </div>

        <div style={{ display: 'flex', gap: 7, marginTop: 10, flexWrap: 'wrap' }}>
          {results && (
            <>
              <button
                className="chip"
                onClick={() => setPlayHour(results.summary.peakCoolingHour)}
              >
                Jump to peak cooling
              </button>
              <button
                className="chip"
                onClick={() => setPlayHour(results.summary.peakPlantHour)}
              >
                Jump to peak demand
              </button>
              {results.summary.peakHeatingKw > 0 && (
                <button
                  className="chip"
                  onClick={() => setPlayHour(results.summary.peakHeatingHour)}
                >
                  Jump to peak heating
                </button>
              )}
            </>
          )}
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 'var(--gap)' }}>
        <Card title="The week up to this hour">
          {loading ? <p className="hint">Loading…</p> : (
            <>
              <LineChart
                height={180} unit=" kW"
                marker={markerIdx}
                xLabel={(i) => hourLabel(from + i)}
                xTickCount={4}
                series={[
                  { key: 'coolingLoadKw', label: 'Cooling load', values: win('coolingLoadKw'), fill: true },
                  { key: 'plantPowerKw', label: 'Plant electricity', values: win('plantPowerKw') },
                ]}
              />
              <div style={{ marginTop: 14 }}>
                <LineChart
                  height={150} unit=" °C"
                  marker={markerIdx}
                  yMax={robustMax([
                    win('oaDrybulbC'), win('cwSupplyC'), win('chwSupplyC'),
                  ])}
                  xLabel={(i) => hourLabel(from + i)}
                  xTickCount={4}
                  series={[
                    { key: 'oaDrybulbC', label: 'Outdoor air', values: win('oaDrybulbC') },
                    { key: 'cwSupplyC', label: 'Condenser water', values: win('cwSupplyC') },
                    { key: 'chwSupplyC', label: 'Chilled water', values: win('chwSupplyC') },
                  ]}
                />
              </div>
            </>
          )}
        </Card>

        <Card title="This hour">
          <Readout label="Cooling load" value={fmt(at('coolingLoadKw'))} unit="kW" />
          <Readout label="Cooling delivered" value={fmt(at('coolingDeliveredKw'))} unit="kW" />
          <Readout label="Chiller power" value={fmt(at('chillerPowerKw'))} unit="kW" />
          <Readout label="Tower fans" value={fmt(at('towerPowerKw'), 1)} unit="kW" />
          <Readout label="Pumps" value={fmt(at('pumpPowerKw'), 1)} unit="kW" />
          <Readout label="Plant total" value={fmt(at('plantPowerKw'))} unit="kW" />
          <div className="divider" />
          <Readout label="Instant COP"
            value={at('plantPowerKw') > 0.5
              ? (at('coolingDeliveredKw') / at('plantPowerKw')).toFixed(2)
              : '—'} />
          <Readout label="Chiller part load"
            value={at('chillerPlr') > 0 ? at('chillerPlr').toFixed(2) : 'off'} />
          <div className="divider" />
          <Readout label="Outdoor dry-bulb" value={at('oaDrybulbC').toFixed(1)} unit="°C" />
          <Readout label="Outdoor wet-bulb" value={at('oaWetbulbC').toFixed(1)} unit="°C" />
          <Readout label="Chilled water" value={at('chwSupplyC').toFixed(1)} unit="°C" />
          <Readout label="Condenser water" value={at('cwSupplyC').toFixed(1)} unit="°C" />
          {at('boilerHeatKw') > 0 && (
            <>
              <div className="divider" />
              <Readout label="Boiler output" value={fmt(at('boilerHeatKw'))} unit="kW" />
              <Readout label="Heating load" value={fmt(at('heatingLoadKw'))} unit="kW" />
            </>
          )}
        </Card>
      </div>
    </div>
  )
}

/** The frame the mimic panel should draw, for whichever step is showing. */
export function useMimicFrame() {
  const { telemetry, playHour, step, jobState } = useStore()
  const running = isRunning(jobState)

  return useMemo(() => {
    if (!telemetry.length) return null
    if (running) return telemetry[telemetry.length - 1]
    if (step === 'monitor') {
      // Telemetry is strided, so find the frame nearest the scrubbed hour.
      let best = telemetry[0]
      let bestGap = Infinity
      for (const f of telemetry) {
        const gap = Math.abs(f.hour - playHour)
        if (gap < bestGap) { bestGap = gap; best = f }
      }
      return best
    }
    return telemetry[telemetry.length - 1]
  }, [telemetry, playHour, step, running])
}
