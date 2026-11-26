"""FastAPI web server powering the GTA CLIP COMMAND Control Center API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from contextlib import asynccontextmanager
import sys
import threading
from datetime import date, datetime, time, timezone
from fractions import Fraction
import math
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.models import (
    FinalClip, Creator, RightsAgreement, ProcessingJob,
    DeliveryLog, MediaSource, CandidateClip, now_utc, generate_id,
)
from src.core.config import get_settings
from src.core.logger import get_logger
from src.dashboard.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    create_session_token,
    credentials_are_valid,
    get_auth_settings,
    session_token_is_valid,
)

logger = get_logger("dashboard.api")
settings = get_settings()


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    get_auth_settings()
    yield


app = FastAPI(title="GTA CLIP COMMAND API", version="2.0.0", lifespan=app_lifespan)


@app.middleware("http")
async def single_owner_auth(request: Request, call_next):
    auth_settings = get_auth_settings()
    public_paths = {"/healthz", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}
    protected_path = (
        request.url.path.startswith(("/api/", "/video/", "/thumb/", "/docs", "/redoc"))
        or request.url.path == "/openapi.json"
    )
    if auth_settings.enabled and protected_path and request.url.path not in public_paths:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not session_token_is_valid(token, auth_settings):
            response = JSONResponse(
                {"detail": "Authentication required."},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Strict-Transport-Security", "max-age=63072000")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class RejectRequest(BaseModel):
    reason: str


class RerenderRequest(BaseModel):
    start: float
    end: float
    layout: str = "stacked"


class ClipEditRequest(BaseModel):
    title: Optional[str] = None
    hook_text: Optional[str] = None
    subtitle_text: Optional[str] = None
    highlight_description: Optional[str] = None
    optimal_start: Optional[float] = None
    optimal_end: Optional[float] = None
    layout_mode: Optional[str] = None


class BulkApproveRequest(BaseModel):
    clip_ids: list[str]


class DashboardLoginRequest(BaseModel):
    username: str
    password: str


class CreatorCreateRequest(BaseModel):
    handle: str
    name: str
    channel_url: str
    platform: str = "youtube"
    content_restrictions: Optional[str] = None
    branding_guidelines: Optional[str] = None
    authorized_platforms: Optional[str] = "tiktok,youtube_shorts,instagram_reels"


class CreatorUpdateRequest(BaseModel):
    handle: Optional[str] = None
    name: Optional[str] = None
    channel_url: Optional[str] = None
    platform: Optional[str] = None
    status: Optional[str] = None
    content_restrictions: Optional[str] = None
    branding_guidelines: Optional[str] = None
    authorized_platforms: Optional[str] = None


class ModelSelectRequest(BaseModel):
    reasoning_model: Optional[str] = None
    vision_model: Optional[str] = None
    reasoning_provider: str = "ollama"
    vision_provider: str = "ollama"


class AISettingsRequest(BaseModel):
    reasoning_provider: str = "ollama"
    vision_provider: str = "ollama"
    transcription_provider: str = "faster_whisper"
    cloud_base_url: str = "https://api.openai.com/v1"
    cloud_api_key: Optional[str] = None
    clear_api_key: bool = False
    cloud_reasoning_model: str = ""
    cloud_vision_model: str = ""
    cloud_transcription_model: str = "whisper-large-v3-turbo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


RIGHTS_AGREEMENT_TYPES = {"exclusive", "non_exclusive", "revenue_share", "fair_use_reviewed"}
RIGHTS_STATUSES = {"active", "pending", "expired", "revoked"}
RIGHTS_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
MAX_RIGHTS_DOCUMENT_BYTES = 20 * 1024 * 1024


def _rights_document_payload(agreement: RightsAgreement) -> dict[str, Any]:
    doc_path = Path(agreement.documentation_path) if agreement.documentation_path else None
    effective_status = agreement.status
    if agreement.status == "active" and agreement.valid_to is not None and agreement.valid_to < now_utc():
        effective_status = "expired"
    return {
        "id": agreement.id,
        "agreement_type": agreement.agreement_type,
        "status": agreement.status,
        "effective_status": effective_status,
        "valid_from": agreement.valid_from.isoformat() if agreement.valid_from else None,
        "valid_to": agreement.valid_to.isoformat() if agreement.valid_to else None,
        "rev_share_percent": agreement.rev_share_percent,
        "documentation_name": doc_path.name if doc_path else None,
        "documentation_available": bool(doc_path and doc_path.is_file()),
        "document_url": f"/api/rights/{agreement.id}/document" if doc_path and doc_path.is_file() else None,
        "authorized_by": agreement.authorized_by,
        "notes": agreement.notes,
        "created_at": agreement.created_at.isoformat() if agreement.created_at else None,
    }


def _rights_expiry_date(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    try:
        return datetime.combine(date.fromisoformat(value.strip()), time.max)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Valid until must be a date in YYYY-MM-DD format") from exc


async def _archive_rights_document(file: UploadFile, creator_id: str, agreement_id: str) -> Path:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in RIGHTS_DOCUMENT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Permission documents must be PDF, DOC, DOCX, PNG, JPG, or JPEG")
    contents = await file.read(MAX_RIGHTS_DOCUMENT_BYTES + 1)
    if not contents:
        raise HTTPException(status_code=422, detail="The permission document is empty")
    if len(contents) > MAX_RIGHTS_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Permission documents must be 20 MB or smaller")
    settings.agreements_dir.mkdir(parents=True, exist_ok=True)
    archived = settings.agreements_dir / f"{creator_id}_{agreement_id}{extension}"
    archived.write_bytes(contents)
    return archived


def _ffprobe_duration(path: Path) -> float:
    ffprobe = settings.ffprobe_bin
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        return float(info.get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


def _ffprobe_metadata(path: Path) -> dict:
    ffprobe = settings.ffprobe_bin
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        video_stream = next(
            (s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}
        )
        return {
            "duration": float(info.get("format", {}).get("duration", 0)),
            "width": int(video_stream.get("width", 1920)),
            "height": int(video_stream.get("height", 1080)),
            "fps": float(Fraction(video_stream.get("avg_frame_rate", "60/1"))),
        }
    except Exception:
        return {"duration": 0.0, "width": 1920, "height": 1080, "fps": 60.0}


def _get_worker():
    from src.dashboard.worker import get_worker
    return get_worker()


def _storage_gb(directory: Path) -> float:
    total = 0
    try:
        for f in directory.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return round(total / (1024 ** 3), 3)


# ---------------------------------------------------------------------------
# Health and single-owner authentication
# ---------------------------------------------------------------------------

@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status(request: Request):
    auth_settings = get_auth_settings()
    return {
        "auth_enabled": auth_settings.enabled,
        "authenticated": (
            not auth_settings.enabled
            or session_token_is_valid(request.cookies.get(SESSION_COOKIE_NAME), auth_settings)
        ),
    }


@app.post("/api/auth/login")
def auth_login(payload: DashboardLoginRequest, response: Response):
    auth_settings = get_auth_settings()
    if not auth_settings.enabled:
        raise HTTPException(status_code=409, detail="Dashboard authentication is disabled.")
    if not credentials_are_valid(payload.username, payload.password, auth_settings):
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(auth_settings),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True}


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}


# ---------------------------------------------------------------------------
# Root / static
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def get_index():
    index_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# OVERVIEW
# ---------------------------------------------------------------------------

@app.get("/api/overview")
def get_overview(db: Session = Depends(get_db)):
    total_videos = db.query(MediaSource).count()
    processing = db.query(ProcessingJob).filter(ProcessingJob.status == "running").count()
    completed = db.query(ProcessingJob).filter(ProcessingJob.status == "completed").count()
    failed = db.query(ProcessingJob).filter(ProcessingJob.status == "failed").count()
    total_clips = db.query(FinalClip).count()
    pending_review = db.query(FinalClip).filter(FinalClip.approval_status == "pending_review").count()
    approved = db.query(FinalClip).filter(FinalClip.approval_status == "approved").count()
    rejected = db.query(FinalClip).filter(FinalClip.approval_status == "rejected").count()
    storage_gb = _storage_gb(settings.data_dir)

    # Recent activity: last 10 jobs
    recent_jobs = (
        db.query(ProcessingJob)
        .order_by(ProcessingJob.created_at.desc())
        .limit(10)
        .all()
    )
    recent_activity = [
        {
            "job_id": j.id,
            "status": j.status,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "message": f"{j.media.title if j.media else 'Video'} — {j.status.replace('_', ' ').title()}",
        }
        for j in recent_jobs
    ]

    return {
        "total_videos": total_videos,
        "processing": processing,
        "completed": completed,
        "failed": failed,
        "total_clips": total_clips,
        "pending_review": pending_review,
        "approved": approved,
        "rejected": rejected,
        "storage_gb": storage_gb,
        "recent_activity": recent_activity,
    }


# ---------------------------------------------------------------------------
# UPLOAD
# ---------------------------------------------------------------------------

@app.get("/api/upload/creators")
def upload_creators(db: Session = Depends(get_db)):
    """List creators with active rights status for the upload form."""
    creators = db.query(Creator).filter(Creator.status == "active").all()
    result = []
    now = now_utc()
    for cr in creators:
        agreements = (
            db.query(RightsAgreement)
            .filter(RightsAgreement.creator_id == cr.id, RightsAgreement.status == "active")
            .all()
        )
        has_active_rights = any(a.valid_to is None or a.valid_to >= now for a in agreements)
        result.append({
            "id": cr.id,
            "handle": cr.handle,
            "name": cr.name,
            "platform": cr.platform,
            "has_active_rights": has_active_rights,
            "rights_status": "active" if has_active_rights else ("expired" if agreements else "none"),
        })
    return {"creators": result}


@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...),
    creator_id: str = Form(...),
    title: str = Form(...),
    candidate_count: int = Form(3),
    db: Session = Depends(get_db),
):
    if candidate_count < 1 or candidate_count > 10:
        raise HTTPException(status_code=422, detail="Candidate count must be between 1 and 10")

    creator = db.query(Creator).filter(Creator.id == creator_id).first()
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    # Check active rights
    active_agree = (
        db.query(RightsAgreement)
        .filter(RightsAgreement.creator_id == creator_id, RightsAgreement.status == "active")
        .first()
    )
    if not active_agree:
        raise HTTPException(status_code=403, detail="Creator has no active rights agreement")

    # Save file
    settings.raw_footage_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name if file.filename else "upload.mp4"
    dest = settings.raw_footage_dir / safe_name
    counter = 1
    while dest.exists():
        dest = settings.raw_footage_dir / f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
        counter += 1

    content = await file.read()
    dest.write_bytes(content)

    # SHA-256 duplicate check
    sha = _sha256(dest)
    existing_media = db.query(MediaSource).filter(MediaSource.sha256_hash == sha).first()
    if existing_media:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=f"Duplicate file (media_id={existing_media.id})")

    # ffprobe metadata
    meta = _ffprobe_metadata(dest)

    # Create MediaSource
    media = MediaSource(
        creator_id=creator_id,
        title=title,
        file_path=str(dest),
        source_type="upload",
        duration_seconds=meta["duration"],
        width=meta["width"],
        height=meta["height"],
        fps=meta["fps"],
        sha256_hash=sha,
    )
    db.add(media)
    db.flush()

    # Create ProcessingJob
    job = ProcessingJob(
        media_id=media.id,
        status="queued",
        current_stage="intake",
        progress_percent=0,
        requested_clip_count=candidate_count,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info("Uploaded %s → media=%s job=%s", safe_name, media.id, job.id)
    return {"job_id": job.id, "media_id": media.id, "file": str(dest)}


# ---------------------------------------------------------------------------
# QUEUE MANAGEMENT
# ---------------------------------------------------------------------------

@app.get("/api/queue")
def get_queue(db: Session = Depends(get_db)):
    jobs = (
        db.query(ProcessingJob)
        .order_by(ProcessingJob.created_at.desc())
        .all()
    )
    result = []
    for j in jobs:
        media = db.query(MediaSource).filter(MediaSource.id == j.media_id).first()
        creator = db.query(Creator).filter(Creator.id == media.creator_id).first() if media else None
        result.append({
            "id": j.id,
            "media_id": j.media_id,
            "title": media.title if media else None,
            "creator_handle": creator.handle if creator else None,
            "duration": media.duration_seconds if media else None,
            "status": j.status,
            # SQLite is the source of truth. The worker's in-memory snapshot can
            # lag while run_pipeline advances its own checkpoints.
            "stage": j.current_stage,
            "progress": j.progress_percent,
            "current_model": j.current_model,
            "candidate_count": getattr(j, "candidate_count", 0) or 0,
            "clip_count": getattr(j, "clip_count", 0) or 0,
            "requested_clip_count": getattr(j, "requested_clip_count", 3) or 3,
            "error": j.error_message,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        })
    return {"jobs": result}


@app.post("/api/queue/{job_id}/start")
def queue_start(job_id: str, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    media = db.query(MediaSource).filter(MediaSource.id == job.media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    creator = db.query(Creator).filter(Creator.id == media.creator_id).first()
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    if job.status != "queued":
        raise HTTPException(status_code=409, detail=f"Job cannot be started while its status is '{job.status}'.")

    worker = _get_worker()
    enqueued = worker.enqueue(job.id, media.id, creator.handle, media.file_path)
    return {"status": "queued" if enqueued else "already_queued", "job_id": job_id}


@app.post("/api/queue/{job_id}/pause")
def queue_pause(job_id: str):
    _get_worker().pause(job_id)
    return {"status": "pause_requested", "job_id": job_id}


@app.post("/api/queue/{job_id}/resume")
def queue_resume(job_id: str):
    _get_worker().resume(job_id)
    return {"status": "resume_requested", "job_id": job_id}


@app.post("/api/queue/{job_id}/cancel")
def queue_cancel(job_id: str):
    _get_worker().cancel(job_id)
    return {"status": "cancelled", "job_id": job_id}


@app.post("/api/queue/{job_id}/retry")
def queue_retry(job_id: str, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Cannot retry job with status '{job.status}'")
    if db.query(FinalClip).filter(FinalClip.job_id == job_id).count():
        raise HTTPException(status_code=409, detail="This job already produced clips. Review those clips instead of retrying.")
    media = db.query(MediaSource).filter(MediaSource.id == job.media_id).first()
    creator = db.query(Creator).filter(Creator.id == media.creator_id).first() if media else None
    if not media or not creator:
        raise HTTPException(status_code=409, detail="The source video or creator for this job is missing")

    job.status = "queued"
    job.current_stage = "intake"
    job.progress_percent = 0
    job.candidate_count = 0
    job.clip_count = 0
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.current_model = None
    job.stage_logs = None
    job.stage_states = None
    db.query(CandidateClip).filter(CandidateClip.job_id == job_id).delete(synchronize_session=False)
    db.commit()
    _get_worker().enqueue(job.id, media.id, creator.handle, media.file_path)
    return {"status": "queued", "job_id": job_id}


@app.delete("/api/queue/{job_id}")
def queue_delete(job_id: str, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("queued", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Cannot delete job with status '{job.status}'")
    if db.query(FinalClip).filter(FinalClip.job_id == job_id).count():
        raise HTTPException(status_code=409, detail="This job has generated clips. Review or export them before deleting the job.")
    removed = _get_worker().remove(job_id)
    db.delete(job)
    db.commit()
    return {"removed": True, "worker_state_removed": removed, "job_id": job_id}


# ---------------------------------------------------------------------------
# MONITOR
# ---------------------------------------------------------------------------

@app.get("/api/monitor/{job_id}")
def get_monitor(job_id: str, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    stage_states = None
    stage_logs = None
    try:
        stage_states = json.loads(job.stage_states) if job.stage_states else {}
    except Exception:
        stage_states = {}
    try:
        stage_logs = json.loads(job.stage_logs) if job.stage_logs else []
    except Exception:
        stage_logs = []

    stage_order = ["intake", "audio_demux", "transcription", "signals", "fusion", "ai_evaluation", "composition", "finalize"]
    current = job.current_stage if job.current_stage in stage_order else "intake"
    current_index = stage_order.index(current)
    if not stage_states or not any(name in stage_states for name in stage_order):
        stage_states = {}
        for index, stage_name in enumerate(stage_order):
            if job.status == "completed" or (job.status != "queued" and index < current_index):
                stage_states[stage_name] = "completed"
            elif index == current_index:
                stage_states[stage_name] = "failed" if job.status == "failed" else ("cancelled" if job.status == "cancelled" else ("paused" if job.status == "paused" else ("completed" if job.status == "completed" else ("pending" if job.status == "queued" else "running"))))
            else:
                stage_states[stage_name] = "pending"

    worker_state = _get_worker().get_status(job_id) or {}
    if worker_state.get("logs"):
        stage_logs = worker_state["logs"][-300:]
    else:
        job_log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "jobs" / f"{job_id}.log"
        if job_log_path.is_file():
            try:
                stage_logs = job_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            except OSError:
                pass
        else:
            # Compatibility for jobs started before per-job logging was added.
            # The worker is single-threaded, so each start marker bounds one job.
            shared_log = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "pipeline.log"
            if shared_log.is_file():
                try:
                    lines = shared_log.read_text(encoding="utf-8", errors="replace").splitlines()
                    start_marker = f"Starting pipeline for job {job_id}"
                    start_at = next((i for i in range(len(lines) - 1, -1, -1) if start_marker in lines[i]), None)
                    if start_at is not None:
                        end_at = next((i for i in range(start_at + 1, len(lines)) if "Starting pipeline for job job_" in lines[i]), len(lines))
                        stage_logs = lines[start_at:end_at][-300:]
                except OSError:
                    pass

    return {
        "job_id": job_id,
        "status": job.status,
        "stage": job.current_stage,
        "progress": job.progress_percent,
        "stage_states": stage_states,
        "stage_logs": stage_logs,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
        "title": job.media.title if job.media else None,
    }


# ---------------------------------------------------------------------------
# SSE EVENT STREAM
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def sse_events():
    """Server-Sent Events stream. Streams pipeline events to connected clients."""
    worker = _get_worker()
    q = worker.subscribe()

    async def generator():
        try:
            while True:
                try:
                    # heartbeat every 5 seconds
                    payload = await asyncio.wait_for(q.get(), timeout=5.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {\"type\":\"heartbeat\"}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            worker.unsubscribe(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# CLIPS (preserved + new)
# ---------------------------------------------------------------------------

@app.get("/api/clips")
def list_clips(db: Session = Depends(get_db)):
    clips = db.query(FinalClip).order_by(FinalClip.created_at.desc()).all()
    out = []
    for c in clips:
        has_video = c.output_path and Path(c.output_path).exists()
        has_thumb = c.thumbnail_path and Path(c.thumbnail_path).exists()
        out.append({
            "id": c.id,
            "title": c.title,
            "hook_text": c.hook_text,
            "virality_score": c.virality_score,
            "humor_score": c.humor_score,
            "action_score": c.action_score,
            "duration": c.duration,
            "optimal_start": c.optimal_start,
            "optimal_end": c.optimal_end,
            "layout_mode": c.layout_mode,
            "approval_status": c.approval_status,
            "status": "pending" if c.approval_status == "pending_review" else c.approval_status,
            "duration_seconds": c.duration,
            "creator_handle": c.creator.handle if c.creator else None,
            "export_path": c.output_path,
            "video_url": f"/video/{c.id}" if has_video else None,
            "thumbnail_url": f"/thumb/{c.id}" if has_thumb else None,
        })
    return {"clips": out}


@app.get("/video/{clip_id}")
def stream_video(clip_id: str, db: Session = Depends(get_db)):
    clip = db.query(FinalClip).filter(FinalClip.id == clip_id).first()
    if not clip or not clip.output_path or not Path(clip.output_path).exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(clip.output_path, media_type="video/mp4")


@app.get("/thumb/{clip_id}")
def get_thumbnail(clip_id: str, db: Session = Depends(get_db)):
    clip = db.query(FinalClip).filter(FinalClip.id == clip_id).first()
    if not clip or not clip.thumbnail_path or not Path(clip.thumbnail_path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(clip.thumbnail_path, media_type="image/jpeg")


@app.post("/api/clips/{clip_id}/approve")
def approve_clip(clip_id: str, db: Session = Depends(get_db)):
    clip = db.query(FinalClip).filter(FinalClip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip.approval_status = "approved"
    for platform in ["tiktok", "youtube_shorts", "instagram_reels"]:
        delivery = DeliveryLog(
            clip_id=clip.id,
            creator_id=clip.creator_id,
            platform=platform,
            status="queued",
            destination_path=clip.output_path,
        )
        db.add(delivery)
    db.commit()
    logger.info("Clip %s approved.", clip.id)
    return {"status": "success", "approval_status": "approved"}


@app.post("/api/clips/{clip_id}/reject")
def reject_clip(clip_id: str, req: RejectRequest, db: Session = Depends(get_db)):
    clip = db.query(FinalClip).filter(FinalClip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip.approval_status = "rejected"
    clip.rejection_reason = req.reason
    db.commit()
    logger.info("Clip %s rejected: %s", clip.id, req.reason)
    return {"status": "success", "approval_status": "rejected"}


@app.put("/api/clips/{clip_id}")
def edit_clip(clip_id: str, req: ClipEditRequest, db: Session = Depends(get_db)):
    clip = db.query(FinalClip).filter(FinalClip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if req.title is not None:
        clip.title = req.title
    if req.hook_text is not None:
        clip.hook_text = req.hook_text
    if req.subtitle_text is not None:
        clip.subtitle_text = req.subtitle_text
    if req.highlight_description is not None:
        clip.highlight_description = req.highlight_description
    if req.optimal_start is not None:
        clip.optimal_start = req.optimal_start
    if req.optimal_end is not None:
        clip.optimal_end = req.optimal_end
    if req.layout_mode is not None:
        clip.layout_mode = req.layout_mode
    db.commit()
    return {"status": "updated", "clip_id": clip_id}


@app.post("/api/clips/{clip_id}/rerender")
def rerender_clip(
    clip_id: str,
    req: RerenderRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    clip = db.query(FinalClip).filter(FinalClip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    media = None
    if clip.job_id:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == clip.job_id).first()
        if job:
            media = db.query(MediaSource).filter(MediaSource.id == job.media_id).first()

    def _do_rerender():
        try:
            from src.composer.nvenc_renderer import NvencRenderer
            renderer = NvencRenderer()
            export_path = settings.exports_dir / f"rerender_{clip_id}.mp4"
            renderer.render_clip(
                video_path=Path(media.file_path) if media else Path(clip.output_path),
                start_time=req.start,
                end_time=req.end,
                output_path=export_path,
                layout_mode=req.layout,
            )
        except Exception as exc:
            logger.error("Rerender failed for clip %s: %s", clip_id, exc)

    background_tasks.add_task(_do_rerender)
    return {"status": "rerender_started", "clip_id": clip_id}


@app.post("/api/clips/bulk-approve")
def bulk_approve(req: BulkApproveRequest, db: Session = Depends(get_db)):
    approved_count = 0
    not_found = 0
    for clip_id in req.clip_ids:
        clip = db.query(FinalClip).filter(FinalClip.id == clip_id).first()
        if clip:
            clip.approval_status = "approved"
            approved_count += 1
        else:
            not_found += 1
    db.commit()
    return {"approved": approved_count, "not_found": not_found}


# ---------------------------------------------------------------------------
# LIBRARY
# ---------------------------------------------------------------------------

@app.get("/api/library")
def get_library(
    creator_id: Optional[str] = None,
    status: Optional[str] = None,
    min_score: Optional[int] = None,
    max_duration: Optional[float] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(FinalClip)
    if creator_id:
        q = q.filter(FinalClip.creator_id == creator_id)
    if status:
        q = q.filter(FinalClip.approval_status == status)
    if min_score is not None:
        q = q.filter(FinalClip.virality_score >= min_score)
    if max_duration is not None:
        q = q.filter(FinalClip.duration <= max_duration)
    if search:
        like = f"%{search}%"
        q = q.filter(
            FinalClip.title.ilike(like) | FinalClip.hook_text.ilike(like)
        )
    clips = q.order_by(FinalClip.created_at.desc()).all()
    result = []
    for c in clips:
        # Fetch related source title + creator handle
        job = db.query(ProcessingJob).filter(ProcessingJob.id == c.job_id).first() if c.job_id else None
        media = db.query(MediaSource).filter(MediaSource.id == job.media_id).first() if job else None
        creator = db.query(Creator).filter(Creator.id == c.creator_id).first()
        result.append({
            "id": c.id,
            "title": c.title,
            "hook_text": c.hook_text,
            "highlight_description": getattr(c, "highlight_description", None),
            "subtitle_text": getattr(c, "subtitle_text", None),
            "source_title": media.title if media else None,
            "creator_handle": creator.handle if creator else None,
            "file_size_bytes": getattr(c, "file_size_bytes", 0) or 0,
            "virality_score": c.virality_score,
            "humor_score": c.humor_score,
            "action_score": c.action_score,
            "duration": c.duration,
            "duration_seconds": c.duration,
            "optimal_start": c.optimal_start,
            "optimal_end": c.optimal_end,
            "approval_status": c.approval_status,
            "status": "pending" if c.approval_status == "pending_review" else c.approval_status,
            "thumbnail_url": f"/thumb/{c.id}" if c.thumbnail_path and Path(c.thumbnail_path).exists() else None,
            "video_url": f"/video/{c.id}" if c.output_path and Path(c.output_path).exists() else None,
            "export_path": c.output_path,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return {"clips": result}


# ---------------------------------------------------------------------------
# CREATORS CRUD
# ---------------------------------------------------------------------------

@app.get("/api/creators")
def list_creators(db: Session = Depends(get_db)):
    creators = db.query(Creator).all()
    results = []
    now = now_utc()
    for cr in creators:
        agreements = (
            db.query(RightsAgreement)
            .filter(RightsAgreement.creator_id == cr.id)
            .order_by(RightsAgreement.created_at.desc())
            .all()
        )
        active_agree = next((a for a in agreements if a.status == "active" and (a.valid_to is None or a.valid_to >= now)), None)
        if active_agree:
            rights_status = "active"
        elif any(a.status == "pending" for a in agreements):
            rights_status = "pending"
        elif any(a.status == "expired" or (a.valid_to is not None and a.valid_to < now) for a in agreements):
            rights_status = "expired"
        elif any(a.status == "revoked" for a in agreements):
            rights_status = "revoked"
        else:
            rights_status = "none"
        results.append({
            "id": cr.id,
            "handle": cr.handle,
            "name": cr.name,
            "channel_url": cr.channel_url,
            "platform": cr.platform,
            "status": cr.status,
            "content_restrictions": cr.content_restrictions,
            "branding_guidelines": cr.branding_guidelines,
            "authorized_platforms": cr.authorized_platforms,
            "has_active_rights": active_agree is not None,
            "rights_status": rights_status,
            "agreement_type": active_agree.agreement_type if active_agree else None,
            "rev_share_percent": active_agree.rev_share_percent if active_agree else 0.0,
            "clip_count": db.query(FinalClip).filter(FinalClip.creator_id == cr.id).count(),
        })
    return {"creators": results}


@app.get("/api/creators/{creator_id}/rights")
def list_creator_rights(creator_id: str, db: Session = Depends(get_db)):
    creator = db.query(Creator).filter(Creator.id == creator_id).first()
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    agreements = (
        db.query(RightsAgreement)
        .filter(RightsAgreement.creator_id == creator_id)
        .order_by(RightsAgreement.created_at.desc())
        .all()
    )
    return {"creator_id": creator.id, "creator_handle": creator.handle,
            "agreements": [_rights_document_payload(a) for a in agreements]}


@app.post("/api/creators/{creator_id}/rights", status_code=201)
async def create_creator_rights(
    creator_id: str,
    agreement_type: str = Form(...),
    authorized_by: str = Form(...),
    status: str = Form("active"),
    valid_to: Optional[str] = Form(None),
    rev_share_percent: float = Form(0.0),
    notes: Optional[str] = Form(None),
    document: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    creator = db.query(Creator).filter(Creator.id == creator_id).first()
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    if agreement_type not in RIGHTS_AGREEMENT_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported agreement type")
    if status not in RIGHTS_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported rights status")
    if not authorized_by.strip():
        raise HTTPException(status_code=422, detail="Authorized by is required")
    if not math.isfinite(rev_share_percent) or not 0 <= rev_share_percent <= 100:
        raise HTTPException(status_code=422, detail="Revenue share must be between 0 and 100")

    expiry = _rights_expiry_date(valid_to)
    agreement_id = generate_id("ra")
    archived = await _archive_rights_document(document, creator_id, agreement_id)
    agreement = RightsAgreement(
        id=agreement_id,
        creator_id=creator_id,
        agreement_type=agreement_type,
        status=status,
        valid_to=expiry,
        rev_share_percent=rev_share_percent,
        documentation_path=str(archived),
        authorized_by=authorized_by.strip(),
        notes=notes.strip() if notes else None,
    )
    try:
        db.add(agreement)
        db.commit()
        db.refresh(agreement)
    except Exception:
        db.rollback()
        archived.unlink(missing_ok=True)
        raise
    return _rights_document_payload(agreement)


@app.put("/api/rights/{agreement_id}")
async def update_creator_rights(
    agreement_id: str,
    agreement_type: Optional[str] = Form(None),
    authorized_by: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    valid_to: Optional[str] = Form(None),
    rev_share_percent: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    document: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    agreement = db.query(RightsAgreement).filter(RightsAgreement.id == agreement_id).first()
    if not agreement:
        raise HTTPException(status_code=404, detail="Rights agreement not found")
    if agreement_type is not None:
        if agreement_type not in RIGHTS_AGREEMENT_TYPES:
            raise HTTPException(status_code=422, detail="Unsupported agreement type")
        agreement.agreement_type = agreement_type
    if status is not None:
        if status not in RIGHTS_STATUSES:
            raise HTTPException(status_code=422, detail="Unsupported rights status")
        agreement.status = status
    if authorized_by is not None:
        if not authorized_by.strip():
            raise HTTPException(status_code=422, detail="Authorized by is required")
        agreement.authorized_by = authorized_by.strip()
    if valid_to is not None:
        agreement.valid_to = _rights_expiry_date(valid_to)
    if rev_share_percent is not None:
        if not math.isfinite(rev_share_percent) or not 0 <= rev_share_percent <= 100:
            raise HTTPException(status_code=422, detail="Revenue share must be between 0 and 100")
        agreement.rev_share_percent = rev_share_percent
    if notes is not None:
        agreement.notes = notes.strip() or None

    archived = None
    if document is not None and document.filename:
        archived = await _archive_rights_document(document, agreement.creator_id, agreement_id)
        agreement.documentation_path = str(archived)
    try:
        db.commit()
        db.refresh(agreement)
    except Exception:
        db.rollback()
        if archived:
            archived.unlink(missing_ok=True)
        raise
    return _rights_document_payload(agreement)


@app.post("/api/rights/{agreement_id}/revoke")
def revoke_creator_rights(agreement_id: str, db: Session = Depends(get_db)):
    agreement = db.query(RightsAgreement).filter(RightsAgreement.id == agreement_id).first()
    if not agreement:
        raise HTTPException(status_code=404, detail="Rights agreement not found")
    agreement.status = "revoked"
    db.commit()
    return _rights_document_payload(agreement)


@app.get("/api/rights/{agreement_id}/document")
def get_rights_document(agreement_id: str, db: Session = Depends(get_db)):
    agreement = db.query(RightsAgreement).filter(RightsAgreement.id == agreement_id).first()
    if not agreement or not agreement.documentation_path:
        raise HTTPException(status_code=404, detail="Permission document not found")
    document = Path(agreement.documentation_path).resolve()
    agreements_root = settings.agreements_dir.resolve()
    if not document.is_relative_to(agreements_root) or not document.is_file():
        raise HTTPException(status_code=404, detail="Archived permission document is unavailable")
    return FileResponse(document, filename=document.name)


@app.post("/api/creators", status_code=201)
def create_creator(req: CreatorCreateRequest, db: Session = Depends(get_db)):
    existing = db.query(Creator).filter(Creator.handle == req.handle).first()
    if existing:
        raise HTTPException(status_code=409, detail="Creator handle already exists")
    cr = Creator(
        handle=req.handle,
        name=req.name,
        channel_url=req.channel_url,
        platform=req.platform,
        content_restrictions=req.content_restrictions,
        branding_guidelines=req.branding_guidelines,
        authorized_platforms=req.authorized_platforms or "tiktok,youtube_shorts,instagram_reels",
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return {"id": cr.id, "handle": cr.handle}


@app.put("/api/creators/{creator_id}")
def update_creator(creator_id: str, req: CreatorUpdateRequest, db: Session = Depends(get_db)):
    cr = db.query(Creator).filter(Creator.id == creator_id).first()
    if not cr:
        raise HTTPException(status_code=404, detail="Creator not found")
    if req.handle is not None:
        handle = req.handle.strip()
        if not handle:
            raise HTTPException(status_code=422, detail="Creator handle is required")
        duplicate = db.query(Creator).filter(Creator.handle == handle, Creator.id != creator_id).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="Creator handle already exists")
        cr.handle = handle
    if req.name is not None:
        cr.name = req.name
    if req.channel_url is not None:
        cr.channel_url = req.channel_url
    if req.platform is not None:
        cr.platform = req.platform
    if req.status is not None:
        cr.status = req.status
    if req.content_restrictions is not None:
        cr.content_restrictions = req.content_restrictions
    if req.branding_guidelines is not None:
        cr.branding_guidelines = req.branding_guidelines
    if req.authorized_platforms is not None:
        cr.authorized_platforms = req.authorized_platforms
    db.commit()
    return {"status": "updated", "creator_id": creator_id}


@app.delete("/api/creators/{creator_id}")
def delete_creator(creator_id: str, db: Session = Depends(get_db)):
    cr = db.query(Creator).filter(Creator.id == creator_id).first()
    if not cr:
        raise HTTPException(status_code=404, detail="Creator not found")
    clips = (
        db.query(FinalClip)
        .filter(FinalClip.creator_id == creator_id)
        .count()
    )
    media_count = db.query(MediaSource).filter(MediaSource.creator_id == creator_id).count()
    if clips or media_count:
        raise HTTPException(
            status_code=409,
            detail=f"Creator has {media_count} source video(s) and {clips} clip(s). Remove those records first; creator deletion is blocked to protect them.",
        )
    db.delete(cr)
    db.commit()
    return {"status": "deleted", "creator_id": creator_id}


# ---------------------------------------------------------------------------
# JOBS (preserved)
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(ProcessingJob).order_by(ProcessingJob.created_at.desc()).all()
    return {
        "jobs": [
            {
                "id": j.id,
                "media_id": j.media_id,
                "status": j.status,
                "current_stage": j.current_stage,
                "progress_percent": j.progress_percent,
                "error_message": j.error_message,
                "created_at": j.created_at.isoformat(),
            }
            for j in jobs
        ]
    }


# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------

@app.get("/api/models")
async def get_models(db: Session = Depends(get_db)):
    ollama_base = settings.ollama_base_url

    # Fetch available models
    tags_data: dict = {}
    ps_data: dict = {}
    ollama_connected = False
    ollama_version = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            tags_resp = await client.get(f"{ollama_base}/api/tags")
            ollama_connected = tags_resp.status_code == 200
            tags_data = tags_resp.json() if ollama_connected else {}
            ps_resp = await client.get(f"{ollama_base}/api/ps")
            ps_data = ps_resp.json() if ps_resp.status_code == 200 else {}
            if ollama_connected:
                version_resp = await client.get(f"{ollama_base}/api/version")
                if version_resp.status_code == 200:
                    ollama_version = version_resp.json().get("version")
    except Exception as exc:
        logger.warning("Ollama unreachable: %s", exc)

    # GPU memory via nvidia-smi
    gpu_info: dict = {}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            gpu_info = {
                "name": parts[0].strip(),
                "total_mb": int(parts[1].strip()),
                "used_mb": int(parts[2].strip()),
                "free_mb": int(parts[3].strip()),
            }
    except Exception:
        pass

    # System RAM via psutil
    ram_info: dict = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram_info = {
            "total_gb": round(vm.total / (1024 ** 3), 2),
            "used_gb": round(vm.used / (1024 ** 3), 2),
            "available_gb": round(vm.available / (1024 ** 3), 2),
        }
    except Exception:
        pass

    from src.reasoning.provider_config import get_ai_config
    ai = get_ai_config()
    cloud_models: list[dict[str, str]] = []
    if ai["cloud_api_key"] and "openai_compatible" in (ai["reasoning_provider"], ai["vision_provider"]):
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.get(
                    f"{ai['cloud_base_url'].rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {ai['cloud_api_key']}"},
                )
                response.raise_for_status()
                payload = response.json()
                cloud_models = [
                    {"name": model["id"], "provider": "Cloud", "size": None, "modified_at": None}
                    for model in payload.get("data", [])
                    if isinstance(model, dict) and isinstance(model.get("id"), str) and model["id"].strip()
                ]
        except Exception as exc:
            logger.warning("Could not list cloud models (%s).", type(exc).__name__)

    from src.core.models import SystemSetting
    reasoning_setting = db.query(SystemSetting).filter(SystemSetting.key == "ai_reasoning_model").first() or db.query(SystemSetting).filter(SystemSetting.key == "reasoning_model").first()
    vision_setting = db.query(SystemSetting).filter(SystemSetting.key == "ai_vision_model").first() or db.query(SystemSetting).filter(SystemSetting.key == "vision_model").first()
    active_reasoning = ai["reasoning_model"] or (reasoning_setting.value if reasoning_setting else settings.reasoning_model)
    active_vision = ai["vision_model"] or (vision_setting.value if vision_setting else settings.vision_model)

    loaded_models = ps_data.get("models", [])
    gpu_total = gpu_info.get("total_mb")
    gpu_used = gpu_info.get("used_mb")

    return {
        "ollama_connected": ollama_connected,
        "ollama_version": ollama_version,
        "models": tags_data.get("models", []),
        "cloud_models": cloud_models,
        "reasoning_provider": ai["reasoning_provider"],
        "vision_provider": ai["vision_provider"],
        "loaded_model": loaded_models[0].get("name", "") if loaded_models else "",
        "gpu_vram_used_mb": gpu_used,
        "gpu_vram_total_mb": gpu_total,
        "ram_used_gb": ram_info.get("used_gb"),
        "ram_total_gb": ram_info.get("total_gb"),
        "available_models": tags_data.get("models", []),
        "loaded_models": loaded_models,
        "gpu": gpu_info,
        "ram": ram_info,
        "active_reasoning_model": active_reasoning,
        "active_vision_model": active_vision,
    }


@app.post("/api/models/select")
def select_models(req: ModelSelectRequest, db: Session = Depends(get_db)):
    from src.core.models import SystemSetting
    if req.reasoning_provider not in ("ollama", "openai_compatible") or req.vision_provider not in ("ollama", "openai_compatible"):
        raise HTTPException(status_code=422, detail="Unsupported model provider")
    updates = []
    if req.reasoning_model is not None:
        key = "ai_cloud_reasoning_model" if req.reasoning_provider == "openai_compatible" else "ai_reasoning_model"
        updates.append((key, req.reasoning_model))
    if req.vision_model is not None:
        key = "ai_cloud_vision_model" if req.vision_provider == "openai_compatible" else "ai_vision_model"
        updates.append((key, req.vision_model))
    for key, value in updates:
        s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if s:
            s.value = value
            s.updated_at = _now()
        else:
            db.add(SystemSetting(key=key, value=value, updated_at=_now()))
    db.commit()
    return {"status": "updated"}


@app.get("/api/settings/ai")
def get_ai_settings(db: Session = Depends(get_db)):
    from src.core.models import SystemSetting
    values = {row.key: row.value for row in db.query(SystemSetting).filter(SystemSetting.key.like("ai_%")).all()}
    from src.core.secrets import has_usable_secret

    encrypted_key = values.get("ai_cloud_api_key", "")
    return {
        "reasoning_provider": values.get("ai_reasoning_provider", "ollama"),
        "vision_provider": values.get("ai_vision_provider", "ollama"),
        "transcription_provider": values.get("ai_transcription_provider", "faster_whisper"),
        "cloud_base_url": values.get("ai_cloud_base_url", "https://api.openai.com/v1"),
        "cloud_reasoning_model": values.get("ai_cloud_reasoning_model", ""),
        "cloud_vision_model": values.get("ai_cloud_vision_model", ""),
        "cloud_transcription_model": values.get("ai_cloud_transcription_model", "whisper-large-v3-turbo"),
        "api_key_configured": has_usable_secret(encrypted_key),
    }


@app.put("/api/settings/ai")
def update_ai_settings(req: AISettingsRequest, db: Session = Depends(get_db)):
    from src.core.models import SystemSetting
    if req.reasoning_provider not in ("ollama", "openai_compatible") or req.vision_provider not in ("ollama", "openai_compatible"):
        raise HTTPException(status_code=400, detail="Reasoning and vision providers must be Ollama or OpenAI-compatible.")
    if req.transcription_provider not in ("faster_whisper", "openai_compatible"):
        raise HTTPException(status_code=400, detail="Transcription provider must be Faster-Whisper or OpenAI-compatible.")
    base_url = req.cloud_base_url.strip().rstrip("/")
    parsed_url = urlsplit(base_url)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed_url.scheme not in ("https", "http") or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise HTTPException(status_code=400, detail="Enter a valid API base URL.")
    if parsed_url.scheme != "https" and not (parsed_url.scheme == "http" and parsed_url.hostname.lower() in local_hosts):
        raise HTTPException(status_code=400, detail="Use HTTPS for cloud endpoints. HTTP is allowed only for localhost.")
    updates = {
        "ai_reasoning_provider": req.reasoning_provider,
        "ai_vision_provider": req.vision_provider,
        "ai_transcription_provider": req.transcription_provider,
        "ai_cloud_base_url": base_url,
        "ai_cloud_reasoning_model": req.cloud_reasoning_model.strip(),
        "ai_cloud_vision_model": req.cloud_vision_model.strip(),
        "ai_cloud_transcription_model": req.cloud_transcription_model.strip() or "whisper-large-v3-turbo",
    }
    if req.clear_api_key:
        updates["ai_cloud_api_key"] = ""
    elif req.cloud_api_key:
        from src.core.secrets import encrypt_secret
        try:
            updates["ai_cloud_api_key"] = encrypt_secret(req.cloud_api_key)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        except Exception as exc:
            logger.warning("Could not encrypt cloud API key (%s).", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Unable to securely encrypt the API key.") from None
    for key, value in updates.items():
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row:
            row.value = value
            row.updated_at = _now()
        else:
            db.add(SystemSetting(key=key, value=value, updated_at=_now()))
    db.commit()
    return {"status": "updated", "api_key_configured": get_ai_settings(db)["api_key_configured"]}


@app.post("/api/settings/ai/test")
def test_ai_cloud_settings(req: AISettingsRequest, db: Session = Depends(get_db)):
    from src.reasoning.openai_compatible import list_models
    from src.reasoning.provider_config import get_ai_config
    current = get_ai_config()
    base_url = req.cloud_base_url.strip().rstrip("/") or current["cloud_base_url"]
    api_key = req.cloud_api_key or current["cloud_api_key"]
    if not api_key:
        raise HTTPException(status_code=400, detail="Enter an API key before testing the cloud connection.")
    try:
        models = list_models(base_url, api_key)
        return {"connected": True, "model_count": len(models), "models": [{"id": m.get("id", "")} for m in models[:100]]}
    except Exception as exc:
        logger.warning("Cloud provider connection check failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Connection failed. Check the endpoint, API key, and provider compatibility.") from None


@app.post("/api/models/evict")
async def evict_models():
    """Evict all loaded Ollama models by sending keep_alive=0."""
    ollama_base = settings.ollama_base_url
    evicted = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            ps_resp = await client.get(f"{ollama_base}/api/ps")
            if ps_resp.status_code == 200:
                loaded = ps_resp.json().get("models", [])
                for model in loaded:
                    name = model.get("name", "")
                    if name:
                        await client.post(
                            f"{ollama_base}/api/generate",
                            json={"model": name, "keep_alive": 0},
                        )
                        evicted.append(name)
    except Exception as exc:
        logger.warning("Evict error: %s", exc)
    return {"evicted": evicted}


# ---------------------------------------------------------------------------
# ANALYTICS
# ---------------------------------------------------------------------------

@app.get("/api/analytics")
def get_analytics(db: Session = Depends(get_db)):
    videos_processed = db.query(ProcessingJob).filter(ProcessingJob.status == "completed").count()
    clips_produced = db.query(FinalClip).count()
    approved = db.query(FinalClip).filter(FinalClip.approval_status == "approved").count()
    rejected = db.query(FinalClip).filter(FinalClip.approval_status == "rejected").count()
    total_reviewed = approved + rejected
    approval_rate = round(approved / total_reviewed * 100, 1) if total_reviewed else 0.0
    rejection_rate = round(rejected / total_reviewed * 100, 1) if total_reviewed else 0.0

    # Avg processing minutes (started_at to completed_at)
    avg_minutes = 0.0
    try:
        completed_jobs = (
            db.query(ProcessingJob)
            .filter(
                ProcessingJob.status == "completed",
                ProcessingJob.started_at.isnot(None),
                ProcessingJob.completed_at.isnot(None),
            )
            .all()
        )
        if completed_jobs:
            durations = [
                (j.completed_at - j.started_at).total_seconds() / 60
                for j in completed_jobs
                if j.completed_at and j.started_at
            ]
            avg_minutes = round(sum(durations) / len(durations), 2) if durations else 0.0
    except Exception:
        pass

    # By creator
    creators = db.query(Creator).all()
    by_creator = []
    for cr in creators:
        c_clips = db.query(FinalClip).filter(FinalClip.creator_id == cr.id).count()
        c_approved = db.query(FinalClip).filter(
            FinalClip.creator_id == cr.id, FinalClip.approval_status == "approved"
        ).count()
        c_rejected = db.query(FinalClip).filter(
            FinalClip.creator_id == cr.id, FinalClip.approval_status == "rejected"
        ).count()
        if c_clips > 0:
            by_creator.append({
                "handle": cr.handle,
                "clips": c_clips,
                "approved": c_approved,
                "rejected": c_rejected,
            })

    # Delivery by platform
    all_deliveries = db.query(DeliveryLog).all()
    platform_counts: dict[str, int] = {}
    for d in all_deliveries:
        platform_counts[d.platform] = platform_counts.get(d.platform, 0) + 1
    delivery_by_platform = [
        {"platform": p, "count": c} for p, c in platform_counts.items()
    ]

    return {
        "videos_processed": videos_processed,
        "clips_produced": clips_produced,
        "approval_rate_pct": approval_rate,
        "rejection_rate_pct": rejection_rate,
        "avg_processing_minutes": avg_minutes,
        "by_creator": by_creator,
        "delivery_by_platform": delivery_by_platform,
    }
