import uuid
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.models_brand import CompliancePack, Platform
from app.services.brand_rag import rag_retrieve, run_compliance_scan

router = APIRouter(prefix="/brand", tags=["brand"])

class PackIn(BaseModel):
    name: str; platform: Platform = Platform.generic; rules: dict
class ScanIn(BaseModel):
    platform: Platform = Platform.generic
    transcript: Optional[str] = ""; captions: Optional[List[str]] = []; overlays: Optional[List[str]] = []
class ScanOut(BaseModel):
    score: int; violations: List[dict]; suggestions: List[dict]

@router.post("/packs", response_model=dict, status_code=201)
async def create_pack(payload: PackIn, session: AsyncSession = Depends(get_session)):
    pid = str(uuid.uuid4())
    await session.execute(insert(CompliancePack).values(id=pid, name=payload.name, platform=payload.platform, rules=payload.rules))
    await session.commit(); return {"packId": pid}

@router.post("/scan", response_model=ScanOut, status_code=201)
async def scan(payload: ScanIn, session: AsyncSession = Depends(get_session)):
    result = await run_compliance_scan(session, payload.platform, {"transcript": payload.transcript or "", "captions": payload.captions or [], "overlays": payload.overlays or []})
    return ScanOut(score=result["score"], violations=result["violations"], suggestions=result["suggestions"])
