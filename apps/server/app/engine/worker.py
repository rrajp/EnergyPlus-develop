"""Child process that runs one EnergyPlus simulation.

Run as `python -m app.engine.worker <run_dir>`.  The parent supervisor reads
newline-delimited JSON from stdout:

    {"t": "progress", "pct": 0.42}
    {"t": "log",      "line": "Starting Simulation at 01/01/2017"}
    {"t": "telemetry","hour": 1234, "values": {...}}
    {"t": "result",   "ok": true, "exit_code": 0}

EnergyPlus is used through its Python API rather than the CLI so we get the
real progress callback and can sample live variable values while the run is
still in flight.  It lives in a child process because a fatal input error
inside the API terminates the process that hosts it.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main(run_dir: Path) -> int:
    spec = json.loads((run_dir / "job.json").read_text())
    engine_dir = Path(spec["engine_dir"])
    sys.path.insert(0, str(engine_dir))

    from pyenergyplus.api import EnergyPlusAPI  # noqa: E402  (needs sys.path)

    api = EnergyPlusAPI()
    state = api.state_manager.new_state()
    api.runtime.set_console_output_status(state, False)

    telemetry: list[dict[str, str]] = spec.get("telemetry", [])
    stride = max(1, int(spec.get("telemetry_stride_hours", 1)))
    want_telemetry = bool(telemetry) and spec.get("stream_telemetry", True)

    # ---- progress + log callbacks -------------------------------------
    last_pct = -1

    def on_progress(pct: int) -> None:
        nonlocal last_pct
        if pct != last_pct:
            last_pct = pct
            emit({"t": "progress", "pct": max(0.0, min(1.0, pct / 100.0))})

    def on_message(msg: bytes) -> None:
        try:
            line = msg.decode("utf-8", errors="replace").rstrip()
        except Exception:
            return
        if line:
            emit({"t": "log", "line": line})

    api.runtime.callback_progress(state, on_progress)
    api.runtime.callback_message(state, on_message)

    # ---- live telemetry ------------------------------------------------
    if want_telemetry:
        for item in telemetry:
            api.exchange.request_variable(state, item["variable"], item["key"])

        handles: dict[str, int] | None = None
        last_hour = -1

        def on_timestep(st) -> None:
            nonlocal handles, last_hour
            ex = api.exchange
            if not ex.api_data_fully_ready(st):
                return
            if handles is None:
                handles = {}
                for item in telemetry:
                    h = ex.get_variable_handle(st, item["variable"], item["key"])
                    if h >= 0:
                        handles[item["id"]] = h
                emit({"t": "telemetry_ready", "resolved": sorted(handles)})

            # One sample per `stride` hours keeps the stream readable over a
            # full year without throttling the simulation itself.
            hour = (ex.day_of_year(st) - 1) * 24 + ex.hour(st)
            if hour == last_hour or hour % stride:
                return
            last_hour = hour
            values = {k: ex.get_variable_value(st, h) for k, h in handles.items()}
            emit({
                "t": "telemetry",
                "hour": hour,
                "month": ex.month(st),
                "day": ex.day_of_month(st),
                "values": values,
            })

        api.runtime.callback_end_zone_timestep_after_zone_reporting(
            state, on_timestep
        )

    # ---- run -----------------------------------------------------------
    args = [
        "-d", str(run_dir),
        "-w", spec["weather"],
        "-r",
        str(run_dir / "model.epJSON"),
    ]
    try:
        code = api.runtime.run_energyplus(state, args)
    except Exception:
        emit({"t": "result", "ok": False, "exit_code": 1,
              "error": traceback.format_exc(limit=4)})
        return 1
    finally:
        api.state_manager.delete_state(state)

    emit({"t": "result", "ok": code == 0, "exit_code": code})
    return 0 if code == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        emit({"t": "result", "ok": False, "exit_code": 2,
              "error": "usage: worker.py <run_dir>"})
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
