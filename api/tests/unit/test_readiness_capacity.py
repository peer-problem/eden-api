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


def test_scheduler_off_serves_requests_and_keeps_the_pilot_writer(monkeypatch) -> None:
    """A production API without the in-process scheduler still records pilot usage.

    The scheduler now runs as eden-scheduler.service, so the API's own process role
    no longer decides whether pilot rows are written with the ingestion account.
    """
    settings = _production_settings().model_copy(update={"SCHEDULER_ENABLED": False})
    disposed = []
    monkeypatch.setattr(
        main_module,
        "create_database_engine",
        lambda _settings: SimpleNamespace(dispose=lambda: disposed.append("reader")),
    )
    monkeypatch.setattr(
        main_module,
        "create_scheduler_database_engine",
        lambda _settings: SimpleNamespace(dispose=lambda: disposed.append("writer")),
    )
    monkeypatch.setattr(main_module, "create_session_factory", lambda engine: ("factory", engine))
    monkeypatch.setattr(main_module, "MariaDBReadRepository", lambda _factory: ReadyRepository())

    def forbidden_scheduler(*_args):
        raise AssertionError("Scheduler-off API must not start the scheduler")

    monkeypatch.setattr("app.scheduler.runtime.start_scheduler", forbidden_scheduler)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/internal/readiness", headers={"X-EDEN-Pilot": "test-client"})
        assert response.status_code == 200
        assert response.json()["scheduler_enabled"] is False
        assert response.json()["warnings"] == []
        assert not hasattr(app.state, "scheduler")
        assert app.state.pilot_session_factory[0] == "factory"
    assert disposed == ["reader", "writer"]
