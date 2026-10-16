#!/usr/bin/env bash
# Local / server start. Override with env vars, e.g. PORT=9000 NAD_API_KEYS=k1,k2 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/uvicorn ]; then
  uv venv -q .venv && uv pip install -q --python .venv/bin/python -r requirements.txt
fi
exec .venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" --workers "${WORKERS:-2}"
