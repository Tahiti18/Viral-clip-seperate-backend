# Apply .sql migrations using libpq-form connection string
if [[ -n "${DATABASE_URL_PSQL:-}" ]]; then
  echo "Applying SQL migrations with psql..."
  for f in migrations/*.sql; do
    echo "Running $f..."
    psql "${DATABASE_URL_PSQL}" --set ON_ERROR_STOP=1 -f "$f"
  done
else
  echo "DATABASE_URL_PSQL not set; skipping migrations."
fi
