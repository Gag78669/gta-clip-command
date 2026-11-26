"""Single-owner dashboard authentication using signed, expiring cookies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

from src.core.config import Settings

SESSION_COOKIE_NAME = "gta_dashboard_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool
    username: str
    password: str = field(repr=False)
    session_secret: str = field(repr=False)


def get_auth_settings() -> AuthSettings:
    config = Settings()
    enabled = config.auth_enabled
    username = config.dashboard_username
    password = config.dashboard_password.get_secret_value()
    session_secret = config.dashboard_session_secret.get_secret_value()

    if enabled:
        missing = [
            name
            for name, value in (
                ("DASHBOARD_USERNAME", username),
                ("DASHBOARD_PASSWORD", password),
                ("DASHBOARD_SESSION_SECRET", session_secret),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Set the required dashboard authentication variables: " + ", ".join(missing))
        if len(password) < 16:
            raise RuntimeError("DASHBOARD_PASSWORD must be at least 16 characters long.")
        if len(session_secret.encode("utf-8")) < 32:
            raise RuntimeError("DASHBOARD_SESSION_SECRET must contain at least 32 bytes.")

    return AuthSettings(enabled, username, password, session_secret)


def credentials_are_valid(username: str, password: str, settings: AuthSettings) -> bool:
    username_matches = hmac.compare_digest(username.encode("utf-8"), settings.username.encode("utf-8"))
    password_matches = hmac.compare_digest(password.encode("utf-8"), settings.password.encode("utf-8"))
    return username_matches and password_matches


def create_session_token(settings: AuthSettings, *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = json.dumps(
        {"sub": settings.username, "exp": issued_at + SESSION_TTL_SECONDS},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_payload = _encode(payload)
    signature = hmac.new(
        settings.session_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return encoded_payload + "." + _encode(signature)


def session_token_is_valid(token: str | None, settings: AuthSettings, *, now: int | None = None) -> bool:
    if not token or not settings.enabled:
        return False
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        provided_signature = _decode(encoded_signature)
        expected_signature = hmac.new(
            settings.session_secret.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            return False
        payload = json.loads(_decode(encoded_payload))
        current_time = int(time.time()) if now is None else now
        return (
            hmac.compare_digest(str(payload.get("sub", "")).encode("utf-8"), settings.username.encode("utf-8"))
            and isinstance(payload.get("exp"), int)
            and payload["exp"] > current_time
        )
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return False


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
