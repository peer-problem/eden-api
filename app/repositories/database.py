from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import mariadb
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.observability.metrics import instrument_database_engine

MINIMUM_CONNECTOR_C_VERSION = (3, 4, 0)


def verified_mariadb_connect_args(settings: Settings) -> dict[str, object]:
    if mariadb.client_version_info < MINIMUM_CONNECTOR_C_VERSION:
        raise RuntimeError("MariaDB Connector/C 3.4 or newer is required")
    return {
        "connect_timeout": settings.DB_CONNECT_TIMEOUT_SECONDS,
        "ssl": True,
        "ssl_verify_cert": True,
    }


def create_database_engine(settings: Settings) -> Engine:
    return _create_database_engine(
        settings,
        database_url=settings.database_url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )


def create_scheduler_database_engine(settings: Settings) -> Engine:
    """Use a separate bounded pool so scheduler work cannot consume API slots."""
    return _create_database_engine(
        settings,
        database_url=settings.scheduler_database_url,
        pool_size=settings.SCHEDULER_DB_POOL_SIZE,
        max_overflow=0,
    )


def _create_database_engine(
    settings: Settings,
    *,
    database_url: str,
    pool_size: int,
    max_overflow: int,
) -> Engine:
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        pool_size=pool_size,
        max_overflow=max_overflow,
        connect_args=verified_mariadb_connect_args(settings),
    )
    instrument_database_engine(engine)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
