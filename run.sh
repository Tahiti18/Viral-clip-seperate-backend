#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=.
PORT="${PORT:-8080}"

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

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
