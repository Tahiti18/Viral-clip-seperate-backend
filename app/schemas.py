from typing import Optional, List
from pydantic import BaseModel, HttpUrl
from datetime import datetime
from app.models import JobState

class CreateJobIn(BaseModel):
  sourceUrl: HttpUrl
  inputMinutes: int
  plan: str
  webhookUrl: Optional[HttpUrl] = None
  idempotencyKey: Optional[str] = None

class JobTimelineItem(BaseModel):
  state: JobState
  at: datetime

class JobOut(BaseModel):
  jobId: str
  state: JobState
  etaSeconds: Optional[int]
  lane: str
  targets: dict | None = None
  timeline: Optional[List[JobTimelineItem]] = None
  outputs: Optional[list] = None
