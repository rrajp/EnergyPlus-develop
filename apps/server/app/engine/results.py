"""Reads `eplusout.sql` into the shapes the UI charts and tables consume."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..models import ComplianceTier, PlantConfig, Tariff

#: EnergyPlus reports keys in upper case; these read badly through `.title()`.
_ACRONYMS = ("CHW", "HHW", "TES", "CW", "HW", "OA", "VFD", "COP")


def pretty_name(raw: str) -> str:
    """`PRIMARY CHW PUMP` -> `Primary CHW Pump`."""
    words = []
    for w in raw.split():
        upper = w.upper()
        words.append(upper if upper in _ACRONYMS else w.capitalize())
    return " ".join(words)


J_TO_KWH = 1.0 / 3_600_000.0
J_TO_MWH = 1.0 / 3_600_000_000.0
KW_PER_TR = 3.51685
HOURS = 8760


# --------------------------------------------------------------------------
# ECSBC total-system-efficiency thresholds
# --------------------------------------------------------------------------

#: Total System Efficiency limits in kW of plant input per kW of cooling
#: delivered -- the reciprocal of whole-plant COP.  These are the reference
#: values the tool ships with; they are surfaced in the UI as editable so a
#: project can pin the exact figure its authority has adopted.
TSE_LIMITS: dict[ComplianceTier, float] = {
    ComplianceTier.ecsbc: 0.24,
    ComplianceTier.super_ecsbc: 0.19,
}


@dataclass(frozen=True)
class SeriesSpec:
    """How one chart series is assembled out of report variables."""

    key: str
    label: str
    unit: str
    variables: tuple[str, ...]
    reduce: str = "sum"       # sum | mean | first
    scale: float = 1.0
    absolute: bool = False
    key_filter: str | None = None


SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("oaDrybulbC", "Outdoor air dry-bulb", "°C",
               ("Site Outdoor Air Drybulb Temperature",), "mean"),
    SeriesSpec("oaWetbulbC", "Outdoor air wet-bulb", "°C",
               ("Site Outdoor Air Wetbulb Temperature",), "mean"),
    SeriesSpec("coolingLoadKw", "Building cooling load", "kW",
               ("Plant Load Profile Heat Transfer Rate",), "sum",
               scale=0.001, absolute=True, key_filter="BUILDING COOLING LOAD"),
    SeriesSpec("heatingLoadKw", "Building heating load", "kW",
               ("Plant Load Profile Heat Transfer Rate",), "sum",
               scale=0.001, absolute=True, key_filter="BUILDING HEATING LOAD"),
    SeriesSpec("coolingDeliveredKw", "Cooling delivered", "kW",
               ("Chiller Evaporator Cooling Rate",), "sum", scale=0.001),
    SeriesSpec("chillerPowerKw", "Chiller power", "kW",
               ("Chiller Electricity Rate",), "sum", scale=0.001),
    SeriesSpec("towerPowerKw", "Tower fan power", "kW",
               ("Cooling Tower Fan Electricity Rate",), "sum", scale=0.001),
    SeriesSpec("pumpPowerKw", "Pump power", "kW",
               ("Pump Electricity Rate",), "sum", scale=0.001),
    SeriesSpec("heatPumpPowerKw", "Heat pump power", "kW",
               ("Heat Pump Electricity Rate",), "sum", scale=0.001),
    SeriesSpec("boilerHeatKw", "Boiler heat output", "kW",
               ("Boiler Heating Rate",), "sum", scale=0.001),
    SeriesSpec("heatPumpHeatKw", "Heat pump heat output", "kW",
               ("Heat Pump Load Side Heat Transfer Rate",), "sum", scale=0.001),
    SeriesSpec("towerHeatKw", "Heat rejected", "kW",
               ("Cooling Tower Heat Transfer Rate",), "sum", scale=0.001),
    # Read loop temperature at the supply node, not by averaging equipment
    # outlets: a chiller that is off still reports its outlet node, and those
    # idle values drag the average to physically impossible numbers.
    SeriesSpec("chwSupplyC", "Chilled-water supply", "°C",
               ("System Node Temperature",), "mean",
               key_filter="CHW SUPPLY OUTLET NODE"),
    SeriesSpec("cwSupplyC", "Condenser-water supply", "°C",
               ("System Node Temperature",), "mean",
               key_filter="CW SUPPLY OUTLET NODE"),
    SeriesSpec("hhwSupplyC", "Hot-water supply", "°C",
               ("System Node Temperature",), "mean",
               key_filter="HHW SUPPLY OUTLET NODE"),
    SeriesSpec("chillerPlr", "Chiller part-load ratio", "-",
               ("Chiller Part Load Ratio",), "mean"),
)

SERIES_BY_KEY = {s.key: s for s in SERIES}

#: Which raw series each derived series is built from.
DERIVED_DEPS: dict[str, set[str]] = {
    "plantPowerKw": {
        "chillerPowerKw", "towerPowerKw", "pumpPowerKw", "heatPumpPowerKw",
    },
}

#: Derived series computed from the ones above rather than read from SQL.
DERIVED: dict[str, tuple[str, str, Callable[[dict[str, list[float]], int], float]]] = {
    "plantPowerKw": (
        "Total plant electricity", "kW",
        lambda d, i: (
            d.get("chillerPowerKw", [0.0] * HOURS)[i]
            + d.get("towerPowerKw", [0.0] * HOURS)[i]
            + d.get("pumpPowerKw", [0.0] * HOURS)[i]
            + d.get("heatPumpPowerKw", [0.0] * HOURS)[i]
        ),
    ),
}


class ResultsError(RuntimeError):
    pass


# --------------------------------------------------------------------------


class ResultsReader:
    def __init__(self, sql_path: Path) -> None:
        if not sql_path.exists():
            raise ResultsError(
                "EnergyPlus produced no SQL output file; the run did not "
                "get far enough to report anything."
            )
        self.conn = sqlite3.connect(f"file:{sql_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> ResultsReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- primitives ------------------------------------------------------

    def hour_count(self) -> int:
        row = self.conn.execute(
            "select count(*) n from Time where IntervalType=1"
        ).fetchone()
        return int(row["n"]) if row else 0

    def dictionary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "select ReportDataDictionaryIndex idx, KeyValue, Name, Units "
            "from ReportDataDictionary"
        ).fetchall()

    def totals_by_key(self, variable: str) -> dict[str, float]:
        """Annual sum of a variable, broken out by its key (equipment name)."""
        rows = self.conn.execute(
            """select d.KeyValue k, sum(r.Value) v
               from ReportData r
               join ReportDataDictionary d
                 on r.ReportDataDictionaryIndex = d.ReportDataDictionaryIndex
               where d.Name = ?
               group by d.KeyValue""",
            (variable,),
        ).fetchall()
        return {r["k"]: float(r["v"]) for r in rows}

    def total(self, variable: str, key: str | None = None) -> float:
        by = self.totals_by_key(variable)
        if key is not None:
            return by.get(key, 0.0)
        return sum(by.values())

    def peak(self, variable: str) -> tuple[float, int]:
        """Largest hourly value of a variable summed across keys, and its hour."""
        rows = self.conn.execute(
            """select t.TimeIndex ti, sum(r.Value) v
               from ReportData r
               join ReportDataDictionary d
                 on r.ReportDataDictionaryIndex = d.ReportDataDictionaryIndex
               join Time t on t.TimeIndex = r.TimeIndex
               where d.Name = ? and t.IntervalType = 1
               group by t.TimeIndex order by v desc limit 1""",
            (variable,),
        ).fetchone()
        if not rows:
            return 0.0, 0
        return float(rows["v"]), int(rows["ti"]) - 1

    # -- series ----------------------------------------------------------

    def series(self, spec: SeriesSpec, hours: int) -> list[float]:
        placeholders = ",".join("?" for _ in spec.variables)
        params: list[Any] = list(spec.variables)
        where_key = ""
        if spec.key_filter:
            where_key = " and d.KeyValue = ?"
            params.append(spec.key_filter)

        agg = "avg" if spec.reduce == "mean" else "sum"
        rows = self.conn.execute(
            f"""select t.TimeIndex ti, {agg}(r.Value) v
                from ReportData r
                join ReportDataDictionary d
                  on r.ReportDataDictionaryIndex = d.ReportDataDictionaryIndex
                join Time t on t.TimeIndex = r.TimeIndex
                where d.Name in ({placeholders}){where_key}
                  and t.IntervalType = 1
                group by t.TimeIndex""",
            params,
        ).fetchall()

        out = [0.0] * hours
        for r in rows:
            i = int(r["ti"]) - 1
            if 0 <= i < hours:
                v = float(r["v"]) * spec.scale
                out[i] = abs(v) if spec.absolute else v
        return out

    def all_series(self, hours: int) -> dict[str, list[float]]:
        return self.some_series(list(SERIES_BY_KEY) + list(DERIVED), hours)

    def some_series(self, keys: Iterable[str], hours: int) -> dict[str, list[float]]:
        """Read only the requested series, plus whatever the derived ones need.

        Reading all fifteen when a chart asked for three is the difference
        between a chart that appears and a chart that spins.
        """
        wanted = set(keys)
        needed: set[str] = set()
        for k in wanted:
            if k in DERIVED:
                needed |= DERIVED_DEPS.get(k, set())
            elif k in SERIES_BY_KEY:
                needed.add(k)

        data = {k: self.series(SERIES_BY_KEY[k], hours) for k in sorted(needed)}
        for k in wanted & set(DERIVED):
            fn = DERIVED[k][2]
            data[k] = [fn(data, i) for i in range(hours)]
        return {k: v for k, v in data.items() if k in wanted}

    # -- per-equipment ---------------------------------------------------

    def equipment_breakdown(self) -> list[dict[str, Any]]:
        """Annual totals per named piece of equipment."""
        catalogue: list[tuple[str, str, str, str, float]] = [
            # (kind, energy variable, output variable, unit, J->unit scale)
            ("chiller", "Chiller Electricity Energy",
             "Chiller Evaporator Cooling Energy", "MWh", J_TO_MWH),
            ("boiler", "Boiler NaturalGas Energy",
             "Boiler Heating Energy", "MWh", J_TO_MWH),
            ("tower", "Cooling Tower Fan Electricity Energy", "", "MWh", J_TO_MWH),
            ("pump", "Pump Electricity Energy", "", "MWh", J_TO_MWH),
            ("heatpump", "Heat Pump Electricity Energy",
             "Heat Pump Load Side Heat Transfer Energy", "MWh", J_TO_MWH),
        ]
        out: list[dict[str, Any]] = []
        for kind, energy_var, output_var, unit, scale in catalogue:
            energy = self.totals_by_key(energy_var)
            output = self.totals_by_key(output_var) if output_var else {}
            for name, joules in sorted(energy.items()):
                delivered = output.get(name, 0.0) * scale
                consumed = joules * scale
                row: dict[str, Any] = {
                    "name": pretty_name(name),
                    "kind": kind,
                    "consumedMwh": round(consumed, 2),
                    "unit": unit,
                }
                if output_var:
                    row["deliveredMwh"] = round(delivered, 2)
                    row["efficiency"] = (
                        round(delivered / consumed, 3) if consumed > 0 else None
                    )
                out.append(row)
        return out

    # -- errors ----------------------------------------------------------

    def error_counts(self) -> dict[str, Any]:
        try:
            rows = self.conn.execute(
                "select ErrorType, count(*) n from Errors group by ErrorType"
            ).fetchall()
        except sqlite3.DatabaseError:
            return {"warnings": 0, "severes": 0}
        counts = {int(r["ErrorType"]): int(r["n"]) for r in rows}
        # EnergyPlus error types: 0 warning, 1 severe, 2 fatal
        return {
            "warnings": counts.get(0, 0),
            "severes": counts.get(1, 0) + counts.get(2, 0),
        }


# --------------------------------------------------------------------------
# top-level assembly
# --------------------------------------------------------------------------


def _month_index(hour: int) -> int:
    """0-based month for an hour-of-year, non-leap."""
    cum = (0, 744, 1416, 2160, 2880, 3624, 4344,
           5088, 5832, 6552, 7296, 8016, 8760)
    for m in range(12):
        if cum[m] <= hour < cum[m + 1]:
            return m
    return 11


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def summarise(
    run_dir: Path, cfg: PlantConfig, *, error_counts: dict[str, int] | None = None
) -> dict[str, Any]:
    """Build the full results payload for one completed run."""
    with ResultsReader(run_dir / "eplusout.sql") as r:
        hours = r.hour_count() or HOURS
        data = r.all_series(hours)

        cooling_delivered = r.total("Chiller Evaporator Cooling Energy") * J_TO_MWH
        cooling_requested = r.total("Plant Load Profile Cooling Energy") * J_TO_MWH
        heating_requested = r.total("Plant Load Profile Heating Energy") * J_TO_MWH
        boiler_heat = r.total("Boiler Heating Energy") * J_TO_MWH
        hp_heat = r.total("Heat Pump Load Side Heat Transfer Energy") * J_TO_MWH

        chiller_e = r.total("Chiller Electricity Energy") * J_TO_MWH
        tower_e = r.total("Cooling Tower Fan Electricity Energy") * J_TO_MWH
        pump_e = r.total("Pump Electricity Energy") * J_TO_MWH
        hp_e = r.total("Heat Pump Electricity Energy") * J_TO_MWH
        boiler_aux_e = r.total("Boiler Ancillary Electricity Energy") * J_TO_MWH
        boiler_elec = r.total("Boiler Electricity Energy") * J_TO_MWH

        gas = r.total("Boiler NaturalGas Energy") * J_TO_MWH
        propane = r.total("Boiler Propane Energy") * J_TO_MWH
        oil = (
            r.total("Boiler FuelOilNo1 Energy") + r.total("Boiler FuelOilNo2 Energy")
        ) * J_TO_MWH

        equipment = r.equipment_breakdown()
        errors = error_counts or r.error_counts()

    plant_elec = chiller_e + tower_e + pump_e + hp_e + boiler_aux_e + boiler_elec
    heating_delivered = boiler_heat + hp_heat

    peak_plant_kw = max(data["plantPowerKw"], default=0.0)
    peak_plant_hour = (
        data["plantPowerKw"].index(peak_plant_kw) if peak_plant_kw else 0
    )
    peak_cooling_kw = max(data["coolingLoadKw"], default=0.0)
    peak_cooling_hour = (
        data["coolingLoadKw"].index(peak_cooling_kw) if peak_cooling_kw else 0
    )
    peak_heating_kw = max(data["heatingLoadKw"], default=0.0)
    peak_heating_hour = (
        data["heatingLoadKw"].index(peak_heating_kw) if peak_heating_kw else 0
    )

    # Unmet hours: the metric that annual totals hide. Distribution pump heat
    # can make annual "delivered" exceed annual "asked for" even while the
    # plant misses setpoint on the hottest afternoons, so count the hours the
    # loop actually drifted instead of trusting the totals.
    UNMET_TOLERANCE_K = 1.0
    chw_setpoint = cfg.chw_loop.supply_setpoint_c
    chw_temps = data.get("chwSupplyC", [])
    cooling_hours = [
        i for i, v in enumerate(data.get("coolingLoadKw", [])) if v > 0.0
    ]
    unmet_cooling_hours = sum(
        1 for i in cooling_hours
        if i < len(chw_temps) and chw_temps[i] > chw_setpoint + UNMET_TOLERANCE_K
    )
    worst_chw_c = max(
        (chw_temps[i] for i in cooling_hours if i < len(chw_temps)), default=0.0
    )

    hhw_setpoint = cfg.hhw_loop.supply_setpoint_c
    hhw_temps = data.get("hhwSupplyC", [])
    heating_hours = [
        i for i, v in enumerate(data.get("heatingLoadKw", [])) if v > 0.0
    ]
    unmet_heating_hours = sum(
        1 for i in heating_hours
        if i < len(hhw_temps) and hhw_temps[i] < hhw_setpoint - UNMET_TOLERANCE_K
    )

    system_cop = cooling_delivered / plant_elec if plant_elec > 0 else 0.0
    tse = plant_elec / cooling_delivered if cooling_delivered > 0 else None

    tier = cfg.compliance_tier
    limit = TSE_LIMITS[tier]
    compliant = tse is not None and tse <= limit
    best_tier = None
    if tse is not None:
        for t in (ComplianceTier.super_ecsbc, ComplianceTier.ecsbc):
            if tse <= TSE_LIMITS[t]:
                best_tier = t.value
                break

    # ---- monthly rollup ------------------------------------------------
    monthly: list[dict[str, Any]] = []
    for m in range(12):
        acc = {
            "month": MONTHS[m],
            "coolingMwh": 0.0,
            "heatingMwh": 0.0,
            "electricityMwh": 0.0,
            "peakKw": 0.0,
        }
        monthly.append(acc)
    for h in range(hours):
        m = _month_index(h)
        acc = monthly[m]
        acc["coolingMwh"] += data["coolingDeliveredKw"][h] / 1000.0
        acc["heatingMwh"] += (
            data["boilerHeatKw"][h] + data["heatPumpHeatKw"][h]
        ) / 1000.0
        p = data["plantPowerKw"][h]
        acc["electricityMwh"] += p / 1000.0
        acc["peakKw"] = max(acc["peakKw"], p)
    for acc in monthly:
        for k in ("coolingMwh", "heatingMwh", "electricityMwh", "peakKw"):
            acc[k] = round(acc[k], 2)

    # ---- cost ------------------------------------------------------------
    t = cfg.tariff
    energy_cost = plant_elec * 1000.0 * t.energy_charge
    demand_cost = peak_plant_kw * t.demand_charge * 12.0
    fuel_cost = (gas + propane + oil) * 1000.0 * t.gas_charge_per_kwh
    total_cost = energy_cost + demand_cost + fuel_cost

    summary = {
        "coolingDeliveredMwh": round(cooling_delivered, 1),
        "coolingRequestedMwh": round(cooling_requested, 1),
        "heatingDeliveredMwh": round(heating_delivered, 1),
        "heatingRequestedMwh": round(heating_requested, 1),
        "plantElectricityMwh": round(plant_elec, 1),
        "fuelMwh": round(gas + propane + oil, 1),
        "systemCop": round(system_cop, 3),
        "chillerCop": round(cooling_delivered / chiller_e, 3) if chiller_e else 0.0,
        "boilerEfficiency": round(boiler_heat / gas, 3) if gas else None,
        "peakPlantKw": round(peak_plant_kw, 1),
        "peakPlantHour": peak_plant_hour,
        "peakCoolingKw": round(peak_cooling_kw, 1),
        "peakCoolingHour": peak_cooling_hour,
        "peakCoolingTr": round(peak_cooling_kw / KW_PER_TR, 1),
        "peakHeatingKw": round(peak_heating_kw, 1),
        "peakHeatingHour": peak_heating_hour,
        "hours": hours,
        "unmetCoolingHours": unmet_cooling_hours,
        "unmetHeatingHours": unmet_heating_hours,
        "coolingHours": len(cooling_hours),
        "heatingHours": len(heating_hours),
        "worstChwSupplyC": round(worst_chw_c, 2),
        "chwSetpointC": chw_setpoint,
        "warningCount": errors.get("warnings", 0),
        "severeCount": errors.get("severes", 0),
    }

    end_use = {
        "chillers": round(chiller_e, 1),
        "towerFans": round(tower_e, 1),
        "pumps": round(pump_e, 1),
        "heatPumps": round(hp_e, 1),
        "boilerAncillary": round(boiler_aux_e + boiler_elec, 1),
    }
    fuel_mix = {
        "naturalGas": round(gas, 1),
        "propane": round(propane, 1),
        "fuelOil": round(oil, 1),
    }

    return {
        "summary": summary,
        "endUse": end_use,
        "fuelMix": fuel_mix,
        "equipment": equipment,
        "monthly": monthly,
        "compliance": {
            "tier": tier.value,
            "metric": "Total System Efficiency",
            "unit": "kW/kW",
            "value": round(tse, 4) if tse is not None else None,
            "limit": limit,
            "compliant": compliant,
            "bestTierMet": best_tier,
            "systemCop": round(system_cop, 3),
            "note": (
                "Total System Efficiency is whole-plant electrical input per "
                "unit of cooling delivered — chillers, tower fans and every "
                "pump included. It is the reciprocal of system COP. Confirm "
                "the limit against the code edition your project is filed "
                "under before relying on the verdict."
            ),
        },
        "cost": {
            "currency": t.currency,
            "energy": round(energy_cost),
            "demand": round(demand_cost),
            "fuel": round(fuel_cost),
            "total": round(total_cost),
        },
        "seriesMeta": [
            {"key": s.key, "label": s.label, "unit": s.unit} for s in SERIES
        ] + [
            {"key": k, "label": lbl, "unit": u}
            for k, (lbl, u, _) in DERIVED.items()
        ],
    }


def load_series(
    run_dir: Path, keys: Iterable[str], freq: str = "hourly"
) -> dict[str, Any]:
    """Fetch selected series, optionally rolled up to daily or monthly."""
    wanted = [k for k in keys if k in SERIES_BY_KEY or k in DERIVED]
    if not wanted:
        raise ResultsError("No known series requested.")

    with ResultsReader(run_dir / "eplusout.sql") as r:
        hours = r.hour_count() or HOURS
        data = r.some_series(wanted, hours)

    out: dict[str, list[float]] = {}
    for k in wanted:
        vals = data.get(k, [0.0] * hours)
        if freq == "daily":
            vals = [
                sum(vals[d * 24:(d + 1) * 24]) / 24.0
                for d in range(hours // 24)
            ]
        elif freq == "monthly":
            buckets: list[list[float]] = [[] for _ in range(12)]
            for h, v in enumerate(vals):
                buckets[_month_index(h)].append(v)
            vals = [sum(b) / len(b) if b else 0.0 for b in buckets]
        out[k] = [round(v, 3) for v in vals]

    return {"frequency": freq, "length": len(next(iter(out.values()))), "series": out}
