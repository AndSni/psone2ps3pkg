#!/usr/bin/env bash
# Create a local .venv on first run, then launch. Arguments pass straight through:
#   ./run.sh                       # open the window
#   ./run.sh --headless PATH       # build without a window
#   ./run.sh --check               # verify the build tools are present
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null
"$VENV/bin/python" -m pip install --quiet -e ".[dev]"

exec "$VENV/bin/psone2ps3pkg" "$@"
