#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=.
if [[ "${RUN_MIGRATIONS:-1}" == "1" ]]; then
  if [[ -n "${DATABASE_URL:-}" ]]; then
    echo "Applying SQL migrations..."
    psql "$DATABASE_URL" -f migrations/001_init.sql || true
    psql "$DATABASE_URL" -f migrations/002_ab_variants.sql || true
    psql "$DATABASE_URL" -f migrations/003_brand_compliance.sql || true
  else
    echo "DATABASE_URL not set; skipping migrations."
  fi
fi
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
