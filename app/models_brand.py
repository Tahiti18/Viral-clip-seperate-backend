import enum
from sqlalchemy import Enum, Integer, String, Text, ForeignKey, TIMESTAMP, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db import Base

class Platform(str, enum.Enum):
    generic="generic"; tiktok="tiktok"; shorts="shorts"; reels="reels"; x="x"; youtube="youtube"; instagram="instagram"

class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    style: Mapped[dict] = mapped_column(JSON, nullable=True)
    lexicon: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    docs = relationship("BrandDoc", back_populates="brand", cascade="all, delete-orphan", order_by="BrandDoc.created_at")

class BrandDoc(Base):
    __tablename__ = "brand_docs"
    id: Mapped[str] = mapped_column(primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    brand = relationship("Brand", back_populates="docs")

class CompliancePack(Base):
    __tablename__ = "compliance_packs"
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False, default=Platform.generic)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

class ComplianceScan(Base):
    __tablename__ = "compliance_scans"
    id: Mapped[str] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String, nullable=False)
    brand_id: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False, default=Platform.generic)
    input: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
