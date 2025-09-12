import asyncio, json, uuid
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import select, insert, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.models import Job, Org, Plan, JobEvent, JobSlaAudit, JobState
from app.schemas import CreateJobIn, JobOut, JobTimelineItem
from app.services.eta import compute_eta_seconds, lane_str
from app.config import settings

router = APIRouter(prefix="/jobs", tags=["jobs"])

async def _ensure_demo_org(session: AsyncSession) -> Org:
    org = (await session.execute(select(Org).limit(1))).scalar_one_or_none()
    if org: return org
    new_id = str(uuid.uuid4())
    await session.execute(insert(Org).values(id=new_id, name="Demo Org"))
    await session.commit()
    return (await session.execute(select(Org).where(Org.id==new_id))).scalar_one()

def _auth(x_api_key: str | None):
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(401, "Unauthorized")

@router.post("", status_code=201, response_model=JobOut)
async def create_job(payload: CreateJobIn, session: AsyncSession = Depends(get_session), x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    org = await _ensure_demo_org(session)
    plan = (await session.execute(select(Plan).where(Plan.id==payload.plan))).scalar_one_or_none()
    if not plan:
        raise HTTPException(400, "Unknown plan. Use: express | priority | standard")
    if payload.inputMinutes <= 0 or payload.inputMinutes > plan.max_input_minutes:
        raise HTTPException(400, f"inputMinutes must be 1..{plan.max_input_minutes} for {plan.id}")
    if payload.idempotencyKey:
        dup = (await session.execute(select(Job).where(Job.org_id==org.id, Job.idempotency_key==payload.idempotencyKey))).scalar_one_or_none()
        if dup:
            eta = dup.eta_seconds if dup.eta_seconds is not None else await compute_eta_seconds(session, dup)
            return JobOut(jobId=dup.id, state=dup.state, etaSeconds=eta, lane=lane_str(dup.lane))
    job_id = str(uuid.uuid4())
    await session.execute(insert(Job).values(id=job_id, org_id=org.id, source_url=str(payload.sourceUrl), input_minutes=payload.inputMinutes, plan_id=plan.id, lane=plan.lane, state=JobState.QUEUED, idempotency_key=payload.idempotencyKey))
    await session.execute(insert(JobEvent).values(job_id=job_id, state=JobState.QUEUED))
    await session.commit()
    job = (await session.execute(select(Job).where(Job.id==job_id))).scalar_one()
    eta = await compute_eta_seconds(session, job)
    await session.execute(update(Job).where(Job.id==job_id).values(eta_seconds=eta))
    await session.commit()
    return JobOut(jobId=job_id, state=JobState.QUEUED, etaSeconds=eta, lane=lane_str(plan.lane))

@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = (await session.execute(select(Job).where(Job.id==job_id))).scalar_one_or_none()
    if not job: raise HTTPException(404, "Job not found")
    events = (await session.execute(select(JobEvent).where(JobEvent.job_id==job_id).order_by(JobEvent.at))).scalars().all()
    timeline = [JobTimelineItem(state=e.state, at=e.at) for e in events]
    eta = job.eta_seconds
    if job.state not in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELED) and eta is None:
        eta = await compute_eta_seconds(session, job)
    return JobOut(jobId=job.id, state=job.state, etaSeconds=eta, lane=lane_str(job.lane), timeline=timeline)

@router.get("/{job_id}/stream")
async def stream_job(job_id: str, session: AsyncSession = Depends(get_session)):
    async def event_gen():
        while True:
            job = (await session.execute(select(Job).where(Job.id==job_id))).scalar_one_or_none()
            if not job:
                yield "event: error\ndata: " + json.dumps({"message":"Job not found"}) + "\n\n"; return
            data = {"jobId": job.id, "state": job.state, "etaSeconds": job.eta_seconds}
            yield "event: state\ndata: " + json.dumps(data, default=str) + "\n\n"
            if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELED, JobState.TIMED_OUT): return
            await asyncio.sleep(1.0)
    return StreamingResponse(event_gen(), media_type="text/event-stream")

@router.get("/queue")
async def queue_status(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Job.lane, func.count(), func.avg(Job.eta_seconds))
        .where(Job.state.in_([JobState.QUEUED, JobState.INGESTING, JobState.TRANSCRIBING, JobState.ANALYZING, JobState.EDITING, JobState.RENDERING]))
        .group_by(Job.lane)
    )).all()
    def lane_str2(l): return {0:"P0",1:"P1",2:"P2"}.get(l,"P2")
    lanes = {"P0":{"count":0,"avgEtaSeconds":None},"P1":{"count":0,"avgEtaSeconds":None},"P2":{"count":0,"avgEtaSeconds":None}}
    for lane, cnt, avg_eta in rows:
        lanes[lane_str2(lane)] = {"count": int(cnt), "avgEtaSeconds": int(avg_eta) if avg_eta else None}
    return {"lanes": lanes, "throughput": {"P0":1.6,"P1":1.2,"P2":1.0}}
