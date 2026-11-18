"""Account authentication, email verification, and free-tier usage accounting."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import update
from src.core.config import get_settings
from src.core.models import AuthSession, DailyUsage, EmailVerification, QuotaReservation, UserAccount


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 310_000)
    except Exception:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def send_verification_email(email: str, token: str) -> None:
    cfg = get_settings()
    if not cfg.smtp_host or not cfg.smtp_from:
        raise RuntimeError("Email verification is not configured. Set SMTP_HOST and SMTP_FROM.")
    origin = cfg.frontend_origin.rstrip("/")
    link = f"{origin}/?verify={token}"
    msg = EmailMessage()
    msg["Subject"] = "Verify your GTA Clip Command account"
    msg["From"] = cfg.smtp_from
    msg["To"] = email
    msg.set_content(f"Verify your email address by opening this link within 24 hours:\n\n{link}\n")
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as server:
        server.starttls()
        if cfg.smtp_user:
            server.login(cfg.smtp_user, cfg.smtp_password)
        server.send_message(msg)


def new_verification(db, user_id: str, email: str) -> None:
    token = secrets.token_urlsafe(32)
    db.add(EmailVerification(token_hash=_hash_token(token), user_id=user_id,
                             expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=24)))
    db.commit()
    send_verification_email(email, token)


def new_session(db, user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    db.add(AuthSession(token_hash=_hash_token(token), user_id=user_id,
                       expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)))
    db.commit()
    return token


def session_user(db, token: str | None) -> UserAccount | None:
    if not token:
        return None
    row = db.query(AuthSession).filter(AuthSession.token_hash == _hash_token(token)).first()
    if not row or row.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        return None
    user = db.query(UserAccount).filter(UserAccount.id == row.user_id).first()
    return user if user and user.verified_at else None


def reserve_seconds(db, user_id: str, job_id: str, seconds: int) -> None:
    """Atomically reserve duration against the account's current UTC-day allowance."""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    day = datetime.now(timezone.utc).date().isoformat()
    dialect = db.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(DailyUsage).values(user_id=user_id, usage_date=day, reserved_seconds=0, consumed_seconds=0)
    stmt = stmt.on_conflict_do_nothing(index_elements=["user_id", "usage_date"])
    db.execute(stmt)
    cfg = get_settings()
    result = db.execute(
        update(DailyUsage)
        .where(DailyUsage.user_id == user_id, DailyUsage.usage_date == day,
               DailyUsage.consumed_seconds + DailyUsage.reserved_seconds + seconds <= cfg.daily_free_seconds)
        .values(reserved_seconds=DailyUsage.reserved_seconds + seconds)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ValueError("Daily free processing limit reached. Try again after 00:00 UTC.")
    db.add(QuotaReservation(job_id=job_id, user_id=user_id, usage_date=day, seconds=seconds, status="reserved"))


def settle_reservation(db, job_id: str, *, consume: bool) -> None:
    reservations = db.query(QuotaReservation).filter(QuotaReservation.job_id == job_id, QuotaReservation.status == "reserved").all()
    for reservation in reservations:
        row = db.query(DailyUsage).filter(DailyUsage.user_id == reservation.user_id, DailyUsage.usage_date == reservation.usage_date).first()
        if row:
            row.reserved_seconds = max(0, row.reserved_seconds - reservation.seconds)
            if consume:
                row.consumed_seconds += reservation.seconds
        reservation.status = "consumed" if consume else "released"


def usage_payload(db, user_id: str) -> dict:
    day = datetime.now(timezone.utc).date().isoformat()
    row = db.query(DailyUsage).filter(DailyUsage.user_id == user_id, DailyUsage.usage_date == day).first()
    reserved = row.reserved_seconds if row else 0
    consumed = row.consumed_seconds if row else 0
    cap = get_settings().daily_free_seconds
    return {"usage_date": day, "limit_seconds": cap, "consumed_seconds": consumed,
            "reserved_seconds": reserved, "remaining_seconds": max(0, cap - consumed - reserved)}
