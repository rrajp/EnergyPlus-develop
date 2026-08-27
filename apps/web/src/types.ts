/** Wire types. These mirror `apps/server/app/models.py`; keep them in step. */

export type CondenserType = 'WaterCooled' | 'AirCooled'
export type Compressor = 'Centrifugal' | 'Screw' | 'MagneticBearing'
export type ChillerModel = 'Chiller:Electric:EIR' | 'Chiller:Electric:ReformulatedEIR'
export type TowerModel =
  | 'CoolingTower:SingleSpeed' | 'CoolingTower:TwoSpeed' | 'CoolingTower:VariableSpeed'
export type BoilerFuel =
  | 'NaturalGas' | 'Electricity' | 'Propane' | 'FuelOilNo1' | 'FuelOilNo2'
export type EfficiencyBasis = 'EnteringBoiler' | 'LeavingBoiler'
export type PumpModel =
  | 'Pump:ConstantSpeed' | 'Pump:VariableSpeed'
  | 'HeaderedPumps:ConstantSpeed' | 'HeaderedPumps:VariableSpeed'
export type ChwArrangement = 'ConstantPrimary' | 'PrimarySecondary' | 'VariablePrimary'
export type LoadDistribution =
  | 'SequentialLoad' | 'UniformLoad' | 'UniformPLR' | 'SequentialUniformPLR'
export type HeatPumpSource = 'AirSource' | 'WaterSource'
export type ComplianceTier = 'ECSBC' | 'SUPER_ECSBC'
export type JobState =
  | 'queued' | 'preparing' | 'running' | 'parsing' | 'complete' | 'failed' | 'cancelled'
export type Severity = 'info' | 'warning' | 'error'

export interface Chiller {
  id: string; name: string; enabled: boolean
  model: ChillerModel; condenser: CondenserType; compressor: Compressor
  catalog_key: string | null
  capacity_kw: number; reference_cop: number
  ref_chw_leaving_c: number; ref_cond_entering_c: number
  min_part_load_ratio: number; max_part_load_ratio: number
  optimum_part_load_ratio: number; sizing_factor: number
}

export interface Boiler {
  id: string; name: string; enabled: boolean
  fuel: BoilerFuel; catalog_key: string | null
  capacity_kw: number; efficiency: number; efficiency_basis: EfficiencyBasis
  design_outlet_c: number; max_outlet_c: number
  parasitic_electric_w: number; sizing_factor: number
}

export interface CoolingTower {
  id: string; name: string; enabled: boolean
  model: TowerModel; autosize: boolean
  design_water_flow_m3s: number | null
  design_air_flow_m3s: number | null
  fan_power_kw: number | null
  design_approach_c: number; design_range_c: number
  design_inlet_wetbulb_c: number; min_air_flow_ratio: number; basin_heater_w: number
}

export interface HeatPump {
  id: string; name: string; enabled: boolean
  source: HeatPumpSource; catalog_key: string | null
  capacity_kw: number; reference_cop: number
  min_source_temp_c: number; sizing_factor: number
}

export interface ThermalStorage {
  enabled: boolean; name: string
  volume_m3: number; height_m: number
  tank_setpoint_c: number; deadband_c: number; charge_leaving_c: number
  charge_start_hour: number; charge_end_hour: number; charge_on_weekends: boolean
  ambient_temp_c: number; u_value_w_m2k: number; nodes: number
}

export interface Pump {
  model: PumpModel; design_head_pa: number; motor_efficiency: number
  banked_count: number; standby_count: number; min_flow_fraction: number
}

export interface PumpSet {
  primary_chw: Pump; secondary_chw: Pump | null; condenser: Pump
  primary_hhw: Pump; secondary_hhw: Pump | null; tes_distribution: Pump
  condenser_arrangement: 'SharedBank' | 'Dedicated'
}

export interface ChilledWaterLoop {
  arrangement: ChwArrangement; supply_setpoint_c: number; design_delta_t: number
  loop_exit_max_c: number; loop_exit_min_c: number; load_distribution: LoadDistribution
}
export interface HotWaterLoop {
  enabled: boolean; supply_setpoint_c: number; design_delta_t: number
  load_distribution: LoadDistribution; primary_secondary: boolean
}
export interface CondenserLoop {
  supply_setpoint_c: number; design_delta_t: number
  follow_oa_wetbulb: boolean; wetbulb_approach_c: number
  min_setpoint_c: number; max_setpoint_c: number
}

export interface WeatherRef {
  asset_id: string; label?: string; city?: string | null; climate_zone?: string | null
}
export interface LoadProfileRef {
  asset_id: string; label?: string
  cooling_column?: string | null; heating_column?: string | null
  units?: 'kW' | 'W' | 'TR' | 'MW'
  peak_cooling_kw?: number | null; peak_heating_kw?: number | null
}
export interface Tariff {
  currency: string; energy_charge: number; demand_charge: number
  gas_charge_per_kwh: number
}
export interface RunPeriod {
  begin_month: number; begin_day: number; end_month: number; end_day: number
  timesteps_per_hour: number; do_sizing: boolean
}
export interface SimOptions {
  run_period: RunPeriod; stream_telemetry: boolean
  telemetry_stride_hours: number; keep_artifacts: boolean
}

export interface PlantConfig {
  name: string; version: number
  chillers: Chiller[]; boilers: Boiler[]; towers: CoolingTower[]
  heat_pumps: HeatPump[]; storage: ThermalStorage
  pumps: PumpSet
  chw_loop: ChilledWaterLoop; hhw_loop: HotWaterLoop; cw_loop: CondenserLoop
  weather: WeatherRef | null; load: LoadProfileRef | null
  tariff: Tariff; compliance_tier: ComplianceTier; options: SimOptions
}

/* ---- assets ----------------------------------------------------------- */

export interface WeatherAsset {
  id: string; label: string; city: string; region: string; country: string
  latitude: number; longitude: number; elevation: number; time_zone: number
  source: 'bundled' | 'upload'
}
export interface LoadAsset {
  id: string; label: string
  peak_cooling_kw: number; peak_heating_kw: number
  annual_cooling_mwh: number; annual_heating_mwh: number
  has_heating: boolean; peak_cooling_hour: number; peak_heating_hour: number
  source: 'sample' | 'upload'; note: string
}

export interface CatalogChiller {
  key: string; label: string; manufacturer: string
  modelObject: ChillerModel; condenser: CondenserType
  capacityKw: number; capacityTr: number; referenceCop: number
  refChwLeavingC: number; refCondEnteringC: number
  minPlr: number; maxPlr: number; optimumPlr: number
}
export interface CatalogBoiler {
  key: string; label: string; nominalEfficiency: number
  efficiencyBasis: EfficiencyBasis; note: string
}
export interface Catalog {
  chillers: { waterCooled: CatalogChiller[]; airCooled: CatalogChiller[] }
  boilerCurves: CatalogBoiler[]
  manufacturers: string[]
  counts: { waterCooled: number; airCooled: number }
}

/* ---- jobs ------------------------------------------------------------- */

export interface ReadinessItem {
  id: string; severity: Severity; title: string; detail: string; blocking: boolean
}

export interface JobSummary {
  id: string; state: JobState; progress: number; phase: string
  plant_name: string; created_at: number
  started_at: number | null; finished_at: number | null
  error: string | null; warnings: number; severes: number
}

export interface SchematicNode {
  id: string; kind: 'chiller' | 'boiler' | 'tower' | 'heatpump' | 'pump' | 'load'
  label: string; loop: 'chw' | 'hhw' | 'cw'
  capacityKw?: number; cop?: number; condenser?: string
  efficiency?: number; fuel?: string; model?: string
  approachC?: number; source?: string; peakKw?: number; variable?: string
}
export interface SchematicEdge {
  from: string; to: string; loop: string; kind: 'flow' | 'return' | 'reject'
}
export interface Schematic {
  loops: { id: string; label: string; color: string }[]
  nodes: SchematicNode[]
  edges: SchematicEdge[]
}

export interface ResultSummary {
  coolingDeliveredMwh: number; coolingRequestedMwh: number
  heatingDeliveredMwh: number; heatingRequestedMwh: number
  plantElectricityMwh: number; fuelMwh: number
  systemCop: number; chillerCop: number; boilerEfficiency: number | null
  peakPlantKw: number; peakPlantHour: number
  peakCoolingKw: number; peakCoolingHour: number; peakCoolingTr: number
  peakHeatingKw: number; peakHeatingHour: number
  hours: number; warningCount: number; severeCount: number
  unmetCoolingHours: number; unmetHeatingHours: number
  coolingHours: number; heatingHours: number
  worstChwSupplyC: number; chwSetpointC: number
}

export interface Compliance {
  tier: ComplianceTier; metric: string; unit: string
  value: number | null; limit: number; compliant: boolean
  bestTierMet: string | null; systemCop: number; note: string
}

export interface EquipmentRow {
  name: string; kind: string; consumedMwh: number; unit: string
  deliveredMwh?: number; efficiency?: number | null
}

export interface MonthRow {
  month: string; coolingMwh: number; heatingMwh: number
  electricityMwh: number; peakKw: number
}

export interface Results {
  summary: ResultSummary
  endUse: Record<string, number>
  fuelMix: Record<string, number>
  equipment: EquipmentRow[]
  monthly: MonthRow[]
  compliance: Compliance
  cost: { currency: string; energy: number; demand: number; fuel: number; total: number }
  seriesMeta: { key: string; label: string; unit: string }[]
}

/* ---- stream events ---------------------------------------------------- */

export interface TelemetryFrame {
  t: 'telemetry'; hour: number; month: number; day: number
  values: Record<string, number>
}

export type StreamEvent =
  | { t: 'snapshot'; job: JobSummary; config: PlantConfig
      readiness: ReadinessItem[]; diagnostics: string[]
      schematic: Schematic; telemetryMeta: { id: string; variable: string; key: string }[]
      log: string[]; telemetry: TelemetryFrame[]
      results: Results | null; qa: ReadinessItem[] }
  | { t: 'state'; state: JobState; phase: string; progress: number }
  | { t: 'progress'; progress: number }
  | { t: 'log'; line: string }
  | { t: 'telemetry_ready'; resolved: string[] }
  | TelemetryFrame
  | { t: 'done'; results: Results; qa: ReadinessItem[]; elapsed: number }
  | { t: 'error'; message: string; log?: string[] }
  | { t: 'ping' }
