"""Comprehensive integration tests for GTA CLIP COMMAND Control Center endpoints."""

import pytest
from fastapi.testclient import TestClient
from src.dashboard.api import app
from src.core.db import get_db, Base
from src.core.models import Creator, RightsAgreement, FinalClip, ProcessingJob, MediaSource, SystemSetting
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def control_client(tmp_path):
    test_db_url = f"sqlite:///{tmp_path / 'test_control.db'}"
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
        handle="@TrevorGamer",
        name="Trevor Philips",
        channel_url="https://youtube.com/@Trevor",
        platform="youtube",
        status="active",
        content_restrictions="No PG content",
        branding_guidelines="Trevor orange font",
    )
    db.add(cr)
    db.flush()

    ra = RightsAgreement(
        creator_id=cr.id,
        agreement_type="exclusive",
        status="active",
        rev_share_percent=50.0,
        documentation_path="/docs/agreement.pdf",
        authorized_by="Management",
    )
    db.add(ra)
    db.flush()

    media = MediaSource(
        creator_id=cr.id,
        title="Sandy Shores Heist",
        file_path="heist.mp4",
        duration_seconds=300.0,
        sha256_hash="dummy_sha256_123456",
    )
    db.add(media)
    db.flush()

    job = ProcessingJob(
        media_id=media.id,
        status="completed",
        current_stage="finalize",
        progress_percent=100,
        candidate_count=3,
        clip_count=2,
    )
    db.add(job)
    db.flush()

    c1 = FinalClip(
        job_id=job.id,
        creator_id=cr.id,
        title="Tank Rampage",
        hook_text="TANK VS POLICE 💥",
        optimal_start=30.0,
        optimal_end=60.0,
        duration=30.0,
        virality_score=92,
        humor_score=80,
        action_score=99,
        approval_status="pending_review",
    )
    c2 = FinalClip(
        job_id=job.id,
        creator_id=cr.id,
        title="Plane Stunt",
        hook_text="IMPOSSIBLE LANDING 🛩️",
        optimal_start=120.0,
        optimal_end=150.0,
        duration=30.0,
        virality_score=88,
        humor_score=70,
        action_score=95,
        approval_status="approved",
    )
    db.add_all([c1, c2])
    db.commit()
    db.close()

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_overview_endpoint(control_client):
    res = control_client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["total_videos"] == 1
    assert data["completed"] == 1
    assert data["total_clips"] == 2
    assert data["pending_review"] == 1
    assert data["approved"] == 1
    assert "storage_gb" in data
    assert "recent_activity" in data


def test_upload_creators_list(control_client):
    res = control_client.get("/api/upload/creators")
    assert res.status_code == 200
    data = res.json()
    assert len(data["creators"]) >= 1
    creator = data["creators"][0]
    assert creator["handle"] == "@TrevorGamer"
    assert creator["has_active_rights"] is True


def test_queue_endpoint(control_client):
    res = control_client.get("/api/queue")
    assert res.status_code == 200
    data = res.json()
    assert len(data["jobs"]) == 1
    j = data["jobs"][0]
    assert j["title"] == "Sandy Shores Heist"
    assert j["creator_handle"] == "@TrevorGamer"
    assert j["status"] == "completed"


def test_monitor_endpoint(control_client):
    queue_res = control_client.get("/api/queue")
    job_id = queue_res.json()["jobs"][0]["id"]

    res = control_client.get(f"/api/monitor/{job_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == job_id
    assert data["status"] == "completed"
    assert "stage_states" in data
    assert "stage_logs" in data


def test_library_endpoint(control_client):
    res = control_client.get("/api/library")
    assert res.status_code == 200
    data = res.json()
    assert len(data["clips"]) == 2

    # Filter by search
    res_search = control_client.get("/api/library?search=Tank")
    assert len(res_search.json()["clips"]) == 1
    assert res_search.json()["clips"][0]["title"] == "Tank Rampage"

    # Filter by status
    res_status = control_client.get("/api/library?status=approved")
    assert len(res_status.json()["clips"]) == 1
    assert res_status.json()["clips"][0]["title"] == "Plane Stunt"


def test_edit_clip_endpoint(control_client):
    lib_res = control_client.get("/api/library?search=Tank")
    clip_id = lib_res.json()["clips"][0]["id"]

    update_payload = {
        "title": "Tank Rampage Remastered",
        "hook_text": "UPDATED HOOK 💥",
        "subtitle_text": "Custom Subtitles Here",
        "layout_mode": "action_crop",
    }
    res = control_client.put(f"/api/clips/{clip_id}", json=update_payload)
    assert res.status_code == 200
    assert res.json()["status"] == "updated"

    # Verify update
    verify_res = control_client.get("/api/library?search=Remastered")
    assert len(verify_res.json()["clips"]) == 1
    updated_clip = verify_res.json()["clips"][0]
    assert updated_clip["title"] == "Tank Rampage Remastered"
    assert updated_clip["hook_text"] == "UPDATED HOOK 💥"


def test_bulk_approve_endpoint(control_client):
    lib_res = control_client.get("/api/library?status=pending_review")
    pending_ids = [c["id"] for c in lib_res.json()["clips"]]
    assert len(pending_ids) == 1

    res = control_client.post("/api/clips/bulk-approve", json={"clip_ids": pending_ids})
    assert res.status_code == 200
    assert res.json()["approved"] == 1

    # Verify no more pending
    verify_res = control_client.get("/api/library?status=pending_review")
    assert len(verify_res.json()["clips"]) == 0


def test_creator_crud_endpoints(control_client):
    # Create new creator
    new_creator = {
        "handle": "@FranklinClips",
        "name": "Franklin Clinton",
        "channel_url": "https://youtube.com/@Franklin",
        "platform": "youtube",
        "content_restrictions": "Family friendly",
        "branding_guidelines": "Green accent",
        "authorized_platforms": "youtube_shorts,tiktok",
    }
    create_res = control_client.post("/api/creators", json=new_creator)
    assert create_res.status_code == 201
    creator_id = create_res.json()["id"]

    # Update creator
    update_res = control_client.put(
        f"/api/creators/{creator_id}",
        json={"name": "Franklin Clinton (Updated)", "status": "active"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "updated"

    # Delete creator (allowed because no approved clips)
    del_res = control_client.delete(f"/api/creators/{creator_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"


def test_models_endpoint(control_client):
    res = control_client.get("/api/models")
    assert res.status_code == 200
    data = res.json()
    assert "available_models" in data
    assert "loaded_models" in data
    assert "gpu" in data
    assert "ram" in data
    assert "active_reasoning_model" in data
    assert "active_vision_model" in data


def test_models_select_endpoint(control_client):
    select_payload = {
        "reasoning_model": "qwen3:8b",
        "vision_model": "qwen3.5:4b",
    }
    res = control_client.post("/api/models/select", json=select_payload)
    assert res.status_code == 200
    assert res.json()["status"] == "updated"

    # Check updated models
    models_res = control_client.get("/api/models")
    assert models_res.json()["active_reasoning_model"] == "qwen3:8b"
    assert models_res.json()["active_vision_model"] == "qwen3.5:4b"


def test_analytics_endpoint(control_client):
    res = control_client.get("/api/analytics")
    assert res.status_code == 200
    data = res.json()
    assert data["videos_processed"] == 1
    assert data["clips_produced"] == 2
    assert "approval_rate_pct" in data
    assert "rejection_rate_pct" in data
    assert "by_creator" in data
    assert "delivery_by_platform" in data
