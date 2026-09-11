from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import create_app


class ReadyRepository:
    def ready(self) -> bool:
        return True


class CapacityGate:
    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons

    def readiness_reasons(self, _settings: Settings) -> tuple[str, ...]:
        return self.reasons


def _production_settings() -> Settings:
    return Settings(
        ENVIRONMENT="production",
        DB_HOST="database.invalid",
        DB_USER="read-only",
        DB_PASSWORD="read-secret",  # noqa: S106 - isolated test settings
        INGESTION_DB_USER="ingestion",
        INGESTION_DB_PASSWORD="write-secret",  # noqa: S106 - isolated test settings
        SCHEDULER_ENABLED=True,
        SNAPSHOT_RETENTION_ENABLED=True,
    )


def test_production_readiness_warns_without_scheduler_capacity() -> None:
    app = create_app(_production_settings(), ReadyRepository())

    with TestClient(app) as client:
        response = client.get("/internal/readiness")

    assert response.status_code == 200
    assert response.json()["reasons"] == []
    assert response.json()["warnings"] == ["scheduler_capacity_unavailable"]


def test_production_readiness_surfaces_capacity_gate_warnings() -> None:
    app = create_app(_production_settings(), ReadyRepository())

    with TestClient(app) as client:
        app.state.scheduler = SimpleNamespace(
            capacity_gate=CapacityGate(("capacity_product_pause",)),
            shutdown=lambda wait=False: None,
        )
        response = client.get("/internal/readiness")

    assert response.status_code == 200
    assert response.json()["reasons"] == []
    assert response.json()["warnings"] == ["capacity_product_pause"]


def test_production_readiness_passes_with_a_clear_capacity_gate() -> None:
    app = create_app(_production_settings(), ReadyRepository())

    with TestClient(app) as client:
        app.state.scheduler = SimpleNamespace(
            capacity_gate=CapacityGate(()),
            shutdown=lambda wait=False: None,
        )
        response = client.get("/internal/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "scheduler_enabled": True,
        "reasons": [],
        "warnings": [],
    }


def test_scheduler_off_serves_requests_without_opening_a_writer(monkeypatch) -> None:
    settings = _production_settings().model_copy(update={"SCHEDULER_ENABLED": False})
    disposed = []
    monkeypatch.setattr(
        main_module,
        "create_database_engine",
        lambda _settings: SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    monkeypatch.setattr(main_module, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(main_module, "MariaDBReadRepository", lambda _factory: ReadyRepository())

    def forbidden_writer(_settings):
        raise AssertionError("Scheduler-off API must not open an ingestion connection")

    monkeypatch.setattr(main_module, "create_scheduler_database_engine", forbidden_writer)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/internal/readiness", headers={"X-EDEN-Pilot": "test-client"})
        assert response.status_code == 200
        assert response.json()["scheduler_enabled"] is False
        assert response.json()["warnings"] == []
        assert not hasattr(app.state, "scheduler")
        assert not hasattr(app.state, "pilot_session_factory")
    assert disposed == [True]
