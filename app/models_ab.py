import enum
from sqlalchemy import Enum, Integer, String, Text, ForeignKey, TIMESTAMP, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db import Base

class ExperimentState(str, enum.Enum):
    DRAFT="DRAFT"; RUNNING="RUNNING"; PROMOTED="PROMOTED"; STOPPED="STOPPED"
class VariantState(str, enum.Enum):
    READY="READY"; PAUSED="PAUSED"; KILLED="KILLED"; PROMOTED="PROMOTED"

class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    target_metric: Mapped[str] = mapped_column(String, nullable=False)
    min_impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    min_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    prior_alpha: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    prior_beta: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[ExperimentState] = mapped_column(Enum(ExperimentState), nullable=False, default=ExperimentState.DRAFT)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    variants = relationship("Variant", back_populates="experiment", cascade="all, delete-orphan", order_by="Variant.created_at")

class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[str] = mapped_column(primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[ExperimentState] = mapped_column(Enum(ExperimentState), nullable=False, default=ExperimentState.DRAFT)
    hook_text: Mapped[str] = mapped_column(Text, nullable=False)
    caption_text: Mapped[str] = mapped_column(Text, nullable=False)
    style_preset: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    experiment = relationship("Experiment", back_populates="variants")
    stats = relationship("VariantStat", back_populates="variant", uselist=False, cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("experiment_id", "index", name="uq_variant_exp_idx"),)

class VariantStat(Base):
    __tablename__ = "variant_stats"
    variant_id: Mapped[str] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), primary_key=True)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    watch3s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    watch30s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alpha: Mapped[float] = mapped_column(Integer, nullable=False, default=1)
    beta: Mapped[float] = mapped_column(Integer, nullable=False, default=1)
    last_ingested_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    variant = relationship("Variant", back_populates="stats")
