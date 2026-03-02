"""SQLite engine creation, WAL mode, and schema management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event, text
from sqlmodel import Session, SQLModel, create_engine

# Current schema version — increment when tables change
SCHEMA_VERSION = 1

# Module-level engine cache (one per db path)
_engines: dict[str, Engine] = {}


def create_db_engine(db_path: Path) -> Engine:
    """Create a SQLite engine with WAL mode and busy timeout.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        SQLAlchemy Engine configured for concurrent access.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    # Enable WAL mode and busy timeout on every connection
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn: Any, connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables and set schema version.

    Safe to call multiple times — SQLModel.metadata.create_all is idempotent.
    """
    # Import tables to register them with SQLModel metadata
    import wade.db.tables  # noqa: F401

    SQLModel.metadata.create_all(engine)

    # Set schema version if not already set
    with Session(engine) as session:
        # Create schema version table if needed
        session.exec(  # type: ignore[call-overload]
            text("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)")
        )
        result = session.exec(text("SELECT version FROM _schema_version"))  # type: ignore[call-overload]
        row = result.first()
        if row is None:
            session.exec(  # type: ignore[call-overload]
                text(f"INSERT INTO _schema_version (version) VALUES ({SCHEMA_VERSION})")
            )
        session.commit()


def get_or_create_engine(project_root: Path) -> Engine:
    """Get or create a cached engine for a project.

    The database is stored at `<project_root>/.wade/wade.db`.
    """
    db_path = project_root / ".wade" / "wade.db"
    key = str(db_path)

    if key not in _engines:
        engine = create_db_engine(db_path)
        init_db(engine)
        _engines[key] = engine

    return _engines[key]
