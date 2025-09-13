#!/usr/bin/env bash
set -euo pipefail

# Railway provides $PORT; default for local runs
PORT="${PORT:-8080}"

# Ensure local imports work (repo root on PYTHONPATH)
export PYTHONPATH="${PYTHONPATH:-.}"

# Apply .sql migrations using libpq-form connection string
if [[ -n "${DATABASE_URL_PSQL:-}" ]]; then
  echo "Applying SQL migrations with psql..."
  shopt -s nullglob
  for f in migrations/*.sql; do
    echo "Running $f..."
    psql "${DATABASE_URL_PSQL}" --set ON_ERROR_STOP=1 -f "$f"
  done
else
  echo "DATABASE_URL_PSQL not set; skipping migrations."
fi

# Which FastAPI app to serve (override via Railway var APP_MODULE if needed)
APP_MODULE="${APP_MODULE:-main:app}"

echo "Starting Uvicorn -> $APP_MODULE on :$PORT"
exec uvicorn "$APP_MODULE" --host 0.0.0.0 --port "$PORT"
