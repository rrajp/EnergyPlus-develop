"""Job registry, run supervision and event fan-out.

One job owns one directory under `var/runs/<id>/` holding the generated
epJSON, the EnergyPlus output files, and the parsed results.  Runs happen in a
child process (see `worker.py`); this module supervises it, turns its NDJSON
into events, and fans those out to every WebSocket subscriber.

Late subscribers are not penalised: every event is kept in a bounded replay
buffer, so a client that connects mid-run still receives the whole story.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from ..assets import AssetError, get_store
from ..checks import blocking, physics_qa, readiness
from ..config import SERVER_DIR, get_settings
from ..idf.builder import PlantBuilder
from ..models import (
    JobState,
    JobSummary,
    PlantConfig,
    ReadinessItem,
)
from .results import ResultsError, summarise

MAX_LOG_LINES = 4000
MAX_EVENTS = 6000
MAX_TELEMETRY = 9000


class JobError(RuntimeError):
    pass


class BlockedError(JobError):
    """Raised when readiness checks refuse to let a run start."""

    def __init__(self, items: list[ReadinessItem]) -> None:
        super().__init__("The configuration is not ready to simulate.")
        self.items = items


@dataclass
class Job:
    id: str
    config: PlantConfig
    run_dir: Path
    created_at: float
    state: JobState = JobState.queued
    progress: float = 0.0
    phase: str = "Queued"
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    readiness: list[ReadinessItem] = field(default_factory=list)
    schematic: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    results: dict[str, Any] | None = None
    qa: list[ReadinessItem] = field(default_factory=list)

    log: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    telemetry: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_TELEMETRY)
    )
    events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_EVENTS)
    )
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    telemetry_meta: list[dict[str, str]] = field(default_factory=list)

    _proc: Any = None
    _task: asyncio.Task | None = None
    _cancelled: bool = False

    # -- views ----------------------------------------------------------

    def summary(self) -> JobSummary:
        counts = (self.results or {}).get("summary", {})
        return JobSummary(
            id=self.id,
            state=self.state,
            progress=self.progress,
            phase=self.phase,
            plant_name=self.config.name,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
            warnings=counts.get("warningCount", 0),
            severes=counts.get("severeCount", 0),
        )

    def snapshot(self) -> dict[str, Any]:
        """Everything a fresh subscriber needs to render the current state."""
        return {
            "t": "snapshot",
            "job": self.summary().model_dump(mode="json"),
            # The config travels with the snapshot so a shared run link can
            # rebuild the whole screen, diagram included, from the URL alone.
            "config": self.config.model_dump(mode="json"),
            "readiness": [r.model_dump(mode="json") for r in self.readiness],
            "diagnostics": self.diagnostics,
            "schematic": self.schematic,
            "telemetryMeta": self.telemetry_meta,
            "log": list(self.log)[-400:],
            "telemetry": list(self.telemetry),
            "results": self.results,
            "qa": [r.model_dump(mode="json") for r in self.qa],
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: deque[str] = deque()
        self._semaphore: asyncio.Semaphore | None = None

    def _sem(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(get_settings().max_concurrent_runs)
        return self._semaphore

    # -- registry -------------------------------------------------------

    def get(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            revived = self._rehydrate(job_id)
            if revived is None:
                raise JobError(f"Unknown job '{job_id}'.")
            self._jobs[job_id] = revived
            self._order.append(job_id)
        return self._jobs[job_id]

    def _rehydrate(self, job_id: str) -> Job | None:
        """Rebuild a finished job from its run directory.

        The registry is in memory, but the artifacts are on disk. Without this
        a server restart would break every shared run link and every result the
        user had not yet downloaded.
        """
        if not job_id.isalnum():
            return None  # never let an id escape the run root
        run_dir = get_settings().run_root / job_id
        config_path = run_dir / "config.json"
        if not run_dir.is_dir() or not config_path.exists():
            return None

        try:
            config = PlantConfig.model_validate_json(config_path.read_text())
        except Exception:
            return None

        created = config_path.stat().st_mtime
        job = Job(id=job_id, config=config, run_dir=run_dir, created_at=created)

        err = run_dir / "eplusout.err"
        if err.exists():
            tail = err.read_text(errors="replace").splitlines()[-200:]
            job.log.extend(tail)

        try:
            built = PlantBuilder(
                config,
                version=get_settings().idf_version,
                load_csv=None,
                has_heating_column=config.load is not None,
            ).build()
            job.schematic = built.schematic
            job.diagnostics = built.diagnostics
        except Exception:
            pass

        if (run_dir / "eplusout.sql").exists():
            try:
                job.results = summarise(run_dir, config)
                job.qa = physics_qa(job.results["summary"])
                job.state = JobState.complete
                job.phase = "Complete"
                job.progress = 1.0
                job.finished_at = (run_dir / "eplusout.sql").stat().st_mtime
                return job
            except Exception as exc:
                job.error = f"Could not read the stored results: {exc}"
        else:
            job.error = "This run produced no results."
        job.state = JobState.failed
        job.phase = "Failed"
        return job

    def list(self) -> list[JobSummary]:
        return [self._jobs[i].summary() for i in reversed(self._order)]

    def _evict(self) -> None:
        keep = get_settings().job_retention
        while len(self._order) > keep:
            old = self._order.popleft()
            job = self._jobs.pop(old, None)
            if job and job.state in (
                JobState.complete, JobState.failed, JobState.cancelled
            ):
                shutil.rmtree(job.run_dir, ignore_errors=True)

    # -- events ---------------------------------------------------------

    def publish(self, job: Job, event: dict[str, Any]) -> None:
        job.events.append(event)
        dead: list[asyncio.Queue] = []
        for q in job.subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            job.subscribers.discard(q)

    @contextlib.contextmanager
    def subscribe(self, job: Job):
        q: asyncio.Queue = asyncio.Queue(maxsize=2048)
        job.subscribers.add(q)
        try:
            yield q
        finally:
            job.subscribers.discard(q)

    def _set_state(
        self, job: Job, state: JobState, phase: str, progress: float | None = None
    ) -> None:
        job.state = state
        job.phase = phase
        if progress is not None:
            job.progress = progress
        self.publish(job, {
            "t": "state",
            "state": state.value,
            "phase": phase,
            "progress": job.progress,
        })

    # -- creation -------------------------------------------------------

    def create(self, config: PlantConfig, *, dry_run: bool = False) -> Job:
        settings = get_settings()
        settings.require_engine()
        store = get_store()

        load = None
        weather_path: Path | None = None
        try:
            if config.load is not None:
                load = store.load(config.load.asset_id)
            if config.weather is not None:
                weather_path = Path(store.weather(config.weather.asset_id).path)
        except AssetError as exc:
            raise JobError(str(exc)) from exc

        items = readiness(config, load, weather_path is not None)
        blockers = blocking(items)
        if blockers:
            raise BlockedError(items)

        job_id = uuid.uuid4().hex[:12]
        run_dir = settings.run_root / job_id
        run_dir.mkdir(parents=True, exist_ok=True)

        job = Job(
            id=job_id,
            config=config,
            run_dir=run_dir,
            created_at=time.time(),
            readiness=items,
        )

        assert load is not None and weather_path is not None  # readiness proved it
        builder = PlantBuilder(
            config,
            version=settings.idf_version,
            load_csv=Path(load.path),
            peak_cooling_w=load.peak_cooling_kw * 1000.0,
            peak_heating_w=load.peak_heating_kw * 1000.0,
            has_heating_column=load.has_heating,
        )
        built = builder.build()
        job.schematic = built.schematic
        job.diagnostics = built.diagnostics
        (run_dir / "model.epJSON").write_text(json.dumps(built.epjson, indent=1))
        (run_dir / "config.json").write_text(
            config.model_dump_json(indent=1)
        )

        job.telemetry_meta = _telemetry_spec(built.schematic)
        (run_dir / "job.json").write_text(json.dumps({
            "engine_dir": str(settings.require_engine()),
            "weather": str(weather_path),
            "telemetry": job.telemetry_meta,
            "stream_telemetry": config.options.stream_telemetry,
            "telemetry_stride_hours": config.options.telemetry_stride_hours,
        }, indent=1))

        self._jobs[job_id] = job
        self._order.append(job_id)
        self._evict()

        if dry_run:
            job.state = JobState.complete
            job.phase = "Model generated (dry run)"
            job.progress = 1.0
            job.finished_at = time.time()
        else:
            job._task = asyncio.create_task(self._execute(job))
        return job

    # -- execution ------------------------------------------------------

    async def _execute(self, job: Job) -> None:
        try:
            async with self._sem():
                if job._cancelled:
                    self._set_state(job, JobState.cancelled, "Cancelled")
                    return
                await self._run_process(job)
        except asyncio.CancelledError:
            self._set_state(job, JobState.cancelled, "Cancelled")
            raise
        except Exception as exc:  # surfaced to the client, never swallowed
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            self._set_state(job, JobState.failed, "Failed")
            self.publish(job, {"t": "error", "message": job.error})

    async def _run_process(self, job: Job) -> None:
        job.started_at = time.time()
        self._set_state(job, JobState.preparing, "Starting EnergyPlus", 0.0)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.engine.worker", str(job.run_dir),
            cwd=str(SERVER_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        job._proc = proc
        self._set_state(job, JobState.running, "Running annual simulation")

        ok = False
        exit_code = -1
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                job.log.append(line)
                self.publish(job, {"t": "log", "line": line})
                continue

            kind = msg.get("t")
            if kind == "progress":
                job.progress = float(msg.get("pct", 0.0))
                self.publish(job, {"t": "progress", "progress": job.progress})
            elif kind == "log":
                text = msg.get("line", "")
                job.log.append(text)
                self.publish(job, {"t": "log", "line": text})
            elif kind == "telemetry":
                job.telemetry.append(msg)
                self.publish(job, msg)
            elif kind == "telemetry_ready":
                self.publish(job, msg)
            elif kind == "result":
                ok = bool(msg.get("ok"))
                exit_code = int(msg.get("exit_code", -1))
                if msg.get("error"):
                    job.error = msg["error"]

        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") \
            if proc.stderr else ""
        await proc.wait()

        if job._cancelled:
            job.finished_at = time.time()
            self._set_state(job, JobState.cancelled, "Cancelled")
            return

        if not ok:
            job.error = job.error or (
                stderr.strip()[-2000:]
                or f"EnergyPlus exited with code {exit_code}."
            )
            job.finished_at = time.time()
            self._set_state(job, JobState.failed, "Simulation failed")
            self.publish(job, {
                "t": "error",
                "message": job.error,
                "log": list(job.log)[-120:],
            })
            return

        # ---- parse ------------------------------------------------------
        self._set_state(job, JobState.parsing, "Reading results", 1.0)
        try:
            results = await asyncio.to_thread(
                summarise, job.run_dir, job.config
            )
        except Exception as exc:
            job.error = f"Could not read the results: {exc}"
            job.finished_at = time.time()
            self._set_state(job, JobState.failed, "Result parsing failed")
            self.publish(job, {"t": "error", "message": job.error})
            return

        job.results = results
        job.qa = physics_qa(results["summary"])
        job.finished_at = time.time()
        self._set_state(job, JobState.complete, "Complete", 1.0)
        self.publish(job, {
            "t": "done",
            "results": results,
            "qa": [q.model_dump(mode="json") for q in job.qa],
            "elapsed": job.finished_at - (job.started_at or job.finished_at),
        })

        _prune_artifacts(job.run_dir)

    # -- cancellation ---------------------------------------------------

    async def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state in (JobState.complete, JobState.failed, JobState.cancelled):
            return job
        job._cancelled = True
        if job._proc is not None and job._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                job._proc.terminate()
            try:
                await asyncio.wait_for(job._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    job._proc.kill()
        elif job._task is not None:
            job._task.cancel()
        return job


# --------------------------------------------------------------------------


#: Intermediates no endpoint serves. `eplusout.eso` alone is ~28 MB of an
#: 85 MB annual run, and ReadVars has already turned it into the CSV we do
#: offer, so keeping it costs a third of the disk for nothing.
_DISPOSABLE = (
    "eplusout.eso", "eplusout.audit", "eplusout.bnd", "eplusout.mtd",
    "eplusout.shd", "eplusout.dxf", "eplusout.dbg", "eplusssz.csv",
    "epluszsz.csv", "eplusout.rdd", "eplusout.mdd",
)


def _prune_artifacts(run_dir: Path) -> None:
    """Drop the run files nothing downloads. Keeps a deployment's disk sane."""
    for name in _DISPOSABLE:
        with contextlib.suppress(OSError):
            (run_dir / name).unlink(missing_ok=True)


def _telemetry_spec(schematic: dict[str, Any]) -> list[dict[str, str]]:
    """Live variables to sample, derived from the model we just built.

    Deriving these from the schematic guarantees the animated plant diagram
    and the telemetry stream refer to exactly the same equipment.
    """
    spec: list[dict[str, str]] = [
        {"id": "oaDrybulb", "variable": "Site Outdoor Air Drybulb Temperature",
         "key": "ENVIRONMENT"},
        {"id": "oaWetbulb", "variable": "Site Outdoor Air Wetbulb Temperature",
         "key": "ENVIRONMENT"},
    ]
    per_kind = {
        "chiller": (
            ("power", "Chiller Electricity Rate"),
            ("cooling", "Chiller Evaporator Cooling Rate"),
            ("plr", "Chiller Part Load Ratio"),
        ),
        "boiler": (
            ("heat", "Boiler Heating Rate"),
            ("plr", "Boiler Part Load Ratio"),
        ),
        "tower": (
            ("power", "Cooling Tower Fan Electricity Rate"),
            ("reject", "Cooling Tower Heat Transfer Rate"),
        ),
        "heatpump": (
            ("power", "Heat Pump Electricity Rate"),
            ("heat", "Heat Pump Load Side Heat Transfer Rate"),
        ),
        "pump": (
            ("power", "Pump Electricity Rate"),
            ("flow", "Pump Mass Flow Rate"),
        ),
        "load": (
            ("rate", "Plant Load Profile Heat Transfer Rate"),
        ),
    }
    for node in schematic.get("nodes", []):
        for suffix, variable in per_kind.get(node["kind"], ()):
            spec.append({
                "id": f"{node['id']}::{suffix}",
                "variable": variable,
                "key": node["label"].upper(),
            })
    return spec


_manager: JobManager | None = None


def get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
