"""Deterministic Creator Rights & Legal Authorization Gate for GTA CLIP COMMAND.

Ensures no creator footage is processed, clipped, or delivered without active,
verified contractual authorization stored in persistent SQLite metadata.
"""

from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from src.core.models import Creator, RightsAgreement, MediaSource, now_utc
from src.core.logger import get_logger

logger = get_logger("ingestion.rights_gate")


class UnauthorizedCreatorError(Exception):
    """Raised when an operation attempts to process footage from an unauthorized creator."""
    pass


class RightsExpiredError(UnauthorizedCreatorError):
    """Raised when an agreement has expired."""
    pass


class RightsCheckResult(BaseModel):
    """Outcome of a creator authorization verification check."""
    authorized: bool = Field(description="True if all authorization requirements are satisfied")
    creator_id: str
    creator_handle: str
    agreement_id: str | None = None
    agreement_type: str | None = None
    rev_share_percent: float = 0.0
    reason: str = Field(description="Explanation of authorization or rejection")
    checked_at: datetime = Field(default_factory=now_utc)


class RightsGate:
    """Enforces strict legal compliance before any video ingestion or clipping can proceed."""

    def __init__(self, db: Session):
        self.db = db

    def register_creator(
        self,
        handle: str,
        name: str,
        channel_url: str,
        platform: str = "youtube",
    ) -> Creator:
        """Register a new content creator in the system."""
        existing = self.db.query(Creator).filter(Creator.handle == handle).first()
        if existing:
            logger.warning("Creator with handle %s already registered (id: %s)", handle, existing.id)
            return existing

        creator = Creator(
            handle=handle,
            name=name,
            channel_url=channel_url,
            platform=platform,
            status="active",
        )
        self.db.add(creator)
        self.db.commit()
        self.db.refresh(creator)
        logger.info("Registered new creator '%s' (handle: %s, id: %s)", name, handle, creator.id)
        return creator

    def grant_rights(
        self,
        creator_id: str,
        agreement_type: str,
        documentation_path: str,
        authorized_by: str,
        valid_to: datetime | None = None,
        rev_share_percent: float = 0.0,
        notes: str | None = None,
    ) -> RightsAgreement:
        """Grant a documented clipping agreement to a creator."""
        creator = self.db.query(Creator).filter(Creator.id == creator_id).first()
        if not creator:
            raise UnauthorizedCreatorError(f"Creator id '{creator_id}' not found.")

        # Verify documentation exists or warn
        doc_file = Path(documentation_path)
        if not doc_file.exists():
            logger.warning(
                "Documentation path '%s' does not exist on disk yet. Ensure signed contract is uploaded.",
                documentation_path,
            )

        agreement = RightsAgreement(
            creator_id=creator.id,
            agreement_type=agreement_type,
            status="active",
            valid_from=now_utc(),
            valid_to=valid_to,
            rev_share_percent=rev_share_percent,
            documentation_path=str(documentation_path),
            authorized_by=authorized_by,
            notes=notes,
        )
        self.db.add(agreement)
        self.db.commit()
        self.db.refresh(agreement)
        logger.info(
            "Granted agreement %s (%s) for creator %s",
            agreement.id,
            agreement_type,
            creator.handle,
        )
        return agreement

    def verify_creator(self, creator_id: str, strict: bool = True) -> RightsCheckResult:
        """Check whether a creator is authorized to have footage processed."""
        creator = self.db.query(Creator).filter(Creator.id == creator_id).first()
        if not creator:
            msg = f"Creator ID '{creator_id}' does not exist in registry."
            logger.error("Rights Gate REJECTED: %s", msg)
            if strict:
                raise UnauthorizedCreatorError(msg)
            return RightsCheckResult(
                authorized=False,
                creator_id=creator_id,
                creator_handle="UNKNOWN",
                reason=msg,
            )

        if creator.status != "active":
            msg = f"Creator '{creator.handle}' status is '{creator.status}' (must be 'active')."
            logger.error("Rights Gate REJECTED: %s", msg)
            if strict:
                raise UnauthorizedCreatorError(msg)
            return RightsCheckResult(
                authorized=False,
                creator_id=creator.id,
                creator_handle=creator.handle,
                reason=msg,
            )

        # Query active agreements
        now = now_utc()
        agreements = (
            self.db.query(RightsAgreement)
            .filter(
                RightsAgreement.creator_id == creator.id,
                RightsAgreement.status == "active",
            )
            .all()
        )

        if not agreements:
            msg = f"No active rights agreement found for creator '{creator.handle}'."
            logger.error("Rights Gate REJECTED: %s", msg)
            if strict:
                raise UnauthorizedCreatorError(msg)
            return RightsCheckResult(
                authorized=False,
                creator_id=creator.id,
                creator_handle=creator.handle,
                reason=msg,
            )

        # Check validity window
        valid_agreement = None
        for agreement in agreements:
            if agreement.valid_to is not None and agreement.valid_to < now:
                agreement.status = "expired"
                self.db.commit()
                continue
            valid_agreement = agreement
            break

        if not valid_agreement:
            msg = f"All rights agreements for creator '{creator.handle}' have expired."
            logger.error("Rights Gate REJECTED: %s", msg)
            if strict:
                raise RightsExpiredError(msg)
            return RightsCheckResult(
                authorized=False,
                creator_id=creator.id,
                creator_handle=creator.handle,
                reason=msg,
            )

        logger.info(
            "Rights Gate APPROVED: Creator '%s' authorized under agreement %s (%s).",
            creator.handle,
            valid_agreement.id,
            valid_agreement.agreement_type,
        )

        return RightsCheckResult(
            authorized=True,
            creator_id=creator.id,
            creator_handle=creator.handle,
            agreement_id=valid_agreement.id,
            agreement_type=valid_agreement.agreement_type,
            rev_share_percent=valid_agreement.rev_share_percent,
            reason=f"Valid {valid_agreement.agreement_type} agreement authorized by {valid_agreement.authorized_by}.",
        )

    def verify_media(self, media_id: str, strict: bool = True) -> RightsCheckResult:
        """Verify that the media source belongs to an authorized creator."""
        media = self.db.query(MediaSource).filter(MediaSource.id == media_id).first()
        if not media:
            msg = f"MediaSource '{media_id}' does not exist in registry."
            logger.error("Rights Gate REJECTED: %s", msg)
            if strict:
                raise UnauthorizedCreatorError(msg)
            return RightsCheckResult(
                authorized=False,
                creator_id="UNKNOWN",
                creator_handle="UNKNOWN",
                reason=msg,
            )

        return self.verify_creator(media.creator_id, strict=strict)
