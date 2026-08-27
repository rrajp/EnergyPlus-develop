"""Weather and building-load asset store.

Assets are content-addressed files under `var/assets`.  Two kinds exist:

* `epw`  -- weather files, either bundled with the engine/repo or uploaded
* `load` -- 8,760-hour building load profiles as CSV

Load CSVs are normalised on ingest into the exact two-column, watts-valued
form EnergyPlus `Schedule:File` reads, with cooling stored as **negative**
watts (see the note in `idf/builder`).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import REPO_ROOT, get_settings

HOURS = 8760

_UNIT_TO_W = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0, "TR": 3516.85}

_COOLING_HINTS = ("cool", "chw", "chilled", "cooling load", "tr", "ton")
_HEATING_HINTS = ("heat", "hhw", "hot", "heating load")


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass
class WeatherAsset:
    id: str
    label: str
    path: str
    city: str = ""
    region: str = ""
    country: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    elevation: float = 0.0
    time_zone: float = 0.0
    source: str = "bundled"     # bundled | upload

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("path")
        return d


@dataclass
class LoadAsset:
    id: str
    label: str
    path: str
    peak_cooling_kw: float = 0.0
    peak_heating_kw: float = 0.0
    annual_cooling_mwh: float = 0.0
    annual_heating_mwh: float = 0.0
    has_heating: bool = False
    peak_cooling_hour: int = 0
    peak_heating_hour: int = 0
    source: str = "upload"      # sample | upload
    note: str = ""

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("path")
        return d


class AssetError(ValueError):
    pass


# --------------------------------------------------------------------------
# EPW metadata
# --------------------------------------------------------------------------


def read_epw_header(path: Path) -> dict[str, Any]:
    """Parse the LOCATION line of an EPW. Returns {} if it looks wrong."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return {}
    parts = [p.strip() for p in first.split(",")]
    if not parts or parts[0].upper() != "LOCATION" or len(parts) < 10:
        return {}

    def num(i: int) -> float:
        try:
            return float(parts[i])
        except (ValueError, IndexError):
            return 0.0

    return {
        "city": parts[1],
        "region": parts[2],
        "country": parts[3],
        "latitude": num(6),
        "longitude": num(7),
        "time_zone": num(8),
        "elevation": num(9),
    }


# --------------------------------------------------------------------------
# load CSV ingest
# --------------------------------------------------------------------------


def _sniff_columns(header: list[str]) -> tuple[int | None, int | None]:
    """Guess which columns hold cooling and heating, by name."""
    cool = heat = None
    for i, raw in enumerate(header):
        name = raw.strip().lower()
        if cool is None and any(h in name for h in _COOLING_HINTS):
            cool = i
        if heat is None and any(h in name for h in _HEATING_HINTS):
            heat = i
    return cool, heat


def _to_float(cell: str) -> float | None:
    cell = cell.strip().replace(",", "")
    if not cell:
        return None
    try:
        v = float(cell)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_load_csv(
    text: str,
    *,
    units: str = "kW",
    cooling_column: str | None = None,
    heating_column: str | None = None,
) -> tuple[list[float], list[float], list[str]]:
    """Return (cooling_w, heating_w, notes), each list padded/trimmed to 8760.

    Values come back as positive magnitudes in watts; the sign convention is
    applied later when the EnergyPlus CSV is written.
    """
    notes: list[str] = []
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise AssetError("The CSV is empty.")

    header: list[str] = []
    first_numeric = _to_float(rows[0][0]) is not None
    if not first_numeric:
        header = rows[0]
        rows = rows[1:]

    ci = hi = None
    if header:
        if cooling_column:
            ci = next(
                (i for i, h in enumerate(header)
                 if h.strip().lower() == cooling_column.strip().lower()), None
            )
        if heating_column:
            hi = next(
                (i for i, h in enumerate(header)
                 if h.strip().lower() == heating_column.strip().lower()), None
            )
        if ci is None or hi is None:
            gc, gh = _sniff_columns(header)
            ci = ci if ci is not None else gc
            hi = hi if hi is not None else gh

    ncols = max((len(r) for r in rows), default=0)
    if ci is None:
        # No usable header: assume the first numeric column is cooling and a
        # second numeric column, if present, is heating.
        ci = 1 if ncols > 1 and _to_float(rows[0][0]) is None else 0
        if ncols >= 2 and hi is None:
            hi = ci + 1 if ci + 1 < ncols else None
        if header:
            notes.append(
                "Could not identify columns by name; used column order "
                f"(cooling = column {ci + 1}"
                + (f", heating = column {hi + 1}" if hi is not None else "")
                + ")."
            )

    scale = _UNIT_TO_W.get(units, 1000.0)
    cooling: list[float] = []
    heating: list[float] = []
    skipped = 0
    for r in rows:
        cv = _to_float(r[ci]) if ci < len(r) else None
        if cv is None:
            skipped += 1
            continue
        cooling.append(abs(cv) * scale)
        if hi is not None and hi < len(r):
            hv = _to_float(r[hi])
            heating.append(abs(hv) * scale if hv is not None else 0.0)
        else:
            heating.append(0.0)

    if not cooling:
        raise AssetError("No numeric load values found in the CSV.")
    if skipped:
        notes.append(f"Skipped {skipped} row(s) with no numeric load value.")

    if len(cooling) > HOURS:
        notes.append(
            f"CSV had {len(cooling)} rows; used the first {HOURS} (one full year)."
        )
        cooling, heating = cooling[:HOURS], heating[:HOURS]
    elif len(cooling) < HOURS:
        notes.append(
            f"CSV had only {len(cooling)} rows; padded to {HOURS} hours with zeros."
        )
        pad = HOURS - len(cooling)
        cooling += [0.0] * pad
        heating += [0.0] * pad

    return cooling, heating, notes


#: Below this fraction of peak the distribution pumps would be throttled to a
#: minimum rather than shut off entirely.
MIN_FLOW_FRACTION = 0.15


def _flow_fraction(load_w: float, peak_w: float) -> float:
    """Distribution flow the load needs, as a fraction of design flow.

    Returning a constant 1.0 here is a trap: `LoadProfile:Plant` would then
    demand full design flow in every hour of the year, so the loop pumps never
    shut off.  On a loop whose load falls to zero for a season -- hot water in
    summer, typically -- the pump work has nowhere to go and EnergyPlus fatals
    with `CheckForRunawayPlantTemps`.  Tracking the load keeps the pumps
    honest and matches how a real variable-flow distribution system runs.
    """
    if peak_w <= 0.0 or load_w <= 0.0:
        return 0.0
    return min(1.0, max(MIN_FLOW_FRACTION, load_w / peak_w))


def write_energyplus_csv(
    path: Path, cooling_w: Iterable[float], heating_w: Iterable[float]
) -> None:
    """Write the four-column schedule file EnergyPlus reads.

    ==========  ===============================================
    column 1    cooling load, **negative** watts (heat added to
                the chilled-water loop)
    column 2    heating load, positive watts
    column 3    chilled-water distribution flow fraction, 0..1
    column 4    hot-water distribution flow fraction, 0..1
    ==========  ===============================================
    """
    cooling = [abs(c) for c in cooling_w]
    heating = [abs(h) for h in heating_w]
    peak_c = max(cooling) if cooling else 0.0
    peak_h = max(heating) if heating else 0.0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "cooling_w_negative", "heating_w_positive",
            "cooling_flow_fraction", "heating_flow_fraction",
        ])
        for c, h in zip(cooling, heating):
            w.writerow([
                f"{-c:.3f}", f"{h:.3f}",
                f"{_flow_fraction(c, peak_c):.5f}",
                f"{_flow_fraction(h, peak_h):.5f}",
            ])


# --------------------------------------------------------------------------
# sample load profiles
# --------------------------------------------------------------------------

@dataclass
class SampleSpec:
    key: str
    label: str
    peak_cooling_kw: float
    peak_heating_kw: float
    #: hour-of-day occupancy shape, 24 values 0..1
    weekday: tuple[float, ...]
    weekend_scale: float
    #: how strongly cooling tracks the summer season, 0..1
    seasonality: float
    heating_seasonality: float


_OFFICE = (0.15, 0.12, 0.12, 0.12, 0.15, 0.25, 0.45, 0.70, 0.88, 0.95, 0.98,
           1.00, 0.98, 0.97, 0.99, 1.00, 0.95, 0.80, 0.55, 0.38, 0.28, 0.22,
           0.18, 0.16)
_HOTEL = (0.55, 0.50, 0.48, 0.47, 0.48, 0.55, 0.70, 0.82, 0.80, 0.72, 0.68,
          0.70, 0.72, 0.72, 0.72, 0.74, 0.80, 0.90, 0.98, 1.00, 0.95, 0.85,
          0.72, 0.62)
_RETAIL = (0.20, 0.18, 0.18, 0.18, 0.20, 0.28, 0.40, 0.55, 0.75, 0.90, 0.97,
           1.00, 1.00, 0.98, 0.97, 0.97, 0.96, 0.94, 0.90, 0.80, 0.60, 0.40,
           0.28, 0.22)
_HOSPITAL = (0.70, 0.68, 0.66, 0.66, 0.68, 0.74, 0.84, 0.92, 0.97, 1.00, 1.00,
             0.99, 0.98, 0.98, 0.98, 0.97, 0.95, 0.92, 0.88, 0.84, 0.80, 0.77,
             0.74, 0.72)
_CAMPUS = (0.30, 0.26, 0.24, 0.24, 0.28, 0.40, 0.58, 0.78, 0.92, 0.98, 1.00,
           1.00, 0.98, 0.97, 0.96, 0.94, 0.88, 0.76, 0.62, 0.52, 0.45, 0.40,
           0.35, 0.32)

#: `seasonality` is the winter floor for cooling as a fraction of peak, and
#: `heating_seasonality` the summer floor for heating -- both keyword-passed,
#: because getting this argument order wrong silently flattens the year.
SAMPLES: tuple[SampleSpec, ...] = (
    SampleSpec("large-office", "Large Office · 3,500 kW peak",
               peak_cooling_kw=3500, peak_heating_kw=900, weekday=_OFFICE,
               weekend_scale=0.30, seasonality=0.22, heating_seasonality=0.05),
    SampleSpec("large-hotel", "Large Hotel · 2,200 kW peak",
               peak_cooling_kw=2200, peak_heating_kw=1200, weekday=_HOTEL,
               weekend_scale=0.90, seasonality=0.35, heating_seasonality=0.15),
    SampleSpec("mixed-campus", "Mixed-Use Campus · 6,000 kW peak",
               peak_cooling_kw=6000, peak_heating_kw=1800, weekday=_CAMPUS,
               weekend_scale=0.45, seasonality=0.28, heating_seasonality=0.08),
    SampleSpec("hospital", "Hospital · 4,000 kW peak",
               peak_cooling_kw=4000, peak_heating_kw=2400, weekday=_HOSPITAL,
               weekend_scale=0.92, seasonality=0.42, heating_seasonality=0.20),
    SampleSpec("retail-mall", "Retail Mall · 5,000 kW peak",
               peak_cooling_kw=5000, peak_heating_kw=700, weekday=_RETAIL,
               weekend_scale=1.00, seasonality=0.25, heating_seasonality=0.06),
)


def _season(hour_of_year: int, peak_day: int, strength: float) -> float:
    """Smooth annual sinusoid peaking on `peak_day`, floored by `strength`."""
    day = hour_of_year // 24
    phase = 2.0 * math.pi * (day - peak_day) / 365.0
    wave = (math.cos(phase) + 1.0) / 2.0          # 1.0 at peak_day, 0 opposite
    return strength + (1.0 - strength) * wave


def generate_sample(spec: SampleSpec) -> tuple[list[float], list[float]]:
    """Build a deterministic 8,760-hour profile for a sample building."""
    cooling: list[float] = []
    heating: list[float] = []
    for h in range(HOURS):
        hour_of_day = h % 24
        day = h // 24
        # 1 Jan 2001 was a Monday in the EnergyPlus default calendar; treat
        # days 5 and 6 of each week as the weekend.
        weekend = (day % 7) in (5, 6)
        occ = spec.weekday[hour_of_day]
        if weekend:
            occ *= spec.weekend_scale

        cool = spec.peak_cooling_kw * occ * _season(h, 196, spec.seasonality)
        # Heating peaks in winter, so it runs on the opposite phase.
        heat_occ = 0.4 + 0.6 * occ
        heat = (
            spec.peak_heating_kw
            * heat_occ
            * _season(h, 13, spec.heating_seasonality)
        )
        # Below a small threshold the plant would not run at all.
        cooling.append(max(0.0, cool - 0.02 * spec.peak_cooling_kw) * 1000.0)
        heating.append(max(0.0, heat - 0.05 * spec.peak_heating_kw) * 1000.0)
    return cooling, heating


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------


class AssetStore:
    def __init__(self) -> None:
        s = get_settings()
        self.root = s.asset_root
        self.weather_dir = self.root / "weather"
        self.load_dir = self.root / "loads"
        self.weather_dir.mkdir(parents=True, exist_ok=True)
        self.load_dir.mkdir(parents=True, exist_ok=True)
        self._weather: dict[str, WeatherAsset] = {}
        self._loads: dict[str, LoadAsset] = {}
        self._index = self.root / "index.json"
        self._load_index()
        self._discover_bundled()

    # -- persistence ----------------------------------------------------

    def _load_index(self) -> None:
        if not self._index.exists():
            return
        try:
            data = json.loads(self._index.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for d in data.get("weather", []):
            if Path(d["path"]).exists():
                self._weather[d["id"]] = WeatherAsset(**d)
        for d in data.get("loads", []):
            if Path(d["path"]).exists():
                self._loads[d["id"]] = LoadAsset(**d)

    def _save_index(self) -> None:
        payload = {
            "weather": [asdict(w) for w in self._weather.values()
                        if w.source == "upload"],
            "loads": [asdict(l) for l in self._loads.values()],
        }
        self._index.write_text(json.dumps(payload, indent=1))

    # -- discovery ------------------------------------------------------

    def _discover_bundled(self) -> None:
        settings = get_settings()
        roots: list[Path] = []
        try:
            roots.append(settings.weather_dir)
        except Exception:
            pass
        repo_weather = REPO_ROOT / "weather"
        if repo_weather.is_dir():
            roots.append(repo_weather)
        roots.extend(d for d in settings.extra_weather_dirs if d.is_dir())

        for root in roots:
            if not root.is_dir():
                continue
            for epw in sorted(root.glob("*.epw")):
                aid = "epw-" + hashlib.sha1(
                    str(epw.resolve()).encode()
                ).hexdigest()[:10]
                if aid in self._weather:
                    continue
                meta = read_epw_header(epw)
                label = meta.get("city") or epw.stem
                region = meta.get("region", "")
                country = meta.get("country", "")
                pretty = ", ".join(p for p in (label, region, country) if p)
                self._weather[aid] = WeatherAsset(
                    id=aid,
                    label=pretty or epw.stem,
                    path=str(epw.resolve()),
                    city=meta.get("city", ""),
                    region=region,
                    country=country,
                    latitude=meta.get("latitude", 0.0),
                    longitude=meta.get("longitude", 0.0),
                    elevation=meta.get("elevation", 0.0),
                    time_zone=meta.get("time_zone", 0.0),
                    source="bundled",
                )

        for spec in SAMPLES:
            aid = f"sample-{spec.key}"
            if aid in self._loads:
                continue
            path = self.load_dir / f"{aid}.csv"
            if not path.exists():
                cooling, heating = generate_sample(spec)
                write_energyplus_csv(path, cooling, heating)
            self._loads[aid] = self._describe_load(
                aid, spec.label, path, source="sample",
                note="Synthetic profile generated from an occupancy schedule "
                     "and an annual seasonal curve.",
            )
        self._save_index()

    # -- helpers --------------------------------------------------------

    def _describe_load(
        self, aid: str, label: str, path: Path, *, source: str, note: str = ""
    ) -> LoadAsset:
        cooling, heating = self.read_load(path)
        peak_c = max(cooling) if cooling else 0.0
        peak_h = max(heating) if heating else 0.0
        return LoadAsset(
            id=aid,
            label=label,
            path=str(path.resolve()),
            peak_cooling_kw=peak_c / 1000.0,
            peak_heating_kw=peak_h / 1000.0,
            annual_cooling_mwh=sum(cooling) / 1e6,
            annual_heating_mwh=sum(heating) / 1e6,
            has_heating=peak_h > 0.0,
            peak_cooling_hour=cooling.index(peak_c) if cooling else 0,
            peak_heating_hour=heating.index(peak_h) if heating else 0,
            source=source,
            note=note,
        )

    @staticmethod
    def read_load(path: Path) -> tuple[list[float], list[float]]:
        """Read a normalised EnergyPlus load CSV back as positive watts."""
        cooling: list[float] = []
        heating: list[float] = []
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader, None)  # header
            for row in reader:
                if len(row) < 2:
                    continue
                try:
                    cooling.append(abs(float(row[0])))
                    heating.append(abs(float(row[1])))
                except ValueError:
                    continue
        return cooling, heating

    # -- public API -----------------------------------------------------

    def list_weather(self) -> list[dict[str, Any]]:
        return sorted(
            (w.public() for w in self._weather.values()),
            key=lambda d: (d["source"] != "upload", d["label"]),
        )

    def list_loads(self) -> list[dict[str, Any]]:
        return sorted(
            (l.public() for l in self._loads.values()),
            key=lambda d: (d["source"] != "upload", d["label"]),
        )

    def weather(self, aid: str) -> WeatherAsset:
        if aid not in self._weather:
            raise AssetError(f"Unknown weather asset '{aid}'.")
        return self._weather[aid]

    def load(self, aid: str) -> LoadAsset:
        if aid not in self._loads:
            raise AssetError(f"Unknown load asset '{aid}'.")
        return self._loads[aid]

    def add_weather(self, filename: str, data: bytes) -> WeatherAsset:
        if not filename.lower().endswith(".epw"):
            raise AssetError("Weather files must have a .epw extension.")
        digest = hashlib.sha1(data).hexdigest()[:12]
        aid = f"epw-up-{digest}"
        path = self.weather_dir / f"{aid}.epw"
        path.write_bytes(data)
        meta = read_epw_header(path)
        if not meta:
            path.unlink(missing_ok=True)
            raise AssetError(
                "That file does not start with a valid EPW LOCATION header."
            )
        pretty = ", ".join(
            p for p in (meta.get("city"), meta.get("region"), meta.get("country"))
            if p
        )
        asset = WeatherAsset(
            id=aid,
            label=pretty or Path(filename).stem,
            path=str(path.resolve()),
            city=meta.get("city", ""),
            region=meta.get("region", ""),
            country=meta.get("country", ""),
            latitude=meta.get("latitude", 0.0),
            longitude=meta.get("longitude", 0.0),
            elevation=meta.get("elevation", 0.0),
            time_zone=meta.get("time_zone", 0.0),
            source="upload",
        )
        self._weather[aid] = asset
        self._save_index()
        return asset

    def add_load(
        self,
        filename: str,
        data: bytes,
        *,
        units: str = "kW",
        cooling_column: str | None = None,
        heating_column: str | None = None,
    ) -> tuple[LoadAsset, list[str]]:
        text = data.decode("utf-8-sig", errors="replace")
        cooling, heating, notes = parse_load_csv(
            text,
            units=units,
            cooling_column=cooling_column,
            heating_column=heating_column,
        )
        digest = hashlib.sha1(data).hexdigest()[:12]
        aid = f"load-{digest}"
        path = self.load_dir / f"{aid}.csv"
        write_energyplus_csv(path, cooling, heating)
        asset = self._describe_load(
            aid, Path(filename).stem, path, source="upload",
            note="; ".join(notes),
        )
        self._loads[aid] = asset
        self._save_index()
        return asset, notes

    def load_series(self, aid: str) -> tuple[list[float], list[float]]:
        return self.read_load(Path(self.load(aid).path))


_store: AssetStore | None = None


def get_store() -> AssetStore:
    global _store
    if _store is None:
        _store = AssetStore()
    return _store
