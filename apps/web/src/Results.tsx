/** Step 2: what the run produced, and whether it complies. */

import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import {
  CarpetPlot, DurationCurve, Gauge, LineChart, ShareBar, StackedBars,
  compact, fmt, hourLabel, robustMax,
} from './charts'
import { Card, Empty, IconDownload, Kpi, Readout, Segmented } from './ui'
import { RunSummaryBadges } from './RunBar'
import { totalCooling, useStore } from './store'


type SeriesMap = Record<string, number[]>

/** Fetches hourly series once per job and memoises them. */
function useSeries(keys: string[]) {
  const jobId = useStore((s) => s.job?.id)
  const complete = useStore((s) => s.jobState === 'complete')
  const toast = useStore((s) => s.toast)
  const [data, setData] = useState<SeriesMap>({})
  const [loading, setLoading] = useState(false)
  const wanted = keys.join(',')

  useEffect(() => {
    if (!jobId || !complete) return
    let alive = true
    setLoading(true)
    api.series(jobId, wanted.split(','), 'hourly')
      .then((r) => { if (alive) setData(r.series) })
      .catch((e) => {
        if (alive) toast('error', e instanceof Error ? e.message : String(e))
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [jobId, complete, wanted, toast])

  return { data, loading }
}

export function ResultsView() {
  const { results, config, job } = useStore()
  type View = 'overview' | 'energy' | 'profile' | 'equipment'
  const [view, setView] = useState<View>(() => {
    // ?view= deep-links a specific tab, so a link can point at the chart
    // being discussed rather than the front page of the results.
    const q = new URLSearchParams(location.search).get('view')
    return (['overview', 'energy', 'profile', 'equipment'] as string[]).includes(q ?? '')
      ? (q as View) : 'overview'
  })

  if (!results) {
    return (
      <Card title="Results">
        <Empty title="No results yet">
          Run the plant and the annual outcome lands here — energy, cost,
          compliance, and the shape of every hour.
        </Empty>
      </Card>
    )
  }

  const s = results.summary
  const c = results.compliance

  return (
    <div className="stack" style={{ gap: 'var(--gap)' }}>
      <div className="kpis">
        <Kpi
          label="Cooling delivered" value={fmt(s.coolingDeliveredMwh)} unit="MWh"
          sub={`peak ${fmt(s.peakCoolingKw)} kW · ${fmt(s.peakCoolingTr)} TR`}
        />
        <Kpi
          label="System COP" value={s.systemCop.toFixed(2)}
          sub={`chillers alone ${s.chillerCop.toFixed(2)}`}
        />
        <Kpi
          label="Unmet hours" value={fmt(s.unmetCoolingHours)}
          sub={s.unmetCoolingHours > 0
            ? `worst ${s.worstChwSupplyC.toFixed(1)} °C vs ${s.chwSetpointC.toFixed(1)} °C setpoint`
            : 'chilled water held setpoint all year'}
        />
        <Kpi
          label="Plant electricity" value={fmt(s.plantElectricityMwh)} unit="MWh"
          sub={`peak demand ${fmt(s.peakPlantKw)} kW (hourly mean)`}
        />
        <Kpi
          label="Heating delivered" value={fmt(s.heatingDeliveredMwh)} unit="MWh"
          sub={s.fuelMwh > 0 ? `${fmt(s.fuelMwh)} MWh fuel` : 'no fuel use'}
        />
        <Kpi
          label={`Annual cost`} value={compact(results.cost.total)}
          unit={results.cost.currency}
          sub={`energy ${compact(results.cost.energy)} · demand ${compact(results.cost.demand)}`
            + (results.cost.fuel > 0 ? ` · fuel ${compact(results.cost.fuel)}` : '')}
        />
      </div>

      <Card
        title="Detail"
        aside={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
            <RunSummaryBadges />
            <Segmented
              value={view} onChange={setView}
              options={[
                { value: 'overview', label: 'Overview' },
                { value: 'energy', label: 'Energy' },
                { value: 'profile', label: 'Profile' },
                { value: 'equipment', label: 'Equipment' },
              ]}
            />
          </div>
        }
      >
        {view === 'overview' && <Overview />}
        {view === 'energy' && <EnergyView />}
        {view === 'profile' && <ProfileView />}
        {view === 'equipment' && <EquipmentView />}
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--gap)' }}>
        <Card title={`${c.tier === 'ECSBC' ? 'ECSBC' : 'Super ECSBC'} compliance`}>
          <Gauge
            value={c.value} limit={c.limit}
            label={c.metric} unit={c.unit}
          />
          <p className="hint" style={{ marginTop: 12 }}>{c.note}</p>
          {c.bestTierMet && (
            <p className="hint" style={{ marginTop: 6 }}>
              Best tier met: <b>{c.bestTierMet === 'SUPER_ECSBC' ? 'Super ECSBC' : 'ECSBC'}</b>.
            </p>
          )}
        </Card>

        <Card
          title="Artifacts"
          aside={job && <span className="tag">run {job.id}</span>}
        >
          <p className="hint" style={{ marginBottom: 10 }}>
            Everything the run produced, exactly as EnergyPlus wrote it.
          </p>
          <div style={{ display: 'grid', gap: 6 }}>
            {[
              ['model', 'Generated model (epJSON)'],
              ['errors', 'Error file (eplusout.err)'],
              ['table', 'Tabular report (HTML)'],
              ['variables', 'Hourly variables (CSV)'],
              ['sql', 'Results database (SQLite)'],
              ['config', 'Plant configuration (JSON)'],
            ].map(([key, label]) => (
              <a
                key={key} className="btn btn-sm"
                style={{ justifyContent: 'flex-start' }}
                href={job ? api.artifactUrl(job.id, key) : '#'}
                download
              >
                <IconDownload />{label}
              </a>
            ))}
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            Installed cooling capacity {fmt(totalCooling(config))} kW against a
            {' '}{fmt(s.peakCoolingKw)} kW peak.
          </p>
        </Card>
      </div>
    </div>
  )
}

/* ---- overview ---------------------------------------------------------- */

function Overview() {
  const { results, diagnostics } = useStore()
  if (!results) return null
  const s = results.summary
  const unmetCooling = s.coolingRequestedMwh - s.coolingDeliveredMwh
  const unmetHeating = s.heatingRequestedMwh - s.heatingDeliveredMwh

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22 }}>
      <div>
        <h3 className="tag" style={{ marginBottom: 8 }}>Where the electricity went</h3>
        <ShareBar
          unit="MWh"
          parts={[
            { key: 'chillers', label: 'Chillers', value: results.endUse.chillers },
            { key: 'pumps', label: 'Pumps', value: results.endUse.pumps },
            { key: 'towerFans', label: 'Tower fans', value: results.endUse.towerFans },
            { key: 'heatPumps', label: 'Heat pumps', value: results.endUse.heatPumps },
            { key: 'boilerAncillary', label: 'Boiler ancillary', value: results.endUse.boilerAncillary },
          ]}
        />
      </div>

      <div>
        <h3 className="tag" style={{ marginBottom: 8 }}>Balance</h3>
        <Readout label="Cooling asked for" value={fmt(s.coolingRequestedMwh)} unit="MWh" />
        <Readout label="Cooling delivered" value={fmt(s.coolingDeliveredMwh)} unit="MWh" />
        <Readout
          label={unmetCooling > 0 ? 'Unmet cooling' : 'Extra (pump heat)'}
          value={fmt(Math.abs(unmetCooling), 1)} unit="MWh"
        />
        <Readout label="Heating asked for" value={fmt(s.heatingRequestedMwh)} unit="MWh" />
        <Readout label="Heating delivered" value={fmt(s.heatingDeliveredMwh)} unit="MWh" />
        {unmetHeating > 0 && (
          <Readout label="Unmet heating" value={fmt(unmetHeating, 1)} unit="MWh" />
        )}
        {s.boilerEfficiency !== null && (
          <Readout
            label="Boiler seasonal efficiency"
            value={(s.boilerEfficiency * 100).toFixed(1)} unit="%"
          />
        )}
        <Readout
          label="Unmet cooling hours" value={fmt(s.unmetCoolingHours)}
          unit={`of ${fmt(s.coolingHours)}`}
        />
        <Readout label="Peak cooling hour" value={hourLabel(s.peakCoolingHour)} />
        <Readout label="Peak demand hour" value={hourLabel(s.peakPlantHour)} />
      </div>

      {diagnostics.length > 0 && (
        <div style={{ gridColumn: '1 / -1' }}>
          <h3 className="tag" style={{ marginBottom: 6 }}>Model notes</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {diagnostics.map((d, i) => (
              <li key={i} className="hint" style={{ marginBottom: 3 }}>{d}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/* ---- energy ------------------------------------------------------------ */

function EnergyView() {
  const results = useStore((s) => s.results)
  if (!results) return null
  const m = results.monthly

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div>
        <h3 className="tag" style={{ marginBottom: 8 }}>
          Delivered energy by month
        </h3>
        <StackedBars
          unit=" MWh"
          labels={m.map((x) => x.month)}
          series={[
            { key: 'coolingDeliveredKw', label: 'Cooling', values: m.map((x) => x.coolingMwh) },
            { key: 'boilerHeatKw', label: 'Heating', values: m.map((x) => x.heatingMwh) },
          ]}
        />
      </div>

      <div>
        <h3 className="tag" style={{ marginBottom: 8 }}>
          Plant electricity and peak demand by month
        </h3>
        <StackedBars
          unit=" MWh"
          labels={m.map((x) => x.month)}
          series={[
            { key: 'plantPowerKw', label: 'Electricity (MWh)', values: m.map((x) => x.electricityMwh) },
          ]}
        />
        <table className="table" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Month</th>
              <th className="n">Cooling MWh</th>
              <th className="n">Heating MWh</th>
              <th className="n">Electricity MWh</th>
              <th className="n">Peak kW</th>
            </tr>
          </thead>
          <tbody>
            {m.map((r) => (
              <tr key={r.month}>
                <td>{r.month}</td>
                <td className="n">{fmt(r.coolingMwh, 1)}</td>
                <td className="n">{fmt(r.heatingMwh, 1)}</td>
                <td className="n">{fmt(r.electricityMwh, 1)}</td>
                <td className="n">{fmt(r.peakKw)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ---- profile ----------------------------------------------------------- */

function ProfileView() {
  const { config, results } = useStore()
  const { data, loading } = useSeries([
    'coolingLoadKw', 'coolingDeliveredKw', 'plantPowerKw',
    'oaDrybulbC', 'chwSupplyC', 'cwSupplyC', 'hhwSupplyC', 'heatingLoadKw',
  ])
  const [zoom, setZoom] = useState<'year' | 'peak'>('year')

  const cooling = data.coolingLoadKw ?? []
  const power = data.plantPowerKw ?? []
  if (loading || !cooling.length) {
    return <p className="hint">Loading the hourly series…</p>
  }

  const peakHour = results?.summary.peakCoolingHour ?? 0
  const from = Math.max(0, peakHour - 84)
  const to = Math.min(cooling.length, peakHour + 84)
  const slice = (a: number[]) => zoom === 'peak' ? a.slice(from, to) : a
  const xLabel = (i: number) =>
    hourLabel(zoom === 'peak' ? from + i : i).split(' · ')[zoom === 'peak' ? 1 : 0]
      ? zoom === 'peak' ? hourLabel(from + i) : hourLabel(i).split(' · ')[0]
      : String(i)

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <h3 className="tag" style={{ flex: 1 }}>
          {zoom === 'year' ? 'The whole year, hour by hour' : 'The week around peak cooling'}
        </h3>
        <Segmented
          value={zoom} onChange={setZoom}
          options={[
            { value: 'year', label: 'Full year' },
            { value: 'peak', label: 'Peak week' },
          ]}
        />
      </div>

      <LineChart
        height={210} unit=" kW"
        xLabel={xLabel}
        series={[
          { key: 'coolingLoadKw', label: 'Cooling load', values: slice(cooling), fill: true },
          { key: 'plantPowerKw', label: 'Plant electricity', values: slice(power) },
          ...(data.heatingLoadKw?.some((v) => v > 0)
            ? [{ key: 'heatingLoadKw', label: 'Heating load', values: slice(data.heatingLoadKw) }]
            : []),
        ]}
      />

      <div>
        <h3 className="tag" style={{ marginBottom: 8 }}>
          Loop temperatures and outdoor air
        </h3>
        <p className="hint" style={{ marginBottom: 6 }}>
          Scaled to the bulk of the year. Brief excursions above the axis are
          the unmet hours counted above.
        </p>
        <LineChart
          height={170} unit=" °C"
          xLabel={xLabel}
          yMax={robustMax([
            slice(data.oaDrybulbC ?? []), slice(data.cwSupplyC ?? []),
            slice(data.chwSupplyC ?? []), slice(data.hhwSupplyC ?? []),
          ])}
          series={[
            { key: 'oaDrybulbC', label: 'Outdoor air', values: slice(data.oaDrybulbC ?? []) },
            { key: 'cwSupplyC', label: 'Condenser water', values: slice(data.cwSupplyC ?? []) },
            { key: 'chwSupplyC', label: 'Chilled water', values: slice(data.chwSupplyC ?? []) },
            ...(data.hhwSupplyC?.some((v) => v > 0)
              ? [{ key: 'hhwSupplyC', label: 'Hot water', values: slice(data.hhwSupplyC) }]
              : []),
          ]}
        />
      </div>

      <div>
        <h3 className="tag" style={{ marginBottom: 8 }}>
          Cooling load, every hour of the year
        </h3>
        <CarpetPlot values={cooling} unit="kW" hue="var(--chw)" />
      </div>

      <div>
        <h3 className="tag" style={{ marginBottom: 8 }}>
          Load duration — how many hours the plant sees each load
        </h3>
        <DurationCurve
          values={cooling}
          capacities={[{
            label: `Installed capacity ${fmt(totalCooling(config))} kW`,
            value: totalCooling(config),
          }]}
        />
      </div>
    </div>
  )
}

/* ---- equipment --------------------------------------------------------- */

function EquipmentView() {
  const results = useStore((s) => s.results)
  if (!results) return null

  const groups = useMemo(() => {
    const by: Record<string, typeof results.equipment> = {}
    for (const e of results.equipment) (by[e.kind] ??= []).push(e)
    return by
  }, [results])

  const titles: Record<string, string> = {
    chiller: 'Chillers', boiler: 'Boilers', tower: 'Cooling towers',
    pump: 'Pumps', heatpump: 'Heat pumps',
  }

  return (
    <div className="stack" style={{ gap: 18 }}>
      {Object.entries(groups).map(([kind, rows]) => (
        <div key={kind}>
          <h3 className="tag" style={{ marginBottom: 6 }}>{titles[kind] ?? kind}</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th className="n">Input MWh</th>
                {rows[0].deliveredMwh !== undefined && <th className="n">Output MWh</th>}
                {rows[0].deliveredMwh !== undefined && <th className="n">Efficiency</th>}
                <th className="n">Share of group</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const total = rows.reduce((a, x) => a + x.consumedMwh, 0)
                return (
                  <tr key={r.name}>
                    <td>{r.name}</td>
                    <td className="n">{fmt(r.consumedMwh, 1)}</td>
                    {r.deliveredMwh !== undefined && (
                      <td className="n">{fmt(r.deliveredMwh, 1)}</td>
                    )}
                    {r.deliveredMwh !== undefined && (
                      <td className="n">
                        {r.efficiency == null ? '—'
                          : kind === 'boiler'
                            ? `${(r.efficiency * 100).toFixed(1)}%`
                            : `COP ${r.efficiency.toFixed(2)}`}
                      </td>
                    )}
                    <td className="n">
                      {total > 0 ? `${((r.consumedMwh / total) * 100).toFixed(1)}%` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}
      <p className="hint">
        A machine showing zero input never ran. With lead–lag sequencing that is
        expected for the last unit in the order; switch the chiller sequencing to
        uniform part load if you want the duty shared.
      </p>
    </div>
  )
}
