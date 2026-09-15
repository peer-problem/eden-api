from __future__ import annotations

from unittest.mock import Mock

from app.config import Settings
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
