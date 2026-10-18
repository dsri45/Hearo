"""SQLite engine configuration for local Hearo persistence."""

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "hearo.db"


def database_url() -> str:
    """Return the configured database URL, creating the local data folder."""

    configured = os.getenv("HEARO_DATABASE_URL")
    if configured:
        return configured

    DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"


def create_database_engine(url: str | None = None) -> Engine:
    """Create an SQLite engine with safe prototype defaults."""

    resolved_url = url or database_url()
    is_memory_database = resolved_url.endswith(":memory:")
    options: dict[str, object] = {
        "connect_args": {"check_same_thread": False},
    }
    if is_memory_database:
        options["poolclass"] = StaticPool

    engine = create_engine(resolved_url, **options)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        if not is_memory_database:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine
