from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Job, Plan, JobState
from app.config import settings

LANE_MAP = {0: "P0", 1: "P1", 2: "P2"}
THROUGHPUT = {0: settings.THROUGHPUT_P0, 1: settings.THROUGHPUT_P1, 2: settings.THROUGHPUT_P2}

async def queue_minutes_ahead(session: AsyncSession, current: Job) -> float:
    from sqlalchemy import func
    stmt = (
        select(Job.id, Job.created_at, Job.lane, Job.input_minutes, Plan.target_multiplier)
        .join(Plan, Plan.id == Job.plan_id)
        .where(Job.state.in_([JobState.QUEUED, JobState.INGESTING, JobState.TRANSCRIBING, JobState.ANALYZING, JobState.EDITING, JobState.RENDERING]))
    )
    rows = (await session.execute(stmt)).all()
    total = 0.0
    for (_id, created_at, lane, input_minutes, target_multiplier) in rows:
        ahead = (lane < current.lane) or (lane == current.lane and created_at < current.created_at)
        if ahead:
            total += input_minutes * float(target_multiplier)
    return total

async def compute_eta_seconds(session: AsyncSession, job: Job) -> int:
    q_ahead = await queue_minutes_ahead(session, job)
    exp_min = job.input_minutes * float((await session.get(Plan, job.plan_id)).target_multiplier)
    effective = THROUGHPUT.get(job.lane, 1.0)
    eta_minutes = (q_ahead / effective) + exp_min
    return int(eta_minutes * 60)

def lane_str(lane: int) -> str:
    return LANE_MAP.get(lane, "P2")
