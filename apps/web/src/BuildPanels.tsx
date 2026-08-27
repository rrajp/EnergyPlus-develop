/** Step 1 side panel: what is in the plant, and the form for whatever is selected. */

import { useMemo, useState } from 'react'
import { api } from './api'
import {
  Card, Empty, Field, IconCopy, IconPlus, IconTrash, IconUpload,
  NumberInput, Readout, Segmented, Select, Switch, TextInput,
} from './ui'
import { totalCooling, totalHeating, useStore } from './store'
import type { Selection } from './store'
import type {
  Boiler, CatalogChiller, Chiller, CoolingTower, HeatPump, PlantConfig,
} from './types'

const TR = 3.51685

/* ==========================================================================
   plant contents
   ========================================================================== */

const LOOP_OF: Record<string, string> = {
  chiller: 'var(--chw)', tower: 'var(--cw)',
  boiler: 'var(--hhw)', heatpump: 'var(--hhw)',
}

export function PlantContents() {
  const {
    config, selection, select, addChiller, addBoiler, addTower, addHeatPump,
    sizeToLoad, loads,
  } = useStore()
  const load = loads.find((l) => l.id === config.load?.asset_id)
  const coolShort = load ? totalCooling(config) < load.peak_cooling_kw : false
  const heatShort = load ? totalHeating(config) < load.peak_heating_kw : false

  const rows: {
    kind: 'chiller' | 'boiler' | 'tower' | 'heatpump'
    id: string; name: string; meta: string; enabled: boolean
  }[] = [
    ...config.chillers.map((c) => ({
      kind: 'chiller' as const, id: c.id, name: c.name, enabled: c.enabled,
      meta: `${Math.round(c.capacity_kw)} kW · ${Math.round(c.capacity_kw / TR)} TR · COP ${c.reference_cop}`,
    })),
    ...config.towers.map((t) => ({
      kind: 'tower' as const, id: t.id, name: t.name, enabled: t.enabled,
      meta: `${t.model.replace('CoolingTower:', '')} · ${t.design_approach_c} K approach`,
    })),
    ...config.heat_pumps.map((h) => ({
      kind: 'heatpump' as const, id: h.id, name: h.name, enabled: h.enabled,
      meta: `${Math.round(h.capacity_kw)} kW · COP ${h.reference_cop}`,
    })),
    ...config.boilers.map((b) => ({
      kind: 'boiler' as const, id: b.id, name: b.name, enabled: b.enabled,
      meta: `${Math.round(b.capacity_kw)} kW · ${b.fuel} · η ${b.efficiency}`,
    })),
  ]

  return (
    <Card
      title="Plant"
      aside={<span className="tag">{rows.length} items</span>}
      flush
    >
      <div style={{ padding: 12, paddingBottom: 10 }}>
        <div className="palette">
          <button className="palette-btn" onClick={addChiller}>
            <IconPlus />Chiller
          </button>
          <button className="palette-btn" onClick={addTower}>
            <IconPlus />Tower
          </button>
          <button className="palette-btn" onClick={addBoiler}>
            <IconPlus />Boiler
          </button>
          <button className="palette-btn" onClick={addHeatPump}>
            <IconPlus />Heat pump
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <Empty title="Empty plant">
          Add a chiller to serve cooling, or a boiler for heating. Everything
          else — loops, pumps, headers — is wired for you.
        </Empty>
      ) : (
        <div className="eqlist">
          {rows.map((r) => (
            <button
              key={r.id} className="eqrow"
              aria-selected={selection.id === r.id}
              onClick={() => select({ kind: r.kind, id: r.id } as Selection)}
            >
              <span className="eqrow-bar" style={{ background: LOOP_OF[r.kind] }} />
              <span style={{ minWidth: 0 }}>
                <div className="eqrow-name" style={{
                  opacity: r.enabled ? 1 : 0.5,
                  textDecoration: r.enabled ? undefined : 'line-through',
                }}>{r.name}</div>
                <div className="eqrow-meta">{r.meta}</div>
              </span>
            </button>
          ))}
        </div>
      )}

      <div style={{ padding: 12, borderTop: '1px solid var(--line)' }}>
        <Readout
          label="Installed cooling"
          value={`${Math.round(totalCooling(config)).toLocaleString()}`}
          unit={`kW · ${Math.round(totalCooling(config) / TR).toLocaleString()} TR`}
        />
        <Readout
          label="Installed heating"
          value={`${Math.round(totalHeating(config)).toLocaleString()}`}
          unit="kW"
        />
        {load && (coolShort || heatShort) && (
          <>
            <p className="hint" style={{ marginTop: 8 }}>
              {heatShort
                ? `Heating is ${Math.round(load.peak_heating_kw - totalHeating(config)).toLocaleString()} kW short of the ${Math.round(load.peak_heating_kw).toLocaleString()} kW peak. A hot-water loop that can never meet its load will not run.`
                : `Cooling is ${Math.round(load.peak_cooling_kw - totalCooling(config)).toLocaleString()} kW short of the ${Math.round(load.peak_cooling_kw).toLocaleString()} kW peak.`}
            </p>
            <button
              className="btn btn-sm" style={{ marginTop: 8, width: '100%' }}
              onClick={sizeToLoad}
            >
              Size plant to load
            </button>
          </>
        )}
      </div>
    </Card>
  )
}

/* ==========================================================================
   inspector
   ========================================================================== */

export function Inspector() {
  const { config, selection, select, patch, remove, duplicate } = useStore()

  if (selection.kind === null) return <SiteCard />

  const del = () => selection.id && remove(selection.kind, selection.id)
  const dup = () => selection.id && duplicate(selection.kind, selection.id)

  const header = (
    <div style={{ display: 'flex', gap: 5, marginLeft: 'auto' }}>
      <button className="btn btn-ghost btn-sm" onClick={dup} title="Duplicate">
        <IconCopy />
      </button>
      <button className="btn btn-ghost btn-sm btn-danger" onClick={del} title="Remove">
        <IconTrash />
      </button>
      <button className="btn btn-ghost btn-sm" onClick={() => select({ kind: null })}>
        Done
      </button>
    </div>
  )

  if (selection.kind === 'chiller') {
    const c = config.chillers.find((x) => x.id === selection.id)
    if (!c) return <SiteCard />
    return (
      <Card title="Chiller" aside={header}>
        <ChillerForm
          value={c}
          onChange={(fn) => patch((cfg) => {
            const t = cfg.chillers.find((x) => x.id === c.id)
            if (t) fn(t)
          })}
        />
      </Card>
    )
  }

  if (selection.kind === 'tower') {
    const t = config.towers.find((x) => x.id === selection.id)
    if (!t) return <SiteCard />
    return (
      <Card title="Cooling tower" aside={header}>
        <TowerForm
          value={t}
          onChange={(fn) => patch((cfg) => {
            const x = cfg.towers.find((y) => y.id === t.id)
            if (x) fn(x)
          })}
        />
      </Card>
    )
  }

  if (selection.kind === 'boiler') {
    const b = config.boilers.find((x) => x.id === selection.id)
    if (!b) return <SiteCard />
    return (
      <Card title="Boiler" aside={header}>
        <BoilerForm
          value={b}
          onChange={(fn) => patch((cfg) => {
            const x = cfg.boilers.find((y) => y.id === b.id)
            if (x) fn(x)
          })}
        />
      </Card>
    )
  }

  if (selection.kind === 'heatpump') {
    const h = config.heat_pumps.find((x) => x.id === selection.id)
    if (!h) return <SiteCard />
    return (
      <Card title="Heat pump" aside={header}>
        <HeatPumpForm
          value={h}
          onChange={(fn) => patch((cfg) => {
            const x = cfg.heat_pumps.find((y) => y.id === h.id)
            if (x) fn(x)
          })}
        />
      </Card>
    )
  }

  return <SiteCard />
}

/* ---- chiller ----------------------------------------------------------- */

function ChillerForm({
  value: c, onChange,
}: { value: Chiller; onChange: (fn: (c: Chiller) => void) => void }) {
  const catalog = useStore((s) => s.catalog)
  const [search, setSearch] = useState('')

  const pool = useMemo(() => {
    if (!catalog) return []
    const list = c.condenser === 'WaterCooled'
      ? catalog.chillers.waterCooled
      : catalog.chillers.airCooled
    const q = search.trim().toLowerCase()
    const filtered = q
      ? list.filter((e) => e.label.toLowerCase().includes(q))
      : list
    // Nearest capacity first: that is what an engineer is actually choosing on.
    return [...filtered]
      .sort((a, b) => Math.abs(a.capacityKw - c.capacity_kw) -
        Math.abs(b.capacityKw - c.capacity_kw))
      .slice(0, 60)
  }, [catalog, c.condenser, c.capacity_kw, search])

  const applyCatalog = (e: CatalogChiller | null) => {
    onChange((x) => {
      x.catalog_key = e?.key ?? null
      if (e) {
        x.capacity_kw = Math.round(e.capacityKw * 10) / 10
        x.reference_cop = e.referenceCop
        x.ref_chw_leaving_c = e.refChwLeavingC
        x.ref_cond_entering_c = e.refCondEnteringC
        x.model = e.modelObject
        x.min_part_load_ratio = e.minPlr
        x.max_part_load_ratio = e.maxPlr
        x.optimum_part_load_ratio = e.optimumPlr
      }
    })
  }

  return (
    <div className="stack">
      <Field label="Name">
        <TextInput value={c.name} onChange={(v) => onChange((x) => { x.name = v })} />
      </Field>

      <Field label="Condenser">
        <Segmented
          value={c.condenser}
          onChange={(v) => onChange((x) => { x.condenser = v; x.catalog_key = null })}
          options={[
            { value: 'WaterCooled', label: 'Water-cooled' },
            { value: 'AirCooled', label: 'Air-cooled' },
          ]}
        />
      </Field>

      <div className="row">
        <Field label="Capacity" unit="kW">
          <NumberInput
            value={c.capacity_kw} min={1} step={10}
            onChange={(v) => onChange((x) => { x.capacity_kw = v })}
          />
        </Field>
        <Field label="Capacity" unit="TR">
          <NumberInput
            value={Math.round(c.capacity_kw / TR)} min={1} step={5}
            onChange={(v) => onChange((x) => { x.capacity_kw = v * TR })}
          />
        </Field>
      </div>

      <Field
        label="Reference machine"
        hint={c.catalog_key
          ? 'Performance curves come from this machine.'
          : 'No catalog model chosen — curves are taken from the nearest reference machine of the same size and condenser type.'}
      >
        <input
          className="input" placeholder="Search 271 reference chillers…"
          value={search} onChange={(e) => setSearch(e.target.value)}
          style={{ marginBottom: 6 }}
        />
        <select
          className="select" value={c.catalog_key ?? ''}
          onChange={(e) => applyCatalog(
            pool.find((p) => p.key === e.target.value) ?? null)}
        >
          <option value="">Custom — nearest reference curves</option>
          {pool.map((e) => (
            <option key={e.key} value={e.key}>
              {e.label} — {Math.round(e.capacityKw)} kW / COP {e.referenceCop}
            </option>
          ))}
        </select>
      </Field>

      <div className="row">
        <Field label="Reference COP">
          <NumberInput
            value={c.reference_cop} min={1} max={15} step={0.01}
            onChange={(v) => onChange((x) => { x.reference_cop = v })}
          />
        </Field>
        <Field label="Leaving CHW" unit="°C">
          <NumberInput
            value={c.ref_chw_leaving_c} step={0.1}
            onChange={(v) => onChange((x) => { x.ref_chw_leaving_c = v })}
          />
        </Field>
      </div>

      <div className="row">
        <Field label="Entering cond." unit="°C">
          <NumberInput
            value={c.ref_cond_entering_c} step={0.1}
            onChange={(v) => onChange((x) => { x.ref_cond_entering_c = v })}
          />
        </Field>
        <Field label="Min part load" unit="ratio">
          <NumberInput
            value={c.min_part_load_ratio} min={0} max={1} step={0.01}
            onChange={(v) => onChange((x) => { x.min_part_load_ratio = v })}
          />
        </Field>
      </div>

      <Switch
        checked={c.enabled} label="Include in the plant"
        onChange={(v) => onChange((x) => { x.enabled = v })}
      />
    </div>
  )
}

/* ---- tower ------------------------------------------------------------- */

function TowerForm({
  value: t, onChange,
}: { value: CoolingTower; onChange: (fn: (t: CoolingTower) => void) => void }) {
  return (
    <div className="stack">
      <Field label="Name">
        <TextInput value={t.name} onChange={(v) => onChange((x) => { x.name = v })} />
      </Field>

      <Field
        label="Fan control"
        hint={t.model === 'CoolingTower:VariableSpeed'
          ? 'Variable speed follows the fan law, so part-load fan power drops steeply. Its correlation is only valid down to −1 °C wet-bulb; colder hours are clamped and reported as warnings.'
          : 'The UA/NTU model has no wet-bulb correlation limits, so it stays quiet in cold weather.'}
      >
        <Select
          value={t.model}
          onChange={(v) => onChange((x) => { x.model = v })}
          options={[
            { value: 'CoolingTower:VariableSpeed', label: 'Variable speed' },
            { value: 'CoolingTower:TwoSpeed', label: 'Two speed' },
            { value: 'CoolingTower:SingleSpeed', label: 'Single speed' },
          ]}
        />
      </Field>

      <div className="row">
        <Field label="Approach" unit="K">
          <NumberInput
            value={t.design_approach_c} min={0.5} max={15} step={0.1}
            onChange={(v) => onChange((x) => { x.design_approach_c = v })}
          />
        </Field>
        <Field label="Range" unit="K">
          <NumberInput
            value={t.design_range_c} min={1} max={20} step={0.1}
            onChange={(v) => onChange((x) => { x.design_range_c = v })}
          />
        </Field>
      </div>

      <Field label="Design inlet wet-bulb" unit="°C">
        <NumberInput
          value={t.design_inlet_wetbulb_c} step={0.1}
          onChange={(v) => onChange((x) => { x.design_inlet_wetbulb_c = v })}
        />
      </Field>

      <Switch
        checked={t.autosize} label="Let EnergyPlus size the tower"
        onChange={(v) => onChange((x) => { x.autosize = v })}
      />
      {!t.autosize && (
        <div className="row">
          <Field label="Water flow" unit="m³/s">
            <NumberInput
              value={t.design_water_flow_m3s ?? 0} step={0.001}
              onChange={(v) => onChange((x) => { x.design_water_flow_m3s = v })}
            />
          </Field>
          <Field label="Fan power" unit="kW">
            <NumberInput
              value={t.fan_power_kw ?? 0} step={1}
              onChange={(v) => onChange((x) => { x.fan_power_kw = v })}
            />
          </Field>
        </div>
      )}

      <Switch
        checked={t.enabled} label="Include in the plant"
        onChange={(v) => onChange((x) => { x.enabled = v })}
      />
    </div>
  )
}

/* ---- boiler ------------------------------------------------------------ */

function BoilerForm({
  value: b, onChange,
}: { value: Boiler; onChange: (fn: (b: Boiler) => void) => void }) {
  const catalog = useStore((s) => s.catalog)
  const curves = catalog?.boilerCurves ?? []
  const active = curves.find((c) => c.key === b.catalog_key)

  return (
    <div className="stack">
      <Field label="Name">
        <TextInput value={b.name} onChange={(v) => onChange((x) => { x.name = v })} />
      </Field>

      <div className="row">
        <Field label="Capacity" unit="kW">
          <NumberInput
            value={b.capacity_kw} min={1} step={10}
            onChange={(v) => onChange((x) => { x.capacity_kw = v })}
          />
        </Field>
        <Field label="Fuel">
          <Select
            value={b.fuel}
            onChange={(v) => onChange((x) => { x.fuel = v })}
            options={[
              { value: 'NaturalGas', label: 'Natural gas' },
              { value: 'Propane', label: 'Propane' },
              { value: 'FuelOilNo1', label: 'Fuel oil no. 1' },
              { value: 'FuelOilNo2', label: 'Fuel oil no. 2' },
              { value: 'Electricity', label: 'Electricity' },
            ]}
          />
        </Field>
      </div>

      <Field
        label="Efficiency curve"
        hint={active
          ? `${active.note}. Nominal efficiency ${active.nominalEfficiency}, evaluated on ${active.efficiencyBasis === 'EnteringBoiler' ? 'entering' : 'leaving'} water.`
          : 'A flat efficiency, with no part-load or return-temperature effect.'}
      >
        <Select
          value={b.catalog_key ?? ''}
          onChange={(v) => onChange((x) => {
            x.catalog_key = v || null
            const c = curves.find((k) => k.key === v)
            if (c) {
              x.efficiency = c.nominalEfficiency
              x.efficiency_basis = c.efficiencyBasis
            }
          })}
          options={[
            { value: '', label: 'Constant efficiency (no curve)' },
            ...curves.map((c) => ({ value: c.key, label: c.note || c.label })),
          ]}
        />
      </Field>

      <div className="row">
        <Field label="Thermal efficiency">
          <NumberInput
            value={b.efficiency} min={0.2} max={1.2} step={0.01}
            disabled={!!b.catalog_key}
            onChange={(v) => onChange((x) => { x.efficiency = v })}
          />
        </Field>
        <Field label="Max outlet" unit="°C">
          <NumberInput
            value={b.max_outlet_c} step={1}
            onChange={(v) => onChange((x) => { x.max_outlet_c = v })}
          />
        </Field>
      </div>

      <Switch
        checked={b.enabled} label="Include in the plant"
        onChange={(v) => onChange((x) => { x.enabled = v })}
      />
    </div>
  )
}

/* ---- heat pump --------------------------------------------------------- */

function HeatPumpForm({
  value: h, onChange,
}: { value: HeatPump; onChange: (fn: (h: HeatPump) => void) => void }) {
  return (
    <div className="stack">
      <Field label="Name">
        <TextInput value={h.name} onChange={(v) => onChange((x) => { x.name = v })} />
      </Field>

      <Field
        label="Heat source"
        hint="Air-source performance falls away as the outdoor air cools; below the
              cut-off the boilers carry the load."
      >
        <Segmented
          value={h.source}
          onChange={(v) => onChange((x) => { x.source = v })}
          options={[
            { value: 'AirSource', label: 'Air source' },
            { value: 'WaterSource', label: 'Water source' },
          ]}
        />
      </Field>

      <div className="row">
        <Field label="Capacity" unit="kW">
          <NumberInput
            value={h.capacity_kw} min={1} step={10}
            onChange={(v) => onChange((x) => { x.capacity_kw = v })}
          />
        </Field>
        <Field label="Reference COP">
          <NumberInput
            value={h.reference_cop} min={1} max={8} step={0.05}
            onChange={(v) => onChange((x) => { x.reference_cop = v })}
          />
        </Field>
      </div>

      <Field label="Minimum source temperature" unit="°C">
        <NumberInput
          value={h.min_source_temp_c} step={0.5}
          onChange={(v) => onChange((x) => { x.min_source_temp_c = v })}
        />
      </Field>

      <p className="hint">
        Heat pumps use representative air-to-water curves rather than a
        manufacturer catalog model. Treat the output as indicative.
      </p>

      <Switch
        checked={h.enabled} label="Include in the plant"
        onChange={(v) => onChange((x) => { x.enabled = v })}
      />
    </div>
  )
}

/* ==========================================================================
   site: weather, load, loops, tariff
   ========================================================================== */

function SiteCard() {
  const [tab, setTab] = useState<'inputs' | 'loops' | 'cost'>('inputs')
  return (
    <Card
      title="Setup"
      aside={
        <Segmented
          value={tab} onChange={setTab}
          options={[
            { value: 'inputs', label: 'Inputs' },
            { value: 'loops', label: 'Loops' },
            { value: 'cost', label: 'Cost' },
          ]}
        />
      }
      style={{ minHeight: 0 }}
    >
      {tab === 'inputs' && <InputsTab />}
      {tab === 'loops' && <LoopsTab />}
      {tab === 'cost' && <CostTab />}
    </Card>
  )
}

function InputsTab() {
  const { config, weather, loads, patch, refreshAssets, toast } = useStore()
  const [busy, setBusy] = useState<'epw' | 'csv' | null>(null)
  const load = loads.find((l) => l.id === config.load?.asset_id)

  const upload = async (kind: 'epw' | 'csv', file: File) => {
    setBusy(kind)
    try {
      if (kind === 'epw') {
        const { item } = await api.uploadWeather(file)
        await refreshAssets()
        patch((c) => { c.weather = { asset_id: item.id, label: item.label } })
        toast('ok', `Weather file added: ${item.label}`)
      } else {
        const { item, notes } = await api.uploadLoad(file, 'kW')
        await refreshAssets()
        patch((c) => { c.load = { asset_id: item.id, label: item.label } })
        toast('ok', notes.length ? notes.join(' ') : `Load profile added: ${item.label}`)
      }
    } catch (e) {
      toast('error', e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="stack">
      <Field label="Weather" hint="An EPW drives outdoor conditions for all 8,760 hours.">
        <Select
          value={config.weather?.asset_id ?? ''}
          onChange={(v) => {
            const w = weather.find((x) => x.id === v)
            patch((c) => { c.weather = w ? { asset_id: w.id, label: w.label } : null })
          }}
          options={[
            { value: '', label: 'Select a location…' },
            ...weather.map((w) => ({
              value: w.id,
              label: `${w.label}${w.source === 'upload' ? ' (uploaded)' : ''}`,
            })),
          ]}
        />
      </Field>
      <UploadButton
        label={busy === 'epw' ? 'Uploading…' : 'Upload an EPW'}
        accept=".epw" disabled={busy !== null}
        onFile={(f) => upload('epw', f)}
      />

      <div className="divider" />

      <Field
        label="Building load"
        hint="An 8,760-hour CSV of cooling and heating demand. Uploads are matched on column names."
      >
        <Select
          value={config.load?.asset_id ?? ''}
          onChange={(v) => {
            const l = loads.find((x) => x.id === v)
            patch((c) => { c.load = l ? { asset_id: l.id, label: l.label } : null })
          }}
          options={[
            { value: '', label: 'Select a load profile…' },
            ...loads.map((l) => ({
              value: l.id,
              label: `${l.label}${l.source === 'upload' ? ' (uploaded)' : ''}`,
            })),
          ]}
        />
      </Field>
      <UploadButton
        label={busy === 'csv' ? 'Uploading…' : 'Upload a load CSV'}
        accept=".csv,text/csv" disabled={busy !== null}
        onFile={(f) => upload('csv', f)}
      />

      {load && (
        <div style={{
          padding: '9px 11px', background: 'var(--panel-sunk)',
          border: '1px solid var(--line)', borderRadius: 'var(--r-sm)',
        }}>
          <Readout label="Peak cooling" value={Math.round(load.peak_cooling_kw).toLocaleString()} unit="kW" />
          <Readout label="Peak heating" value={Math.round(load.peak_heating_kw).toLocaleString()} unit="kW" />
          <Readout label="Annual cooling" value={Math.round(load.annual_cooling_mwh).toLocaleString()} unit="MWh" />
          <Readout label="Annual heating" value={Math.round(load.annual_heating_mwh).toLocaleString()} unit="MWh" />
          {load.note && <p className="hint" style={{ marginTop: 6 }}>{load.note}</p>}
        </div>
      )}

      <div className="divider" />

      <Field label="Simulation timestep" unit="per hour"
        hint="Four steps an hour is the usual balance of fidelity and run time.">
        <Select
          value={String(config.options.run_period.timesteps_per_hour)}
          onChange={(v) => patch((c) => {
            c.options.run_period.timesteps_per_hour = Number(v)
          })}
          options={[
            { value: '1', label: '1 — fastest' },
            { value: '4', label: '4 — recommended' },
            { value: '6', label: '6' },
            { value: '12', label: '12 — slowest' },
          ]}
        />
      </Field>

      <Switch
        checked={config.options.stream_telemetry}
        label="Stream live plant data while it runs"
        onChange={(v) => patch((c) => { c.options.stream_telemetry = v })}
      />
    </div>
  )
}

function UploadButton({
  label, accept, disabled, onFile,
}: {
  label: string; accept: string; disabled?: boolean; onFile: (f: File) => void
}) {
  return (
    <label className="btn btn-sm" style={{
      justifyContent: 'center', cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1,
    }}>
      <IconUpload />{label}
      <input
        type="file" accept={accept} disabled={disabled} className="sr-only"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
          e.target.value = ''
        }}
      />
    </label>
  )
}

function LoopsTab() {
  const { config, patch } = useStore()
  const set = (fn: (c: PlantConfig) => void) => patch(fn)

  return (
    <div className="stack">
      <Field
        label="Chilled-water arrangement"
        hint={config.chw_loop.arrangement === 'PrimarySecondary'
          ? 'Constant-flow primary through the chillers, variable secondary to the district, decoupled by a common pipe.'
          : config.chw_loop.arrangement === 'VariablePrimary'
            ? 'One variable-speed pump set serves both the chillers and the district. Fewer pumps, tighter controls.'
            : 'A single constant-flow primary loop. Simple, but no distribution turndown.'}
      >
        <Select
          value={config.chw_loop.arrangement}
          onChange={(v) => set((c) => {
            c.chw_loop.arrangement = v
            c.pumps.secondary_chw = v === 'PrimarySecondary'
              ? (c.pumps.secondary_chw ?? {
                model: 'Pump:VariableSpeed', design_head_pa: 179352,
                motor_efficiency: 0.9, banked_count: 1, standby_count: 0,
                min_flow_fraction: 0,
              })
              : null
          })}
          options={[
            { value: 'PrimarySecondary', label: 'Primary–secondary' },
            { value: 'VariablePrimary', label: 'Variable primary flow' },
            { value: 'ConstantPrimary', label: 'Constant primary only' },
          ]}
        />
      </Field>

      <div className="row">
        <Field label="CHW setpoint" unit="°C">
          <NumberInput
            value={config.chw_loop.supply_setpoint_c} step={0.1}
            onChange={(v) => set((c) => { c.chw_loop.supply_setpoint_c = v })}
          />
        </Field>
        <Field label="CHW ΔT" unit="K">
          <NumberInput
            value={config.chw_loop.design_delta_t} min={1} step={0.1}
            onChange={(v) => set((c) => { c.chw_loop.design_delta_t = v })}
          />
        </Field>
      </div>

      <Field label="Chiller sequencing"
        hint="How load is spread across the machines that are on.">
        <Select
          value={config.chw_loop.load_distribution}
          onChange={(v) => set((c) => { c.chw_loop.load_distribution = v })}
          options={[
            { value: 'SequentialLoad', label: 'Sequential (lead–lag)' },
            { value: 'UniformLoad', label: 'Uniform load (equal kW)' },
            { value: 'UniformPLR', label: 'Uniform part-load ratio' },
            { value: 'SequentialUniformPLR', label: 'Sequential, then uniform PLR' },
          ]}
        />
      </Field>

      <div className="divider" />

      <Field
        label="Condenser-water setpoint"
        hint={config.cw_loop.follow_oa_wetbulb
          ? 'Tracks the outdoor wet-bulb plus the approach, which is what a real tower control does and what earns the part-load savings.'
          : 'Held at a fixed temperature all year.'}
      >
        <Switch
          checked={config.cw_loop.follow_oa_wetbulb}
          label="Follow outdoor wet-bulb"
          onChange={(v) => set((c) => { c.cw_loop.follow_oa_wetbulb = v })}
        />
      </Field>

      {config.cw_loop.follow_oa_wetbulb ? (
        <div className="row-3">
          <Field label="Approach" unit="K">
            <NumberInput
              value={config.cw_loop.wetbulb_approach_c} step={0.1}
              onChange={(v) => set((c) => { c.cw_loop.wetbulb_approach_c = v })}
            />
          </Field>
          <Field label="Min" unit="°C">
            <NumberInput
              value={config.cw_loop.min_setpoint_c} step={0.1}
              onChange={(v) => set((c) => { c.cw_loop.min_setpoint_c = v })}
            />
          </Field>
          <Field label="Max" unit="°C">
            <NumberInput
              value={config.cw_loop.max_setpoint_c} step={0.1}
              onChange={(v) => set((c) => { c.cw_loop.max_setpoint_c = v })}
            />
          </Field>
        </div>
      ) : (
        <Field label="Condenser setpoint" unit="°C">
          <NumberInput
            value={config.cw_loop.supply_setpoint_c} step={0.1}
            onChange={(v) => set((c) => { c.cw_loop.supply_setpoint_c = v })}
          />
        </Field>
      )}

      <div className="divider" />

      <div className="row">
        <Field label="Hot-water setpoint" unit="°C">
          <NumberInput
            value={config.hhw_loop.supply_setpoint_c} step={1}
            onChange={(v) => set((c) => { c.hhw_loop.supply_setpoint_c = v })}
          />
        </Field>
        <Field label="Hot-water ΔT" unit="K">
          <NumberInput
            value={config.hhw_loop.design_delta_t} min={1} step={0.5}
            onChange={(v) => set((c) => { c.hhw_loop.design_delta_t = v })}
          />
        </Field>
      </div>

      <div className="divider" />

      <div className="row">
        <Field label="Primary CHW head" unit="Pa">
          <NumberInput
            value={config.pumps.primary_chw.design_head_pa} step={1000}
            onChange={(v) => set((c) => { c.pumps.primary_chw.design_head_pa = v })}
          />
        </Field>
        <Field label="Condenser head" unit="Pa">
          <NumberInput
            value={config.pumps.condenser.design_head_pa} step={1000}
            onChange={(v) => set((c) => { c.pumps.condenser.design_head_pa = v })}
          />
        </Field>
      </div>
      <p className="hint">
        Pump head is the single input that most often ruins a system COP. 179 kPa
        is about 60 ft of water — reasonable for a campus loop, high for a
        compact plant room.
      </p>
    </div>
  )
}

function CostTab() {
  const { config, patch } = useStore()
  return (
    <div className="stack">
      <div className="row">
        <Field label="Currency">
          <TextInput
            value={config.tariff.currency}
            onChange={(v) => patch((c) => { c.tariff.currency = v })}
          />
        </Field>
        <Field label="Energy charge" unit="/kWh">
          <NumberInput
            value={config.tariff.energy_charge} min={0} step={0.1}
            onChange={(v) => patch((c) => { c.tariff.energy_charge = v })}
          />
        </Field>
      </div>
      <div className="row">
        <Field label="Demand charge" unit="/kW/mo">
          <NumberInput
            value={config.tariff.demand_charge} min={0} step={10}
            onChange={(v) => patch((c) => { c.tariff.demand_charge = v })}
          />
        </Field>
        <Field label="Fuel charge" unit="/kWh">
          <NumberInput
            value={config.tariff.gas_charge_per_kwh} min={0} step={0.1}
            onChange={(v) => patch((c) => { c.tariff.gas_charge_per_kwh = v })}
          />
        </Field>
      </div>

      <div className="divider" />

      <Field
        label="Compliance tier"
        hint="Total System Efficiency is checked against this tier's limit once the run finishes."
      >
        <Segmented
          value={config.compliance_tier}
          onChange={(v) => patch((c) => { c.compliance_tier = v })}
          options={[
            { value: 'ECSBC', label: 'ECSBC' },
            { value: 'SUPER_ECSBC', label: 'Super ECSBC' },
          ]}
        />
      </Field>
      <p className="hint">
        Reference limits are 0.24 kW/kW for ECSBC and 0.19 for Super ECSBC.
        Confirm the figure against the code edition your project is filed under.
      </p>
    </div>
  )
}
