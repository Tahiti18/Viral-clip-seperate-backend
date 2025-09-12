import enum
from sqlalchemy import Enum, Integer, String, Text, ForeignKey, TIMESTAMP, Boolean, JSON, SmallInteger, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db import Base

class JobState(str, enum.Enum):
    CREATED="CREATED"; QUEUED="QUEUED"; INGESTING="INGESTING"; TRANSCRIBING="TRANSCRIBING"
    ANALYZING="ANALYZING"; EDITING="EDITING"; RENDERING="RENDERING"; UPLOADING="UPLOADING"
    COMPLETED="COMPLETED"; FAILED="FAILED"; TIMED_OUT="TIMED_OUT"; CANCELED="CANCELED"

class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    lane: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_input_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    target_multiplier: Mapped[float] = mapped_column(nullable=False)
    credit_multiplier: Mapped[float] = mapped_column(nullable=False)

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    input_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False)
    lane: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[JobState] = mapped_column(Enum(JobState), nullable=False, default=JobState.CREATED)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)

    events = relationship("JobEvent", back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.at")
    sla = relationship("JobSlaAudit", back_populates="job", uselist=False, cascade="all, delete-orphan")

class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    state: Mapped[JobState] = mapped_column(Enum(JobState), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    job = relationship("Job", back_populates="events")

class JobSlaAudit(Base):
    __tablename__ = "job_sla_audit"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    target_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    breached: Mapped[bool] = mapped_column(Boolean, nullable=False)
    remedy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    job = relationship("Job", back_populates="sla")
