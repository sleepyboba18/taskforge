"""SQLAlchemy engine and session factory infrastructure."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger("taskforge.database")


class Base(DeclarativeBase):
    """Base class for future SQLAlchemy models."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def initialize_database(database_url: str) -> None:
    """Create database infrastructure without opening a connection."""
    global _engine, _session_factory
    if _engine is not None:
        return
    _engine = create_engine(database_url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    logger.info("Initialized SQLAlchemy database engine")


def initialize_schema() -> None:
    """Create missing ORM tables without altering existing database objects."""
    from app import models  # noqa: F401  # Register all models with Base.metadata.

    Base.metadata.create_all(get_engine())
    logger.info("Initialized database schema")


def get_engine() -> Engine:
    """Return the initialized engine."""
    if _engine is None:
        raise RuntimeError("Database infrastructure has not been initialized.")
    return _engine


def dispose_database() -> None:
    """Dispose inherited resources and reset globals in a newly started process."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> sessionmaker[Session]:
    """Return the factory used to create independent sessions."""
    if _session_factory is None:
        raise RuntimeError("Database infrastructure has not been initialized.")
    return _session_factory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Yield a session and guarantee rollback/close for one operation."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> None:
    """Verify connectivity using a short-lived SQLAlchemy connection."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    logger.info("Database connectivity check succeeded")
