"""SQLite database connection and session management with WAL mode and foreign key enforcement."""

from collections.abc import Generator
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session, with_loader_criteria
from contextvars import ContextVar
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("core.db")
settings = get_settings()
if settings.database_url.startswith("postgres://"):
    settings.database_url = settings.database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif settings.database_url.startswith("postgresql://"):
    settings.database_url = settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_sqlite = settings.database_url.startswith("sqlite")
engine_options = {"connect_args": {"check_same_thread": False}} if _sqlite else {"pool_pre_ping": True}
engine = create_engine(settings.database_url, **engine_options, echo=False)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enforce foreign key constraints and enable WAL mode on every SQLite connection."""
    if not _sqlite:
        return
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


@event.listens_for(Session, "do_orm_execute")
def apply_tenant_filter(execute_state):
    owner = execute_state.session.info.get("owner_id")
    if owner and execute_state.is_select:
        from src.core.models import Creator, RightsAgreement, MediaSource, ProcessingJob, CandidateClip, FinalClip, DeliveryLog, SystemSetting
        for entity in (Creator, RightsAgreement, MediaSource, ProcessingJob, CandidateClip, FinalClip, DeliveryLog, SystemSetting):
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(entity, lambda cls: cls.owner_id == owner, include_aliases=True)
            )


@event.listens_for(Session, "before_flush")
def assign_tenant_owner(session, flush_context, instances):
    owner = session.info.get("owner_id")
    if owner:
        from src.core.models import Creator, RightsAgreement, MediaSource, ProcessingJob, CandidateClip, FinalClip, DeliveryLog, SystemSetting
        for obj in session.new:
            if isinstance(obj, (Creator, RightsAgreement, MediaSource, ProcessingJob, CandidateClip, FinalClip, DeliveryLog, SystemSetting)):
                obj.owner_id = owner


def init_db() -> None:
    """Create all database tables defined in models."""
    logger.info("Initializing SQLite persistent metadata tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully at: %s", settings.database_url)
    migrate_db()


def migrate_db() -> None:
    """Apply additive SQLite column migrations (catches OperationalError for already-existing columns)."""
    import sqlite3

    if not _sqlite:
        return

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
        # tenant ownership (legacy local records remain unowned and visible in local mode only)
        "ALTER TABLE creators ADD COLUMN owner_id VARCHAR(32)",
        "ALTER TABLE rights_agreements ADD COLUMN owner_id VARCHAR(32)",
        "ALTER TABLE media_sources ADD COLUMN owner_id VARCHAR(32)",
        "ALTER TABLE processing_jobs ADD COLUMN owner_id VARCHAR(32)",
        "ALTER TABLE candidate_clips ADD COLUMN owner_id VARCHAR(32)",
        "ALTER TABLE final_clips ADD COLUMN owner_id VARCHAR(32)",
        "ALTER TABLE delivery_logs ADD COLUMN owner_id VARCHAR(32)",
        "ALTER TABLE system_settings ADD COLUMN owner_id VARCHAR(32)",
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
    owner = tenant_id.get()
    if owner:
        db.info["owner_id"] = owner
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for standalone pipeline script sessions."""
    db = SessionLocal()
    owner = tenant_id.get()
    if owner:
        db.info["owner_id"] = owner
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
