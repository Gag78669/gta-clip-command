"""Launcher for the GTA CLIP COMMAND local review and approval dashboard."""

import sys
from pathlib import Path
import uvicorn

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import get_settings
from src.core.db import init_db
from src.core.logger import get_logger
from src.dashboard.worker import get_worker

logger = get_logger("scripts.dashboard")
settings = get_settings()


def main():
    # Initialize DB tables + run migrations
    logger.info("Running database initialization and migrations...")
    init_db()

    # Initialize background worker (resets stale jobs, starts dispatcher thread)
    logger.info("Initializing pipeline worker...")
    get_worker()

    logger.info(
        "Launching Human Approval Dashboard at: http://127.0.0.1:8000"
    )
    uvicorn.run(
        "src.dashboard.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
