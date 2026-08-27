"""FastAPI application entry point.

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .auth import RequireSession, credentials
from .auth import router as auth_router
from .config import APPS_DIR, get_settings

log = logging.getLogger("dhc")

app = FastAPI(
    title="District Plant Simulator",
    version="1.0.0",
    description=(
        "Design a district heating and cooling plant, run it through "
        "EnergyPlus, and watch the simulation as it happens."
    ),
)

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Order matters: the gate wraps the app, so it runs before any route.
app.add_middleware(RequireSession)

app.include_router(auth_router)
app.include_router(router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a bare traceback to the browser, but keep it in the log."""
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


@app.on_event("startup")
async def announce() -> None:
    engine = settings.engine_dir
    if engine is None:
        log.warning(
            "No EnergyPlus install found. Set ENERGYPLUS_DIR or run "
            "apps/scripts/fetch_engine.sh; the API will refuse to start jobs."
        )
    else:
        log.info("EnergyPlus %s at %s", settings.version, engine)

    creds = credentials()
    if creds.username == "admin" and creds.password == "admin123":
        log.warning(
            "Using the default admin/admin123 login. Set DHC_USERNAME and "
            "DHC_PASSWORD before exposing this service publicly."
        )
    if creds.ephemeral_secret:
        log.info(
            "Session key generated at startup; everyone is signed out on "
            "restart. Set DHC_SECRET to keep sessions across restarts."
        )


# The built UI is served from the same origin in production, which removes the
# CORS and WebSocket-origin questions entirely.
_dist = APPS_DIR / "web" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")
