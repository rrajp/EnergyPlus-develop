"""Equipment catalog mined from the EnergyPlus reference DataSets.

EnergyPlus ships several hundred manufacturer-derived chiller models in
`DataSets/Chillers.idf` and `DataSets/AirCooledChiller.idf`, each with its
own CAPFT / EIRFT / EIRFPLR curve set, plus boiler efficiency curves in
`DataSets/Boilers.idf`.  Rather than hand-copy a handful of coefficients we
parse those files and expose the whole library to the UI.

Everything is cached after first parse; the files are static per install.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterator

from ..config import get_settings

# --------------------------------------------------------------------------
# a small, forgiving IDF reader
# --------------------------------------------------------------------------

_COMMENT = re.compile(r"!.*$", re.MULTILINE)


def parse_idf(text: str) -> Iterator[tuple[str, list[str]]]:
    """Yield `(object_type, fields)` for every object in an IDF string.

    Fields are returned as raw strings with surrounding whitespace stripped;
    the first field is the object's name for every type we care about.
    """
    text = _COMMENT.sub("", text)
    for chunk in text.split(";"):
        parts = [p.strip() for p in chunk.split(",")]
        parts = [p for p in parts if p != ""]
        if len(parts) < 2:
            continue
        yield parts[0], parts[1:]


def _floats(fields: list[str], start: int, count: int) -> list[float]:
    out: list[float] = []
    for i in range(start, start + count):
        try:
            out.append(float(fields[i]))
        except (IndexError, ValueError):
            out.append(0.0)
    return out


# --------------------------------------------------------------------------
# curve records
# --------------------------------------------------------------------------

# (curve type -> number of coefficients, number of independent variables)
_CURVE_SHAPE = {
    "Curve:Linear": (2, 1),
    "Curve:Quadratic": (3, 1),
    "Curve:Cubic": (4, 1),
    "Curve:Quartic": (5, 1),
    "Curve:Biquadratic": (6, 2),
    "Curve:Bicubic": (10, 2),
}


@dataclass(frozen=True)
class Curve:
    name: str
    type: str
    coefficients: list[float]
    limits: list[float]
    """[min_x, max_x] or [min_x, max_x, min_y, max_y]."""

    def to_epjson(self) -> dict[str, Any]:
        n = len(self.coefficients)
        obj: dict[str, Any] = {
            f"coefficient{i + 1}_{tag}": c
            for i, (c, tag) in enumerate(zip(self.coefficients, _COEF_TAGS[self.type]))
        }
        keys = ("minimum_value_of_x", "maximum_value_of_x",
                "minimum_value_of_y", "maximum_value_of_y")
        for key, val in zip(keys, self.limits):
            obj[key] = val
        assert n  # coefficients are never empty for a parsed curve
        return obj


_COEF_TAGS = {
    "Curve:Linear": ["constant", "x"],
    "Curve:Quadratic": ["constant", "x", "x_2"],
    "Curve:Cubic": ["constant", "x", "x_2", "x_3"],
    "Curve:Quartic": ["constant", "x", "x_2", "x_3", "x_4"],
    "Curve:Biquadratic": ["constant", "x", "x_2", "y", "y_2", "x_y"],
    "Curve:Bicubic": [
        "constant", "x", "x_2", "y", "y_2", "x_y",
        "x_3", "y_3", "x_2_y", "x_y_2",
    ],
}


def _curve_from_fields(ctype: str, fields: list[str]) -> Curve | None:
    shape = _CURVE_SHAPE.get(ctype)
    if shape is None:
        return None
    ncoef, nvars = shape
    name = fields[0]
    coefs = _floats(fields, 1, ncoef)
    limits = _floats(fields, 1 + ncoef, nvars * 2)
    return Curve(name=name, type=ctype, coefficients=coefs, limits=limits)


# --------------------------------------------------------------------------
# catalog entries
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChillerEntry:
    key: str
    label: str
    manufacturer: str
    model_object: str          # Chiller:Electric:EIR | ...ReformulatedEIR
    condenser: str             # WaterCooled | AirCooled
    capacity_w: float
    reference_cop: float
    ref_chw_leaving_c: float
    ref_cond_entering_c: float
    min_plr: float
    max_plr: float
    optimum_plr: float
    min_unloading: float
    curves: dict[str, Curve] = field(default_factory=dict)

    @property
    def capacity_kw(self) -> float:
        return self.capacity_w / 1000.0

    @property
    def capacity_tr(self) -> float:
        return self.capacity_w / 3516.85

    def public(self) -> dict[str, Any]:
        """The shape the UI's catalog picker consumes."""
        return {
            "key": self.key,
            "label": self.label,
            "manufacturer": self.manufacturer,
            "modelObject": self.model_object,
            "condenser": self.condenser,
            "capacityKw": round(self.capacity_kw, 1),
            "capacityTr": round(self.capacity_tr, 1),
            "referenceCop": self.reference_cop,
            "refChwLeavingC": self.ref_chw_leaving_c,
            "refCondEnteringC": self.ref_cond_entering_c,
            "minPlr": self.min_plr,
            "maxPlr": self.max_plr,
            "optimumPlr": self.optimum_plr,
        }


@dataclass(frozen=True)
class BoilerCurveEntry:
    key: str
    label: str
    curve: Curve
    nominal_efficiency: float
    efficiency_basis: str
    note: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "nominalEfficiency": self.nominal_efficiency,
            "efficiencyBasis": self.efficiency_basis,
            "note": self.note,
        }


# The dataset comments in Boilers.idf spell out the intended pairing of curve,
# nominal efficiency and temperature-evaluation variable.  Encode that here.
_BOILER_CURVE_META = {
    "CondensingBoilerEff": (0.89, "EnteringBoiler",
                            "Gas-fired condensing boiler (Viessmann-derived)"),
    "NonCondensingBoilerEff": (0.80, "LeavingBoiler",
                               "Low-temperature gas-fired, non-condensing"),
    "Circa1975HighTempBoiler": (0.76, "LeavingBoiler",
                                "Circa-1975 high-temperature boiler"),
    "Circa1983MidTempBoiler": (0.78, "LeavingBoiler",
                               "Circa-1983 mid-temperature boiler"),
    "NewerLowTempBoiler": (0.83, "LeavingBoiler",
                           "Modern low-temperature boiler"),
}


# --------------------------------------------------------------------------
# manufacturer / label extraction
# --------------------------------------------------------------------------

_NAME_RE = re.compile(
    r"^ElectricEIRChiller\s+(?P<mfr>[A-Za-z][A-Za-z0-9&.]*)\s*(?P<rest>.*)$"
)


def _describe(raw_name: str) -> tuple[str, str]:
    """Split a dataset chiller name into (manufacturer, human label)."""
    m = _NAME_RE.match(raw_name)
    if not m:
        return "Reference", raw_name
    mfr = m.group("mfr")
    rest = m.group("rest").strip()
    return mfr, f"{mfr} {rest}".strip()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# --------------------------------------------------------------------------
# parsing the dataset files
# --------------------------------------------------------------------------


def _read(path_name: str) -> str:
    path = get_settings().datasets_dir / path_name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


# Field indices into a Chiller:Electric:EIR object (after the name).
# 0 capacity, 1 COP, 2 ref CHW leaving, 3 ref cond entering,
# 4 CHW flow, 5 cond flow, 6 CAPFT, 7 EIRFT, 8 EIRFPLR,
# 9 min PLR, 10 max PLR, 11 optimum PLR, 12 min unloading
_EIR_FIELDS = {
    "capacity": 0, "cop": 1, "chwt": 2, "cwt": 3,
    "capft": 6, "eirft": 7, "eirfplr": 8,
    "min_plr": 9, "max_plr": 10, "opt_plr": 11, "min_unload": 12,
}
# Chiller:Electric:ReformulatedEIR has no separate condenser-flow field
# ordering difference that matters to us at these indices.
_REFORM_FIELDS = dict(_EIR_FIELDS)


@lru_cache(maxsize=1)
def _parse_datasets() -> tuple[dict[str, ChillerEntry], dict[str, BoilerCurveEntry]]:
    curves: dict[str, Curve] = {}
    raw_chillers: list[tuple[str, list[str], str]] = []

    for filename, condenser_hint in (
        ("Chillers.idf", None),
        ("AirCooledChiller.idf", "AirCooled"),
    ):
        text = _read(filename)
        if not text:
            continue
        for otype, fields in parse_idf(text):
            if otype in _CURVE_SHAPE:
                c = _curve_from_fields(otype, fields)
                if c:
                    curves[c.name.upper()] = c
            elif otype in ("Chiller:Electric:EIR", "Chiller:Electric:ReformulatedEIR"):
                raw_chillers.append((otype, fields, condenser_hint))

    chillers: dict[str, ChillerEntry] = {}
    for otype, fields, condenser_hint in raw_chillers:
        name = fields[0]
        body = fields[1:]
        idx = _EIR_FIELDS if otype.endswith("EIR") else _REFORM_FIELDS

        def f(key: str) -> str:
            i = idx[key]
            return body[i] if i < len(body) else ""

        def fnum(key: str, default: float) -> float:
            try:
                return float(f(key))
            except ValueError:
                return default

        capacity = fnum("capacity", 0.0)
        cop = fnum("cop", 0.0)
        if capacity <= 0 or cop <= 0:
            continue

        # Condenser type is a named field late in the object; find it by value
        # rather than index, since the two chiller objects differ in length.
        condenser = condenser_hint or (
            "AirCooled" if "AirCooled" in body else "WaterCooled"
        )

        entry_curves: dict[str, Curve] = {}
        for role in ("capft", "eirft", "eirfplr"):
            cname = f(role).upper()
            if cname in curves:
                entry_curves[role] = curves[cname]
        if len(entry_curves) != 3:
            continue  # incomplete curve set is unusable

        mfr, label = _describe(name)
        key = _slug(name)
        chillers[key] = ChillerEntry(
            key=key,
            label=label,
            manufacturer=mfr,
            model_object=otype,
            condenser=condenser,
            capacity_w=capacity,
            reference_cop=cop,
            ref_chw_leaving_c=fnum("chwt", 6.67),
            ref_cond_entering_c=fnum("cwt", 29.44),
            min_plr=fnum("min_plr", 0.1),
            max_plr=fnum("max_plr", 1.0),
            optimum_plr=fnum("opt_plr", 1.0),
            min_unloading=fnum("min_unload", 0.1),
            curves=entry_curves,
        )

    boilers: dict[str, BoilerCurveEntry] = {}
    for otype, fields in parse_idf(_read("Boilers.idf")):
        if otype not in _CURVE_SHAPE:
            continue
        c = _curve_from_fields(otype, fields)
        if c is None or c.name not in _BOILER_CURVE_META:
            continue
        eff, basis, note = _BOILER_CURVE_META[c.name]
        boilers[_slug(c.name)] = BoilerCurveEntry(
            key=_slug(c.name),
            label=c.name,
            curve=c,
            nominal_efficiency=eff,
            efficiency_basis=basis,
            note=note,
        )

    return chillers, boilers


def chiller_catalog() -> dict[str, ChillerEntry]:
    return _parse_datasets()[0]


def boiler_catalog() -> dict[str, BoilerCurveEntry]:
    return _parse_datasets()[1]


def get_chiller(key: str | None) -> ChillerEntry | None:
    if not key:
        return None
    return chiller_catalog().get(key)


def get_boiler_curve(key: str | None) -> BoilerCurveEntry | None:
    if not key:
        return None
    return boiler_catalog().get(key)


# --------------------------------------------------------------------------
# generic fallbacks, used when the user picks "Custom (no catalog model)"
# --------------------------------------------------------------------------

def _pick_generic(condenser: str, target_kw: float) -> ChillerEntry | None:
    """Nearest-capacity catalog entry, used to seed custom chillers.

    Choosing a real reference machine of a similar size gives far better
    part-load behaviour than a flat generic curve, and keeps the physics
    defensible.
    """
    pool = [e for e in chiller_catalog().values() if e.condenser == condenser]
    if not pool:
        return None
    return min(pool, key=lambda e: abs(e.capacity_kw - target_kw))


def curves_for_chiller(
    catalog_key: str | None, condenser: str, capacity_kw: float
) -> tuple[dict[str, Curve], ChillerEntry | None]:
    """Resolve the curve set for a chiller, falling back to a size match."""
    entry = get_chiller(catalog_key)
    if entry is None:
        entry = _pick_generic(condenser, capacity_kw)
    if entry is None:
        return {}, None
    return entry.curves, entry


def catalog_payload() -> dict[str, Any]:
    """Everything the UI needs to render its equipment pickers."""
    chillers = chiller_catalog()
    water = sorted(
        (e for e in chillers.values() if e.condenser == "WaterCooled"),
        key=lambda e: e.capacity_kw,
    )
    air = sorted(
        (e for e in chillers.values() if e.condenser == "AirCooled"),
        key=lambda e: e.capacity_kw,
    )
    return {
        "chillers": {
            "waterCooled": [e.public() for e in water],
            "airCooled": [e.public() for e in air],
        },
        "boilerCurves": [e.public() for e in boiler_catalog().values()],
        "manufacturers": sorted({e.manufacturer for e in chillers.values()}),
        "counts": {"waterCooled": len(water), "airCooled": len(air)},
    }
