import uuid
from typing import Optional, List, Literal, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.models import Job
from app.models_ab import Experiment, Variant, VariantStat, VariantState, ExperimentState
from app.services.ab_logic import VariantPosterior, recommend_allocations, should_promote

router = APIRouter(prefix="/ab", tags=["ab"])

class VariantIn(BaseModel):
    hookText: str; captionText: str; stylePreset: Optional[str] = None
class CreateExperimentIn(BaseModel):
    jobId: str; name: str
    platform: Literal["tiktok","shorts","reels","x"]
    targetMetric: Literal["CTR","Watch3s","Watch30s"] = "CTR"
    minImpressions: int = 500; minRuntimeSeconds: int = 3600
    variants: List[VariantIn]
class ExperimentOut(BaseModel):
    experimentId: str; state: ExperimentState; platform: str; targetMetric: str; minImpressions: int; variants: List[Dict]
class MetricsBatchIn(BaseModel):
    items: List[Dict]
class DecideOut(BaseModel):
    experimentId: str; state: ExperimentState; allocations: Dict[str, float]; promote: Optional[Dict] = None

@router.post("/experiments", response_model=ExperimentOut, status_code=201)
async def create_experiment(payload: CreateExperimentIn, session: AsyncSession = Depends(get_session)):
    job = (await session.execute(select(Job).where(Job.id==payload.jobId))).scalar_one_or_none()
    if not job: raise HTTPException(404, "Job not found")
    exp_id = str(uuid.uuid4())
    await session.execute(insert(Experiment).values(id=exp_id, job_id=payload.jobId, org_id=job.org_id, name=payload.name,
        platform=payload.platform, target_metric=payload.targetMetric, min_impressions=payload.minImpressions, min_runtime_seconds=payload.minRuntimeSeconds, state=ExperimentState.RUNNING))
    rows = []
    for i, v in enumerate(payload.variants):
        vid = str(uuid.uuid4())
        rows.append({"id": vid, "experiment_id": exp_id, "index": i, "hook_text": v.hookText, "caption_text": v.captionText, "style_preset": v.stylePreset or None})
    await session.execute(insert(Variant), rows)
    stats_rows = [{"variant_id": r["id"], "impressions":0, "clicks":0, "watch3s":0, "watch30s":0, "alpha":1, "beta":1} for r in rows]
    await session.execute(insert(VariantStat), stats_rows)
    await session.commit()
    variants_out = [{"variantId": r["id"], "index": r["index"], "state": "READY",
                     "hookText": r["hook_text"], "captionText": r["caption_text"], "stylePreset": r["style_preset"]} for r in rows]
    return ExperimentOut(experimentId=exp_id, state=ExperimentState.RUNNING, platform=payload.platform, targetMetric=payload.targetMetric, minImpressions=payload.minImpressions, variants=variants_out)

@router.get("/experiments/{exp_id}", response_model=ExperimentOut)
async def get_experiment(exp_id: str, session: AsyncSession = Depends(get_session)):
    exp = (await session.execute(select(Experiment).where(Experiment.id==exp_id))).scalar_one_or_none()
    if not exp: raise HTTPException(404, "Not found")
    vars = (await session.execute(select(Variant).where(Variant.experiment_id==exp_id).order_by(Variant.index))).scalars().all()
    out = []
    for v in vars:
        st = (await session.execute(select(VariantStat).where(VariantStat.variant_id==v.id))).scalar_one()
        out.append({"variantId": v.id, "index": v.index, "state": v.state, "hookText": v.hook_text, "captionText": v.caption_text, "stylePreset": v.style_preset,
                    "impressions": st.impressions, "clicks": st.clicks, "watch3s": st.watch3s, "watch30s": st.watch30s, "alpha": st.alpha, "beta": st.beta})
    return ExperimentOut(experimentId=exp.id, state=exp.state, platform=exp.platform, targetMetric=exp.target_metric, minImpressions=exp.min_impressions, variants=out)

@router.post("/experiments/{exp_id}/metrics", response_model=dict)
async def ingest_metrics(exp_id: str, payload: MetricsBatchIn, session: AsyncSession = Depends(get_session)):
    exp = (await session.execute(select(Experiment).where(Experiment.id==exp_id))).scalar_one_or_none()
    if not exp: raise HTTPException(404, "Not found")
    for row in payload.items:
        vid = row.get("variantId")
        st = (await session.execute(select(VariantStat).where(VariantStat.variant_id==vid))).scalar_one_or_none()
        if not st: continue
        imp = int(row.get("impressionsDelta", 0)); clk = int(row.get("clicksDelta", 0)); w3  = int(row.get("watch3sDelta", 0)); w30 = int(row.get("watch30sDelta", 0))
        successes_now = clk if exp.target_metric=="CTR" else (w3 if exp.target_metric=="Watch3s" else w30)
        failures = (st.impressions + imp) - ((st.clicks if exp.target_metric=="CTR" else (st.watch3s if exp.target_metric=="Watch3s" else st.watch30s)) + successes_now)
        alpha = exp.prior_alpha + ((st.clicks + clk) if exp.target_metric=="CTR" else (st.watch3s + w3) if exp.target_metric=="Watch3s" else (st.watch30s + w30))
        beta = exp.prior_beta + max(failures, 0)
        await session.execute(update(VariantStat).where(VariantStat.variant_id==vid).values(
            impressions=st.impressions + imp, clicks=st.clicks + clk, watch3s=st.watch3s + w3, watch30s=st.watch30s + w30, alpha=alpha, beta=beta
        ))
    await session.commit(); return {"ok": True}

@router.post("/experiments/{exp_id}/decide", response_model=DecideOut)
async def decide(exp_id: str, session: AsyncSession = Depends(get_session)):
    exp = (await session.execute(select(Experiment).where(Experiment.id==exp_id))).scalar_one_or_none()
    if not exp: raise HTTPException(404, "Not found")
    vars = (await session.execute(select(Variant).where(Variant.experiment_id==exp_id))).scalars().all()
    posteriors = []
    for v in vars:
        st = (await session.execute(select(VariantStat).where(VariantStat.variant_id==v.id))).scalar_one()
        successes = st.clicks if exp.target_metric=="CTR" else (st.watch3s if exp.target_metric=="Watch3s" else st.watch30s)
        posteriors.append(VariantPosterior(variant_id=v.id, impressions=st.impressions, successes=successes, alpha=exp.prior_alpha + successes, beta=exp.prior_beta + max(st.impressions - successes, 0)))
    allocations = recommend_allocations(posteriors, min_share=0.10)
    runtime_ok = True
    promote, winner_id, winner_mean = should_promote(posteriors, exp.min_impressions, runtime_ok)
    promote_payload = {"variantId": winner_id, "posteriorMean": float(winner_mean)} if promote and winner_id else None
    if promote and winner_id:
        await session.execute(update(Experiment).where(Experiment.id==exp_id).values(state=ExperimentState.PROMOTED))
        await session.execute(update(Variant).where(Variant.experiment_id==exp_id, Variant.id==winner_id).values(state=VariantState.PROMOTED))
    await session.commit()
    return DecideOut(experimentId=exp_id, state=("PROMOTED" if promote else exp.state), allocations=allocations, promote=promote_payload)
