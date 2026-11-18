from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base


logger = logging.getLogger(__name__)


def build_sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def create_sqlite_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(build_sqlite_url(path), future=True)


def create_sqlite_session_factory(path: Path) -> sessionmaker[Session]:
    try:
        engine = create_sqlite_engine(path)
        Base.metadata.create_all(engine)
        _upgrade_tasks_table(engine)
        return sessionmaker(bind=engine, expire_on_commit=False, future=True)
    except Exception:
        logger.exception(
            "Failed to initialize SQLite session factory.",
            extra={"event": "sqlite_session_init_failed", "component": "db"},
        )
        raise


def _upgrade_tasks_table(engine: Engine) -> None:
    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(tasks)")
        }
        if "created_at" not in columns:
            connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN created_at TEXT")
        connection.exec_driver_sql(
            "UPDATE tasks SET created_at = updated_at WHERE created_at IS NULL"
        )
