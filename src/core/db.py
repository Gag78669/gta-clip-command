"""SQLite database connection and session management with WAL mode and foreign key enforcement."""

from collections.abc import Generator
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("core.db")
settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enforce foreign key constraints and enable WAL mode on every SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.close()


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)
Base = declarative_base()


def init_db() -> None:
    """Create all database tables defined in models."""
    logger.info("Initializing SQLite persistent metadata tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully at: %s", settings.database_url)
    migrate_db()


def migrate_db() -> None:
    """Apply additive SQLite column migrations (catches OperationalError for already-existing columns)."""
    import sqlite3

    db_path = settings.database_url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    migrations = [
        # creators
        "ALTER TABLE creators ADD COLUMN content_restrictions TEXT",
        "ALTER TABLE creators ADD COLUMN branding_guidelines TEXT",
        "ALTER TABLE creators ADD COLUMN authorized_platforms TEXT",
        # processing_jobs
        "ALTER TABLE processing_jobs ADD COLUMN current_model TEXT",
        "ALTER TABLE processing_jobs ADD COLUMN started_at DATETIME",
        "ALTER TABLE processing_jobs ADD COLUMN completed_at DATETIME",
        "ALTER TABLE processing_jobs ADD COLUMN candidate_count INTEGER DEFAULT 0",
        "ALTER TABLE processing_jobs ADD COLUMN clip_count INTEGER DEFAULT 0",
        "ALTER TABLE processing_jobs ADD COLUMN stage_logs TEXT",
        "ALTER TABLE processing_jobs ADD COLUMN stage_states TEXT",
        # final_clips
        "ALTER TABLE final_clips ADD COLUMN subtitle_text TEXT",
        "ALTER TABLE final_clips ADD COLUMN highlight_description TEXT",
        "ALTER TABLE final_clips ADD COLUMN file_size_bytes INTEGER DEFAULT 0",
    ]

    for sql in migrations:
        try:
            cursor.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Create system_settings if not present
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Database migrations applied successfully.")


def get_db() -> Generator[Session, None, None]:
    """Dependency generator for database sessions (FastAPI compatible)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for standalone pipeline script sessions."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
