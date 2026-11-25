"""Integration tests for FastAPI dashboard and review endpoints."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from src.dashboard.api import app
from src.core.db import get_db, Base
from src.core.models import Creator, RightsAgreement, FinalClip, ProcessingJob, MediaSource
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def test_client(tmp_path):
    test_db_url = f"sqlite:///{tmp_path / 'test_api.db'}"
    engine = create_engine(test_db_url)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Seed data
    db = TestingSession()
    cr = Creator(
        handle="@TestStreamer",
        name="Test Streamer",
        channel_url="https://youtube.com/@Test",
        platform="youtube",
        status="active",
    )
    db.add(cr)
    db.flush()

    media = MediaSource(
        creator_id=cr.id,
        title="GTA Livestream",
        file_path="test.mp4",
        duration_seconds=120.0,
    )
    db.add(media)
    db.flush()

    job = ProcessingJob(media_id=media.id, status="completed", current_stage="completed")
    db.add(job)
    db.flush()

    clip = FinalClip(
        job_id=job.id,
        creator_id=cr.id,
        title="Epic Helicopter Jump",
        hook_text="WATCH THIS INSANE STUNT 🚀",
        optimal_start=10.0,
        optimal_end=35.0,
        duration=25.0,
        virality_score=95,
        humor_score=85,
        action_score=98,
        approval_status="pending_review",
    )
    db.add(clip)
    db.commit()
    db.close()

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_index_serves_html(test_client):
    res = test_client.get("/")
    assert res.status_code == 200
    assert "GTA CLIP COMMAND" in res.text
    assert "phone-mockup" in res.text


def test_list_clips_returns_seeded_clip(test_client):
    res = test_client.get("/api/clips")
    assert res.status_code == 200
    data = res.json()
    assert "clips" in data
    assert len(data["clips"]) == 1
    c = data["clips"][0]
    assert c["title"] == "Epic Helicopter Jump"
    assert c["virality_score"] == 95
    assert c["approval_status"] == "pending_review"


def test_approve_clip_workflow(test_client):
    # Get clip ID
    list_res = test_client.get("/api/clips")
    clip_id = list_res.json()["clips"][0]["id"]

    # Approve
    res = test_client.post(f"/api/clips/{clip_id}/approve")
    assert res.status_code == 200
    assert res.json()["approval_status"] == "approved"

    # Verify status changed
    updated = test_client.get("/api/clips").json()["clips"][0]
    assert updated["approval_status"] == "approved"


def test_save_ai_settings_encrypts_api_key_on_sqlite(test_client, monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUD_KEY_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(
        "src.core.secrets.get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path, database_url="sqlite:///test_api.db"),
    )
    api_key = "sk-test-not-a-real-provider-key"

    response = test_client.put(
        "/api/settings/ai",
        json={"cloud_api_key": api_key},
    )

    assert response.status_code == 200
    assert response.json()["api_key_configured"] is True
    settings = test_client.get("/api/settings/ai")
    assert settings.status_code == 200
    assert settings.json()["api_key_configured"] is True
    assert api_key not in settings.text


def test_cloud_connection_returns_available_model_ids(test_client, monkeypatch):
    available_models = [{"id": "reasoning-model"}, {"id": "vision-model"}]
    monkeypatch.setattr("src.reasoning.openai_compatible.list_models", lambda base_url, api_key: available_models)

    response = test_client.post(
        "/api/settings/ai/test",
        json={"cloud_base_url": "https://api.example.com/v1", "cloud_api_key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json()["model_count"] == 2
    assert response.json()["models"] == available_models
