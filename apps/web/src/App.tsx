/** The frame: masthead, step rail, persistent mimic panel, and the side rail. */

import { useEffect, useState } from 'react'
import { Inspector, PlantContents } from './BuildPanels'
import { Login } from './Login'
import { Mimic } from './Mimic'
import { MonitorView, useMimicFrame } from './Monitor'
import { ResultsView } from './Results'
import { RunBar } from './RunBar'
import {
  IconCheck, IconMoon, IconReset, IconSignOut, IconSun, IconX,
} from './ui'
import { fmt } from './charts'
import { isRunning, useStore } from './store'
import { applyTheme, readTheme, storeTheme } from './theme'
import type { Theme } from './theme'
import type { Step } from './store'

const STEPS: { key: Step; label: string }[] = [
  { key: 'build', label: 'Build & simulate' },
  { key: 'results', label: 'Results & compliance' },
  { key: 'monitor', label: 'Plant monitor' },
]

export default function App() {
  const {
    step, setStep, config, patch, booting, engine,
    jobState, results, selection, select, reset, telemetry,
    user, authChecked, checkSession, signOut,
  } = useStore()

  useEffect(() => {
    // Establish who this is first; boot() only runs once there is a session.
    void checkSession().then(() => {
      if (!useStore.getState().user) return
      // ?job=<id> attaches to a run started elsewhere, so a link to a
      // simulation shows the same screen its author saw.
      const q = new URLSearchParams(location.search)
      const id = q.get('job')
      if (id) useStore.getState().attach(id)
      const s = q.get('step')
      if (!id && (s === 'build' || s === 'results' || s === 'monitor')) {
        useStore.getState().setStep(s)
      }
    })
  }, [checkSession])

  // Warn before losing an in-flight run.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isRunning(useStore.getState().jobState)) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  const [diagramOpen, setDiagramOpen] = useState<boolean | null>(null)
  const frame = useMimicFrame()
  const running = isRunning(jobState)
  const mode = running ? 'live' : step === 'monitor' ? 'playback' : 'design'
  const hasResults = results !== null
  const hasTelemetry = telemetry.length > 0 || hasResults

  // The diagram leads the Build step and steps aside for the others, unless
  // the reader says otherwise.
  const showDiagram = diagramOpen ?? step === 'build'

  const disabled = (k: Step) =>
    (k === 'results' && !hasResults) || (k === 'monitor' && !hasTelemetry)

  if (!authChecked) {
    return <div className="boot-splash">Loading…</div>
  }
  if (!user) {
    return <Login />
  }

  return (
    <div className="app">
      <header className="masthead">
        <span className="mark">
          <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="7" fill="var(--ink)" />
            <path
              d="M7 21h4.2l3.1-10 3.2 14 3.1-9H25"
              stroke="var(--chw)" strokeWidth="2.3" fill="none"
              strokeLinejoin="round" strokeLinecap="round"
            />
          </svg>
          Plantworks
        </span>

        <input
          className="masthead-name" value={config.name}
          aria-label="Plant name"
          onChange={(e) => patch((c) => { c.name = e.target.value })}
        />

        <nav className="steps" aria-label="Workflow">
          {STEPS.map((s, i) => {
            const done = (s.key === 'build' && hasResults)
            return (
              <button
                key={s.key} className={`step${done ? ' step-done' : ''}`}
                aria-current={step === s.key}
                disabled={disabled(s.key)}
                title={disabled(s.key) ? 'Run a simulation first' : undefined}
                onClick={() => setStep(s.key)}
              >
                <span className="step-n">
                  {done ? <IconCheck size={11} /> : i + 1}
                </span>
                {s.label}
              </button>
            )
          })}
        </nav>

        <span className="masthead-spacer" />

        {booting ? (
          <span className="tag">Connecting…</span>
        ) : engine?.found ? (
          <span className="badge badge-neutral" title="Simulation engine in use">
            EnergyPlus {engine.version}
          </span>
        ) : (
          <span className="badge badge-bad">No engine found</span>
        )}

        <ThemeToggle />

        <button
          className="btn btn-ghost btn-sm" onClick={() => {
            if (confirm('Discard this plant and start over?')) reset()
          }}
          title="Reset to defaults"
        >
          <IconReset />
        </button>

        <button
          className="btn btn-ghost btn-sm"
          onClick={() => {
            if (isRunning(useStore.getState().jobState)
              && !confirm('A simulation is running. Sign out and lose it?')) return
            void signOut()
          }}
          title={`Signed in as ${user} — sign out`}
        >
          <IconSignOut />
        </button>
      </header>

      <div className="work">
        <div className="stage">
          {showDiagram ? (
          <div className="mimic">
            <Mimic
              config={config}
              frame={frame}
              mode={mode}
              selectedId={selection.id}
              onSelect={(kind, id) => {
                select({ kind, id })
                if (step !== 'build') setStep('build')
              }}
            />

            <div className="mimic-toolbar">
              <span className={`badge ${running ? 'badge-live' : 'badge-neutral'}`}>
                {running
                  ? <><span className="dot dot-pulse" />Live</>
                  : mode === 'playback' ? 'Playback' : 'Design'}
              </span>
            </div>

            <div className="mimic-legend">
              {[
                ['chw', 'Chilled water'],
                ['cw', 'Condenser water'],
                ['hhw', 'Hot water'],
              ].map(([k, label]) => (
                <span key={k} className="legend-item">
                  <span className="legend-swatch" style={{ background: `var(--${k})` }} />
                  <span style={{ fontSize: 'var(--t-xs)', color: 'var(--ink-2)' }}>
                    {label}
                  </span>
                </span>
              ))}
              <span className="legend-item" style={{ marginLeft: 4 }}>
                <span className="dot" style={{ color: 'var(--live)' }} />
                <span style={{ fontSize: 'var(--t-xs)', color: 'var(--ink-2)' }}>
                  Running
                </span>
              </span>
            </div>

            <button
              className="btn btn-ghost btn-sm"
              style={{ position: 'absolute', top: 10, left: 10, zIndex: 3 }}
              onClick={() => setDiagramOpen(false)}
            >
              Collapse diagram
            </button>
          </div>
          ) : (
            <PlantStrip onExpand={() => setDiagramOpen(true)} />
          )}

          {step === 'build' && showDiagram && (
            <p className="hint" style={{ paddingLeft: 2 }}>
              Click any machine in the diagram to edit it. Pipes, headers,
              bypasses and setpoint controls are generated for you.
            </p>
          )}

          {step === 'results' && <ResultsView />}
          {step === 'monitor' && <MonitorView />}
        </div>

        <aside className="aside scroll">
          {step === 'build' ? (
            <>
              <PlantContents />
              <Inspector />
            </>
          ) : (
            <PlantContents />
          )}
        </aside>

        <div className="runbar-slot">
          <RunBar />
        </div>
      </div>

      <Toasts />
    </div>
  )
}


/* ---- collapsed plant strip -------------------------------------------- */

function PlantStrip({ onExpand }: { onExpand: () => void }) {
  const { config, results } = useStore()
  const chillers = config.chillers.filter((c) => c.enabled)
  const towers = config.towers.filter((t) => t.enabled)
  const heaters = [
    ...config.boilers.filter((b) => b.enabled),
    ...config.heat_pumps.filter((h) => h.enabled),
  ]
  const s = results?.summary

  const loops: { id: string; name: string; detail: string; value: string }[] = []
  if (chillers.length) {
    loops.push({
      id: 'chw', name: `Chilled water · ${chillers.length} chiller${chillers.length > 1 ? 's' : ''}`,
      detail: '', value: s ? `${fmt(s.coolingDeliveredMwh)} MWh` : `${fmt(
        chillers.reduce((a, c) => a + c.capacity_kw, 0))} kW`,
    })
  }
  if (towers.length) {
    loops.push({
      id: 'cw', name: `Condenser water · ${towers.length} tower${towers.length > 1 ? 's' : ''}`,
      detail: '', value: s ? `${fmt(results!.endUse.towerFans)} MWh fans` : 'heat rejection',
    })
  }
  if (heaters.length) {
    loops.push({
      id: 'hhw', name: `Hot water · ${heaters.length} unit${heaters.length > 1 ? 's' : ''}`,
      detail: '', value: s ? `${fmt(s.heatingDeliveredMwh)} MWh` : `${fmt(
        heaters.reduce((a, h) => a + h.capacity_kw, 0))} kW`,
    })
  }

  return (
    <div className="plantstrip">
      {loops.map((l) => (
        <span key={l.id} className="strip-loop">
          <span className="strip-rail" style={{ background: `var(--${l.id})` }} />
          <span>
            <div className="strip-name">{l.name}</div>
            <div className="strip-value">{l.value}</div>
          </span>
        </span>
      ))}
      <span style={{ flex: 1 }} />
      <button className="btn btn-sm" onClick={onExpand}>Show diagram</button>
    </div>
  )
}

/* ---- theme ------------------------------------------------------------- */

function ThemeToggle() {
  const [theme, setThemeState] = useState<Theme>(readTheme)

  const setTheme = (next: Theme) => {
    setThemeState(next)
    applyTheme(next)
    storeTheme(next)
  }

  const next: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
  const label = { system: 'Match system', light: 'Light', dark: 'Dark' }[theme]

  return (
    <button
      className="btn btn-ghost btn-sm"
      onClick={() => setTheme(next[theme])}
      title={`Theme: ${label}`}
      aria-label={`Theme: ${label}. Click to change.`}
    >
      {theme === 'dark' ? <IconMoon /> : <IconSun />}
    </button>
  )
}

/* ---- toasts ------------------------------------------------------------ */

function Toasts() {
  const { toasts, dismiss } = useStore()
  if (!toasts.length) return null
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          <span style={{ flex: 1 }}>{t.text}</span>
          <button
            className="btn btn-ghost btn-sm" onClick={() => dismiss(t.id)}
            aria-label="Dismiss"
          >
            <IconX size={12} />
          </button>
        </div>
      ))}
    </div>
  )
}
