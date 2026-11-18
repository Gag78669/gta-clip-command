"""Render worker service: consume durable Redis job IDs and execute one pipeline at a time."""
from __future__ import annotations

import logging
import time

from src.core.config import get_settings
from src.core.db import SessionLocal, init_db, tenant_id
from src.core.logger import get_logger
from src.core.models import ProcessingJob, MediaSource, Creator
from src.dashboard.worker import get_worker
from src.core.queue import get_redis_client

logger = get_logger("scripts.hosted_worker")


def main() -> None:
    cfg = get_settings()
    if not cfg.redis_url:
        raise RuntimeError("REDIS_URL is required for the hosted worker.")
    if not cfg.auth_enabled or not cfg.storage_bucket or not cfg.cloud_key_encryption_key:
        raise RuntimeError("Hosted worker requires AUTH_ENABLED, S3-compatible object storage, and CLOUD_KEY_ENCRYPTION_KEY.")
    init_db()
    pipeline_worker = get_worker()
    queue = get_redis_client(cfg.redis_url)
    logger.info("Hosted pipeline worker listening for jobs")
    while True:
        item = queue.blpop("gta:processing:jobs", timeout=5)
        if not item:
            continue
        job_id = item[1]
        db = SessionLocal()
        try:
            job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if not job or job.status not in ("queued", "failed"):
                continue
            media = db.query(MediaSource).filter(MediaSource.id == job.media_id).first()
            creator = db.query(Creator).filter(Creator.id == media.creator_id).first() if media else None
            if not media or not creator:
                job.status = "failed"
                job.error_message = "Source video or creator record is unavailable."
                db.commit()
                continue
            owner_id, media_id, creator_handle, video_path = job.owner_id, media.id, creator.handle, media.file_path
        finally:
            db.close()
        if not owner_id:
            logger.error("Refusing hosted job %s without an account owner", job_id)
            continue
        try:
            pipeline_worker.enqueue(job_id, media_id, creator_handle, video_path, owner_id)
            # PipelineWorker's dispatcher is serial; wait until this job reaches a terminal state.
            while True:
                time.sleep(2)
                token = tenant_id.set(owner_id)
                try:
                    with SessionLocal() as check_db:
                        current = check_db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                        if not current or current.status in ("completed", "failed", "cancelled"):
                            break
                finally:
                    tenant_id.reset(token)
        except Exception:
            logger.exception("Worker service failed to execute job %s", job_id)
            # Requeue with a bounded attempt counter carried by Redis. A poison job is marked failed.
            attempts_key = f"gta:processing:attempts:{job_id}"
            attempts = queue.incr(attempts_key)
            if attempts <= 2:
                queue.rpush("gta:processing:jobs", job_id)
            else:
                token = tenant_id.set(owner_id)
                try:
                    with SessionLocal() as fail_db:
                        current = fail_db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                        if current:
                            current.status = "failed"
                            current.error_message = "Worker could not start this job after multiple attempts."
                            fail_db.commit()
                finally:
                    tenant_id.reset(token)


if __name__ == "__main__":
    main()
