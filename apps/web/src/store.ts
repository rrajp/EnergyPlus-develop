/** Application state: the plant being designed, and the run watching it. */

import { create } from 'zustand'
import { api, ApiError, foldTelemetry, openStream } from './api'
import type { FoldedTelemetry, StreamHandle } from './api'
import type {
  Boiler, Catalog, Chiller, CoolingTower, HeatPump, JobState, JobSummary,
  LoadAsset, PlantConfig, Pump, ReadinessItem, Results, Schematic,
  StreamEvent, WeatherAsset,
} from './types'

export type Step = 'build' | 'results' | 'monitor'
export type Selection =
  | { kind: 'chiller' | 'boiler' | 'tower' | 'heatpump'; id: string }
  | { kind: 'storage' | 'loops' | 'pumps' | 'site' | null; id?: undefined }

let seq = 0
const uid = () => `c${(seq++).toString(36)}${Math.random().toString(36).slice(2, 7)}`

/* ---- factory defaults ---------------------------------------------------
   Sized for a mid-scale campus so a first-time visitor can press Run
   immediately and get a physically sensible answer.                        */

export const newPump = (head = 179352): Pump => ({
  model: 'Pump:VariableSpeed', design_head_pa: head, motor_efficiency: 0.9,
  banked_count: 1, standby_count: 0, min_flow_fraction: 0,
})

export const newChiller = (n: number): Chiller => ({
  id: uid(), name: `Chiller ${n}`, enabled: true,
  model: 'Chiller:Electric:EIR', condenser: 'WaterCooled', compressor: 'Centrifugal',
  catalog_key: null, capacity_kw: 1400, reference_cop: 6.1,
  ref_chw_leaving_c: 6.67, ref_cond_entering_c: 29.44,
  min_part_load_ratio: 0.1, max_part_load_ratio: 1, optimum_part_load_ratio: 0.85,
  sizing_factor: 1,
})

export const newBoiler = (n: number): Boiler => ({
  id: uid(), name: `Boiler ${n}`, enabled: true, fuel: 'NaturalGas',
  catalog_key: 'noncondensingboilereff', capacity_kw: 1000, efficiency: 0.8,
  efficiency_basis: 'LeavingBoiler', design_outlet_c: 82, max_outlet_c: 95,
  parasitic_electric_w: 0, sizing_factor: 1,
})

export const newTower = (n: number): CoolingTower => ({
  id: uid(), name: `Cooling Tower ${n}`, enabled: true,
  model: 'CoolingTower:VariableSpeed', autosize: true,
  design_water_flow_m3s: null, design_air_flow_m3s: null, fan_power_kw: null,
  design_approach_c: 3.9, design_range_c: 5.6, design_inlet_wetbulb_c: 25.6,
  min_air_flow_ratio: 0.2, basin_heater_w: 0,
})

export const newHeatPump = (n: number): HeatPump => ({
  id: uid(), name: `Heat Pump ${n}`, enabled: true, source: 'AirSource',
  catalog_key: null, capacity_kw: 500, reference_cop: 3.2,
  min_source_temp_c: -10, sizing_factor: 1,
})

export function defaultConfig(): PlantConfig {
  return {
    name: 'New District Plant', version: 1,
    chillers: [newChiller(1), newChiller(2), newChiller(3)],
    boilers: [newBoiler(1)],
    towers: [newTower(1), newTower(2)],
    heat_pumps: [],
    storage: {
      enabled: false, name: 'Thermal Storage Tank', volume_m3: 500, height_m: 10,
      tank_setpoint_c: 6, deadband_c: 1, charge_leaving_c: 5,
      charge_start_hour: 22, charge_end_hour: 6, charge_on_weekends: true,
      ambient_temp_c: 25, u_value_w_m2k: 0.3, nodes: 6,
    },
    pumps: {
      primary_chw: newPump(), secondary_chw: newPump(), condenser: newPump(119568),
      primary_hhw: newPump(), secondary_hhw: null, tes_distribution: newPump(),
      condenser_arrangement: 'SharedBank',
    },
    chw_loop: {
      arrangement: 'PrimarySecondary', supply_setpoint_c: 6.67, design_delta_t: 6.67,
      loop_exit_max_c: 98, loop_exit_min_c: 1, load_distribution: 'SequentialLoad',
    },
    hhw_loop: {
      enabled: true, supply_setpoint_c: 82, design_delta_t: 11,
      load_distribution: 'SequentialLoad', primary_secondary: false,
    },
    cw_loop: {
      supply_setpoint_c: 29.4, design_delta_t: 5.6, follow_oa_wetbulb: true,
      wetbulb_approach_c: 3.9, min_setpoint_c: 18.3, max_setpoint_c: 35,
    },
    weather: null, load: null,
    tariff: {
      currency: 'INR', energy_charge: 8.5, demand_charge: 350,
      gas_charge_per_kwh: 3.2,
    },
    compliance_tier: 'ECSBC',
    options: {
      run_period: {
        begin_month: 1, begin_day: 1, end_month: 12, end_day: 31,
        timesteps_per_hour: 4, do_sizing: true,
      },
      stream_telemetry: true, telemetry_stride_hours: 3, keep_artifacts: true,
    },
  }
}

/**
 * The sample the default plant is sized for: 3,430 kW cooling against
 * 3 x 1,400 kW of chillers, and 855 kW heating against a 1,000 kW boiler.
 * Change one and check the other.
 */
export const DEFAULT_LOAD = 'sample-large-office'

/* ---- toasts ------------------------------------------------------------ */

export interface Toast { id: number; kind: 'error' | 'ok' | 'info'; text: string }
let toastId = 0

/* ---- store ------------------------------------------------------------- */

const TELEMETRY_CAP = 3200

interface State {
  step: Step
  config: PlantConfig
  selection: Selection

  catalog: Catalog | null
  weather: WeatherAsset[]
  loads: LoadAsset[]
  engine: { found: boolean; version: string | null } | null
  booting: boolean

  user: string | null
  authChecked: boolean
  signingIn: boolean
  /** Server-reported: the shipped demo password is still in force. */
  defaultCredentials: boolean
  authError: string | null

  job: JobSummary | null
  jobState: JobState | null
  progress: number
  phase: string
  log: string[]
  telemetry: FoldedTelemetry[]
  schematic: Schematic | null
  results: Results | null
  qa: ReadinessItem[]
  readiness: ReadinessItem[]
  diagnostics: string[]
  streamStatus: 'idle' | 'open' | 'retrying' | 'closed'
  elapsed: number | null

  playHour: number
  playing: boolean

  toasts: Toast[]
}

interface Actions {
  checkSession(): Promise<void>
  signIn(username: string, password: string): Promise<void>
  signOut(): Promise<void>
  boot(): Promise<void>
  attach(jobId: string): void
  setStep(s: Step): void
  select(s: Selection): void
  patch(fn: (c: PlantConfig) => void): void

  addChiller(): void
  addBoiler(): void
  addTower(): void
  addHeatPump(): void
  remove(kind: Selection['kind'], id: string): void
  duplicate(kind: Selection['kind'], id: string): void

  sizeToLoad(): void
  refreshAssets(): Promise<void>
  run(): Promise<void>
  cancel(): Promise<void>
  reset(): void

  setPlayHour(h: number): void
  setPlaying(p: boolean): void

  toast(kind: Toast['kind'], text: string): void
  dismiss(id: number): void
}


/** A `?step=` in the URL outranks the step a snapshot would choose. */
function requestedStep(): Step | null {
  const q = new URLSearchParams(location.search).get('step')
  return q === 'build' || q === 'results' || q === 'monitor' ? q : null
}

/** Turns stream events into store updates. Shared by run() and attach(). */
function makeHandler(get: () => State & Actions) {
  return (ev: StreamEvent) => {
    const s = get()
    const set = (patch: Partial<State>) => useStore.setState(patch)
    switch (ev.t) {
      case 'snapshot':
        set({
          job: ev.job, jobState: ev.job.state, progress: ev.job.progress,
          config: ev.config ?? s.config,
          phase: ev.job.phase, schematic: ev.schematic,
          readiness: ev.readiness, diagnostics: ev.diagnostics,
          log: ev.log, results: ev.results, qa: ev.qa,
          telemetry: ev.telemetry.map(foldTelemetry),
          // Attaching to a finished run lands on its results, the same place
          // the person who started it ended up — unless the link asked for a
          // particular step, which arrives before the snapshot does.
          step: requestedStep() ?? (ev.results ? 'results' : s.step),
          // Open playback on the hour that matters, not 1 January 00:00.
          playHour: ev.results?.summary.peakCoolingHour ?? s.playHour,
        })
        break
      case 'state':
        set({ jobState: ev.state, phase: ev.phase, progress: ev.progress })
        break
      case 'progress':
        set({ progress: ev.progress })
        break
      case 'log':
        set({ log: [...s.log.slice(-600), ev.line] })
        break
      case 'telemetry': {
        const next = [...s.telemetry, foldTelemetry(ev)]
        set({ telemetry: next.slice(-TELEMETRY_CAP) })
        break
      }
      case 'done':
        set({
          results: ev.results, qa: ev.qa, elapsed: ev.elapsed,
          jobState: 'complete', progress: 1, phase: 'Complete',
          step: 'results',
          playHour: ev.results.summary.peakCoolingHour,
        })
        get().toast('ok', `Simulation finished in ${ev.elapsed.toFixed(1)} s`)
        break
      case 'error':
        set({ jobState: 'failed', phase: 'Failed' })
        get().toast('error', ev.message.split('\n')[0].slice(0, 220))
        break
    }
      
  }
}

let stream: StreamHandle | null = null

export const useStore = create<State & Actions>((set, get) => ({
  step: 'build',
  config: defaultConfig(),
  selection: { kind: null },

  catalog: null, weather: [], loads: [], engine: null, booting: true,

  user: null, authChecked: false, signingIn: false, authError: null,
  defaultCredentials: false,

  job: null, jobState: null, progress: 0, phase: '',
  log: [], telemetry: [], schematic: null, results: null,
  qa: [], readiness: [], diagnostics: [],
  streamStatus: 'idle', elapsed: null,

  playHour: 0, playing: false,
  toasts: [],

  /* -- session --------------------------------------------------------- */

  async checkSession() {
    try {
      const { user, usingDefaultCredentials } = await api.session()
      set({ user, authChecked: true, defaultCredentials: usingDefaultCredentials })
      if (user) await get().boot()
      else set({ booting: false })
    } catch {
      // An unreachable session endpoint means no server at all; the login
      // screen reports that when the sign-in attempt fails.
      set({ authChecked: true, booting: false })
    }
  },

  async signIn(username, password) {
    set({ signingIn: true, authError: null })
    try {
      const { user } = await api.login(username, password)
      set({ user, signingIn: false, booting: true })
      await get().boot()
    } catch (e) {
      set({
        signingIn: false,
        authError: e instanceof Error ? e.message : String(e),
      })
    }
  },

  async signOut() {
    try {
      await api.logout()
    } catch {
      // Clearing locally is what matters; the cookie expires on its own.
    }
    get().reset()
    set({ user: null, catalog: null, weather: [], loads: [], engine: null })
  },

  /* -- boot ------------------------------------------------------------ */

  async boot() {
    try {
      const [health, catalog, weather, loads] = await Promise.all([
        api.health(), api.catalog(), api.weather(), api.loads(),
      ])
      set({
        engine: { found: health.engine.found, version: health.engine.version },
        catalog,
        weather: weather.items,
        loads: loads.items,
        booting: false,
      })
      // Preselect inputs the default plant can actually serve. Picking
      // "whichever sample sorts first" shipped a plant with 1,000 kW of
      // boilers against a 2,280 kW heating peak, and an undersized hot-water
      // loop does not degrade gracefully -- it diverges (see DEFAULT_LOAD).
      const cfg = get().config
      if (!cfg.load && loads.items.length) {
        const pick = loads.items.find((l) => l.id === DEFAULT_LOAD)
          ?? loads.items.find((l) => l.source === 'sample')
          ?? loads.items[0]
        get().patch((c) => { c.load = { asset_id: pick.id, label: pick.label } })
      }
      if (!get().config.weather && weather.items.length) {
        const pick = weather.items.find((w) => /chicago/i.test(w.label))
          ?? weather.items[0]
        get().patch((c) => { c.weather = { asset_id: pick.id, label: pick.label } })
      }
    } catch (e) {
      set({ booting: false })
      get().toast('error', e instanceof ApiError ? e.message : String(e))
    }
  },

  setStep: (step) => set({ step }),
  select: (selection) => set({ selection }),

  patch(fn) {
    const next = structuredClone(get().config)
    fn(next)
    set({ config: next })
  },

  addChiller() {
    get().patch((c) => { c.chillers.push(newChiller(c.chillers.length + 1)) })
    const c = get().config.chillers
    set({ selection: { kind: 'chiller', id: c[c.length - 1].id } })
  },
  addBoiler() {
    get().patch((c) => { c.boilers.push(newBoiler(c.boilers.length + 1)) })
    const b = get().config.boilers
    set({ selection: { kind: 'boiler', id: b[b.length - 1].id } })
  },
  addTower() {
    get().patch((c) => { c.towers.push(newTower(c.towers.length + 1)) })
    const t = get().config.towers
    set({ selection: { kind: 'tower', id: t[t.length - 1].id } })
  },
  addHeatPump() {
    get().patch((c) => { c.heat_pumps.push(newHeatPump(c.heat_pumps.length + 1)) })
    const h = get().config.heat_pumps
    set({ selection: { kind: 'heatpump', id: h[h.length - 1].id } })
  },

  remove(kind, id) {
    get().patch((c) => {
      if (kind === 'chiller') c.chillers = c.chillers.filter((x) => x.id !== id)
      if (kind === 'boiler') c.boilers = c.boilers.filter((x) => x.id !== id)
      if (kind === 'tower') c.towers = c.towers.filter((x) => x.id !== id)
      if (kind === 'heatpump') c.heat_pumps = c.heat_pumps.filter((x) => x.id !== id)
    })
    if (get().selection.id === id) set({ selection: { kind: null } })
  },

  duplicate(kind, id) {
    get().patch((c) => {
      const copy = <T extends { id: string; name: string }>(list: T[]) => {
        const src = list.find((x) => x.id === id)
        if (!src) return
        list.push({ ...structuredClone(src), id: uid(), name: `${src.name} copy` })
      }
      if (kind === 'chiller') copy(c.chillers)
      if (kind === 'boiler') copy(c.boilers)
      if (kind === 'tower') copy(c.towers)
      if (kind === 'heatpump') copy(c.heat_pumps)
    })
  },

  /**
   * Size the plant to the selected load profile, N+1 on the cooling side.
   *
   * Heating is sized to at least the peak, because an undersized hot-water
   * loop cannot be simulated: the load component draws a fixed wattage
   * whatever the water temperature, so a permanent deficit walks the loop
   * temperature down until EnergyPlus aborts.
   */
  sizeToLoad() {
    const { config, loads } = get()
    const load = loads.find((l) => l.id === config.load?.asset_id)
    if (!load) {
      get().toast('error', 'Choose a building load profile first.')
      return
    }

    get().patch((c) => {
      const chillers = c.chillers.filter((x) => x.enabled)
      if (chillers.length && load.peak_cooling_kw > 0) {
        // N+1: size the running machines to the peak, then the spare matches.
        const duty = Math.max(1, chillers.length - 1)
        const each = Math.ceil((load.peak_cooling_kw / duty) / 10) * 10
        for (const ch of c.chillers) ch.capacity_kw = each
      }
      const heaters = [
        ...c.boilers.filter((x) => x.enabled),
        ...c.heat_pumps.filter((x) => x.enabled),
      ]
      if (heaters.length && load.peak_heating_kw > 0) {
        const each = Math.ceil((load.peak_heating_kw * 1.1 / heaters.length) / 10) * 10
        for (const b of c.boilers) b.capacity_kw = each
        for (const h of c.heat_pumps) h.capacity_kw = each
      }
    })
    get().toast('ok', `Plant sized for ${load.label}.`)
  },

  async refreshAssets() {
    const [weather, loads] = await Promise.all([api.weather(), api.loads()])
    set({ weather: weather.items, loads: loads.items })
  },

  /* -- running --------------------------------------------------------- */

  async run() {
    stream?.close()
    set({
      log: [], telemetry: [], results: null, qa: [], diagnostics: [],
      progress: 0, phase: 'Submitting', jobState: 'queued', elapsed: null,
      streamStatus: 'idle', playHour: 0, playing: false,
    })
    let jobId: string
    try {
      const { job, readiness } = await api.createJob(get().config)
      jobId = job.id
      set({ job, readiness, jobState: job.state })
    } catch (e) {
      if (e instanceof ApiError && e.readiness) {
        set({ readiness: e.readiness, jobState: null, phase: '' })
        get().toast('error', e.message)
      } else {
        set({ jobState: null, phase: '' })
        get().toast('error', e instanceof Error ? e.message : String(e))
      }
      return
    }

    stream = openStream(
      jobId,
      makeHandler(get),
      (status) => set({ streamStatus: status }),
    )
  },

  attach(jobId: string) {
    stream?.close()
    // jobState stays null until the snapshot lands. Claiming 'queued' here
    // would make isRunning() true and put a Stop button on a run that may
    // already be finished.
    set({
      job: null, jobState: null, phase: 'Opening run…', progress: 0,
      log: [], telemetry: [], results: null, qa: [], readiness: [],
      diagnostics: [], elapsed: null,
    })
    stream = openStream(jobId, makeHandler(get), (status) => set({ streamStatus: status }))
  },

  async cancel() {
    const job = get().job
    if (!job) return
    try {
      await api.cancel(job.id)
      set({ jobState: 'cancelled', phase: 'Cancelled' })
      stream?.close()
    } catch (e) {
      get().toast('error', e instanceof Error ? e.message : String(e))
    }
  },

  reset() {
    stream?.close()
    const { weather, loads } = get()
    const cfg = defaultConfig()
    if (loads.length) {
      const p = loads.find((l) => l.source === 'sample') ?? loads[0]
      cfg.load = { asset_id: p.id, label: p.label }
    }
    if (weather.length) {
      cfg.weather = { asset_id: weather[0].id, label: weather[0].label }
    }
    set({
      config: cfg, selection: { kind: null }, step: 'build',
      job: null, jobState: null, progress: 0, phase: '', log: [], telemetry: [],
      results: null, qa: [], readiness: [], diagnostics: [], schematic: null,
      elapsed: null, playHour: 0, playing: false,
    })
  },

  setPlayHour: (playHour) => set({ playHour }),
  setPlaying: (playing) => set({ playing }),

  toast(kind, text) {
    const t: Toast = { id: ++toastId, kind, text }
    set({ toasts: [...get().toasts.slice(-3), t] })
    window.setTimeout(() => get().dismiss(t.id), kind === 'error' ? 9000 : 4500)
  },
  dismiss(id) {
    set({ toasts: get().toasts.filter((t) => t.id !== id) })
  },
}))

/* ---- derived helpers --------------------------------------------------- */

export const isRunning = (s: JobState | null) =>
  s === 'queued' || s === 'preparing' || s === 'running' || s === 'parsing'

export const totalCooling = (c: PlantConfig) =>
  c.chillers.filter((x) => x.enabled).reduce((a, x) => a + x.capacity_kw, 0)

export const totalHeating = (c: PlantConfig) =>
  c.boilers.filter((x) => x.enabled).reduce((a, x) => a + x.capacity_kw, 0) +
  c.heat_pumps.filter((x) => x.enabled).reduce((a, x) => a + x.capacity_kw, 0)
