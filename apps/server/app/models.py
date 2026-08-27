"""Wire schema for the district plant designer.

This module is the contract: the UI posts a `PlantConfig`, the epJSON builder
consumes it, and every field that appears here is something a user can actually
set in the browser.  Keep it declarative -- no engine knowledge leaks in.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

Frac = Annotated[float, Field(ge=0.0, le=1.0)]
Positive = Annotated[float, Field(gt=0.0)]


def _uid() -> str:
    return uuid4().hex[:12]


class Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


# --------------------------------------------------------------------------
# enumerations
# --------------------------------------------------------------------------


class CondenserType(str, Enum):
    water = "WaterCooled"
    air = "AirCooled"


class Compressor(str, Enum):
    centrifugal = "Centrifugal"
    screw = "Screw"
    magnetic = "MagneticBearing"


class ChillerModel(str, Enum):
    eir = "Chiller:Electric:EIR"
    reformulated = "Chiller:Electric:ReformulatedEIR"


class TowerModel(str, Enum):
    single = "CoolingTower:SingleSpeed"
    two = "CoolingTower:TwoSpeed"
    variable = "CoolingTower:VariableSpeed"


class BoilerFuel(str, Enum):
    gas = "NaturalGas"
    electricity = "Electricity"
    propane = "Propane"
    oil1 = "FuelOilNo1"
    oil2 = "FuelOilNo2"


class EfficiencyBasis(str, Enum):
    entering = "EnteringBoiler"
    leaving = "LeavingBoiler"


class PumpModel(str, Enum):
    constant = "Pump:ConstantSpeed"
    variable = "Pump:VariableSpeed"
    headered_constant = "HeaderedPumps:ConstantSpeed"
    headered_variable = "HeaderedPumps:VariableSpeed"


class ChwArrangement(str, Enum):
    """How the chilled-water loop moves water between plant and load."""

    constant_primary = "ConstantPrimary"
    primary_secondary = "PrimarySecondary"
    variable_primary = "VariablePrimary"


class CondenserArrangement(str, Enum):
    shared = "SharedBank"
    dedicated = "Dedicated"


class LoadDistribution(str, Enum):
    sequential = "SequentialLoad"
    uniform = "UniformLoad"
    uniform_plr = "UniformPLR"
    sequential_uniform_plr = "SequentialUniformPLR"


class HeatPumpSource(str, Enum):
    air = "AirSource"
    water = "WaterSource"


class ComplianceTier(str, Enum):
    ecsbc = "ECSBC"
    super_ecsbc = "SUPER_ECSBC"


# --------------------------------------------------------------------------
# equipment
# --------------------------------------------------------------------------


class Chiller(Base):
    id: str = Field(default_factory=_uid)
    name: str = "Chiller"
    enabled: bool = True
    model: ChillerModel = ChillerModel.eir
    condenser: CondenserType = CondenserType.water
    compressor: Compressor = Compressor.centrifugal
    catalog_key: str | None = None
    """Selects a curve set from `idf.catalog`; None means use the generic set."""

    capacity_kw: Positive = 1758.0  # ~500 TR
    reference_cop: Positive = 6.1
    ref_chw_leaving_c: float = Field(default=6.67, ge=-5, le=20)
    ref_cond_entering_c: float = Field(default=29.44, ge=5, le=50)
    min_part_load_ratio: Frac = 0.10
    max_part_load_ratio: Annotated[float, Field(gt=0, le=1.5)] = 1.0
    optimum_part_load_ratio: Frac = 0.85
    sizing_factor: Annotated[float, Field(ge=1.0, le=2.0)] = 1.0

    @property
    def capacity_w(self) -> float:
        return self.capacity_kw * 1000.0


class Boiler(Base):
    id: str = Field(default_factory=_uid)
    name: str = "Boiler"
    enabled: bool = True
    fuel: BoilerFuel = BoilerFuel.gas
    catalog_key: str | None = None
    capacity_kw: Positive = 1000.0
    efficiency: Annotated[float, Field(gt=0.2, le=1.2)] = 0.80
    efficiency_basis: EfficiencyBasis = EfficiencyBasis.leaving
    design_outlet_c: float = Field(default=82.0, ge=30, le=120)
    max_outlet_c: float = Field(default=95.0, ge=40, le=140)
    parasitic_electric_w: float = Field(default=0.0, ge=0)
    sizing_factor: Annotated[float, Field(ge=1.0, le=2.0)] = 1.0

    @property
    def capacity_w(self) -> float:
        return self.capacity_kw * 1000.0


class CoolingTower(Base):
    id: str = Field(default_factory=_uid)
    name: str = "Cooling Tower"
    enabled: bool = True
    model: TowerModel = TowerModel.variable
    autosize: bool = True
    design_water_flow_m3s: float | None = None
    design_air_flow_m3s: float | None = None
    fan_power_kw: float | None = None
    design_approach_c: float = Field(default=3.9, ge=0.5, le=15)
    design_range_c: float = Field(default=5.6, ge=1, le=20)
    design_inlet_wetbulb_c: float = Field(default=25.6, ge=5, le=35)
    min_air_flow_ratio: Frac = 0.2
    basin_heater_w: float = Field(default=0.0, ge=0)


class HeatPump(Base):
    id: str = Field(default_factory=_uid)
    name: str = "Heat Pump"
    enabled: bool = True
    source: HeatPumpSource = HeatPumpSource.air
    catalog_key: str | None = None
    capacity_kw: Positive = 500.0
    reference_cop: Positive = 3.2
    min_source_temp_c: float = Field(default=-10.0, ge=-40, le=30)
    sizing_factor: Annotated[float, Field(ge=1.0, le=2.0)] = 1.0

    @property
    def capacity_w(self) -> float:
        return self.capacity_kw * 1000.0


class ThermalStorage(Base):
    """Stratified chilled-water tank on its own charge loop."""

    enabled: bool = False
    name: str = "Thermal Storage Tank"
    volume_m3: Positive = 500.0
    height_m: Positive = 10.0
    tank_setpoint_c: float = Field(default=6.0, ge=0, le=20)
    deadband_c: Positive = 1.0
    charge_leaving_c: float = Field(default=5.0, ge=-2, le=18)
    charge_start_hour: Annotated[int, Field(ge=0, le=24)] = 22
    charge_end_hour: Annotated[int, Field(ge=0, le=24)] = 6
    charge_on_weekends: bool = True
    ambient_temp_c: float = Field(default=25.0, ge=-20, le=50)
    u_value_w_m2k: float = Field(default=0.3, ge=0, le=10)
    nodes: Annotated[int, Field(ge=1, le=10)] = 6


class Pump(Base):
    model: PumpModel = PumpModel.variable
    design_head_pa: Positive = 179352.0  # ~60 ft w.g.
    motor_efficiency: Frac = 0.90
    banked_count: Annotated[int, Field(ge=1, le=8)] = 1
    standby_count: Annotated[int, Field(ge=0, le=4)] = 0
    """N+X redundancy; surfaced by the readiness checks, not by EnergyPlus."""

    min_flow_fraction: Frac = 0.0


class PumpSet(Base):
    primary_chw: Pump = Field(default_factory=Pump)
    secondary_chw: Pump | None = None
    condenser: Pump = Field(default_factory=lambda: Pump(design_head_pa=119568.0))
    primary_hhw: Pump = Field(default_factory=Pump)
    secondary_hhw: Pump | None = None
    tes_distribution: Pump = Field(default_factory=Pump)
    condenser_arrangement: CondenserArrangement = CondenserArrangement.shared


# --------------------------------------------------------------------------
# loops
# --------------------------------------------------------------------------


class ChilledWaterLoop(Base):
    arrangement: ChwArrangement = ChwArrangement.primary_secondary
    supply_setpoint_c: float = Field(default=6.67, ge=-2, le=20)
    design_delta_t: Positive = 6.67
    loop_exit_max_c: float = Field(default=98.0, ge=20, le=120)
    loop_exit_min_c: float = Field(default=1.0, ge=-10, le=20)
    load_distribution: LoadDistribution = LoadDistribution.sequential


class HotWaterLoop(Base):
    enabled: bool = True
    supply_setpoint_c: float = Field(default=82.0, ge=25, le=120)
    design_delta_t: Positive = 11.0
    load_distribution: LoadDistribution = LoadDistribution.sequential
    primary_secondary: bool = False


class CondenserLoop(Base):
    supply_setpoint_c: float = Field(default=29.4, ge=10, le=45)
    design_delta_t: Positive = 5.6
    follow_oa_wetbulb: bool = True
    wetbulb_approach_c: float = Field(default=3.9, ge=0.5, le=15)
    min_setpoint_c: float = Field(default=18.3, ge=5, le=35)
    max_setpoint_c: float = Field(default=35.0, ge=15, le=50)


# --------------------------------------------------------------------------
# site / load / run
# --------------------------------------------------------------------------


class LoadProfileRef(Base):
    """Points at an uploaded or sample 8760-hour CSV held by the asset store."""

    asset_id: str
    label: str = ""
    cooling_column: str | None = None
    heating_column: str | None = None
    units: Literal["kW", "W", "TR", "MW"] = "kW"
    peak_cooling_kw: float | None = None
    peak_heating_kw: float | None = None
    annual_cooling_mwh: float | None = None
    annual_heating_mwh: float | None = None


class WeatherRef(Base):
    asset_id: str
    label: str = ""
    city: str | None = None
    climate_zone: str | None = None


class Tariff(Base):
    currency: str = "INR"
    energy_charge: float = Field(default=8.5, ge=0)
    demand_charge: float = Field(default=350.0, ge=0)
    gas_charge_per_kwh: float = Field(default=3.2, ge=0)


class RunPeriod(Base):
    begin_month: Annotated[int, Field(ge=1, le=12)] = 1
    begin_day: Annotated[int, Field(ge=1, le=31)] = 1
    end_month: Annotated[int, Field(ge=1, le=12)] = 12
    end_day: Annotated[int, Field(ge=1, le=31)] = 31
    timesteps_per_hour: Annotated[int, Field(ge=1, le=60)] = 4
    do_sizing: bool = True


class SimOptions(Base):
    """Knobs that trade fidelity for turnaround time."""

    run_period: RunPeriod = Field(default_factory=RunPeriod)
    stream_telemetry: bool = True
    telemetry_stride_hours: Annotated[int, Field(ge=1, le=24)] = 1
    keep_artifacts: bool = True


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------


class PlantConfig(Base):
    name: str = "Untitled Plant"
    version: int = 1

    chillers: list[Chiller] = Field(default_factory=list)
    boilers: list[Boiler] = Field(default_factory=list)
    towers: list[CoolingTower] = Field(default_factory=list)
    heat_pumps: list[HeatPump] = Field(default_factory=list)
    storage: ThermalStorage = Field(default_factory=ThermalStorage)

    pumps: PumpSet = Field(default_factory=PumpSet)
    chw_loop: ChilledWaterLoop = Field(default_factory=ChilledWaterLoop)
    hhw_loop: HotWaterLoop = Field(default_factory=HotWaterLoop)
    cw_loop: CondenserLoop = Field(default_factory=CondenserLoop)

    weather: WeatherRef | None = None
    load: LoadProfileRef | None = None
    tariff: Tariff = Field(default_factory=Tariff)
    compliance_tier: ComplianceTier = ComplianceTier.ecsbc
    options: SimOptions = Field(default_factory=SimOptions)

    # ---- derived helpers used by the builder and the readiness checks ----

    @property
    def active_chillers(self) -> list[Chiller]:
        return [c for c in self.chillers if c.enabled]

    @property
    def active_boilers(self) -> list[Boiler]:
        return [b for b in self.boilers if b.enabled]

    @property
    def active_towers(self) -> list[CoolingTower]:
        return [t for t in self.towers if t.enabled]

    @property
    def active_heat_pumps(self) -> list[HeatPump]:
        return [h for h in self.heat_pumps if h.enabled]

    @property
    def needs_condenser_loop(self) -> bool:
        return any(c.condenser is CondenserType.water for c in self.active_chillers)

    @property
    def has_heating_plant(self) -> bool:
        return bool(self.active_boilers or self.active_heat_pumps)

    @property
    def total_cooling_capacity_kw(self) -> float:
        return sum(c.capacity_kw for c in self.active_chillers)

    @property
    def total_heating_capacity_kw(self) -> float:
        return sum(b.capacity_kw for b in self.active_boilers) + sum(
            h.capacity_kw for h in self.active_heat_pumps
        )

    @model_validator(mode="after")
    def _check_topology(self) -> PlantConfig:
        if self.needs_condenser_loop and not self.active_towers:
            raise ValueError(
                "Water-cooled chillers are present but the plant has no cooling tower "
                "to reject heat to."
            )
        if self.storage.enabled and not self.active_chillers:
            raise ValueError(
                "A thermal storage tank needs at least one chiller to charge it."
            )
        if self.chw_loop.arrangement is ChwArrangement.primary_secondary:
            if self.pumps.secondary_chw is None:
                raise ValueError(
                    "A primary-secondary chilled-water loop requires a secondary pump."
                )
        return self


# --------------------------------------------------------------------------
# job / streaming envelope
# --------------------------------------------------------------------------


class JobState(str, Enum):
    queued = "queued"
    preparing = "preparing"
    running = "running"
    parsing = "parsing"
    complete = "complete"
    failed = "failed"
    cancelled = "cancelled"


class Severity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"


class ReadinessItem(Base):
    id: str
    severity: Severity
    title: str
    detail: str = ""
    blocking: bool = False


class JobSummary(Base):
    id: str
    state: JobState
    progress: Frac = 0.0
    phase: str = ""
    plant_name: str = ""
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    warnings: int = 0
    severes: int = 0


class CreateJobRequest(Base):
    config: PlantConfig
    dry_run: bool = False
    """When true the epJSON is generated and validated but EnergyPlus never runs."""


class CreateJobResponse(Base):
    job: JobSummary
    readiness: list[ReadinessItem] = Field(default_factory=list)
