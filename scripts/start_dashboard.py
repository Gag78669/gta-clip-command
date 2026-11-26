"""Launcher for the GTA CLIP COMMAND local review and approval dashboard."""

import sys
from pathlib import Path
import uvicorn

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import get_settings
from src.core.db import init_db
from src.core.logger import get_logger
from src.dashboard.auth import get_auth_settings
from src.dashboard.worker import get_worker

logger = get_logger("scripts.dashboard")
settings = get_settings()


def main():
    auth_settings = get_auth_settings()
    if not auth_settings.enabled:
        raise RuntimeError(
            "Set AUTH_ENABLED=true and configure owner credentials in .env before starting private access."
        )
    if settings.api_host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("For private Tailscale access, API_HOST must bind only to loopback.")

    # Initialize DB tables + run migrations
    logger.info("Running database initialization and migrations...")
    init_db()

    # Initialize background worker (resets stale jobs, starts dispatcher thread)
    logger.info("Initializing pipeline worker...")
    get_worker()

    logger.info(
        "Launching Human Approval Dashboard at: http://%s:%s",
        settings.api_host,
        settings.api_port,
    )
    logger.info("To connect the hosted frontend, run connect_backend_tailscale.bat once and copy its private HTTPS address into Settings.")
    logger.info("Tailscale Serve keeps that private address configured across computer restarts.")
    uvicorn.run(
        "src.dashboard.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

