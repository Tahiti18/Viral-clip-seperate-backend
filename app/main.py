# app/main.py
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import JSONResponse
import os

app = FastAPI(title="Viral Clip Backend")

# ---- simple health check (what Railway probes) ----
@app.get("/health")
def health():
    return {"ok": True}

# ---- API router mounted at /api ----
api = APIRouter(prefix="/api", tags=["api"])

# We’ll read from Postgres using asyncpg if available.
# DATABASE_URL_PSQL is the plain libpq form you set in Railway.
DATABASE_URL = os.getenv("DATABASE_URL_PSQL") or ""
# If only async SQLAlchemy URL is present, fall back by stripping the driver.
if not DATABASE_URL:
    raw = os.getenv("DATABASE_URL", "")
    if raw.startswith("postgresql+asyncpg://"):
        DATABASE_URL = "postgresql://" + raw.split("postgresql+asyncpg://", 1)[1]

@api.get("/jobs")
async def list_jobs():
    if not DATABASE_URL:
        # Clear 500 with message (better than 404)
        raise HTTPException(status_code=500, detail="DATABASE_URL_PSQL or DATABASE_URL not set")

    # Try asyncpg; if missing, return a friendly 500 so we know to add it.
    try:
        import asyncpg  # type: ignore
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="asyncpg not installed on the server. Add 'asyncpg' to requirements.txt."
        )

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT id, org_id, source_url, state, created_at, updated_at
            FROM jobs
            ORDER BY created_at DESC
            LIMIT 50
            """
        )
        # Convert asyncpg Record -> dict
        data = [dict(r) for r in rows]
        return JSONResponse(data)
    finally:
        await conn.close()

# mount the /api router
app.include_router(api)
