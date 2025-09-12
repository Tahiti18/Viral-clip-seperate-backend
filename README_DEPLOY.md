# UnityLab Backend (FastAPI) — Deploy to Railway

## Deploy
1. Create a new Railway **project**.
2. Add a **PostgreSQL** plugin.
3. Create a **service from GitHub** pointing to this repo.
   - Start Command: `bash run.sh`
4. Variables → add:
   - `RUN_MIGRATIONS=1`
   - `API_KEY=changeme` (or any secret)
   - `DATABASE_URL` is auto-injected when you link Postgres.
5. Deploy and copy the public URL (used by the frontend).

## Health
`GET /health` → `{"ok": true}`
