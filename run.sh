#!/usr/bin/env bash
set -euo pipefail

# Ensure local imports work
export PYTHONPATH=.

# Railway gives $PORT; default to 8080 locally
PORT="${PORT:-8080}"

# Apply .sql migrations using the libpq-style connection string
if [[ -n "${DATABASE_URL_PSQL:-}" ]]; then
  echo "Applying SQL migrations with psql..."
  for f in migrations/*.sql; do
    echo "Running $f..."
    psql "${DATABASE_URL_PSQL}" --set ON_ERROR_STOP=1 -f "$f"
  done
else
  echo "DATABASE_URL_PSQL not set; skipping migrations."
fi

echo "Starting Uvicorn..."
# ⬇️ This expects your FastAPI app at app/main.py with a variable named `app`
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
