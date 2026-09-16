"""The API process records pilot usage in production whether or not it hosts the scheduler."""
from __future__ import annotations

from unittest.mock import Mock

from fastapi.testclient import TestClient

import app.main as main_module
import app.scheduler.runtime as runtime_module
from app.config import Settings
from app.main import create_app


def _settings(*, environment: str, scheduler_enabled: bool) -> Settings:
    return Settings(
        ENVIRONMENT=environment,
        DB_HOST="database.invalid",
        DB_USER="read-only",
        DB_PASSWORD="read-secret",  # noqa: S106 - isolated test settings
        INGESTION_DB_USER="ingestion",
        INGESTION_DB_PASSWORD="write-secret",  # noqa: S106 - isolated test settings
        SCHEDULER_ENABLED=scheduler_enabled,
        SNAPSHOT_RETENTION_ENABLED=True,
    )


def _stub_engines(monkeypatch) -> tuple[Mock, Mock]:
    read_engine, ingestion_engine = Mock(name="read_engine"), Mock(name="ingestion_engine")
    monkeypatch.setattr(main_module, "create_database_engine", Mock(return_value=read_engine))
    monkeypatch.setattr(
        main_module, "create_scheduler_database_engine", Mock(return_value=ingestion_engine)
    )
    monkeypatch.setattr(main_module, "create_session_factory", lambda engine: ("factory", engine))
    return read_engine, ingestion_engine


def test_production_api_without_scheduler_keeps_pilot_recording(monkeypatch) -> None:
    read_engine, ingestion_engine = _stub_engines(monkeypatch)
    start = Mock(side_effect=AssertionError("scheduler must not start in the API"))
    monkeypatch.setattr(runtime_module, "start_scheduler", start)
    app = create_app(_settings(environment="production", scheduler_enabled=False))

    with TestClient(app):
        assert app.state.pilot_session_factory == ("factory", ingestion_engine)
        assert not hasattr(app.state, "scheduler")

    read_engine.dispose.assert_called_once_with()
    ingestion_engine.dispose.assert_called_once_with()


def test_production_api_with_scheduler_starts_it_unchanged(monkeypatch) -> None:
    _, ingestion_engine = _stub_engines(monkeypatch)
    runtime = Mock(name="runtime")
    start = Mock(return_value=runtime)
    monkeypatch.setattr(runtime_module, "start_scheduler", start)
    settings = _settings(environment="production", scheduler_enabled=True)
    app = create_app(settings)

    with TestClient(app):
        assert app.state.pilot_session_factory == ("factory", ingestion_engine)
        assert app.state.scheduler is runtime

    start.assert_called_once_with(settings, ("factory", ingestion_engine))
    runtime.shutdown.assert_called_once_with(wait=False)


def test_development_api_without_scheduler_opens_no_ingestion_pool(monkeypatch) -> None:
    _, ingestion_engine = _stub_engines(monkeypatch)
    app = create_app(_settings(environment="development", scheduler_enabled=False))

    with TestClient(app):
        assert not hasattr(app.state, "pilot_session_factory")
        assert not hasattr(app.state, "scheduler")

    ingestion_engine.dispose.assert_not_called()
