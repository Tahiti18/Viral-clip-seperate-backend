#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=.

if [[ "${RUN_MIGRATIONS:-1}" == "1" ]]; then
  if [[ -n "${DATABASE_URL:-}" ]]; then
    echo "Applying SQL migrations..."
    for f in migrations/*.sql; do
      echo "Running $f..."
      psql "$DATABASE_URL" -f "$f"
    done
  else
    echo "DATABASE_URL not set; skipping migrations."
  fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
