#!/usr/bin/env bash
# Dev launcher: creates a venv, installs deps, runs the server.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

export PLEXUS_DEMO_MODE="${PLEXUS_DEMO_MODE:-true}"
exec uvicorn plexus.main:app --reload --host 0.0.0.0 --port 8000
