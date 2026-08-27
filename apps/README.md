# Plantworks — district heating & cooling plant simulator

Design a district energy plant in the browser, run it through **EnergyPlus** on
the server, and watch the plant animate while the simulation is still solving.

```
.
├── Dockerfile  everything in one image
├── engine/     EnergyPlus runtime (downloaded, never committed)
├── scripts/    fetch_engine.sh, dev.sh
├── server/     FastAPI — model generation, run supervision, result parsing
└── web/        Vite + React + TypeScript UI
```

This directory is self-contained: it can be the root of its own repository, or
live as `apps/` inside the EnergyPlus tree. Paths below are written for the
standalone layout — prefix them with `apps/` if you kept the fork. The Docker
build context is always **this directory**, never the EnergyPlus repo root.

## Quick start

```bash
# 1. Get an EnergyPlus runtime (~350 MB, once)
scripts/fetch_engine.sh

# 2. Server
cd server
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m uvicorn app.main:app --reload --port 8000

# 3. UI (separate terminal)
cd web
npm install
npm run dev          # http://localhost:5173, proxies /api to :8000
```

Or just `scripts/dev.sh`, which does both. For a single-origin deployment,
`npm run build` in `web/` and the server mounts `web/dist` at `/` automatically — then only port 8000 is needed.

## How it works

```
  browser                    FastAPI                     child process
 ─────────                  ─────────                   ───────────────
  PlantConfig  ──POST──▶  readiness checks
                          epJSON generation
                          ├─ model.epJSON  ────────────▶ pyenergyplus API
                          │                              ├ callback_progress
   WebSocket  ◀───────────┤  NDJSON fan-out  ◀───────────┤ callback_message
   progress /             │                              └ end_zone_timestep
   log / telemetry        │                                 → get_variable_value
                          │
  Results     ◀──────────  eplusout.sql → summary, series, compliance
```

Runs happen in a **child process** using the EnergyPlus Python API rather than
the CLI. That buys three things the command line cannot give:

- a real `0–100%` progress callback instead of scraping stdout,
- **live variable values sampled mid-run** — the plant diagram animates from
  actual chiller load and outdoor conditions while the year is still solving,
- crash isolation, because a fatal input error inside the API kills the
  process that hosts it.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | sign in, sets the session cookie |
| `POST` | `/api/auth/logout` | sign out |
| `GET` | `/api/auth/me` | current session, if any |
| `GET` | `/api/health` | engine discovery and version (public) |
| `GET` | `/api/catalog` | equipment library mined from the shipped DataSets |
| `GET`/`POST` | `/api/assets/weather` | list / upload EPW files |
| `GET`/`POST` | `/api/assets/loads` | list / upload 8,760-hour load CSVs |
| `POST` | `/api/validate` | readiness checks without building anything |
| `POST` | `/api/jobs` | create and start a run |
| `GET` | `/api/jobs/{id}` | full snapshot (survives a server restart) |
| `DELETE` | `/api/jobs/{id}` | cancel |
| `GET` | `/api/jobs/{id}/results` | parsed summary, compliance, QA |
| `GET` | `/api/jobs/{id}/series` | selected time series, hourly/daily/monthly |
| `GET` | `/api/jobs/{id}/artifact/{name}` | epJSON, .err, .sql, tabular HTML, CSV |
| `WS` | `/api/jobs/{id}/stream` | `progress` / `log` / `telemetry` / `done` |

Shareable links: `/?job=<id>` attaches to any run, `&view=profile` opens a
specific results tab, `&theme=light` pins the appearance.

## Signing in

The app sits behind a single shared login, `admin` / `admin123` by default.

The gate is enforced on the server: the same session cookie guards every
`/api` route and the telemetry WebSocket, not just the screen. A login page
that only hid the UI would leave `POST /api/jobs` open to anyone with the URL.
`GET /api/health` stays public so container health probes work, and the SPA
shell is served to everyone — it renders the login form.

| Variable | Default | Purpose |
|---|---|---|
| `DHC_USERNAME` | `admin` | operator username |
| `DHC_PASSWORD` | `admin123` | operator password |
| `DHC_SECRET` | generated at boot | session signing key |

**Change the password before putting this on a public address**, and set
`DHC_SECRET` to a long random string — otherwise a fresh key is generated at
startup and every session is invalidated on restart.

This is one shared credential, not a user system: no accounts, no roles, no
password reset, and no rate limiting on the login endpoint. It keeps a
demo deployment from being world-writable; it is not what you would put in
front of customer data.

## Deploying with Docker

One image holds the EnergyPlus runtime, the API and the built UI, so there is a
single port to expose and no CORS or WebSocket-origin configuration.

```bash
docker build -t plantworks .              # context = this directory
docker run -p 8000:8000 plantworks        # http://localhost:8000
```

The build downloads EnergyPlus itself — nothing to fetch beforehand — and
verifies inside the image that the binary links, the Python API loads and the
equipment catalog parses. A broken engine fails the build rather than the first
user's run.

Measured on a clean build: **334 MB image, ~110 MB idle memory, 57 MB of disk
per completed annual run.**

### Render

A blueprint lives at the repository root in `render.yaml`; point Render at the
repo and it configures the service. To set it up by hand instead:

| Setting | Value |
|---|---|
| Language / Runtime | Docker |
| Branch | `master` |
| **Dockerfile Path** | `./apps/Dockerfile` |
| **Docker Build Context Directory** | `./apps` |
| Health Check Path | `/api/health` |
| Instances | **1 — do not scale out** |

Both Docker paths matter, and the context is the one that bites. A context of
`app` rather than `apps` fails the build with:

```
error: invalid local: resolve : lstat /opt/render/project/src/app: no such file
```

Render injects `PORT`; the container honours it. Set `DHC_PASSWORD` and
`DHC_USERNAME` in the dashboard, and let Render generate `DHC_SECRET` — without
it the signing key is regenerated at boot and everyone is signed out on every
restart.

Measured in a container capped at 512 MB, the free plan's limit: **53 MB idle,
361 MB peak** during an annual run, which completed. That leaves roughly 150 MB
of headroom, which is why `DHC_MAX_CONCURRENT` is pinned to 1 — a second
concurrent run would not fit.

**What the free plan means here.** The instance sleeps after roughly 15 minutes
of inactivity and its disk is ephemeral, so the first request after a sleep
pays a cold start *and* finds no previous runs: completed simulations and their
`?job=` links do not survive. It also has 512 MB of RAM and a fraction of a
CPU, so an annual run that takes ~6-10 s locally will take substantially
longer. Lower the timestep in *Setup → Inputs* to trade fidelity for
turnaround, and expect to re-run rather than revisit.

For results that outlive a sleep, use a paid instance with a disk mounted at
`/data`.

### Northflank

| Setting | Value |
|---|---|
| Build type | Dockerfile |
| Dockerfile path | `/Dockerfile` (or `/apps/Dockerfile` in the fork layout) |
| **Build context** | the directory holding the Dockerfile — never the EnergyPlus repo root |
| Port | `8000`, HTTP, public |
| Health check | `GET /api/health` |
| Resources | 0.2 vCPU / 512 MB is enough; more vCPU mainly shortens runs |
| Instances | **1 — do not scale out** |

Northflank injects `PORT`; the container honours it and falls back to 8000.

**One instance only.** The job registry, the WebSocket subscriber set and the
run semaphore all live in process memory. A second replica would accept a run
its sibling is executing and answer `404` for a job the user is watching. The
image pins `--workers 1` for the same reason. Scaling this service means
introducing a shared job store and a message bus, not raising the replica count.

**Disk is ephemeral.** Completed runs rehydrate from `DHC_RUN_ROOT` after a
process restart, which covers a crash or a redeploy of the same container, but
a fresh container starts empty and old `?job=` links stop resolving. Mount a
volume at `/data` to keep them. `DHC_JOB_RETENTION` (default 5 in the image)
bounds how many runs are kept before the oldest is deleted.

**Free-tier run times.** An annual simulation takes roughly 6-10 s of CPU at
4 timesteps/hour for a plant of this size. On a shared 0.2 vCPU expect several
times that; the progress bar and live telemetry make the wait legible rather
than shorter. Lower the timestep in *Setup → Inputs* to trade fidelity for
turnaround.

### Image contents

The release tarball is ~750 MB unpacked and most of it is unused here. The
build drops `ExampleFiles`, `Documentation`, `PreProcess`, `MacroDataSets`,
`ExpandObjects` and EnergyPlus's embedded `python_lib` — the API is driven from
the system interpreter and no Python plugins run, which was confirmed by a full
annual simulation with `python_lib` removed. What remains is 138 MB: the
binary, `libenergyplusapi.so`, `pyenergyplus`, the IDD and epJSON schema,
`DataSets` (the equipment catalog) and `WeatherData`.

Completed runs also drop `eplusout.eso` and other intermediates no endpoint
serves — a third of the disk per run, for no loss of function.

## Modelling notes

These are the traps that cost real debugging time. They are encoded in the
builder so they cannot silently come back.

**`LoadProfile:Plant` sign convention.** A *positive* scheduled load means heat
is *removed* from the water — a heating demand. Cooling must be supplied as
**negative watts**. See `PlantLoadProfile.cc`:
`OutletTemp = InletTemp - Power/(mdot·Cp)`.

**Flow-fraction schedules are not optional.** Feeding `LoadProfile:Plant` a
constant `1.0` flow fraction makes it demand full design flow in all 8,760
hours, so the loop pumps never stop. On a loop whose load falls to zero for a
season, pump work accumulates with nowhere to go and EnergyPlus dies with
`CheckForRunawayPlantTemps`. The generated CSV carries a load-tracking flow
fraction in columns 3 and 4.

**Common-pipe loops need constant primary flow.** EnergyPlus cannot vary flow
on the primary side of a primary–secondary loop; a variable-speed primary pump
makes loop temperature diverge. The builder switches it to constant speed and
says so in the model notes.

**Curves are normalised to their own rating point.** Grafting a catalog
machine's CAPFT/EIRFT curves onto a chiller while keeping different reference
temperatures makes the curve evaluate to something other than 1.0 at the stated
reference — the machine then quietly delivers the wrong capacity (0.76× in the
case that prompted this note). The builder adopts the curve source's rating
temperatures and reports the change.

**Pump flow fields differ per object.** `Pump:ConstantSpeed` takes
`design_flow_rate`, `Pump:VariableSpeed` takes `design_maximum_flow_rate`, and
the headered variants take `total_design_flow_rate`. Writing the wrong one is
silently ignored and the pump ends up with zero flow.

**Unmet hours, not annual totals.** Distribution pump heat can make annual
*delivered* cooling exceed annual *requested* cooling even while the plant
misses setpoint on the hottest afternoons. The summary counts the hours the
loop actually drifted past setpoint.

## Equipment catalog

Rather than hard-coding a handful of coefficients, the catalog is parsed at
runtime from the reference datasets EnergyPlus ships:

- `DataSets/Chillers.idf` — 160 water-cooled machines with full curve sets
- `DataSets/AirCooledChiller.idf` — 111 air-cooled machines
- `DataSets/Boilers.idf` — five normalised boiler efficiency curves

That is **271 real reference chillers** from Carrier, McQuay, Multistack, Trane
and York. A chiller with no catalog selection is seeded from the nearest
reference machine of the same size and condenser type, which gives far better
part-load behaviour than a flat generic curve.

## Compliance

Total System Efficiency (TSE) is whole-plant electrical input per unit of
cooling delivered — chillers, tower fans and every pump included. It is the
reciprocal of system COP. The shipped reference limits are **0.24 kW/kW**
(ECSBC) and **0.19 kW/kW** (Super ECSBC), in
`server/app/engine/results.py: TSE_LIMITS`. Confirm the figure against the code
edition a project is filed under before relying on the verdict.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ENERGYPLUS_DIR` | auto-discovered | explicit engine location |
| `DHC_RUN_ROOT` | `server/var/runs` | where run directories live |
| `DHC_ASSET_ROOT` | `server/var/assets` | uploaded EPW and load files |
| `DHC_MAX_CONCURRENT` | `2` | simultaneous EnergyPlus runs |
| `DHC_JOB_RETENTION` | `50` | runs kept before the oldest is deleted |
| `DHC_CORS_ORIGINS` | `localhost:5173` | comma-separated dev origins |
| `DHC_WEATHER_DIRS` | – | extra EPW directories, colon-separated |
| `PORT` | `8000` | listen port (the container honours it) |
| `DHC_USERNAME` / `DHC_PASSWORD` | `admin` / `admin123` | operator login |
| `DHC_SECRET` | generated at boot | session signing key |

Engine discovery order: `ENERGYPLUS_DIR`, `apps/engine/EnergyPlus-*`, this
repo's `build/Products`, the platform's standard install location, then
whatever `energyplus` resolves to on `PATH`.

## Design

Colour is data, not branding. The three loop fluids carry the only saturated
hues, following P&ID convention, so the diagram legend and the chart legend are
the same legend — a chiller is the same blue on the mimic panel, in the
equipment list, and in every chart. Amber is reserved for "this machine is
running now", and motion means fluid is moving: a static design never animates.

The categorical palette was validated with the `dataviz` skill's checker
(`validate_palette.js`, all-pairs) against both the light and dark surfaces.
Light: `#2E86E8 #00A15C #E05A28 #C557BC`. Dark: `#4A93E2 #16A868 #E36537
#C95FAF`. Both pass the lightness band, chroma floor, normal-vision floor and
contrast checks; adjacent CVD separation lands in the 6–8 band, which is why
every chart ships a legend and direct labels.
