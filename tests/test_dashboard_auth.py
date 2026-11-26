"""Tests for the single-owner dashboard authentication gate."""

from fastapi.testclient import TestClient

from src.dashboard.api import app
from src.dashboard.auth import (
    AuthSettings,
    SESSION_TTL_SECONDS,
    create_session_token,
    get_auth_settings,
    session_token_is_valid,
)


def test_auth_settings_load_from_local_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("AUTH_ENABLED", "DASHBOARD_USERNAME", "DASHBOARD_PASSWORD", "DASHBOARD_SESSION_SECRET"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "AUTH_ENABLED=true\n"
        "DASHBOARD_USERNAME=owner\n"
        "DASHBOARD_PASSWORD=a-long-test-password-that-is-safe\n"
        "DASHBOARD_SESSION_SECRET=test-session-secret-with-more-than-32-bytes\n",
        encoding="utf-8",
    )

    settings = get_auth_settings()

    assert settings.enabled is True
    assert settings.username == "owner"
    assert settings.password == "a-long-test-password-that-is-safe"


def test_session_tokens_are_signed_and_expire():
    settings = AuthSettings(
        enabled=True,
        username="owner",
        password="a-long-test-password-that-is-safe",
        session_secret="test-session-secret-with-more-than-32-bytes",
    )
    token = create_session_token(settings, now=1_000)

    assert session_token_is_valid(token, settings, now=1_001)
    assert not session_token_is_valid(token + "tampered", settings, now=1_001)
    assert not session_token_is_valid(token, settings, now=1_000 + SESSION_TTL_SECONDS)


def test_single_owner_login_protects_api_and_logout_clears_session(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_USERNAME", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "a-long-test-password-that-is-safe")
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "test-session-secret-with-more-than-32-bytes")

    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/auth/status").json() == {
            "auth_enabled": True,
            "authenticated": False,
        }
        assert client.get("/api/not-a-real-route").status_code == 401

        rejected = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "incorrect"},
        )
        assert rejected.status_code == 401

        accepted = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "a-long-test-password-that-is-safe"},
        )
        assert accepted.status_code == 200
        cookie = accepted.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "secure" in cookie
        assert "samesite=lax" in cookie
        assert client.get("/api/not-a-real-route").status_code == 404
        assert client.get("/api/auth/status").json()["authenticated"] is True

        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/not-a-real-route").status_code == 401
