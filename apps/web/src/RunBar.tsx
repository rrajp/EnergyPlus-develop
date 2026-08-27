/** The run control: readiness, progress, and the live log. */

import { useEffect, useRef, useState } from 'react'
import {
  IconBolt, IconCheck, IconStop, IconWarn, SeverityIcon,
} from './ui'
import { isRunning, useStore } from './store'

export function RunBar() {
  const {
    jobState, progress, phase, log, readiness, qa, elapsed, run, cancel,
    streamStatus, engine, results, telemetry,
  } = useStore()

  const [showLog, setShowLog] = useState(false)
  const running = isRunning(jobState)
  const blockers = readiness.filter((r) => r.blocking)
  const items = [...readiness, ...qa]
  const errors = items.filter((r) => r.severity === 'error')
  const warnings = items.filter((r) => r.severity === 'warning')

  const lastLine = log.length ? log[log.length - 1] : ''

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="runbar" data-running={running}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {running ? (
            <button className="btn btn-danger" onClick={cancel}>
              <IconStop />Stop
            </button>
          ) : (
            <button
              className="btn btn-primary"
              onClick={run}
              disabled={blockers.length > 0 || !engine?.found}
            >
              <IconBolt />Run simulation
            </button>
          )}
        </div>

        <div>
          <div className="progress" data-idle={!running}>
            <div
              className="progress-fill"
              style={{ width: `${Math.max(running ? 2 : 0, progress * 100)}%` }}
            />
          </div>
          <div className="ticker">
            {running
              ? `${phase}${progress > 0 ? ` · ${(progress * 100).toFixed(0)}%` : ''}${lastLine ? ` · ${lastLine}` : ''}`
              : jobState === 'complete' && elapsed !== null
                ? `Finished in ${elapsed.toFixed(1)} s · ${telemetry.length} telemetry frames captured`
                : jobState === 'failed'
                  ? 'The run failed. Open the log below for the EnergyPlus output.'
                  : blockers.length
                    ? `${blockers.length} item${blockers.length > 1 ? 's' : ''} to resolve before running`
                    : 'Ready to simulate'}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {running && streamStatus === 'retrying' && (
            <span className="badge badge-warn">
              <span className="dot dot-pulse" />Reconnecting
            </span>
          )}
          {running && streamStatus === 'open' && (
            <span className="badge badge-live">
              <span className="dot dot-pulse" />Live
            </span>
          )}
          {!running && errors.length > 0 && (
            <span className="badge badge-bad">{errors.length} to fix</span>
          )}
          {!running && !errors.length && warnings.length > 0 && (
            <span className="badge badge-warn">{warnings.length} to review</span>
          )}
          {!running && !errors.length && !warnings.length && results && (
            <span className="badge badge-ok"><IconCheck size={11} />Checks passed</span>
          )}
          {results && (
            <span className="tag">
              {results.summary.warningCount.toLocaleString()} EnergyPlus warnings
            </span>
          )}
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setShowLog((v) => !v)}
            aria-expanded={showLog}
          >
            {showLog ? 'Hide log' : 'Log'}
          </button>
        </div>
      </div>

      {items.length > 0 && <ChecksStrip />}
      {showLog && <LogConsole lines={log} />}
    </div>
  )
}

function ChecksStrip() {
  const { readiness, qa } = useStore()
  const items = [...readiness, ...qa]
  const rank = { error: 0, warning: 1, info: 2 } as const
  const sorted = [...items].sort((a, b) => rank[a.severity] - rank[b.severity])
  const [open, setOpen] = useState(false)
  const shown = open ? sorted : sorted.slice(0, 2)

  return (
    <div className="card">
      <header className="card-head">
        <h2 className="card-title">Readiness &amp; physics checks</h2>
        <span className="tag">{items.length} item{items.length > 1 ? 's' : ''}</span>
      </header>
      <div className="checks">
        {shown.map((r) => (
          <div key={r.id} className={`check check-${r.severity}`}>
            <span className="check-icon"><SeverityIcon severity={r.severity} /></span>
            <span>
              <div className="check-title">
                {r.title}
                {r.blocking && (
                  <span className="badge badge-bad" style={{ marginLeft: 7 }}>
                    Blocks the run
                  </span>
                )}
              </div>
              {r.detail && <div className="check-detail">{r.detail}</div>}
            </span>
          </div>
        ))}
      </div>
      {sorted.length > 2 && (
        <button
          className="btn btn-ghost btn-sm"
          style={{ margin: 8, alignSelf: 'flex-start' }}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? 'Show fewer' : `Show all ${sorted.length}`}
        </button>
      )}
    </div>
  )
}

function LogConsole({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines.length])

  return (
    <div className="card">
      <header className="card-head">
        <h2 className="card-title">EnergyPlus output</h2>
        <span className="tag">{lines.length} lines</span>
      </header>
      <div className="card-body" style={{ padding: 10 }}>
        <div className="console scroll" ref={ref}>
          {lines.length === 0
            ? 'Nothing yet. Start a run to see EnergyPlus report its progress here.'
            : lines.map((l, i) => (
              <div
                key={i}
                className={
                  /severe|fatal/i.test(l) ? 'console-line-err'
                    : /warning/i.test(l) ? 'console-line-warn' : undefined
                }
              >{l}</div>
            ))}
        </div>
      </div>
    </div>
  )
}

export function RunSummaryBadges() {
  const { results, elapsed } = useStore()
  if (!results) return null
  const s = results.summary
  return (
    <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
      <span className="badge badge-neutral">
        {s.hours.toLocaleString()} hours simulated
      </span>
      {elapsed !== null && (
        <span className="badge badge-neutral">{elapsed.toFixed(1)} s runtime</span>
      )}
      {s.severeCount > 0
        ? <span className="badge badge-bad"><IconWarn size={11} />{s.severeCount} severe</span>
        : <span className="badge badge-ok"><IconCheck size={11} />No severe errors</span>}
    </div>
  )
}
