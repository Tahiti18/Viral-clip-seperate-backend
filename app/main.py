from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import AsyncSessionLocal
from app.models import Plan
from app.routers.jobs import router as jobs_router
from app.routers.ab import router as ab_router
from app.routers.brand import router as brand_router

app = FastAPI(title="UnityLab Render API", version="0.4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(jobs_router, prefix="/v1")
app.include_router(ab_router, prefix="/v1")
app.include_router(brand_router, prefix="/v1")

@app.on_event("startup")
async def seed_plans():
    async with AsyncSessionLocal() as session:  # type: AsyncSession
        existing = (await session.execute(select(Plan.id))).scalars().all()
        need = set(["express","priority","standard"]) - set(existing)
        if need:
            data = []
            if "express" in need:  data.append({"id":"express","lane":0,"max_input_minutes":30,"target_multiplier":0.80,"credit_multiplier":1.50})
            if "priority" in need: data.append({"id":"priority","lane":1,"max_input_minutes":90,"target_multiplier":1.20,"credit_multiplier":1.20})
            if "standard" in need: data.append({"id":"standard","lane":2,"max_input_minutes":600,"target_multiplier":1.80,"credit_multiplier":1.00})
            await session.execute(insert(Plan), data); await session.commit()

@app.get("/health")
async def health(): return {"ok": True}
