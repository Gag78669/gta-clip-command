"""Tests for hosted account passwords and atomic duration reservations."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.accounts import hash_password, reserve_seconds, settle_reservation, usage_payload, verify_password
from src.core.models import Base, UserAccount


@pytest.fixture
def account_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'accounts.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("a-long-test-password")
    second = hash_password("a-long-test-password")
    assert first != second
    assert verify_password("a-long-test-password", first)
    assert not verify_password("wrong-password", first)


def test_quota_reserves_atomically_and_rejects_over_limit(account_db):
    user = UserAccount(email="quota@example.test", password_hash=hash_password("a-long-test-password"))
    account_db.add(user)
    account_db.commit()

    reserve_seconds(account_db, user.id, "job-first", 600)
    account_db.commit()
    with pytest.raises(ValueError, match="Daily free processing limit"):
        reserve_seconds(account_db, user.id, "job-second", 601)

    usage = usage_payload(account_db, user.id)
    assert usage["reserved_seconds"] == 600
    assert usage["remaining_seconds"] == 600


def test_failed_pre_ai_job_releases_reserved_duration(account_db):
    user = UserAccount(email="release@example.test", password_hash=hash_password("a-long-test-password"))
    account_db.add(user)
    account_db.commit()

    reserve_seconds(account_db, user.id, "job-release", 300)
    account_db.commit()
    settle_reservation(account_db, "job-release", consume=False)
    account_db.commit()

    usage = usage_payload(account_db, user.id)
    assert usage["reserved_seconds"] == 0
    assert usage["consumed_seconds"] == 0
    assert usage["remaining_seconds"] == usage["limit_seconds"]


def test_ai_started_job_consumes_reserved_duration(account_db):
    user = UserAccount(email="consume@example.test", password_hash=hash_password("a-long-test-password"))
    account_db.add(user)
    account_db.commit()

    reserve_seconds(account_db, user.id, "job-consume", 420)
    account_db.commit()
    settle_reservation(account_db, "job-consume", consume=True)
    account_db.commit()

    usage = usage_payload(account_db, user.id)
    assert usage["reserved_seconds"] == 0
    assert usage["consumed_seconds"] == 420
    assert usage["remaining_seconds"] == usage["limit_seconds"] - 420
