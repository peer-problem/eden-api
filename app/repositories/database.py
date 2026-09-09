from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.observability.metrics import instrument_database_engine


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


def database_connect_args(settings: Settings) -> dict[str, object]:
    """Use the same timeout and verified TLS settings for runtime and migrations."""
    # Connector/C 3.4+ verifies MariaDB 11.4+ using the password and TLS peer
    # certificate together. No CA file, fingerprint or verification opt-out.
    return {
        "connect_timeout": settings.DB_CONNECT_TIMEOUT_SECONDS,
        "ssl": True,
        "ssl_verify_cert": True,
    }


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
        connect_args=database_connect_args(settings),
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
