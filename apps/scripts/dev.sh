#!/usr/bin/env bash
# Run the API and the UI dev server together.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ -d "$HERE/server/.venv" ] || {
  echo "Creating the server virtualenv ..."
  python3 -m venv "$HERE/server/.venv"
  "$HERE/server/.venv/bin/pip" -q install -r "$HERE/server/requirements.txt"
}
[ -d "$HERE/web/node_modules" ] || (cd "$HERE/web" && npm install)

trap 'kill 0' EXIT
(cd "$HERE/server" && ./.venv/bin/python -m uvicorn app.main:app --reload --port 8000) &
(cd "$HERE/web" && npm run dev) &
wait
