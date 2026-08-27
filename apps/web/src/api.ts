/** Thin API client. Everything is same-origin, so no base URL juggling. */

import type {
  Catalog, JobSummary, LoadAsset, PlantConfig, ReadinessItem,
  Results, Schematic, StreamEvent, TelemetryFrame, WeatherAsset,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly readiness?: ReadinessItem[],
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw new ApiError(
      'Cannot reach the simulation server. Check that it is running.', 0,
    )
  }
  if (!res.ok) {
    let detail: unknown = await res.text()
    try { detail = JSON.parse(detail as string) } catch { /* plain text */ }
    const d = (detail as { detail?: unknown })?.detail ?? detail
    if (d && typeof d === 'object' && 'readiness' in d) {
      const o = d as { message?: string; readiness: ReadinessItem[] }
      throw new ApiError(o.message ?? 'Not ready to simulate.', res.status, o.readiness)
    }
    throw new ApiError(typeof d === 'string' ? d : res.statusText, res.status)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  session: () => request<{
    user: string | null
    authenticated: boolean
    usingDefaultCredentials: boolean
  }>('/api/auth/me'),

  login: (username: string, password: string) =>
    request<{ user: string }>('/api/auth/login', json({ username, password })),

  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),

  health: () => request<{
    ok: boolean
    engine: { found: boolean; path: string | null; version: string | null }
    maxConcurrentRuns: number
  }>('/api/health'),

  catalog: () => request<Catalog>('/api/catalog'),

  weather: () => request<{ items: WeatherAsset[] }>('/api/assets/weather'),
  loads: () => request<{ items: LoadAsset[] }>('/api/assets/loads'),

  loadSeries: (id: string, stride = 1) =>
    request<{ coolingKw: number[]; heatingKw: number[]; stride: number }>(
      `/api/assets/loads/${id}/series?stride=${stride}`,
    ),

  uploadWeather: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ item: WeatherAsset }>('/api/assets/weather',
      { method: 'POST', body: fd })
  },

  uploadLoad: (file: File, units: string) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('units', units)
    return request<{ item: LoadAsset; notes: string[] }>('/api/assets/loads',
      { method: 'POST', body: fd })
  },

  validate: (config: PlantConfig) =>
    request<{ readiness: ReadinessItem[]; blocking: boolean }>(
      '/api/validate', json(config)),

  createJob: (config: PlantConfig, dryRun = false) =>
    request<{ job: JobSummary; readiness: ReadinessItem[] }>(
      '/api/jobs', json({ config, dry_run: dryRun })),

  jobs: () => request<{ items: JobSummary[] }>('/api/jobs'),

  cancel: (id: string) =>
    request<{ job: JobSummary }>(`/api/jobs/${id}`, { method: 'DELETE' }),

  results: (id: string) => request<{
    results: Results; qa: ReadinessItem[]
    diagnostics: string[]; schematic: Schematic
  }>(`/api/jobs/${id}/results`),

  series: (id: string, keys: string[], freq: 'hourly' | 'daily' | 'monthly') =>
    request<{ frequency: string; length: number; series: Record<string, number[]> }>(
      `/api/jobs/${id}/series?keys=${keys.join(',')}&freq=${freq}`),

  artifactUrl: (id: string, name: string) => `/api/jobs/${id}/artifact/${name}`,
}

/* -------------------------------------------------------------------------
   live stream
   ------------------------------------------------------------------------- */

export interface StreamHandle { close(): void }

/**
 * Subscribe to a job's live events.
 *
 * Reconnects with backoff while the job could still be running, because a
 * dropped socket mid-simulation should not cost the user their run.
 */
export function openStream(
  jobId: string,
  onEvent: (e: StreamEvent) => void,
  onStatus?: (s: 'open' | 'closed' | 'retrying') => void,
): StreamHandle {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${location.host}/api/jobs/${jobId}/stream`

  let ws: WebSocket | null = null
  let closed = false
  let attempt = 0
  let timer: number | undefined

  const connect = () => {
    if (closed) return
    ws = new WebSocket(url)
    ws.onopen = () => { attempt = 0; onStatus?.('open') }
    ws.onmessage = (ev) => {
      let msg: StreamEvent
      try { msg = JSON.parse(ev.data as string) } catch { return }
      if (msg.t === 'ping') return
      onEvent(msg)
      if (msg.t === 'done' || msg.t === 'error') { closed = true }
    }
    ws.onclose = () => {
      if (closed) { onStatus?.('closed'); return }
      attempt += 1
      if (attempt > 6) { onStatus?.('closed'); return }
      onStatus?.('retrying')
      timer = window.setTimeout(connect, Math.min(500 * 2 ** attempt, 8000))
    }
    ws.onerror = () => ws?.close()
  }

  connect()
  return {
    close() {
      closed = true
      if (timer) window.clearTimeout(timer)
      ws?.close()
    },
  }
}

/** Aggregate a telemetry frame into the totals the mimic panel needs. */
export function foldTelemetry(frame: TelemetryFrame) {
  const v = frame.values
  const sumBy = (suffix: string, kind: string) =>
    Object.entries(v)
      .filter(([k]) => k.endsWith(`::${suffix}`) && k.startsWith(`${kind}:`))
      .reduce((a, [, n]) => a + (Number.isFinite(n) ? n : 0), 0)

  return {
    hour: frame.hour,
    month: frame.month,
    day: frame.day,
    oaDrybulb: v.oaDrybulb ?? 0,
    oaWetbulb: v.oaWetbulb ?? 0,
    chillerPowerW: sumBy('power', 'chiller'),
    coolingW: sumBy('cooling', 'chiller'),
    towerPowerW: sumBy('power', 'tower'),
    rejectW: sumBy('reject', 'tower'),
    pumpPowerW: sumBy('power', 'pump'),
    boilerHeatW: sumBy('heat', 'boiler'),
    heatPumpPowerW: sumBy('power', 'hp'),
    heatPumpHeatW: sumBy('heat', 'hp'),
    values: v,
  }
}

export type FoldedTelemetry = ReturnType<typeof foldTelemetry>
