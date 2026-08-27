"""Runtime settings and engine discovery."""

from __future__ import annotations

import os
import platform
import shutil
from functools import lru_cache
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SERVER_DIR = APP_DIR.parent
APPS_DIR = SERVER_DIR.parent
REPO_ROOT = APPS_DIR.parent


class EngineNotFound(RuntimeError):
    pass


def _candidate_engine_dirs() -> list[Path]:
    """Places we look for an EnergyPlus install, best first."""
    found: list[Path] = []
    if env := os.environ.get("ENERGYPLUS_DIR"):
        found.append(Path(env))
    # A release unpacked next to us by scripts/fetch_engine.sh
    bundled = APPS_DIR / "engine"
    if bundled.is_dir():
        found.extend(sorted(bundled.glob("EnergyPlus-*"), reverse=True))
    # A build tree from this repo
    found.append(REPO_ROOT / "build" / "Products")
    # System installs
    system = platform.system()
    if system == "Darwin":
        found.extend(sorted(Path("/Applications").glob("EnergyPlus-*"), reverse=True))
    elif system == "Linux":
        found.extend(sorted(Path("/usr/local").glob("EnergyPlus-*"), reverse=True))
    elif system == "Windows":
        found.extend(sorted(Path("C:/").glob("EnergyPlusV*"), reverse=True))
    if which := shutil.which("energyplus"):
        found.append(Path(which).resolve().parent)
    return found


def _is_engine_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    exe = path / ("energyplus.exe" if platform.system() == "Windows" else "energyplus")
    return exe.exists() and (path / "Energy+.idd").exists()


class Settings:
    """Resolved once at import; every path here is absolute."""

    def __init__(self) -> None:
        self.engine_dir: Path | None = next(
            (p for p in _candidate_engine_dirs() if _is_engine_dir(p)), None
        )
        self.run_root = Path(
            os.environ.get("DHC_RUN_ROOT", str(SERVER_DIR / "var" / "runs"))
        )
        self.asset_root = Path(
            os.environ.get("DHC_ASSET_ROOT", str(SERVER_DIR / "var" / "assets"))
        )
        # Extra EPW directories, colon-separated. Lets a container mount or
        # bake in weather without depending on the surrounding repo layout.
        self.extra_weather_dirs = [
            Path(p) for p in os.environ.get("DHC_WEATHER_DIRS", "").split(":") if p
        ]
        self.max_concurrent_runs = int(os.environ.get("DHC_MAX_CONCURRENT", "2"))
        self.job_retention = int(os.environ.get("DHC_JOB_RETENTION", "50"))
        self.cors_origins = [
            o.strip()
            for o in os.environ.get(
                "DHC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if o.strip()
        ]
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.asset_root.mkdir(parents=True, exist_ok=True)

    # -- engine-derived paths -------------------------------------------

    def require_engine(self) -> Path:
        if self.engine_dir is None:
            raise EngineNotFound(
                "No EnergyPlus install found. Set ENERGYPLUS_DIR, or run "
                "`apps/scripts/fetch_engine.sh` to download one."
            )
        return self.engine_dir

    @property
    def executable(self) -> Path:
        name = "energyplus.exe" if platform.system() == "Windows" else "energyplus"
        return self.require_engine() / name

    @property
    def datasets_dir(self) -> Path:
        return self.require_engine() / "DataSets"

    @property
    def weather_dir(self) -> Path:
        return self.require_engine() / "WeatherData"

    @property
    def schema_path(self) -> Path:
        return self.require_engine() / "Energy+.schema.epJSON"

    @property
    def version(self) -> str:
        """Engine version.

        Read from the IDD header first: it is authoritative and survives the
        install being renamed, which a container image routinely does. The
        directory name and the versioned sibling binary are only fallbacks.
        """
        root = self.require_engine()

        idd = root / "Energy+.idd"
        if idd.exists():
            try:
                with idd.open("r", encoding="utf-8", errors="replace") as fh:
                    for _ in range(5):
                        line = fh.readline()
                        if not line:
                            break
                        if "IDD_Version" in line:
                            token = line.split("IDD_Version", 1)[1].strip()
                            if token and token[0].isdigit():
                                return token.split()[0]
            except OSError:
                pass

        for part in root.name.split("-"):
            if part and part[0].isdigit() and "." in part:
                return part

        for exe in sorted(root.glob("energyplus-*")):
            tail = exe.name.split("energyplus-", 1)[1]
            if tail and tail[0].isdigit():
                return tail

        return "unknown"

    @property
    def idf_version(self) -> str:
        """The two-part version EnergyPlus wants in a `Version` object."""
        return ".".join(self.version.split(".")[:2])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
