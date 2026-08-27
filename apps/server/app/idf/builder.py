"""Turns a `PlantConfig` into a runnable epJSON model.

Loop topology follows the standard EnergyPlus half-loop pattern, verified
against `testfiles/PlantApplicationsGuide_Example1.idf`:

    inlet branch -> splitter -> [equipment branches..., bypass] -> mixer -> outlet branch

Every loop here is built by `_half_loop`, so CHW / HHW / condenser / storage
loops cannot drift apart in structure.

One convention worth stating loudly, because getting it backwards produces a
plant that runs but never loads: `LoadProfile:Plant` treats a **positive**
scheduled load as heat *removed* from the water (a heating demand) and a
**negative** load as heat *added* (a cooling demand).  See
`PlantLoadProfile.cc: OutletTemp = InletTemp - Power/(mdot*Cp)`.  The CSV we
emit therefore carries cooling as negative watts.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..models import (
    Boiler,
    Chiller,
    ChwArrangement,
    CondenserType,
    CoolingTower,
    HeatPump,
    HeatPumpSource,
    LoadDistribution,
    PlantConfig,
    Pump,
    PumpModel,
    TowerModel,
)
from .catalog import Curve, curves_for_chiller, get_boiler_curve

WATER_DENSITY = 998.0      # kg/m3 at ~20 C
WATER_CP = 4182.0          # J/kg-K
BIG_LOAD = 1.0e9           # upper limit for plant operation ranges


def flow_for_load(load_w: float, delta_t: float) -> float:
    """Volumetric flow (m3/s) that carries `load_w` across `delta_t`."""
    if delta_t <= 0 or load_w <= 0:
        return 0.0
    return load_w / (WATER_DENSITY * WATER_CP * delta_t)


def _san(name: str) -> str:
    """EnergyPlus names are case-insensitive and comma/semicolon hostile."""
    return name.replace(",", " ").replace(";", " ").replace("!", " ").strip()


@dataclass
class Component:
    """One entry on a branch."""

    object_type: str
    name: str
    inlet: str
    outlet: str

    def as_epjson(self) -> dict[str, str]:
        return {
            "component_object_type": self.object_type,
            "component_name": self.name,
            "component_inlet_node_name": self.inlet,
            "component_outlet_node_name": self.outlet,
        }


@dataclass
class BuildResult:
    epjson: dict[str, Any]
    schematic: dict[str, Any]
    diagnostics: list[str] = field(default_factory=list)
    design_flows: dict[str, float] = field(default_factory=dict)


class PlantBuilder:
    """Assembles the epJSON document for one `PlantConfig`."""

    def __init__(
        self,
        cfg: PlantConfig,
        *,
        version: str,
        load_csv: Path | None = None,
        peak_cooling_w: float = 0.0,
        peak_heating_w: float = 0.0,
        has_heating_column: bool = False,
    ) -> None:
        self.cfg = cfg
        self.version = version
        self.load_csv = load_csv
        self.peak_cooling_w = peak_cooling_w
        self.peak_heating_w = peak_heating_w
        self.has_heating_column = has_heating_column

        self.doc: dict[str, dict[str, Any]] = {}
        self.diagnostics: list[str] = []
        self.design_flows: dict[str, float] = {}
        self._curves_emitted: set[str] = set()
        # schematic graph handed to the UI so the drawing always matches the model
        self._nodes: list[dict[str, Any]] = []
        self._edges: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # low-level emit helpers
    # ------------------------------------------------------------------

    def add(self, obj_type: str, name: str, fields: dict[str, Any]) -> str:
        name = _san(name)
        self.doc.setdefault(obj_type, {})[name] = {
            k: v for k, v in fields.items() if v is not None
        }
        return name

    def emit_curve(self, curve: Curve, prefix: str = "") -> str:
        """Write a catalog curve once, returning the name to reference."""
        name = _san(f"{prefix}{curve.name}") if prefix else _san(curve.name)
        if name not in self._curves_emitted:
            self.add(curve.type, name, curve.to_epjson())
            self._curves_emitted.add(name)
        return name

    def _schema_node(self, nid: str, kind: str, label: str, loop: str, **extra) -> None:
        self._nodes.append(
            {"id": nid, "kind": kind, "label": label, "loop": loop, **extra}
        )

    def _schema_edge(self, src: str, dst: str, loop: str, kind: str = "flow") -> None:
        self._edges.append({"from": src, "to": dst, "loop": loop, "kind": kind})

    # ------------------------------------------------------------------
    # half-loop assembly
    # ------------------------------------------------------------------

    def _half_loop(
        self,
        tag: str,
        equipment: list[tuple[str, list[Component]]],
        *,
        inlet_component: Component | None = None,
    ) -> dict[str, str]:
        """Build one side of a loop.

        `equipment` is a list of `(branch_name, components)`.  Returns the node
        and list names the loop object needs to reference.
        """
        inlet_node = f"{tag} Inlet Node"
        split_inlet = f"{tag} Split Inlet"
        mix_outlet = f"{tag} Mix Outlet"
        outlet_node = f"{tag} Outlet Node"

        inlet_branch = f"{tag} Inlet Branch"
        outlet_branch = f"{tag} Outlet Branch"
        bypass_branch = f"{tag} Bypass Branch"

        # inlet branch: either a pump or a plain pipe
        if inlet_component is None:
            inlet_component = Component(
                "Pipe:Adiabatic", f"{tag} Inlet Pipe", inlet_node, split_inlet
            )
            self.add(
                "Pipe:Adiabatic",
                inlet_component.name,
                {"inlet_node_name": inlet_node, "outlet_node_name": split_inlet},
            )
        self.add(
            "Branch", inlet_branch, {"components": [inlet_component.as_epjson()]}
        )

        # bypass
        self.add(
            "Pipe:Adiabatic",
            f"{tag} Bypass Pipe",
            {
                "inlet_node_name": f"{tag} Bypass In",
                "outlet_node_name": f"{tag} Bypass Out",
            },
        )
        self.add(
            "Branch",
            bypass_branch,
            {
                "components": [
                    Component(
                        "Pipe:Adiabatic",
                        f"{tag} Bypass Pipe",
                        f"{tag} Bypass In",
                        f"{tag} Bypass Out",
                    ).as_epjson()
                ]
            },
        )

        # outlet branch
        self.add(
            "Pipe:Adiabatic",
            f"{tag} Outlet Pipe",
            {"inlet_node_name": mix_outlet, "outlet_node_name": outlet_node},
        )
        self.add(
            "Branch",
            outlet_branch,
            {
                "components": [
                    Component(
                        "Pipe:Adiabatic",
                        f"{tag} Outlet Pipe",
                        mix_outlet,
                        outlet_node,
                    ).as_epjson()
                ]
            },
        )

        # equipment branches
        equip_branch_names: list[str] = []
        for branch_name, comps in equipment:
            self.add(
                "Branch",
                branch_name,
                {"components": [c.as_epjson() for c in comps]},
            )
            equip_branch_names.append(_san(branch_name))

        parallel = equip_branch_names + [bypass_branch]

        self.add(
            "BranchList",
            f"{tag} Branches",
            {
                "branches": [{"branch_name": b} for b in
                             [inlet_branch, *parallel, outlet_branch]]
            },
        )
        self.add(
            "Connector:Splitter",
            f"{tag} Splitter",
            {
                "inlet_branch_name": inlet_branch,
                "branches": [{"outlet_branch_name": b} for b in parallel],
            },
        )
        self.add(
            "Connector:Mixer",
            f"{tag} Mixer",
            {
                "outlet_branch_name": outlet_branch,
                "branches": [{"inlet_branch_name": b} for b in parallel],
            },
        )
        self.add(
            "ConnectorList",
            f"{tag} Connectors",
            {
                "connector_1_object_type": "Connector:Splitter",
                "connector_1_name": _san(f"{tag} Splitter"),
                "connector_2_object_type": "Connector:Mixer",
                "connector_2_name": _san(f"{tag} Mixer"),
            },
        )

        return {
            "inlet_node": inlet_node,
            "outlet_node": outlet_node,
            "branch_list": _san(f"{tag} Branches"),
            "connector_list": _san(f"{tag} Connectors"),
        }

    # ------------------------------------------------------------------
    # pumps
    # ------------------------------------------------------------------

    def _pump(
        self,
        pump: Pump,
        name: str,
        inlet: str,
        outlet: str,
        design_flow: float | str,
        *,
        force_constant: bool = False,
    ) -> Component:
        """Emit a pump object.

        `force_constant` exists because EnergyPlus's common-pipe model cannot
        vary flow on the primary side of a primary-secondary loop.  Feeding it
        a variable-speed primary pump makes the loop temperature diverge and
        the run dies on `CheckForRunawayPlantTemps`.  Constant primary flow is
        also what a primary-secondary arrangement means in the first place, so
        we correct it rather than let the run fail.
        """
        model = pump.model
        if force_constant and model in (PumpModel.variable, PumpModel.headered_variable):
            model = (
                PumpModel.headered_constant
                if model is PumpModel.headered_variable
                else PumpModel.constant
            )
            self.diagnostics.append(
                f"'{name}' was switched to constant speed: a primary-secondary "
                f"loop holds constant flow through the plant, and the "
                f"common-pipe model does not support a variable primary pump."
            )

        headered = model in (
            PumpModel.headered_constant,
            PumpModel.headered_variable,
        )
        variable = model in (PumpModel.variable, PumpModel.headered_variable)

        fields: dict[str, Any] = {
            "inlet_node_name": inlet,
            "outlet_node_name": outlet,
            "design_pump_head": pump.design_head_pa,
            "motor_efficiency": pump.motor_efficiency,
            "fraction_of_motor_inefficiencies_to_fluid_stream": 0.0,
            "design_power_consumption": "Autosize",
        }
        # The flow field is named differently on every one of these four
        # objects; writing the wrong one is silently ignored and the pump ends
        # up with zero flow.
        if headered:
            fields["total_design_flow_rate"] = design_flow
            fields["number_of_pumps_in_bank"] = pump.banked_count
            fields["flow_sequencing_control_scheme"] = "Sequential"
        elif variable:
            fields["design_maximum_flow_rate"] = design_flow
        else:
            fields["design_flow_rate"] = design_flow
        fields["pump_control_type"] = "Intermittent"

        if variable:
            # A cubic that lands on 1.0 at full flow; standard VSD default set.
            fields.update(
                {
                    "coefficient_1_of_the_part_load_performance_curve": 0.0,
                    "coefficient_2_of_the_part_load_performance_curve": 1.0,
                    "coefficient_3_of_the_part_load_performance_curve": 0.0,
                    "coefficient_4_of_the_part_load_performance_curve": 0.0,
                }
            )
            if not headered:
                fields["design_minimum_flow_rate"] = 0.0
                fields["design_minimum_flow_rate_fraction"] = pump.min_flow_fraction

        self.add(model.value, name, fields)
        return Component(model.value, _san(name), inlet, outlet)

    # ------------------------------------------------------------------
    # schedules
    # ------------------------------------------------------------------

    def _schedules(self) -> None:
        self.add("ScheduleTypeLimits", "Any Number", {})
        self.add(
            "ScheduleTypeLimits",
            "Fraction",
            {"lower_limit_value": 0, "upper_limit_value": 1,
             "numeric_type": "Continuous"},
        )
        self.add(
            "ScheduleTypeLimits",
            "On/Off",
            {"lower_limit_value": 0, "upper_limit_value": 1,
             "numeric_type": "Discrete"},
        )
        self.add(
            "Schedule:Constant",
            "Always On",
            {"schedule_type_limits_name": "On/Off", "hourly_value": 1.0},
        )
        self.add(
            "Schedule:Constant",
            "Full Flow",
            {"schedule_type_limits_name": "Fraction", "hourly_value": 1.0},
        )
        self.add(
            "Schedule:Constant",
            "CHW Supply SP Sch",
            {
                "schedule_type_limits_name": "Any Number",
                "hourly_value": self.cfg.chw_loop.supply_setpoint_c,
            },
        )
        self.add(
            "Schedule:Constant",
            "HHW Supply SP Sch",
            {
                "schedule_type_limits_name": "Any Number",
                "hourly_value": self.cfg.hhw_loop.supply_setpoint_c,
            },
        )

        if self.load_csv is not None:
            hours = 8760
            self.add(
                "Schedule:File",
                "Cooling Load Sch",
                {
                    "schedule_type_limits_name": "Any Number",
                    "file_name": str(self.load_csv),
                    "column_number": 1,
                    "rows_to_skip_at_top": 1,
                    "number_of_hours_of_data": hours,
                    "column_separator": "Comma",
                    "interpolate_to_timestep": "No",
                    "minutes_per_item": 60,
                },
            )
            self.add(
                "Schedule:File",
                "Cooling Flow Frac Sch",
                {
                    "schedule_type_limits_name": "Fraction",
                    "file_name": str(self.load_csv),
                    "column_number": 3,
                    "rows_to_skip_at_top": 1,
                    "number_of_hours_of_data": hours,
                    "column_separator": "Comma",
                    "interpolate_to_timestep": "No",
                    "minutes_per_item": 60,
                },
            )
            if self.has_heating_column:
                self.add(
                    "Schedule:File",
                    "Heating Load Sch",
                    {
                        "schedule_type_limits_name": "Any Number",
                        "file_name": str(self.load_csv),
                        "column_number": 2,
                        "rows_to_skip_at_top": 1,
                        "number_of_hours_of_data": hours,
                        "column_separator": "Comma",
                        "interpolate_to_timestep": "No",
                        "minutes_per_item": 60,
                    },
                )
                self.add(
                    "Schedule:File",
                    "Heating Flow Frac Sch",
                    {
                        "schedule_type_limits_name": "Fraction",
                        "file_name": str(self.load_csv),
                        "column_number": 4,
                        "rows_to_skip_at_top": 1,
                        "number_of_hours_of_data": hours,
                        "column_separator": "Comma",
                        "interpolate_to_timestep": "No",
                        "minutes_per_item": 60,
                    },
                )

    # ------------------------------------------------------------------
    # equipment emitters
    # ------------------------------------------------------------------

    def _chiller(self, ch: Chiller, index: int) -> tuple[str, Component, Component | None]:
        """Returns (equipment name, CHW-side component, condenser-side component)."""
        name = _san(ch.name or f"Chiller {index + 1}")
        curves, source = curves_for_chiller(
            ch.catalog_key, ch.condenser.value, ch.capacity_kw
        )
        if not curves:
            raise ValueError(
                f"No performance curves available for chiller '{name}'."
            )
        if ch.catalog_key and source and source.key != ch.catalog_key:
            self.diagnostics.append(
                f"Chiller '{name}': catalog model '{ch.catalog_key}' not found; "
                f"used nearest reference machine '{source.label}'."
            )
        elif not ch.catalog_key and source:
            self.diagnostics.append(
                f"Chiller '{name}': custom model seeded from reference machine "
                f"'{source.label}' ({source.capacity_kw:.0f} kW)."
            )

        # A CAPFT curve is normalised to *its own* machine's rating point.
        # Keeping the user's reference temperatures while grafting on another
        # machine's curves makes the curve evaluate to something other than 1.0
        # at the stated reference, so the chiller quietly delivers the wrong
        # capacity. Adopt the curve source's rating temperatures instead.
        ref_chw = ch.ref_chw_leaving_c
        ref_cond = ch.ref_cond_entering_c
        if source is not None:
            if (
                abs(source.ref_chw_leaving_c - ref_chw) > 0.05
                or abs(source.ref_cond_entering_c - ref_cond) > 0.05
            ):
                self.diagnostics.append(
                    f"Chiller '{name}': rating point moved to "
                    f"{source.ref_chw_leaving_c:.2f} °C chilled water / "
                    f"{source.ref_cond_entering_c:.2f} °C condenser to match the "
                    f"'{source.label}' curve set. Capacity and COP are unchanged."
                )
            ref_chw = source.ref_chw_leaving_c
            ref_cond = source.ref_cond_entering_c

        pfx = f"{name} "
        capft = self.emit_curve(curves["capft"], pfx)
        eirft = self.emit_curve(curves["eirft"], pfx)
        eirfplr = self.emit_curve(curves["eirfplr"], pfx)

        chw_in, chw_out = f"{name} CHW Inlet", f"{name} CHW Outlet"
        water_cooled = ch.condenser is CondenserType.water
        cond_in = f"{name} Cond Inlet"
        cond_out = f"{name} Cond Outlet"

        # The reformulated model's EIRFPLR is a bicubic in condenser leaving
        # temperature, so its object takes a different curve field name.
        obj_type = ch.model.value
        if source is not None and source.model_object != obj_type:
            # Curves came from a machine of the other formulation; follow the
            # curves rather than the requested object, they must agree.
            obj_type = source.model_object
            self.diagnostics.append(
                f"Chiller '{name}': using {obj_type} to match the reference "
                f"curve set."
            )

        fields: dict[str, Any] = {
            "reference_capacity": ch.capacity_w,
            "reference_cop": ch.reference_cop,
            "reference_leaving_chilled_water_temperature": ref_chw,
            "reference_chilled_water_flow_rate": "Autosize",
            "cooling_capacity_function_of_temperature_curve_name": capft,
            "electric_input_to_cooling_output_ratio_function_of_temperature_curve_name": eirft,
            "minimum_part_load_ratio": ch.min_part_load_ratio,
            "maximum_part_load_ratio": ch.max_part_load_ratio,
            "optimum_part_load_ratio": ch.optimum_part_load_ratio,
            "minimum_unloading_ratio": ch.min_part_load_ratio,
            "chilled_water_inlet_node_name": chw_in,
            "chilled_water_outlet_node_name": chw_out,
            "chiller_flow_mode": "ConstantFlow"
            if self.cfg.chw_loop.arrangement is not ChwArrangement.variable_primary
            else "LeavingSetpointModulated",
            "sizing_factor": ch.sizing_factor,
            "leaving_chilled_water_lower_temperature_limit": 2.0,
            "fraction_of_compressor_electric_consumption_rejected_by_condenser": 1.0,
        }

        if obj_type == "Chiller:Electric:ReformulatedEIR":
            fields["reference_leaving_condenser_water_temperature"] = (
                ref_cond + self.cfg.cw_loop.design_delta_t
            )
            fields[
                "electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve_name"
            ] = eirfplr
            fields["condenser_inlet_node_name"] = cond_in
            fields["condenser_outlet_node_name"] = cond_out
            fields["reference_condenser_water_flow_rate"] = "Autosize"
        else:
            fields["reference_entering_condenser_fluid_temperature"] = ref_cond
            fields[
                "electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve_name"
            ] = eirfplr
            fields["condenser_type"] = ch.condenser.value
            fields["condenser_inlet_node_name"] = cond_in
            fields["condenser_outlet_node_name"] = cond_out
            if water_cooled:
                fields["reference_condenser_fluid_flow_rate"] = "Autosize"
            else:
                fields["condenser_fan_power_ratio"] = 0.0

        if not water_cooled:
            # Air-cooled machines need an outdoor air node to draw ambient from.
            self.add("OutdoorAir:Node", cond_in, {"height_above_ground": -1.0})

        self.add(obj_type, name, fields)

        self._schema_node(
            f"chiller:{ch.id}",
            "chiller",
            name,
            "chw",
            capacityKw=ch.capacity_kw,
            cop=ch.reference_cop,
            condenser=ch.condenser.value,
        )

        chw_comp = Component(obj_type, name, chw_in, chw_out)
        cond_comp = (
            Component(obj_type, name, cond_in, cond_out) if water_cooled else None
        )
        return name, chw_comp, cond_comp

    def _boiler(self, b: Boiler, index: int) -> tuple[str, Component]:
        name = _san(b.name or f"Boiler {index + 1}")
        entry = get_boiler_curve(b.catalog_key)
        curve_name = None
        basis = b.efficiency_basis.value
        efficiency = b.efficiency
        if entry is not None:
            curve_name = self.emit_curve(entry.curve, f"{name} ")
            basis = entry.efficiency_basis
            efficiency = entry.nominal_efficiency

        hw_in, hw_out = f"{name} HW Inlet", f"{name} HW Outlet"
        self.add(
            "Boiler:HotWater",
            name,
            {
                "fuel_type": b.fuel.value,
                "nominal_capacity": b.capacity_w,
                "nominal_thermal_efficiency": efficiency,
                "efficiency_curve_temperature_evaluation_variable": basis
                if curve_name
                else None,
                "normalized_boiler_efficiency_curve_name": curve_name,
                "design_water_flow_rate": "Autosize",
                "minimum_part_load_ratio": 0.0,
                "maximum_part_load_ratio": 1.0,
                "optimum_part_load_ratio": 1.0,
                "boiler_water_inlet_node_name": hw_in,
                "boiler_water_outlet_node_name": hw_out,
                "water_outlet_upper_temperature_limit": b.max_outlet_c,
                "boiler_flow_mode": "ConstantFlow",
                "on_cycle_parasitic_electric_load": b.parasitic_electric_w,
                "sizing_factor": b.sizing_factor,
            },
        )
        self._schema_node(
            f"boiler:{b.id}", "boiler", name, "hhw",
            capacityKw=b.capacity_kw, efficiency=efficiency, fuel=b.fuel.value,
        )
        return name, Component("Boiler:HotWater", name, hw_in, hw_out)

    def _tower(self, t: CoolingTower, index: int) -> tuple[str, Component]:
        name = _san(t.name or f"Cooling Tower {index + 1}")
        water_in, water_out = f"{name} CW Inlet", f"{name} CW Outlet"
        oa_node = f"{name} OA Ref Node"
        self.add("OutdoorAir:Node", oa_node, {"height_above_ground": -1.0})

        common: dict[str, Any] = {
            "water_inlet_node_name": water_in,
            "water_outlet_node_name": water_out,
            "outdoor_air_inlet_node_name": oa_node,
            "basin_heater_capacity": t.basin_heater_w,
            "sizing_factor": 1.0,
            "number_of_cells": 1,
        }
        flow = "Autosize" if t.autosize else t.design_water_flow_m3s
        air = "Autosize" if t.autosize else t.design_air_flow_m3s
        power = "Autosize" if t.autosize else (t.fan_power_kw or 0.0) * 1000.0

        if t.model is TowerModel.variable:
            fan_curve = self.add(
                "Curve:Cubic",
                f"{name} Fan Power Ratio",
                {
                    # Fan power ~ (air flow ratio)^3, the textbook fan law.
                    "coefficient1_constant": 0.0,
                    "coefficient2_x": 0.0,
                    "coefficient3_x_2": 0.0,
                    "coefficient4_x_3": 1.0,
                    "minimum_value_of_x": 0.0,
                    "maximum_value_of_x": 1.0,
                },
            )
            fields = {
                **common,
                "model_type": "CoolToolsCrossFlow",
                "design_inlet_air_wet_bulb_temperature": t.design_inlet_wetbulb_c,
                "design_approach_temperature": t.design_approach_c,
                "design_range_temperature": t.design_range_c,
                "design_water_flow_rate": flow,
                "design_air_flow_rate": air,
                "design_fan_power": power,
                "fan_power_ratio_function_of_air_flow_rate_ratio_curve_name": fan_curve,
                "minimum_air_flow_rate_ratio": t.min_air_flow_ratio,
                "fraction_of_tower_capacity_in_free_convection_regime": 0.125,
                "evaporation_loss_mode": "SaturatedExit",
                "blowdown_calculation_mode": "ConcentrationRatio",
                "blowdown_concentration_ratio": 3.0,
                "cell_control": "MaximalCell",
            }
        elif t.model is TowerModel.two:
            fields = {
                **common,
                "design_water_flow_rate": flow,
                "high_fan_speed_air_flow_rate": air,
                "high_fan_speed_fan_power": power,
                "high_fan_speed_u_factor_times_area_value": "Autosize",
                "low_fan_speed_air_flow_rate": "Autocalculate",
                "low_fan_speed_fan_power": "Autocalculate",
                "low_fan_speed_u_factor_times_area_value": "Autocalculate",
                "free_convection_regime_air_flow_rate": "Autocalculate",
                "performance_input_method": "UFactorTimesAreaAndDesignWaterFlowRate",
                "evaporation_loss_mode": "SaturatedExit",
                "blowdown_calculation_mode": "ConcentrationRatio",
                "blowdown_concentration_ratio": 3.0,
            }
        else:
            fields = {
                **common,
                "design_water_flow_rate": flow,
                "design_air_flow_rate": air,
                "design_fan_power": power,
                "design_u_factor_times_area_value": "Autosize",
                "free_convection_regime_air_flow_rate": "Autocalculate",
                "performance_input_method": "UFactorTimesAreaAndDesignWaterFlowRate",
                "evaporation_loss_mode": "SaturatedExit",
                "blowdown_calculation_mode": "ConcentrationRatio",
                "blowdown_concentration_ratio": 3.0,
            }

        self.add(t.model.value, name, fields)
        self._schema_node(
            f"tower:{t.id}", "tower", name, "cw",
            model=t.model.value, approachC=t.design_approach_c,
        )
        return name, Component(t.model.value, name, water_in, water_out)

    def _heat_pump(self, hp: HeatPump, index: int) -> tuple[str, Component]:
        name = _san(hp.name or f"Heat Pump {index + 1}")
        load_in, load_out = f"{name} Load Inlet", f"{name} Load Outlet"
        src_in, src_out = f"{name} Source Inlet", f"{name} Source Outlet"

        # Representative air-to-water heating curves: capacity and EIR both
        # degrade as source air cools, EIR rises at part load.
        capft = self.add(
            "Curve:Biquadratic",
            f"{name} CAPFT",
            {
                "coefficient1_constant": 0.70,
                "coefficient2_x": 0.0135,
                "coefficient3_x_2": -0.0001,
                "coefficient4_y": 0.0180,
                "coefficient5_y_2": -0.00005,
                "coefficient6_x_y": -0.0001,
                "minimum_value_of_x": 20.0,
                "maximum_value_of_x": 65.0,
                "minimum_value_of_y": -20.0,
                "maximum_value_of_y": 40.0,
            },
        )
        eirft = self.add(
            "Curve:Biquadratic",
            f"{name} EIRFT",
            {
                "coefficient1_constant": 1.15,
                "coefficient2_x": -0.004,
                "coefficient3_x_2": 0.0002,
                "coefficient4_y": -0.020,
                "coefficient5_y_2": 0.00035,
                "coefficient6_x_y": -0.0002,
                "minimum_value_of_x": 20.0,
                "maximum_value_of_x": 65.0,
                "minimum_value_of_y": -20.0,
                "maximum_value_of_y": 40.0,
            },
        )
        eirfplr = self.add(
            "Curve:Quadratic",
            f"{name} EIRFPLR",
            {
                "coefficient1_constant": 0.20,
                "coefficient2_x": 0.55,
                "coefficient3_x_2": 0.25,
                "minimum_value_of_x": 0.0,
                "maximum_value_of_x": 1.0,
            },
        )
        self.diagnostics.append(
            f"Heat pump '{name}' uses representative air-to-water performance "
            f"curves, not a manufacturer catalog model."
        )

        if hp.source is HeatPumpSource.air:
            self.add("OutdoorAir:Node", src_in, {"height_above_ground": -1.0})

        self.add(
            "HeatPump:PlantLoop:EIR:Heating",
            name,
            {
                "load_side_inlet_node_name": load_in,
                "load_side_outlet_node_name": load_out,
                "condenser_type": "AirSource"
                if hp.source is HeatPumpSource.air
                else "WaterSource",
                "source_side_inlet_node_name": src_in,
                "source_side_outlet_node_name": src_out,
                "load_side_reference_flow_rate": "Autosize",
                "source_side_reference_flow_rate": "Autosize",
                "reference_capacity": hp.capacity_w,
                "reference_coefficient_of_performance": hp.reference_cop,
                "sizing_factor": hp.sizing_factor,
                "capacity_modifier_function_of_temperature_curve_name": capft,
                "electric_input_to_output_ratio_modifier_function_of_temperature_curve_name": eirft,
                "electric_input_to_output_ratio_modifier_function_of_part_load_ratio_curve_name": eirfplr,
                "minimum_source_inlet_temperature": hp.min_source_temp_c,
                "control_type": "Load",
                "flow_mode": "ConstantFlow",
            },
        )
        self._schema_node(
            f"hp:{hp.id}", "heatpump", name, "hhw",
            capacityKw=hp.capacity_kw, cop=hp.reference_cop, source=hp.source.value,
        )
        return name, Component(
            "HeatPump:PlantLoop:EIR:Heating", name, load_in, load_out
        )

    # ------------------------------------------------------------------
    # operation schemes
    # ------------------------------------------------------------------

    def _operation(
        self,
        loop_tag: str,
        equipment: list[tuple[str, str]],
        mode: str,
        *,
        condenser: bool = False,
    ) -> str:
        """Create an equipment list + load-range scheme, return the scheme name."""
        list_name = self.add(
            "PlantEquipmentList",
            f"{loop_tag} Equipment List",
            {
                "equipment": [
                    {"equipment_object_type": t, "equipment_name": n}
                    for t, n in equipment
                ]
            },
        )
        op_type = (
            "PlantEquipmentOperation:CoolingLoad"
            if mode == "cooling"
            else "PlantEquipmentOperation:HeatingLoad"
        )
        op_name = self.add(
            op_type,
            f"{loop_tag} {mode.title()} Operation",
            {
                "load_range_1_lower_limit": 0.0,
                "load_range_1_upper_limit": BIG_LOAD,
                "range_1_equipment_list_name": list_name,
            },
        )
        scheme_obj = (
            "CondenserEquipmentOperationSchemes"
            if condenser
            else "PlantEquipmentOperationSchemes"
        )
        return self.add(
            scheme_obj,
            f"{loop_tag} Operation Schemes",
            {
                "control_scheme_1_object_type": op_type,
                "control_scheme_1_name": op_name,
                "control_scheme_1_schedule_name": "Always On",
            },
        )

    # ------------------------------------------------------------------
    # loops
    # ------------------------------------------------------------------

    def _chw_loop(self) -> None:
        cfg = self.cfg
        chillers = cfg.active_chillers
        dt = cfg.chw_loop.design_delta_t
        design_flow = flow_for_load(
            max(self.peak_cooling_w, sum(c.capacity_w for c in chillers)), dt
        )
        self.design_flows["chw"] = design_flow

        supply_equipment: list[tuple[str, list[Component]]] = []
        equip_refs: list[tuple[str, str]] = []
        self._cond_components: list[Component] = []

        for i, ch in enumerate(chillers):
            obj_name, chw_comp, cond_comp = self._chiller(ch, i)
            supply_equipment.append((f"{obj_name} CHW Branch", [chw_comp]))
            equip_refs.append((chw_comp.object_type, obj_name))
            if cond_comp is not None:
                self._cond_components.append(cond_comp)

        primary_pump = self._pump(
            cfg.pumps.primary_chw,
            "Primary CHW Pump",
            "CHW Supply Inlet Node",
            "CHW Supply Split Inlet",
            "Autosize",
            force_constant=cfg.chw_loop.arrangement is ChwArrangement.primary_secondary,
        )
        self._schema_node("pump:chw-primary", "pump", "Primary CHW Pump", "chw",
                          variable=cfg.pumps.primary_chw.model.value)

        supply = self._half_loop(
            "CHW Supply", supply_equipment, inlet_component=primary_pump
        )

        # demand side: secondary pump (if any) then the building load
        demand_inlet: Component | None = None
        secondary = cfg.pumps.secondary_chw
        if secondary is not None and cfg.chw_loop.arrangement is ChwArrangement.primary_secondary:
            demand_inlet = self._pump(
                secondary,
                "Secondary CHW Pump",
                "CHW Demand Inlet Node",
                "CHW Demand Split Inlet",
                "Autosize",
            )
            self._schema_node("pump:chw-secondary", "pump", "Secondary CHW Pump",
                              "chw", variable=secondary.model.value)

        load_comp = Component(
            "LoadProfile:Plant", "Building Cooling Load",
            "CHW Load Inlet Node", "CHW Load Outlet Node",
        )
        self.add(
            "LoadProfile:Plant",
            "Building Cooling Load",
            {
                "inlet_node_name": load_comp.inlet,
                "outlet_node_name": load_comp.outlet,
                "load_schedule_name": "Cooling Load Sch",
                "peak_flow_rate": max(design_flow, 1e-6),
                "flow_rate_fraction_schedule_name": "Cooling Flow Frac Sch",
                "plant_loop_fluid_type": "Water",
            },
        )
        self._schema_node("load:cooling", "load", "Building Cooling Load", "chw",
                          peakKw=self.peak_cooling_w / 1000.0)

        demand = self._half_loop(
            "CHW Demand",
            [("CHW Demand Load Branch", [load_comp])],
            inlet_component=demand_inlet,
        )

        scheme = self._operation("CHW", equip_refs, "cooling")

        common_pipe = "None"
        if cfg.chw_loop.arrangement is ChwArrangement.primary_secondary:
            common_pipe = "CommonPipe"

        self.add(
            "PlantLoop",
            "CHW Loop",
            {
                "fluid_type": "Water",
                "plant_equipment_operation_scheme_name": scheme,
                "loop_temperature_setpoint_node_name": supply["outlet_node"],
                "maximum_loop_temperature": cfg.chw_loop.loop_exit_max_c,
                "minimum_loop_temperature": cfg.chw_loop.loop_exit_min_c,
                "maximum_loop_flow_rate": "Autosize",
                "minimum_loop_flow_rate": 0.0,
                "plant_loop_volume": "Autocalculate",
                "plant_side_inlet_node_name": supply["inlet_node"],
                "plant_side_outlet_node_name": supply["outlet_node"],
                "plant_side_branch_list_name": supply["branch_list"],
                "plant_side_connector_list_name": supply["connector_list"],
                "demand_side_inlet_node_name": demand["inlet_node"],
                "demand_side_outlet_node_name": demand["outlet_node"],
                "demand_side_branch_list_name": demand["branch_list"],
                "demand_side_connector_list_name": demand["connector_list"],
                "load_distribution_scheme": cfg.chw_loop.load_distribution.value,
                "common_pipe_simulation": common_pipe,
            },
        )
        self.add(
            "Sizing:Plant",
            "CHW Loop Sizing",
            {
                "plant_or_condenser_loop_name": "CHW Loop",
                "loop_type": "Cooling",
                "design_loop_exit_temperature": cfg.chw_loop.supply_setpoint_c,
                "loop_design_temperature_difference": dt,
                "sizing_option": "Coincident",
                "zone_timesteps_in_averaging_window": 1,
                "coincident_sizing_factor_mode": "GlobalCoolingSizingFactor",
            },
        )
        self.add(
            "SetpointManager:Scheduled",
            "CHW Loop SP Manager",
            {
                "control_variable": "Temperature",
                "schedule_name": "CHW Supply SP Sch",
                "setpoint_node_or_nodelist_name": supply["outlet_node"],
            },
        )

    def _condenser_loop(self) -> None:
        cfg = self.cfg
        dt = cfg.cw_loop.design_delta_t

        supply_equipment: list[tuple[str, list[Component]]] = []
        equip_refs: list[tuple[str, str]] = []
        for i, t in enumerate(cfg.active_towers):
            obj_name, comp = self._tower(t, i)
            supply_equipment.append((f"{obj_name} CW Branch", [comp]))
            equip_refs.append((comp.object_type, obj_name))

        cw_pump = self._pump(
            cfg.pumps.condenser,
            "Condenser Water Pump",
            "CW Supply Inlet Node",
            "CW Supply Split Inlet",
            "Autosize",
        )
        self._schema_node("pump:cw", "pump", "Condenser Water Pump", "cw",
                          variable=cfg.pumps.condenser.model.value)

        supply = self._half_loop(
            "CW Supply", supply_equipment, inlet_component=cw_pump
        )

        demand_equipment = [
            (f"{c.name} Cond Branch", [c]) for c in self._cond_components
        ]
        demand = self._half_loop("CW Demand", demand_equipment)

        scheme = self._operation("CW", equip_refs, "cooling", condenser=True)

        self.add(
            "CondenserLoop",
            "CW Loop",
            {
                "fluid_type": "Water",
                "condenser_equipment_operation_scheme_name": scheme,
                "condenser_loop_temperature_setpoint_node_name": supply["outlet_node"],
                "maximum_loop_temperature": 80.0,
                "minimum_loop_temperature": 5.0,
                "maximum_loop_flow_rate": "Autosize",
                "minimum_loop_flow_rate": 0.0,
                "condenser_loop_volume": "Autocalculate",
                "condenser_side_inlet_node_name": supply["inlet_node"],
                "condenser_side_outlet_node_name": supply["outlet_node"],
                "condenser_side_branch_list_name": supply["branch_list"],
                "condenser_side_connector_list_name": supply["connector_list"],
                "demand_side_inlet_node_name": demand["inlet_node"],
                "demand_side_outlet_node_name": demand["outlet_node"],
                "condenser_demand_side_branch_list_name": demand["branch_list"],
                "condenser_demand_side_connector_list_name": demand["connector_list"],
                "load_distribution_scheme": "SequentialLoad",
            },
        )
        self.add(
            "Sizing:Plant",
            "CW Loop Sizing",
            {
                "plant_or_condenser_loop_name": "CW Loop",
                "loop_type": "Condenser",
                "design_loop_exit_temperature": cfg.cw_loop.supply_setpoint_c,
                "loop_design_temperature_difference": dt,
            },
        )

        if cfg.cw_loop.follow_oa_wetbulb:
            oa_ref = self.add(
                "OutdoorAir:Node", "CW Setpoint OA Node",
                {"height_above_ground": -1.0},
            )
            self.add(
                "SetpointManager:FollowOutdoorAirTemperature",
                "CW Loop SP Manager",
                {
                    "control_variable": "Temperature",
                    "reference_temperature_type": "OutdoorAirWetBulb",
                    "offset_temperature_difference": cfg.cw_loop.wetbulb_approach_c,
                    "maximum_setpoint_temperature": cfg.cw_loop.max_setpoint_c,
                    "minimum_setpoint_temperature": cfg.cw_loop.min_setpoint_c,
                    "setpoint_node_or_nodelist_name": supply["outlet_node"],
                },
            )
        else:
            self.add(
                "Schedule:Constant",
                "CW Supply SP Sch",
                {
                    "schedule_type_limits_name": "Any Number",
                    "hourly_value": cfg.cw_loop.supply_setpoint_c,
                },
            )
            self.add(
                "SetpointManager:Scheduled",
                "CW Loop SP Manager",
                {
                    "control_variable": "Temperature",
                    "schedule_name": "CW Supply SP Sch",
                    "setpoint_node_or_nodelist_name": supply["outlet_node"],
                },
            )

    def _hhw_loop(self) -> None:
        cfg = self.cfg
        dt = cfg.hhw_loop.design_delta_t
        design_flow = flow_for_load(
            max(self.peak_heating_w, cfg.total_heating_capacity_kw * 1000.0), dt
        )
        self.design_flows["hhw"] = design_flow

        supply_equipment: list[tuple[str, list[Component]]] = []
        equip_refs: list[tuple[str, str]] = []

        # Heat pumps lead the sequence: they are cheaper to run than the boilers.
        for i, hp in enumerate(cfg.active_heat_pumps):
            obj_name, comp = self._heat_pump(hp, i)
            supply_equipment.append((f"{obj_name} HW Branch", [comp]))
            equip_refs.append((comp.object_type, obj_name))
        for i, b in enumerate(cfg.active_boilers):
            obj_name, comp = self._boiler(b, i)
            supply_equipment.append((f"{obj_name} HW Branch", [comp]))
            equip_refs.append((comp.object_type, obj_name))

        hw_pump = self._pump(
            cfg.pumps.primary_hhw,
            "Primary HHW Pump",
            "HHW Supply Inlet Node",
            "HHW Supply Split Inlet",
            "Autosize",
            force_constant=cfg.hhw_loop.primary_secondary,
        )
        self._schema_node("pump:hhw-primary", "pump", "Primary HHW Pump", "hhw",
                          variable=cfg.pumps.primary_hhw.model.value)

        supply = self._half_loop(
            "HHW Supply", supply_equipment, inlet_component=hw_pump
        )

        demand_inlet: Component | None = None
        if cfg.hhw_loop.primary_secondary and cfg.pumps.secondary_hhw is not None:
            demand_inlet = self._pump(
                cfg.pumps.secondary_hhw,
                "Secondary HHW Pump",
                "HHW Demand Inlet Node",
                "HHW Demand Split Inlet",
                "Autosize",
            )
            self._schema_node("pump:hhw-secondary", "pump", "Secondary HHW Pump",
                              "hhw", variable=cfg.pumps.secondary_hhw.model.value)

        load_comp = Component(
            "LoadProfile:Plant", "Building Heating Load",
            "HHW Load Inlet Node", "HHW Load Outlet Node",
        )
        self.add(
            "LoadProfile:Plant",
            "Building Heating Load",
            {
                "inlet_node_name": load_comp.inlet,
                "outlet_node_name": load_comp.outlet,
                "load_schedule_name": "Heating Load Sch",
                "peak_flow_rate": max(design_flow, 1e-6),
                "flow_rate_fraction_schedule_name": "Heating Flow Frac Sch",
                "plant_loop_fluid_type": "Water",
            },
        )
        self._schema_node("load:heating", "load", "Building Heating Load", "hhw",
                          peakKw=self.peak_heating_w / 1000.0)

        demand = self._half_loop(
            "HHW Demand",
            [("HHW Demand Load Branch", [load_comp])],
            inlet_component=demand_inlet,
        )
        scheme = self._operation("HHW", equip_refs, "heating")

        self.add(
            "PlantLoop",
            "HHW Loop",
            {
                "fluid_type": "Water",
                "plant_equipment_operation_scheme_name": scheme,
                "loop_temperature_setpoint_node_name": supply["outlet_node"],
                "maximum_loop_temperature": 100.0,
                # Deliberately far below any operating point. An undersized
                # heating plant should show up as unmet hours in the results,
                # not as a `CheckForRunawayPlantTemps` fatal that tells the
                # user nothing.
                "minimum_loop_temperature": 2.0,
                "maximum_loop_flow_rate": "Autosize",
                "minimum_loop_flow_rate": 0.0,
                "plant_loop_volume": "Autocalculate",
                "plant_side_inlet_node_name": supply["inlet_node"],
                "plant_side_outlet_node_name": supply["outlet_node"],
                "plant_side_branch_list_name": supply["branch_list"],
                "plant_side_connector_list_name": supply["connector_list"],
                "demand_side_inlet_node_name": demand["inlet_node"],
                "demand_side_outlet_node_name": demand["outlet_node"],
                "demand_side_branch_list_name": demand["branch_list"],
                "demand_side_connector_list_name": demand["connector_list"],
                "load_distribution_scheme": cfg.hhw_loop.load_distribution.value,
                "common_pipe_simulation": "CommonPipe"
                if cfg.hhw_loop.primary_secondary
                else "None",
            },
        )
        self.add(
            "Sizing:Plant",
            "HHW Loop Sizing",
            {
                "plant_or_condenser_loop_name": "HHW Loop",
                "loop_type": "Heating",
                "design_loop_exit_temperature": cfg.hhw_loop.supply_setpoint_c,
                "loop_design_temperature_difference": dt,
            },
        )
        self.add(
            "SetpointManager:Scheduled",
            "HHW Loop SP Manager",
            {
                "control_variable": "Temperature",
                "schedule_name": "HHW Supply SP Sch",
                "setpoint_node_or_nodelist_name": supply["outlet_node"],
            },
        )

    # ------------------------------------------------------------------
    # shell objects and outputs
    # ------------------------------------------------------------------

    def _shell(self) -> None:
        cfg = self.cfg
        rp = cfg.options.run_period
        self.add("Version", "Version 1", {"version_identifier": self.version})
        self.add(
            "SimulationControl",
            "SimulationControl 1",
            {
                "do_zone_sizing_calculation": "No",
                "do_system_sizing_calculation": "No",
                "do_plant_sizing_calculation": "Yes" if rp.do_sizing else "No",
                "run_simulation_for_sizing_periods": "No",
                "run_simulation_for_weather_file_run_periods": "Yes",
                "do_hvac_sizing_simulation_for_sizing_periods": "No",
            },
        )
        self.add(
            "Building",
            _san(cfg.name) or "District Plant",
            {
                "north_axis": 0.0,
                "terrain": "City",
                "loads_convergence_tolerance_value": 0.04,
                "temperature_convergence_tolerance_value": 0.4,
                "solar_distribution": "MinimalShadowing",
                "maximum_number_of_warmup_days": 25,
                "minimum_number_of_warmup_days": 6,
            },
        )
        self.add(
            "GlobalGeometryRules",
            "GlobalGeometryRules 1",
            {
                "starting_vertex_position": "UpperLeftCorner",
                "vertex_entry_direction": "Counterclockwise",
                "coordinate_system": "Relative",
            },
        )
        self.add(
            "Timestep", "Timestep 1",
            {"number_of_timesteps_per_hour": rp.timesteps_per_hour},
        )
        self.add(
            "RunPeriod",
            "Annual Run",
            {
                "begin_month": rp.begin_month,
                "begin_day_of_month": rp.begin_day,
                "end_month": rp.end_month,
                "end_day_of_month": rp.end_day,
                "day_of_week_for_start_day": "Sunday",
                "use_weather_file_holidays_and_special_days": "Yes",
                "use_weather_file_daylight_saving_period": "Yes",
                "apply_weekend_holiday_rule": "No",
                "use_weather_file_rain_indicators": "Yes",
                "use_weather_file_snow_indicators": "Yes",
            },
        )
        if rp.do_sizing:
            # Plant sizing needs design conditions; pull them from the EPW so the
            # user never has to supply a .ddy.
            self.add(
                "SizingPeriod:WeatherFileConditionType",
                "Summer Extreme",
                {
                    "period_selection": "SummerExtreme",
                    "day_of_week_for_start_day": "Monday",
                    "use_weather_file_daylight_saving_period": "No",
                    "use_weather_file_rain_and_snow_indicators": "No",
                },
            )
            self.add(
                "SizingPeriod:WeatherFileConditionType",
                "Winter Extreme",
                {
                    "period_selection": "WinterExtreme",
                    "day_of_week_for_start_day": "Monday",
                    "use_weather_file_daylight_saving_period": "No",
                    "use_weather_file_rain_and_snow_indicators": "No",
                },
            )

    #: Variables we always report; the results parser and the live telemetry
    #: stream both read from this list, so they cannot fall out of sync.
    OUTPUT_VARIABLES: tuple[str, ...] = (
        "Site Outdoor Air Drybulb Temperature",
        "Site Outdoor Air Wetbulb Temperature",
        "Chiller Electricity Rate",
        "Chiller Electricity Energy",
        "Chiller Evaporator Cooling Rate",
        "Chiller Evaporator Cooling Energy",
        "Chiller Condenser Heat Transfer Energy",
        "Chiller Part Load Ratio",
        "Chiller COP",
        "Chiller Evaporator Outlet Temperature",
        "Cooling Tower Fan Electricity Rate",
        "Cooling Tower Fan Electricity Energy",
        "Cooling Tower Heat Transfer Rate",
        "Cooling Tower Outlet Temperature",
        "Pump Electricity Rate",
        "Pump Electricity Energy",
        "Pump Mass Flow Rate",
        "Boiler Heating Rate",
        "Boiler Heating Energy",
        "Boiler NaturalGas Energy",
        "Boiler Electricity Energy",
        "Boiler Propane Energy",
        "Boiler FuelOilNo1 Energy",
        "Boiler FuelOilNo2 Energy",
        "Boiler Ancillary Electricity Energy",
        "Boiler Part Load Ratio",
        "Heat Pump Electricity Rate",
        "Heat Pump Electricity Energy",
        "Heat Pump Load Side Heat Transfer Rate",
        "Heat Pump Load Side Heat Transfer Energy",
        "Heat Pump Part Load Ratio",
        "Plant Load Profile Heat Transfer Rate",
        "Plant Load Profile Cooling Energy",
        "Plant Load Profile Heating Energy",
        "Plant Load Profile Mass Flow Rate",
        "System Node Temperature",
        "System Node Mass Flow Rate",
    )

    def _outputs(self) -> None:
        for var in self.OUTPUT_VARIABLES:
            self.add(
                "Output:Variable",
                f"Output:Variable {var}",
                {
                    "key_value": "*",
                    "variable_name": var,
                    "reporting_frequency": "Hourly",
                },
            )
        self.add(
            "Output:SQLite", "Output:SQLite 1",
            {"option_type": "SimpleAndTabular"},
        )
        self.add(
            "Output:Table:SummaryReports",
            "Output:Table:SummaryReports 1",
            {"reports": [{"report_name": "AllSummary"}]},
        )
        self.add(
            "OutputControl:Table:Style",
            "OutputControl:Table:Style 1",
            {"column_separator": "HTML", "unit_conversion": "None"},
        )
        # Facility meters only: the EnergyTransfer end-use meters are not
        # created in a zone-free plant model.
        for meter in ("Electricity:Facility", "NaturalGas:Facility"):
            self.add(
                "Output:Meter", f"Output:Meter {meter}",
                {"key_name": meter, "reporting_frequency": "Hourly"},
            )

    # ------------------------------------------------------------------
    # schematic wiring
    # ------------------------------------------------------------------

    def _wire_schematic(self) -> None:
        cfg = self.cfg
        loops = []
        if cfg.active_chillers:
            loops.append(("chw", "Chilled-Water Loop", "#38bdf8"))
        if cfg.needs_condenser_loop:
            loops.append(("cw", "Condenser-Water Loop", "#34d399"))
        if cfg.has_heating_plant and cfg.hhw_loop.enabled:
            loops.append(("hhw", "Hot-Water Loop", "#fb7185"))

        by_loop: dict[str, list[dict[str, Any]]] = {}
        for n in self._nodes:
            by_loop.setdefault(n["loop"], []).append(n)

        for loop_id, *_ in loops:
            members = by_loop.get(loop_id, [])
            pump = next((m for m in members if m["kind"] == "pump"), None)
            gens = [m for m in members if m["kind"] in
                    ("chiller", "boiler", "heatpump", "tower")]
            load = next((m for m in members if m["kind"] == "load"), None)
            if pump:
                for g in gens:
                    self._schema_edge(pump["id"], g["id"], loop_id)
            if load:
                for g in gens:
                    self._schema_edge(g["id"], load["id"], loop_id)
                if pump:
                    self._schema_edge(load["id"], pump["id"], loop_id, kind="return")

        # chillers reject to towers across the condenser loop
        if cfg.needs_condenser_loop:
            chillers = [n for n in self._nodes if n["kind"] == "chiller"
                        and n.get("condenser") == "WaterCooled"]
            towers = [n for n in self._nodes if n["kind"] == "tower"]
            for c in chillers:
                for t in towers:
                    self._schema_edge(c["id"], t["id"], "cw", kind="reject")

        self.schematic = {
            "loops": [
                {"id": i, "label": lbl, "color": col} for i, lbl, col in loops
            ],
            "nodes": self._nodes,
            "edges": self._edges,
        }

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def build(self) -> BuildResult:
        cfg = self.cfg
        self._shell()
        self._schedules()

        if cfg.active_chillers:
            self._chw_loop()
            if cfg.needs_condenser_loop:
                self._condenser_loop()
        else:
            self._cond_components = []
            self.diagnostics.append(
                "No chillers configured; the model has no chilled-water plant."
            )

        if cfg.has_heating_plant and cfg.hhw_loop.enabled:
            if not self.has_heating_column:
                self.diagnostics.append(
                    "Heating equipment is configured but the load profile has no "
                    "heating column; the hot-water loop was omitted."
                )
            else:
                self._hhw_loop()

        self._outputs()
        self._wire_schematic()

        return BuildResult(
            epjson=self.doc,
            schematic=self.schematic,
            diagnostics=_collapse(self.diagnostics),
            design_flows=self.design_flows,
        )

_NAMED = re.compile(r"^(?P<kind>Chiller|Boiler|Heat pump) '(?P<name>[^']+)': (?P<rest>.*)$")


def _collapse(notes: list[str]) -> list[str]:
    """Fold per-machine notes that share the same text into one line.

    Three identical chiller notes tell the reader nothing three times; one
    note naming all three tells them the same thing once.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    order: list[object] = []
    for note in notes:
        m = _NAMED.match(note)
        if not m:
            if note not in order:
                order.append(note)
            continue
        key = (m.group("kind"), m.group("rest"))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        if m.group("name") not in grouped[key]:
            grouped[key].append(m.group("name"))

    out: list[str] = []
    for item in order:
        if isinstance(item, str):
            out.append(item)
            continue
        kind, rest = item
        names = grouped[item]
        if len(names) == 1:
            out.append(f"{kind} '{names[0]}': {rest}")
        else:
            joined = ", ".join(f"'{n}'" for n in names[:-1]) + f" and '{names[-1]}'"
            plural = "s" if kind != "Heat pump" else "s"
            out.append(f"{kind}{plural} {joined}: {rest}")
    return out
