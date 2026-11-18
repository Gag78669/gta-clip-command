"""Database initialization and baseline schema migration script for GTA CLIP COMMAND."""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.db import init_db, get_db_context
from src.core.models import Creator, RightsAgreement
from src.core.logger import get_logger

logger = get_logger("scripts.init_db")


def main():
    logger.info("Starting GTA CLIP COMMAND persistent database initialization...")
    init_db()

    # Seed an example test creator with signed permission documentation
    with get_db_context() as db:
        test_handle = "@RockstarGamerHQ"
        existing = db.query(Creator).filter(Creator.handle == test_handle).first()
        if not existing:
            creator = Creator(
                handle=test_handle,
                name="Rockstar Gamer HQ",
                channel_url="https://youtube.com/@RockstarGamerHQ",
                platform="youtube",
                status="active",
            )
            db.add(creator)
            db.flush()

            # Ensure sample agreement documentation exists
            agreements_dir = Path(__file__).resolve().parent.parent / "data" / "agreements"
            agreements_dir.mkdir(parents=True, exist_ok=True)
            doc_path = agreements_dir / f"{creator.id}_contract.txt"
            doc_path.write_text(
                "OFFICIAL AUTHORIZATION: Rockstar Gamer HQ grants GTA CLIP COMMAND exclusive clipping "
                "and compilation distribution rights for all GTA VI / GTA V livestreams and VODs.",
                encoding="utf-8",
            )

            agreement = RightsAgreement(
                creator_id=creator.id,
                agreement_type="exclusive",
                status="active",
                rev_share_percent=20.0,
                documentation_path=str(doc_path),
                authorized_by="Lead Publisher / Legal Operations",
                notes="Standard 80/20 commercial clipping and syndication agreement.",
            )
            db.add(agreement)
            logger.info("Seeded initial authorized creator: %s with agreement %s", creator.handle, agreement.id)
        else:
            logger.info("Creator %s already present in database.", test_handle)

    logger.info("Database initialization completed successfully.")


if __name__ == "__main__":
    main()
