"""SQLAlchemy ORM models representing the complete persistent state of GTA CLIP COMMAND."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Text,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from src.core.db import Base


def now_utc() -> datetime:
    """Return timezone-naive UTC timestamp for standard SQLite DateTime storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_id(prefix: str = "id") -> str:
    """Generate a collision-resistant prefixed UUIDv4 identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Creator(Base):
    """Content creator / channel entity with ownership rights and platform links."""

    __tablename__ = "creators"

    id = Column(String(32), primary_key=True, default=lambda: generate_id("cr"))
    handle = Column(String(128), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    channel_url = Column(String(512), nullable=False)
    platform = Column(String(64), nullable=False, default="youtube")  # youtube, twitch, kick
    status = Column(String(32), nullable=False, default="active")     # active, paused, blacklisted
    content_restrictions = Column(Text, nullable=True)
    branding_guidelines = Column(Text, nullable=True)
    authorized_platforms = Column(String(255), nullable=True, default="tiktok,youtube_shorts,instagram_reels")
    created_at = Column(DateTime, default=now_utc, nullable=False)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    # Relationships
    agreements = relationship("RightsAgreement", back_populates="creator", cascade="all, delete-orphan")
    media_sources = relationship("MediaSource", back_populates="creator")
    final_clips = relationship("FinalClip", back_populates="creator")
    delivery_logs = relationship("DeliveryLog", back_populates="creator")


class RightsAgreement(Base):
    """Documented legal rights and commercial permissions granting clipping authority."""

    __tablename__ = "rights_agreements"

    id = Column(String(32), primary_key=True, default=lambda: generate_id("ra"))
    creator_id = Column(String(32), ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True)
    agreement_type = Column(String(64), nullable=False)  # exclusive, non_exclusive, revenue_share, fair_use_reviewed
    status = Column(String(32), nullable=False, default="active")  # active, expired, revoked, pending
    valid_from = Column(DateTime, nullable=False, default=now_utc)
    valid_to = Column(DateTime, nullable=True)  # NULL denotes open-ended agreement
    rev_share_percent = Column(Float, nullable=False, default=0.0)
    documentation_path = Column(String(512), nullable=False)  # Path to signed contract or proof file
    authorized_by = Column(String(255), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc, nullable=False)

    # Relationships
    creator = relationship("Creator", back_populates="agreements")

    __table_args__ = (
        Index("idx_rights_lookup", "creator_id", "status"),
    )


class MediaSource(Base):
    """Ingested long-form footage or livestream recording."""

    __tablename__ = "media_sources"

    id = Column(String(32), primary_key=True, default=lambda: generate_id("ms"))
    creator_id = Column(String(32), ForeignKey("creators.id", ondelete="RESTRICT"), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    file_path = Column(String(1024), nullable=False)
    source_type = Column(String(64), nullable=False, default="vod")  # vod, livestream, clip, upload
    duration_seconds = Column(Float, nullable=False, default=0.0)
    width = Column(Integer, nullable=False, default=1920)
    height = Column(Integer, nullable=False, default=1080)
    fps = Column(Float, nullable=False, default=60.0)
    sha256_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=now_utc, nullable=False)

    # Relationships
    creator = relationship("Creator", back_populates="media_sources")
    jobs = relationship("ProcessingJob", back_populates="media", cascade="all, delete-orphan")


class ProcessingJob(Base):
    """Stateful, resumable orchestrator job tracking pipeline stage progress."""

    __tablename__ = "processing_jobs"

    id = Column(String(32), primary_key=True, default=lambda: generate_id("job"))
    media_id = Column(String(32), ForeignKey("media_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="queued")  # queued, running, paused, completed, failed, cancelled
    current_stage = Column(String(64), nullable=False, default="intake")  # 14 pipeline stages
    progress_percent = Column(Integer, nullable=False, default=0)
    current_model = Column(String(64), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    candidate_count = Column(Integer, nullable=False, default=0)
    clip_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    stage_metadata = Column(Text, nullable=True)  # JSON-encoded checkpoints for crash resumption
    stage_logs = Column(Text, nullable=True)      # JSON list of structured logs
    stage_states = Column(Text, nullable=True)    # JSON state dict for the 14 stages
    created_at = Column(DateTime, default=now_utc, nullable=False)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    # Relationships
    media = relationship("MediaSource", back_populates="jobs")
    candidates = relationship("CandidateClip", back_populates="job", cascade="all, delete-orphan")
    final_clips = relationship("FinalClip", back_populates="job", cascade="all, delete-orphan")


class CandidateClip(Base):
    """Potential highlight segment identified by audio, scene, and dialogue fusion."""

    __tablename__ = "candidate_clips"

    id = Column(String(32), primary_key=True, default=lambda: generate_id("cand"))
    job_id = Column(String(32), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)
    excitement_score = Column(Float, nullable=False, default=0.0)
    audio_peak_dbfs = Column(Float, nullable=False, default=-100.0)
    scene_cut_count = Column(Integer, nullable=False, default=0)
    speech_density = Column(Float, nullable=False, default=0.0)
    visual_tags = Column(Text, nullable=True)  # JSON: ["police_chase", "explosion", "streamer_screaming"]
    keyframes = Column(Text, nullable=True)     # JSON array of sampled keyframe paths
    created_at = Column(DateTime, default=now_utc, nullable=False)

    # Relationships
    job = relationship("ProcessingJob", back_populates="candidates")
    final_clip = relationship("FinalClip", back_populates="candidate", uselist=False)


class FinalClip(Base):
    """Ranked, styled vertical video clip prepared for human review and export."""

    __tablename__ = "final_clips"

    id = Column(String(32), primary_key=True, default=lambda: generate_id("clip"))
    candidate_id = Column(String(32), ForeignKey("candidate_clips.id", ondelete="SET NULL"), nullable=True)
    job_id = Column(String(32), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    creator_id = Column(String(32), ForeignKey("creators.id", ondelete="RESTRICT"), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    hook_text = Column(String(255), nullable=False)
    optimal_start = Column(Float, nullable=False)
    optimal_end = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)
    virality_score = Column(Integer, nullable=False, default=0)  # 0-100
    humor_score = Column(Integer, nullable=False, default=0)     # 0-100
    action_score = Column(Integer, nullable=False, default=0)    # 0-100
    layout_mode = Column(String(64), nullable=False, default="stacked")  # stacked, action_crop, blurred_pillar
    subtitle_style = Column(String(64), nullable=False, default="neon_yellow_bounce")
    subtitle_text = Column(Text, nullable=True)
    highlight_description = Column(Text, nullable=True)
    file_size_bytes = Column(Integer, nullable=True, default=0)
    approval_status = Column(String(32), nullable=False, default="pending_review")  # pending_review, approved, rejected, modified
    rejection_reason = Column(Text, nullable=True)
    output_path = Column(String(1024), nullable=True)
    thumbnail_path = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=now_utc, nullable=False)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    # Relationships
    candidate = relationship("CandidateClip", back_populates="final_clip")
    job = relationship("ProcessingJob", back_populates="final_clips")
    creator = relationship("Creator", back_populates="final_clips")
    delivery_logs = relationship("DeliveryLog", back_populates="clip", cascade="all, delete-orphan")


class DeliveryLog(Base):
    """Dispatch and platform delivery auditing for approved clips."""

    __tablename__ = "delivery_logs"

    id = Column(String(32), primary_key=True, default=lambda: generate_id("deliv"))
    clip_id = Column(String(32), ForeignKey("final_clips.id", ondelete="CASCADE"), nullable=False, index=True)
    creator_id = Column(String(32), ForeignKey("creators.id", ondelete="RESTRICT"), nullable=False, index=True)
    platform = Column(String(64), nullable=False)  # tiktok, youtube_shorts, instagram_reels
    status = Column(String(32), nullable=False, default="queued")  # queued, exported, delivered, failed
    destination_path = Column(String(1024), nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    clip = relationship("FinalClip", back_populates="delivery_logs")
    creator = relationship("Creator", back_populates="delivery_logs")


class SystemSetting(Base):
    """Dynamic operational configuration and active AI model selection."""

    __tablename__ = "system_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)
