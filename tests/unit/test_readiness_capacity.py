from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

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
        DB_SSL_CA=Path("/etc/eden/mariadb-ca.pem"),
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
