"""Pre-flight readiness checks and post-run physics QA.

These are the checks an engineer would run by eye before trusting a model.
Blocking items stop the run; warnings are advisory and shown alongside the
results so a number is never presented without its caveat.
"""

from __future__ import annotations

from typing import Any

from .assets import LoadAsset
from .models import (
    ChwArrangement,
    CondenserType,
    PlantConfig,
    PumpModel,
    ReadinessItem,
    Severity,
)

TR_PER_KW = 1.0 / 3.51685


def _item(
    id_: str, sev: Severity, title: str, detail: str = "", blocking: bool = False
) -> ReadinessItem:
    return ReadinessItem(
        id=id_, severity=sev, title=title, detail=detail, blocking=blocking
    )


def readiness(
    cfg: PlantConfig, load: LoadAsset | None, weather_ok: bool
) -> list[ReadinessItem]:
    """Everything worth telling the user before spending a minute of CPU."""
    out: list[ReadinessItem] = []

    # ---- inputs present ------------------------------------------------
    if not weather_ok:
        out.append(_item(
            "weather-missing", Severity.error,
            "No weather file attached",
            "Pick a location or upload an EPW before running.",
            blocking=True,
        ))
    if load is None:
        out.append(_item(
            "load-missing", Severity.error,
            "No building load profile attached",
            "Choose a sample load or upload an 8,760-hour CSV.",
            blocking=True,
        ))

    # ---- plant exists --------------------------------------------------
    if not cfg.active_chillers and not cfg.has_heating_plant:
        out.append(_item(
            "empty-plant", Severity.error, "The plant is empty",
            "Add at least one chiller, boiler or heat pump.",
            blocking=True,
        ))

    if not cfg.active_chillers and load is not None and load.peak_cooling_kw > 0:
        out.append(_item(
            "no-chiller", Severity.error, "Plant has no chiller",
            f"The load profile peaks at {load.peak_cooling_kw:,.0f} kW of "
            f"cooling but there is nothing to serve it.",
            blocking=True,
        ))

    # ---- capacity vs load ----------------------------------------------
    if load is not None and cfg.active_chillers:
        cap = cfg.total_cooling_capacity_kw
        peak = load.peak_cooling_kw
        if cap < peak:
            out.append(_item(
                "cooling-undersized", Severity.warning,
                "Peak load exceeds installed chiller capacity",
                f"Installed {cap:,.0f} kW ({cap * TR_PER_KW:,.0f} TR) against a "
                f"{peak:,.0f} kW peak. Expect unmet hours and a chilled-water "
                f"supply temperature that drifts above setpoint.",
            ))
        elif cap > peak * 2.0:
            out.append(_item(
                "cooling-oversized", Severity.warning,
                "Chiller capacity is more than twice the peak load",
                f"Installed {cap:,.0f} kW against a {peak:,.0f} kW peak. "
                f"Machines will spend the year at low part load, where COP "
                f"falls off.",
            ))

        # N+1 redundancy is standard practice for district cooling
        if len(cfg.active_chillers) == 1:
            out.append(_item(
                "no-standby-chiller", Severity.info,
                "Single chiller, no standby",
                "District plants are normally built N+1 so one machine can be "
                "taken out for service.",
            ))

    if load is not None and cfg.has_heating_plant:
        cap = cfg.total_heating_capacity_kw
        peak = load.peak_heating_kw
        if not load.has_heating:
            out.append(_item(
                "heating-no-column", Severity.warning,
                "Heating equipment configured but the load CSV has no heating column",
                "The hot-water loop will be left out of the model.",
            ))
        elif cap < peak:
            # Blocking, unlike the cooling equivalent. `LoadProfile:Plant`
            # draws a fixed wattage regardless of water temperature, so a
            # permanent heating deficit walks the loop temperature down every
            # pass with nothing to arrest it. EnergyPlus aborts with
            # `CheckForRunawayPlantTemps` a few timesteps into warmup, which
            # tells the user nothing. Catching it here does.
            shortfall = peak - cap
            out.append(_item(
                "heating-undersized", Severity.error,
                "Heating capacity is below the peak heating load",
                f"Installed {cap:,.0f} kW against a {peak:,.0f} kW peak — "
                f"{shortfall:,.0f} kW short. A hot-water loop that can never "
                f"meet its load does not simulate as unmet hours; the loop "
                f"temperature diverges and EnergyPlus stops. Raise heating "
                f"capacity to at least {peak:,.0f} kW, or use "
                f"\u201cSize plant to load\u201d.",
                blocking=True,
            ))
        elif cap < peak * 1.05:
            out.append(_item(
                "heating-tight", Severity.warning,
                "Heating capacity has almost no margin",
                f"Installed {cap:,.0f} kW against a {peak:,.0f} kW peak. "
                f"A boiler derates in service; 10% margin is usual.",
            ))

    if load is not None and load.has_heating and not cfg.has_heating_plant:
        out.append(_item(
            "heating-no-plant", Severity.warning,
            "The load profile has a heating column but the plant has no heating equipment",
            f"{load.peak_heating_kw:,.0f} kW of peak heating demand will be "
            f"ignored.",
        ))

    # ---- heat rejection ------------------------------------------------
    water_cooled = [c for c in cfg.active_chillers
                    if c.condenser is CondenserType.water]
    if water_cooled and not cfg.active_towers:
        out.append(_item(
            "no-tower", Severity.error, "Water-cooled chillers with no cooling tower",
            "Add a tower, or switch the chillers to air-cooled.",
            blocking=True,
        ))
    if cfg.active_towers and not water_cooled:
        out.append(_item(
            "tower-unused", Severity.warning,
            "Cooling towers with nothing to reject to",
            "Every chiller is air-cooled, so the condenser loop carries no heat.",
        ))

    # ---- pumping -------------------------------------------------------
    if cfg.chw_loop.arrangement is ChwArrangement.primary_secondary:
        if cfg.pumps.secondary_chw is None:
            out.append(_item(
                "no-secondary-pump", Severity.error,
                "Primary-secondary loop without a secondary pump",
                blocking=True,
            ))
    if cfg.pumps.primary_chw.standby_count == 0 and len(cfg.active_chillers) > 1:
        out.append(_item(
            "pump-no-standby", Severity.info,
            "No standby on the primary chilled-water pump bank",
            "N+1 standby is the usual recommendation for resilience.",
        ))
    if (
        cfg.chw_loop.arrangement is ChwArrangement.variable_primary
        and cfg.pumps.primary_chw.model
        in (PumpModel.constant, PumpModel.headered_constant)
    ):
        out.append(_item(
            "vpf-constant-pump", Severity.warning,
            "Variable primary flow with a constant-speed pump",
            "Variable primary flow only saves energy if the primary pump can "
            "actually modulate.",
        ))

    # ---- setpoints -----------------------------------------------------
    if cfg.chw_loop.supply_setpoint_c >= cfg.cw_loop.min_setpoint_c:
        out.append(_item(
            "setpoint-inversion", Severity.warning,
            "Chilled-water setpoint is at or above the condenser-water minimum",
            "Check the loop setpoints; the chillers may not lift correctly.",
        ))
    for ch in cfg.active_chillers:
        if abs(ch.ref_chw_leaving_c - cfg.chw_loop.supply_setpoint_c) > 3.0:
            out.append(_item(
                f"chiller-ref-far-{ch.id}", Severity.info,
                f"'{ch.name}' is rated at {ch.ref_chw_leaving_c:.1f} °C but the "
                f"loop runs at {cfg.chw_loop.supply_setpoint_c:.1f} °C",
                "Performance is extrapolated away from the rating point.",
            ))

    # ---- storage -------------------------------------------------------
    if cfg.storage.enabled:
        if not cfg.active_chillers:
            out.append(_item(
                "tes-no-chiller", Severity.error,
                "Thermal storage with no chiller to charge it", blocking=True,
            ))
        if cfg.storage.charge_start_hour == cfg.storage.charge_end_hour:
            out.append(_item(
                "tes-window", Severity.warning,
                "Charge window is empty",
                "Charge start and end hours are the same.",
            ))

    return out


def blocking(items: list[ReadinessItem]) -> list[ReadinessItem]:
    return [i for i in items if i.blocking]


# --------------------------------------------------------------------------
# post-run physics QA
# --------------------------------------------------------------------------


def physics_qa(summary: dict[str, Any]) -> list[ReadinessItem]:
    """Sanity-check the results themselves, the way a reviewer would."""
    out: list[ReadinessItem] = []

    cooling_req = summary.get("coolingRequestedMwh", 0.0)
    cooling_del = summary.get("coolingDeliveredMwh", 0.0)
    heating_req = summary.get("heatingRequestedMwh", 0.0)
    heating_del = summary.get("heatingDeliveredMwh", 0.0)
    elec = summary.get("plantElectricityMwh", 0.0)

    if cooling_del <= 0 and cooling_req > 0:
        out.append(_item(
            "no-cooling", Severity.error,
            "Zero cooling delivered against a non-zero demand",
            "The chilled-water plant never ran. Check equipment availability "
            "and the operation scheme.",
        ))

    if cooling_req > 0:
        unmet = (cooling_req - cooling_del) / cooling_req
        if unmet > 0.005:
            out.append(_item(
                "unmet-cooling", Severity.warning,
                f"{unmet * 100:.1f}% of the cooling demand went unmet",
                "The plant could not keep up. Add capacity or check sequencing.",
            ))
        elif unmet < -0.005:
            # Expected: evaporators also pick up distribution pump heat.
            out.append(_item(
                "cooling-surplus", Severity.info,
                f"Chillers delivered {abs(unmet) * 100:.1f}% more than the "
                f"building asked for",
                "This is normal. The evaporators also remove the heat that the "
                "chilled-water pumps add to the loop.",
            ))

    if heating_req > 0:
        unmet_h = (heating_req - heating_del) / heating_req
        if unmet_h > 0.02:
            out.append(_item(
                "unmet-heating", Severity.warning,
                f"{unmet_h * 100:.1f}% of the heating demand went unmet",
            ))

    if elec > 0 and cooling_del > 0:
        cop = cooling_del / elec
        if cop > 10:
            out.append(_item(
                "cop-implausible", Severity.warning,
                f"System COP of {cop:.1f} looks too good",
                "Check that every parasitic (tower fans, pumps) is on the loop.",
            ))
        elif cop < 1.5:
            out.append(_item(
                "cop-low", Severity.warning,
                f"System COP of {cop:.2f} is very low",
                "Look for oversized pumps, an excessive design head, or "
                "chillers running at very low part load.",
            ))

    if elec > 0 and cooling_del <= 0 and heating_del <= 0:
        out.append(_item(
            "energy-no-output", Severity.error,
            "The plant consumed electricity but delivered nothing",
        ))

    unmet_c = summary.get("unmetCoolingHours", 0)
    if unmet_c:
        total_c = max(1, summary.get("coolingHours", 1))
        pct = unmet_c / total_c * 100
        worst = summary.get("worstChwSupplyC", 0.0)
        sp = summary.get("chwSetpointC", 0.0)
        out.append(_item(
            "unmet-hours-cooling",
            Severity.warning if pct > 0.5 else Severity.info,
            f"Chilled water missed setpoint for {unmet_c:,} hours "
            f"({pct:.1f}% of cooling hours)",
            f"Supply reached {worst:.1f} °C against a {sp:.1f} °C setpoint. "
            f"Chiller capacity falls as condenser temperature rises, so a plant "
            f"can be adequate on paper and still short on the hottest "
            f"afternoons. Add capacity, or lower the condenser setpoint.",
        ))

    unmet_h = summary.get("unmetHeatingHours", 0)
    if unmet_h:
        total_h = max(1, summary.get("heatingHours", 1))
        pct = unmet_h / total_h * 100
        out.append(_item(
            "unmet-hours-heating",
            Severity.warning if pct > 0.5 else Severity.info,
            f"Hot water missed setpoint for {unmet_h:,} hours "
            f"({pct:.1f}% of heating hours)",
        ))

    severes = summary.get("severeCount", 0)
    if severes:
        out.append(_item(
            "severe-errors", Severity.error,
            f"EnergyPlus reported {severes} severe error(s)",
            "The results below may not be trustworthy.",
        ))

    return out
