#!/usr/bin/env bash
# Reliable start script for Railway (Postgres + FastAPI)
set -euo pipefail

# Make local imports work (e.g. "from app import ...")
export PYTHONPATH="${PYTHONPATH:-.}"

# Railway provides $PORT at runtime (defaults to 8080 locally)
PORT="${PORT:-8080}"

# --- Run SQL migrations (only if DATABASE_URL exists) ---
if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Applying SQL migrations with psql…"
  # Fail fast on any SQL error
  for f in migrations/*.sql; do
    [ -e "$f" ] || { echo "No migrations found; skipping."; break; }
    echo "Running $f…"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
  done
else
  echo "DATABASE_URL not set; skipping migrations."
fi

# --- Start FastAPI with Uvicorn ---
# NOTE: keep this module path matching your code layout.
# If your app lives in app/main.py and exposes `app = FastAPI()`,
# the import path is "app.main:app".
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
