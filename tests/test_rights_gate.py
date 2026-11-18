"""Unit tests for Creator Rights & Legal Authorization Gate."""

import pytest
from datetime import timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.core.db import Base
from src.core.models import Creator, RightsAgreement, MediaSource, now_utc
from src.ingestion.rights_gate import (
    RightsGate,
    UnauthorizedCreatorError,
    RightsExpiredError,
)


@pytest.fixture
def db_session(tmp_path):
    """Provide an isolated, clean SQLite in-memory test database."""
    test_db_url = f"sqlite:///{tmp_path / 'test.db'}"
    test_engine = create_engine(test_db_url)
    Base.metadata.create_all(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)
    session = TestingSessionLocal()
    yield session
    session.close()


def test_authorized_creator_passes(db_session, tmp_path):
    gate = RightsGate(db_session)
    creator = gate.register_creator(
        handle="@TestCreator",
        name="Test Creator",
        channel_url="https://youtube.com/@TestCreator",
        platform="youtube",
    )

    doc_file = tmp_path / "contract.txt"
    doc_file.write_text("Valid contract", encoding="utf-8")

    gate.grant_rights(
        creator_id=creator.id,
        agreement_type="exclusive",
        documentation_path=str(doc_file),
        authorized_by="Admin",
        rev_share_percent=15.0,
    )

    result = gate.verify_creator(creator.id)
    assert result.authorized is True
    assert result.creator_handle == "@TestCreator"
    assert result.rev_share_percent == 15.0


def test_unregistered_creator_rejected(db_session):
    gate = RightsGate(db_session)
    with pytest.raises(UnauthorizedCreatorError):
        gate.verify_creator("cr_nonexistent123", strict=True)

    non_strict = gate.verify_creator("cr_nonexistent123", strict=False)
    assert non_strict.authorized is False
    assert "does not exist" in non_strict.reason


def test_inactive_creator_rejected(db_session, tmp_path):
    gate = RightsGate(db_session)
    creator = gate.register_creator(
        handle="@InactiveCreator",
        name="Inactive Guy",
        channel_url="https://youtube.com/@Inactive",
    )
    creator.status = "paused"
    db_session.commit()

    with pytest.raises(UnauthorizedCreatorError, match="status is 'paused'"):
        gate.verify_creator(creator.id)


def test_expired_agreement_rejected(db_session, tmp_path):
    gate = RightsGate(db_session)
    creator = gate.register_creator(
        handle="@ExpiredCreator",
        name="Expired Guy",
        channel_url="https://youtube.com/@Expired",
    )

    doc_file = tmp_path / "contract.txt"
    doc_file.write_text("Expired contract", encoding="utf-8")

    past_date = now_utc() - timedelta(days=5)
    gate.grant_rights(
        creator_id=creator.id,
        agreement_type="non_exclusive",
        documentation_path=str(doc_file),
        authorized_by="Admin",
        valid_to=past_date,
    )

    with pytest.raises(RightsExpiredError):
        gate.verify_creator(creator.id)
