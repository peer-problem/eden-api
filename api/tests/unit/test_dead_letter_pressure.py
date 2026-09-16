from __future__ import annotations

from unittest.mock import Mock

from app.config import Settings
from app.observability.api_latency import ApiLatency
from app.scheduler import runtime


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DB_HOST": "db.example.test",
        "DB_USER": "eden",
        "DB_PASSWORD": "test-only-password",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_dead_letter_pauses_for_api_latency_pressure(monkeypatch) -> None:
    settings = _settings(DEAD_LETTER_API_P95_PAUSE_SECONDS=1.0)
    monkeypatch.setattr(runtime, "recent_api_p95_seconds", lambda: 1.2)
    monkeypatch.setattr(runtime, "system_memory_used_percent", lambda: 20.0)

    assert runtime._dead_letter_pause_reason(settings, None) == "api_latency_pressure"


def test_dead_letter_pauses_for_database_host_memory_pressure(monkeypatch) -> None:
    settings = _settings(DEAD_LETTER_MEMORY_PAUSE_PERCENT=85.0)
    monkeypatch.setattr(runtime, "recent_api_p95_seconds", lambda: 0.1)
    monkeypatch.setattr(runtime, "system_memory_used_percent", lambda: 91.0)

    assert (
        runtime._dead_letter_pause_reason(settings, Mock(source_pause_reason=lambda: None))
        == "database_host_memory_pressure"
    )


def test_production_pressure_uses_separate_api_and_recovers(monkeypatch) -> None:
    settings = _settings(
        ENVIRONMENT="production",
        INGESTION_DB_USER="ingestion",
        INGESTION_DB_PASSWORD="test-ingestion-password",  # noqa: S106
    )
    monkeypatch.setattr(runtime, "recent_api_p95_seconds", lambda: 0.0)
    monkeypatch.setattr(runtime, "system_memory_used_percent", lambda: 20.0)
    monkeypatch.setattr(
        runtime, "read_api_latency", lambda: ApiLatency(available=True, p95_seconds=1.2),
    )
    assert runtime._dead_letter_pause_reason(settings, None) == "api_latency_pressure"

    monkeypatch.setattr(runtime, "read_api_latency", lambda: ApiLatency(available=False))
    assert runtime._dead_letter_pause_reason(settings, None) == "api_metrics_unavailable"

    monkeypatch.setattr(runtime, "read_api_latency", lambda: ApiLatency(available=True))
    assert runtime._dead_letter_pause_reason(settings, None) is None
