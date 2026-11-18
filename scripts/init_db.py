"""Initialize the local application database and schema."""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.db import init_db
from src.core.logger import get_logger

logger = get_logger("scripts.init_db")


def main():
    logger.info("Initializing the local clipping application database...")
    init_db()
    logger.info("Database initialization completed. Register a creator and documented permission before processing footage.")


if __name__ == "__main__":
    main()
