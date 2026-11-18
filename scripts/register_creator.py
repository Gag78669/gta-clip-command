"""Register a creator and archive their documented clipping permission locally."""

import argparse
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import get_settings
from src.core.db import get_db_context, init_db
from src.core.logger import get_logger
from src.core.models import Creator
from src.ingestion.rights_gate import RightsGate

logger = get_logger("scripts.register_creator")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register a creator and a real, documented permission agreement."
    )
    parser.add_argument("--handle", required=True, help="Creator handle, for example @MyChannel")
    parser.add_argument("--name", required=True, help="Creator or channel display name")
    parser.add_argument("--channel-url", required=True, help="Creator channel or profile URL")
    parser.add_argument("--platform", default="youtube", help="Platform name (default: youtube)")
    parser.add_argument("--permission-file", required=True, help="Path to the signed permission document")
    parser.add_argument("--authorized-by", required=True, help="Name of the person who authorized processing")
    parser.add_argument(
        "--agreement-type",
        choices=("exclusive", "non_exclusive", "revenue_share", "fair_use_reviewed"),
        default="non_exclusive",
    )
    parser.add_argument("--rev-share-percent", type=float, default=0.0)
    args = parser.parse_args()

    source_document = Path(args.permission_file).expanduser().resolve()
    if not source_document.is_file():
        parser.error(f"Permission document does not exist: {source_document}")
    if not 0 <= args.rev_share_percent <= 100:
        parser.error("--rev-share-percent must be between 0 and 100")

    settings = get_settings()
    init_db()
    with get_db_context() as db:
        gate = RightsGate(db)
        creator = db.query(Creator).filter(Creator.handle == args.handle).first()
        if creator is None:
            creator = gate.register_creator(
                handle=args.handle,
                name=args.name,
                channel_url=args.channel_url,
                platform=args.platform,
            )

        archived_document = settings.agreements_dir / (
            f"{creator.id}_{uuid.uuid4().hex[:8]}{source_document.suffix.lower()}"
        )
        shutil.copy2(source_document, archived_document)
        agreement = gate.grant_rights(
            creator_id=creator.id,
            agreement_type=args.agreement_type,
            documentation_path=str(archived_document),
            authorized_by=args.authorized_by,
            rev_share_percent=args.rev_share_percent,
        )

    logger.info(
        "Registered %s with documented permission agreement %s.",
        creator.handle,
        agreement.id,
    )
    print(f"Creator registered: {creator.handle}")
    print(f"Agreement: {agreement.id} ({agreement.agreement_type})")
    print(f"Permission document archived at: {archived_document}")


if __name__ == "__main__":
    main()
