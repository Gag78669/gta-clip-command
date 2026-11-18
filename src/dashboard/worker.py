"""Background pipeline queue worker for GTA CLIP COMMAND dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import sys
import threading
import time
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path so scripts.run_pipeline can be imported
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.db import get_db_context
from src.core.config import get_settings
from src.core.logger import get_logger
from src.core.models import ProcessingJob, MediaSource, FinalClip, CandidateClip
from src.core.exceptions import PipelineCancelled

logger = get_logger("dashboard.worker")

# ---------------------------------------------------------------------------
# 14-stage progress map  (stage_name -> percent 5..95)
# ---------------------------------------------------------------------------
STAGE_PROGRESS: dict[str, int] = {
    "intake":        5,
    "rights_check":  10,
    "audio_demux":   15,
    "transcription": 25,
    "signals":       38,
    "scene_detect":  45,
    "fusion":        55,
    "ai_visual":     62,
    "ai_narrative":  68,
    "ai_evaluation": 72,
    "composition":   80,
    "rendering":     88,
    "qa":            92,
    "finalize":      95,
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PipelineWorker:
    """Singleton background worker that runs the clipping pipeline in a thread pool."""

    def __init__(self) -> None:
        self._job_queue: queue.Queue[str] = queue.Queue()
        self._state: dict[str, dict[str, Any]] = {}   # job_id -> state dict
        self._lock = threading.Lock()
        self._pause_events: dict[str, threading.Event] = {}
        self._cancel_flags: dict[str, threading.Event] = {}

        # SSE subscribers – list of asyncio.Queue, one per connected client
        self._sse_queues: list[asyncio.Queue] = []
        self._sse_lock = threading.Lock()

        # Thread pool for pipeline execution (1 job at a time by design)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")

        # Drain stale running jobs on startup
        self._reset_stale_running_jobs()

        # Start the dispatcher thread
        self._thread = threading.Thread(target=self._dispatcher_loop, daemon=True, name="worker-dispatcher")
        self._thread.start()
        self._recover_queued_jobs()
        logger.info("PipelineWorker started.")

    # ------------------------------------------------------------------
    # Startup cleanup
    # ------------------------------------------------------------------
    def _reset_stale_running_jobs(self) -> None:
        try:
            with get_db_context() as db:
                stale = db.query(ProcessingJob).filter(ProcessingJob.status == "running").all()
                for j in stale:
                    j.status = "failed"
                    j.error_message = "Server restarted during processing"
                    if get_settings().auth_enabled:
                        from src.core.accounts import settle_reservation
                        settle_reservation(db, j.id, consume=j.progress_percent >= 25)
                    logger.warning("Marked stale job %s as failed.", j.id)
        except Exception as exc:
            logger.error("Failed to reset stale jobs: %s", exc)

    def _recover_queued_jobs(self) -> None:
        """Rebuild the in-memory queue from durable database rows after a restart."""
        try:
            with get_db_context() as db:
                jobs = (db.query(ProcessingJob)
                        .filter(ProcessingJob.status == "queued")
                        .order_by(ProcessingJob.created_at.asc()).all())
                pending = [(job.id, job.media_id, job.media.creator.handle if job.media and job.media.creator else "",
                            job.media.file_path if job.media else "", job.owner_id) for job in jobs]
            for job_id, media_id, creator_handle, video_path, owner_id in pending:
                if creator_handle and video_path:
                    self.enqueue(job_id, media_id, creator_handle, video_path, owner_id)
        except Exception as exc:
            logger.exception("Could not restore queued jobs after startup: %s", exc)

    # ------------------------------------------------------------------
    # SSE helpers
    # ------------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE client. Returns the client's asyncio.Queue."""
        q: asyncio.Queue = asyncio.Queue()
        with self._sse_lock:
            self._sse_queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._sse_lock:
            try:
                self._sse_queues.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: dict[str, Any]) -> None:
        from src.core.db import tenant_id
        owner = tenant_id.get()
        event.setdefault("owner_id", owner)
        payload = json.dumps(event)
        with self._sse_lock:
            dead: list[asyncio.Queue] = []
            for q in self._sse_queues:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    dead.append(q)
            for d in dead:
                self._sse_queues.remove(d)
        cfg = get_settings()
        if cfg.redis_url:
            try:
                from src.core.queue import get_redis_client
                get_redis_client(cfg.redis_url).publish("gta:processing:events", payload)
            except Exception as exc:
                logger.warning("Could not publish pipeline event: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def enqueue(self, job_id: str, media_id: str, creator_handle: str, video_path: str, owner_id: str | None = None) -> None:
        with self._lock:
            self._state[job_id] = {
                "job_id": job_id,
                "media_id": media_id,
                "creator_handle": creator_handle,
                "video_path": video_path,
                "owner_id": owner_id,
                "status": "queued",
                "stage": "intake",
                "progress": 0,
                "error": None,
            }
            self._pause_events[job_id] = threading.Event()
            self._pause_events[job_id].set()   # not paused
            self._cancel_flags[job_id] = threading.Event()
        self._job_queue.put(job_id)
        self._broadcast({"type": "job_queued", "job_id": job_id})
        logger.info("Job %s enqueued.", job_id)

    def pause(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._state and self._state[job_id]["status"] == "running":
                self._pause_events[job_id].clear()
                self._state[job_id]["status"] = "paused"
                self._update_db_status(job_id, "paused")
                self._broadcast({"type": "job_paused", "job_id": job_id})

    def resume(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._state and self._state[job_id]["status"] == "paused":
                self._state[job_id]["status"] = "running"
                self._pause_events[job_id].set()
                self._update_db_status(job_id, "running")
                self._broadcast({"type": "job_resumed", "job_id": job_id})

    def cancel(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._cancel_flags:
                self._cancel_flags[job_id].set()
            if job_id in self._state:
                self._state[job_id]["status"] = "cancelled"
            if job_id in self._pause_events:
                self._pause_events[job_id].set()  # unblock paused thread
        self._update_db_status(job_id, "cancelled")
        self._broadcast({"type": "job_cancelled", "job_id": job_id})

    def retry(self, job_id: str) -> None:
        with self._lock:
            st = self._state.get(job_id)
            if st and st["status"] in ("failed", "cancelled"):
                st["status"] = "queued"
                st["error"] = None
                st["progress"] = 0
                self._pause_events[job_id] = threading.Event()
                self._pause_events[job_id].set()
                self._cancel_flags[job_id] = threading.Event()
        self._update_db_status(job_id, "queued")
        self._job_queue.put(job_id)
        self._broadcast({"type": "job_queued", "job_id": job_id})

    def remove(self, job_id: str) -> bool:
        with self._lock:
            st = self._state.get(job_id)
            if st and st["status"] in ("queued", "failed", "cancelled"):
                del self._state[job_id]
                self._pause_events.pop(job_id, None)
                self._cancel_flags.pop(job_id, None)
                return True
        return False

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._state.get(job_id, {}))

    # ------------------------------------------------------------------
    # Dispatcher loop (runs in daemon thread)
    # ------------------------------------------------------------------
    def _dispatcher_loop(self) -> None:
        while True:
            job_id = self._job_queue.get()
            with self._lock:
                st = self._state.get(job_id)
            if st is None:
                continue
            if st["status"] not in ("queued",):
                continue
            # Submit to thread pool, block dispatcher until complete
            future = self._executor.submit(self._run_job, job_id)
            future.result()  # wait; ensures serial execution

    # ------------------------------------------------------------------
    # Job execution
    # ------------------------------------------------------------------
    def _run_job(self, job_id: str) -> None:
        with self._lock:
            st = self._state.get(job_id)
            if st is None:
                return
            creator_handle = st["creator_handle"]
            video_path = st["video_path"]
            owner_id = st.get("owner_id")

        from src.core.db import tenant_id
        tenant_token = tenant_id.set(owner_id)

        logger.info("Starting pipeline for job %s", job_id)
        self._update_job_state(job_id, status="running", stage="intake", progress=5)
        self._update_db_status(job_id, "running", started_at=_now())
        self._broadcast({"type": "job_started", "job_id": job_id})

        stop_progress_watch = threading.Event()
        progress_watch = threading.Thread(
            target=self._watch_db_progress,
            args=(job_id, stop_progress_watch),
            daemon=True,
            name=f"progress-{job_id}",
        )
        log_handlers: list[tuple[logging.Logger, logging.Handler]] = []
        downloaded_media: Path | None = None
        try:
            # Import run_pipeline lazily to avoid circular import issues at module load
            import importlib
            pipeline_module = importlib.import_module("scripts.run_pipeline")
            run_pipeline = pipeline_module.run_pipeline

            # Pipeline loggers do not propagate to the root logger, so subscribe
            # directly to the project loggers and keep a per-job durable log.
            from src.core.logger import _LOGGERS
            job_log_path = Path("data/logs/jobs") / f"{job_id}.log"
            job_log_path.parent.mkdir(parents=True, exist_ok=True)
            job_handler = logging.FileHandler(job_log_path, encoding="utf-8")
            job_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
            stream_handler = _JobEventLogHandler(self, job_id)
            for project_logger in list(_LOGGERS.values()):
                project_logger.addHandler(job_handler)
                project_logger.addHandler(stream_handler)
                log_handlers.append((project_logger, job_handler))
                log_handlers.append((project_logger, stream_handler))

            progress_watch.start()

            # Wrap run_pipeline with pause/cancel checkpoints
            # We rely on run_pipeline being synchronous and returning final_clips list
            pipeline_video_path = video_path
            if video_path.startswith("s3://"):
                from src.core.object_storage import download
                downloaded_media = Path(tempfile.gettempdir()) / f"gta-source-{uuid.uuid4().hex}{Path(video_path).suffix or '.mp4'}"
                download(video_path, downloaded_media)
                pipeline_video_path = str(downloaded_media)
            final_clips = run_pipeline(
                video_path=pipeline_video_path,
                creator_handle=creator_handle,
                job_id=job_id,
                checkpoint=lambda: self._pipeline_checkpoint(job_id),
            )

            # Count results
            clip_count = len(final_clips) if final_clips else 0
            if owner_id:
                self._persist_outputs_to_object_storage(job_id, owner_id)

            # Count candidates from DB
            candidate_count = 0
            try:
                with get_db_context() as db:
                    candidate_count = db.query(CandidateClip).filter(
                        CandidateClip.job_id == job_id
                    ).count()
            except Exception:
                pass

            # Finalize
            self._update_job_state(job_id, status="completed", stage="finalize", progress=100)
            self._update_db_status(
                job_id, "completed",
                completed_at=_now(),
                candidate_count=candidate_count,
                clip_count=clip_count,
            )
            self._broadcast({
                "type": "job_completed",
                "job_id": job_id,
                "clip_count": clip_count,
            })
            logger.info("Job %s completed. %d clips produced.", job_id, clip_count)

        except PipelineCancelled:
            self._update_job_state(job_id, status="cancelled", stage="cancelled")
            self._update_db_status(job_id, "cancelled", completed_at=_now())
            self._broadcast({"type": "job_cancelled", "job_id": job_id})
            logger.info("Job %s cancelled at a pipeline checkpoint.", job_id)
        except Exception as exc:
            error_msg = str(exc)
            logger.exception("Job %s failed: %s", job_id, error_msg)
            self._update_job_state(job_id, status="failed", error=error_msg)
            self._update_db_status(job_id, "failed", completed_at=_now(), error_message=error_msg)
            self._broadcast({"type": "job_failed", "job_id": job_id, "error": error_msg})
        finally:
            stop_progress_watch.set()
            if progress_watch.is_alive():
                progress_watch.join(timeout=2)
            for project_logger, handler in log_handlers:
                project_logger.removeHandler(handler)
            for handler in {handler for _, handler in log_handlers}:
                handler.close()
            if downloaded_media:
                downloaded_media.unlink(missing_ok=True)
            tenant_id.reset(tenant_token)

    def _persist_outputs_to_object_storage(self, job_id: str, owner_id: str) -> None:
        from src.core.object_storage import configured, upload_file
        from src.core.models import FinalClip
        if not configured():
            raise RuntimeError("Object storage is required to persist hosted clip outputs.")
        with get_db_context() as db:
            clips = db.query(FinalClip).filter(FinalClip.job_id == job_id).all()
            for clip in clips:
                if clip.output_path and not clip.output_path.startswith("s3://"):
                    path = Path(clip.output_path)
                    if path.is_file():
                        clip.output_path = upload_file(path, f"users/{owner_id}/clips/{clip.id}{path.suffix or '.mp4'}", "video/mp4")
                        path.unlink(missing_ok=True)
                if clip.thumbnail_path and not clip.thumbnail_path.startswith("s3://"):
                    path = Path(clip.thumbnail_path)
                    if path.is_file():
                        clip.thumbnail_path = upload_file(path, f"users/{owner_id}/thumbnails/{clip.id}{path.suffix or '.jpg'}", "image/jpeg")
                        path.unlink(missing_ok=True)

    def _watch_db_progress(self, job_id: str, stop_event: threading.Event) -> None:
        """Keep in-memory state and SSE clients aligned with pipeline DB checkpoints."""
        while not stop_event.wait(1):
            tenant_token = None
            try:
                with self._lock:
                    owner = (self._state.get(job_id) or {}).get("owner_id")
                from src.core.db import tenant_id
                tenant_token = tenant_id.set(owner)
                with get_db_context() as db:
                    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                    if not job:
                        return
                    snapshot = {
                        "status": job.status,
                        "stage": job.current_stage,
                        "progress": job.progress_percent,
                        "candidate_count": job.candidate_count,
                        "clip_count": job.clip_count,
                        "error": job.error_message,
                    }
                    if job.current_stage in ("transcription", "signals", "scene_detect", "fusion", "ai_visual", "ai_narrative", "ai_evaluation", "composition", "rendering", "qa", "finalize", "completed"):
                        from src.core.accounts import settle_reservation
                        settle_reservation(db, job_id, consume=True)
                with self._lock:
                    state = self._state.get(job_id)
                    if state is None:
                        return
                    changed = any(state.get(key) != value for key, value in snapshot.items())
                    if changed:
                        state.update(snapshot)
                if changed:
                    self._broadcast({"type": "job_update", "job_id": job_id, **snapshot})
            except Exception as exc:
                logger.warning("Could not refresh progress for %s: %s", job_id, exc)
            finally:
                if tenant_token is not None:
                    tenant_id.reset(tenant_token)

    def _pipeline_checkpoint(self, job_id: str) -> None:
        """Wait while paused and stop cleanly when cancelled at a safe boundary."""
        while True:
            with self._lock:
                pause_event = self._pause_events.get(job_id)
                cancel_event = self._cancel_flags.get(job_id)
            if cancel_event is None or cancel_event.is_set():
                raise PipelineCancelled()
            try:
                with get_db_context() as db:
                    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                    status = job.status if job else "cancelled"
                if status == "cancelled":
                    raise PipelineCancelled()
                if status == "paused":
                    time.sleep(0.25)
                    continue
            except PipelineCancelled:
                raise
            except Exception as exc:
                logger.warning("Could not read job control state for %s: %s", job_id, exc)
            if pause_event is None or pause_event.wait(timeout=0.25):
                break
        if cancel_event.is_set():
            raise PipelineCancelled()

    # ------------------------------------------------------------------
    # DB sync helpers
    # ------------------------------------------------------------------
    def _update_db_status(
        self,
        job_id: str,
        status: str,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        candidate_count: int | None = None,
        clip_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        try:
            with get_db_context() as db:
                j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                if j:
                    j.status = status
                    if started_at is not None:
                        j.started_at = started_at
                    if completed_at is not None:
                        j.completed_at = completed_at
                    if candidate_count is not None:
                        j.candidate_count = candidate_count
                    if clip_count is not None:
                        j.clip_count = clip_count
                    if error_message is not None:
                        j.error_message = error_message
                        if status in ("completed", "failed", "cancelled"):
                            from src.core.accounts import settle_reservation
                            settle_reservation(db, job_id, consume=(status == "completed" or j.progress_percent >= 25))
        except Exception as exc:
            logger.error("DB sync error for job %s: %s", job_id, exc)

    def _update_job_state(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            if job_id in self._state:
                self._state[job_id].update(kwargs)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_worker_instance: PipelineWorker | None = None
_worker_lock = threading.Lock()


def get_worker() -> PipelineWorker:
    """Return the singleton PipelineWorker, creating it on first call."""
    global _worker_instance
    if _worker_instance is None:
        with _worker_lock:
            if _worker_instance is None:
                _worker_instance = PipelineWorker()
    return _worker_instance


class _JobEventLogHandler(logging.Handler):
    """Fan out one job's pipeline logs to Monitor over SSE and retain a short buffer."""

    def __init__(self, worker: PipelineWorker, job_id: str) -> None:
        super().__init__(logging.INFO)
        self.worker = worker
        self.job_id = job_id
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            with self.worker._lock:
                state = self.worker._state.get(self.job_id)
                if state is not None:
                    logs = state.setdefault("logs", [])
                    logs.append(line)
                    del logs[:-300]
            self.worker._broadcast({"type": "log_line", "job_id": self.job_id, "message": line})
        except Exception:
            self.handleError(record)
