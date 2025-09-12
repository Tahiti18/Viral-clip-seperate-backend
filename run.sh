#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=.

if [[ "${RUN_MIGRATIONS:-1}" == "1" ]]; then
  echo "Applying SQL migrations..."
  for f in migrations/*.sql; do
    echo "Running $f..."
    PGPASSWORD="${POSTGRES_PASSWORD}" psql       -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" -f "$f"
  done
fi

exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
