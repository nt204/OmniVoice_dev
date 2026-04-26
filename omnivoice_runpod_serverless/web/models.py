from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    jobs: Mapped[list["SynthesisJob"]] = relationship(back_populates="user")


class VoicePreset(Base):
    __tablename__ = "voice_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preset_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(16), index=True)
    folder: Mapped[str] = mapped_column(String(255))
    audio_file: Mapped[str] = mapped_column(String(255))
    style_tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SynthesisJob(Base):
    __tablename__ = "synthesis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16), index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    
    # Preset & Style
    voice_preset: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    emotion: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ad_emphasis: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    
    # Core Parameters
    speed: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    pitch_shift: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    num_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    guidance_scale: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    
    # Advanced Metadata
    join_silence_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trailing_silence_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_segment_chars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gain_db: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    
    # Reference Info
    reference_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    reference_audio_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ref_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Status & Output
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runpod_job_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    output_audio_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_audio_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Metrics
    input_chars: Mapped[int] = mapped_column(Integer, default=0, index=True)
    audio_duration_sec: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    compute_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    
    # Future-proofing
    full_config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    user: Mapped[User] = relationship(back_populates="jobs")
    cost: Mapped[Optional["JobCost"]] = relationship(back_populates="job", uselist=False)


class JobCost(Base):
    __tablename__ = "job_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("synthesis_jobs.id"), unique=True, index=True)
    pricing_version: Mapped[str] = mapped_column(String(32), default="v1")
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    audio_duration_sec: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    compute_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    gpu_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    runpod_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    storage_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    bandwidth_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    service_fee_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    cost_breakdown_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    job: Mapped[SynthesisJob] = relationship(back_populates="cost")


class RunpodBillingBucket(Base):
    __tablename__ = "runpod_billing_buckets"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "bucket_size", "bucket_start", name="uq_runpod_billing_bucket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(String(64), index=True)
    bucket_size: Mapped[str] = mapped_column(String(16), default="hour", index=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    time_billed_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gpu_type_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pod_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    disk_space_billed_gb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

