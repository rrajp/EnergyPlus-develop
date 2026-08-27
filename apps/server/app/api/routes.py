"""HTTP and WebSocket surface.

    GET    /api/health              engine discovery + version
    GET    /api/catalog             equipment library mined from the DataSets
    GET    /api/assets/weather      available EPW files
    POST   /api/assets/weather      upload an EPW
    GET    /api/assets/loads        available building-load profiles
    POST   /api/assets/loads        upload an 8,760-hour CSV
    GET    /api/assets/loads/{id}/series   the profile itself, for charting
    POST   /api/validate            readiness checks without building anything
    POST   /api/jobs                create + start a run
    GET    /api/jobs                recent runs
    GET    /api/jobs/{id}           full snapshot
    DELETE /api/jobs/{id}           cancel
    GET    /api/jobs/{id}/results   parsed results
    GET    /api/jobs/{id}/series    selected time series
    GET    /api/jobs/{id}/artifact/{name}   model.epJSON, eplusout.err, ...
    WS     /api/jobs/{id}/stream    live progress, log and telemetry
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse

from ..assets import AssetError, get_store
from ..checks import readiness
from ..config import get_settings
from ..engine.jobs import BlockedError, JobError, get_manager
from ..engine.results import ResultsError, load_series
from ..idf.catalog import catalog_payload
from ..models import CreateJobRequest, CreateJobResponse, PlantConfig

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 40 * 1024 * 1024

#: Artifacts a client may download. Whitelisted so a job id can never be used
#: to walk out of its own run directory.
ARTIFACTS = {
    "model": ("model.epJSON", "application/json"),
    "config": ("config.json", "application/json"),
    "errors": ("eplusout.err", "text/plain"),
    "table": ("eplustbl.htm", "text/html"),
    "variables": ("eplusout.csv", "text/csv"),
    "sql": ("eplusout.sql", "application/vnd.sqlite3"),
}


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------


@router.get("/health")
def health() -> dict[str, Any]:
    s = get_settings()
    engine = s.engine_dir
    return {
        "ok": engine is not None,
        "engine": {
            "found": engine is not None,
            "path": str(engine) if engine else None,
            "version": s.version if engine else None,
        },
        "maxConcurrentRuns": s.max_concurrent_runs,
    }


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    try:
        return catalog_payload()
    except Exception as exc:
        raise HTTPException(503, f"Equipment catalog unavailable: {exc}") from exc


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------


@router.get("/assets/weather")
def list_weather() -> dict[str, Any]:
    return {"items": get_store().list_weather()}


@router.post("/assets/weather")
async def upload_weather(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await _read_upload(file)
    try:
        asset = get_store().add_weather(file.filename or "weather.epw", data)
    except AssetError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"item": asset.public()}


@router.get("/assets/loads")
def list_loads() -> dict[str, Any]:
    return {"items": get_store().list_loads()}


@router.post("/assets/loads")
async def upload_load(
    file: UploadFile = File(...),
    units: str = Form("kW"),
    cooling_column: str | None = Form(None),
    heating_column: str | None = Form(None),
) -> dict[str, Any]:
    data = await _read_upload(file)
    try:
        asset, notes = get_store().add_load(
            file.filename or "load.csv", data,
            units=units,
            cooling_column=cooling_column,
            heating_column=heating_column,
        )
    except AssetError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"item": asset.public(), "notes": notes}


@router.get("/assets/loads/{asset_id}/series")
def load_profile(asset_id: str, stride: int = Query(1, ge=1, le=24)) -> dict[str, Any]:
    try:
        cooling, heating = get_store().load_series(asset_id)
    except AssetError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "coolingKw": [round(v / 1000.0, 2) for v in cooling[::stride]],
        "heatingKw": [round(v / 1000.0, 2) for v in heating[::stride]],
        "stride": stride,
    }


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    return data


# --------------------------------------------------------------------------
# validation and jobs
# --------------------------------------------------------------------------


@router.post("/validate")
def validate(config: PlantConfig) -> dict[str, Any]:
    store = get_store()
    load = None
    weather_ok = False
    if config.load is not None:
        try:
            load = store.load(config.load.asset_id)
        except AssetError:
            load = None
    if config.weather is not None:
        try:
            store.weather(config.weather.asset_id)
            weather_ok = True
        except AssetError:
            weather_ok = False
    items = readiness(config, load, weather_ok)
    return {
        "readiness": [i.model_dump(mode="json") for i in items],
        "blocking": any(i.blocking for i in items),
    }


@router.post("/jobs", response_model=CreateJobResponse)
async def create_job(req: CreateJobRequest) -> CreateJobResponse:
    # Must be async: creating a job schedules the run as an asyncio task, and a
    # sync handler would run in a worker thread with no event loop attached.
    manager = get_manager()
    try:
        job = manager.create(req.config, dry_run=req.dry_run)
    except BlockedError as exc:
        raise HTTPException(
            422,
            detail={
                "message": str(exc),
                "readiness": [i.model_dump(mode="json") for i in exc.items],
            },
        ) from exc
    except JobError as exc:
        raise HTTPException(400, str(exc)) from exc
    return CreateJobResponse(job=job.summary(), readiness=job.readiness)


@router.get("/jobs")
def list_jobs() -> dict[str, Any]:
    return {"items": [j.model_dump(mode="json") for j in get_manager().list()]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _job(job_id).snapshot()


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str) -> dict[str, Any]:
    manager = get_manager()
    try:
        job = await manager.cancel(job_id)
    except JobError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"job": job.summary().model_dump(mode="json")}


@router.get("/jobs/{job_id}/results")
def job_results(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    if job.results is None:
        raise HTTPException(409, f"Job is '{job.state.value}'; no results yet.")
    return {
        "results": job.results,
        "qa": [q.model_dump(mode="json") for q in job.qa],
        "diagnostics": job.diagnostics,
        "schematic": job.schematic,
    }


@router.get("/jobs/{job_id}/series")
def job_series(
    job_id: str,
    keys: str = Query(..., description="comma-separated series keys"),
    freq: str = Query("hourly", pattern="^(hourly|daily|monthly)$"),
) -> dict[str, Any]:
    job = _job(job_id)
    if job.results is None:
        raise HTTPException(409, f"Job is '{job.state.value}'; no results yet.")
    try:
        return load_series(job.run_dir, [k.strip() for k in keys.split(",")], freq)
    except ResultsError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/jobs/{job_id}/artifact/{name}")
def job_artifact(job_id: str, name: str):
    job = _job(job_id)
    if name not in ARTIFACTS:
        raise HTTPException(
            404, f"Unknown artifact '{name}'. Available: {sorted(ARTIFACTS)}"
        )
    filename, media = ARTIFACTS[name]
    path = job.run_dir / filename
    if not path.exists():
        raise HTTPException(404, f"'{filename}' was not produced by this run.")
    return FileResponse(path, media_type=media, filename=f"{job_id}-{filename}")


# --------------------------------------------------------------------------
# live stream
# --------------------------------------------------------------------------


@router.websocket("/jobs/{job_id}/stream")
async def job_stream(ws: WebSocket, job_id: str) -> None:
    await ws.accept()
    manager = get_manager()
    try:
        job = manager.get(job_id)
    except JobError:
        await ws.send_json({
            "t": "error",
            "message": (
                "That run is no longer available. It may have been cleared to "
                "make room for newer runs."
            ),
        })
        await ws.close(code=4404)
        return

    with manager.subscribe(job) as queue:
        await ws.send_json(job.snapshot())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    # Keeps intermediaries from dropping an idle connection.
                    await ws.send_json({"t": "ping"})
                    continue
                await ws.send_json(event)
                if event.get("t") in ("done", "error") or (
                    event.get("t") == "state"
                    and event.get("state") in ("complete", "failed", "cancelled")
                    and queue.empty()
                ):
                    # Let any trailing events drain before closing.
                    if event.get("t") in ("done", "error"):
                        await asyncio.sleep(0.05)
                        while not queue.empty():
                            await ws.send_json(queue.get_nowait())
                        break
        except WebSocketDisconnect:
            return
        except Exception:
            return
    with contextlib.suppress(Exception):
        await ws.close()


def _job(job_id: str):
    try:
        return get_manager().get(job_id)
    except JobError as exc:
        raise HTTPException(404, str(exc)) from exc
