# syntax=docker/dockerfile:1
#
# Plantworks — one image containing the EnergyPlus runtime, the API, and the
# built UI. Serving the UI from the API's own origin means there is exactly
# one port to expose and no CORS or WebSocket-origin configuration.
#
#   docker build -t plantworks apps/
#   docker run -p 8000:8000 plantworks
#
# Build context is `apps/`. Nothing outside it is needed.

# --------------------------------------------------------------------------
# 1. Build the UI
# --------------------------------------------------------------------------
FROM node:22-slim AS web

WORKDIR /build
# Copy manifests first so `npm ci` is cached until dependencies actually change.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./
RUN npm run build


# --------------------------------------------------------------------------
# 2. Fetch and trim the EnergyPlus runtime
#
# The release tarball is ~750 MB unpacked, and most of it is documentation and
# example models this service never opens. Trimming here, in a stage that is
# discarded, keeps the tarball out of the final image entirely.
#
# Only the Ubuntu 24.04 x86_64 build is published for this release, which is
# why the runtime stage below is pinned to ubuntu:24.04 — the shared library
# needs a matching glibc.
# --------------------------------------------------------------------------
FROM ubuntu:24.04 AS engine

ARG EPLUS_VERSION=26.1.0
ARG EPLUS_BUILD=6f2e40d102
ARG EPLUS_ASSET=Linux-Ubuntu24.04-x86_64

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    name="EnergyPlus-${EPLUS_VERSION}-${EPLUS_BUILD}-${EPLUS_ASSET}"; \
    url="https://github.com/NREL/EnergyPlus/releases/download/v${EPLUS_VERSION}/${name}.tar.gz"; \
    mkdir -p /engine; \
    curl -fL --retry 3 -o /tmp/ep.tar.gz "$url"; \
    tar xzf /tmp/ep.tar.gz -C /engine; \
    rm /tmp/ep.tar.gz; \
    mv "/engine/${name}" /engine/energyplus; \
    cd /engine/energyplus; \
    # Not used by this service: sample models, the documentation set, the
    # IDF/IMF preprocessors, and EnergyPlus's own embedded Python standard
    # library (we drive the API from the system interpreter and never run
    # Python plugins — verified by a full annual run with this removed).
    rm -rf ExampleFiles Documentation PreProcess python_lib MacroDataSets \
           ExpandObjects Bugreprt.txt Deprecation.html; \
    test -f energyplus && test -f Energy+.idd && test -d DataSets


# --------------------------------------------------------------------------
# 3. Runtime
# --------------------------------------------------------------------------
FROM ubuntu:24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv libgomp1 libx11-6 curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# The app layout must mirror the repo: the server resolves the built UI at
# ../web/dist relative to its own package.
WORKDIR /app

COPY --from=engine /engine/energyplus /opt/energyplus

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server/app /app/server/app
COPY --from=web /build/dist /app/web/dist

# Run and asset directories live outside the code tree so a read-only root
# filesystem still works if the platform enforces one.
RUN useradd --system --create-home --uid 10001 plant \
 && mkdir -p /data/runs /data/assets \
 && chown -R plant:plant /data /app

ENV ENERGYPLUS_DIR=/opt/energyplus \
    DHC_RUN_ROOT=/data/runs \
    DHC_ASSET_ROOT=/data/assets \
    DHC_WEATHER_DIRS=/opt/energyplus/WeatherData \
    DHC_MAX_CONCURRENT=1 \
    DHC_JOB_RETENTION=5 \
    PORT=8000

# Prove the shipped environment actually works: the binary links, the API
# library loads, and the equipment catalog parses. A broken engine should
# fail the build, not the first user's run.
RUN /opt/energyplus/energyplus --version \
 && PYTHONPATH=/app/server python3 -c "\
import sys; sys.path.insert(0, '/opt/energyplus'); \
from pyenergyplus.api import EnergyPlusAPI; \
api = EnergyPlusAPI(); s = api.state_manager.new_state(); \
api.state_manager.delete_state(s); \
from app.idf.catalog import catalog_payload; \
c = catalog_payload()['counts']; \
assert c['waterCooled'] > 100 and c['airCooled'] > 50, c; \
print('engine OK, catalog', c)"

USER plant
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

# A single worker is required, not a tuning choice: the job registry, the
# WebSocket subscriber set and the run semaphore all live in process memory.
# A second worker would answer for runs it cannot see.
CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir /app/server --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
