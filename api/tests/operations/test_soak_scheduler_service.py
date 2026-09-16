"""Soak evidence follows the scheduler whether it runs inside the API or on its own."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.observability.soak as soak
from app.observability.soak import (
    MONITORED_SERVICES,
    REQUIRED_SERVICES,
    SCHEDULER_SERVICE,
    collect_sample,
    evaluate_samples,
    scheduler_enabled_state,
)


@pytest.mark.parametrize(
    "api_flag,service_state,expected",
    [
        (True, None, True),
        (True, {"active_state": "inactive"}, True),
        (False, {"active_state": "active"}, True),
        (False, {"active_state": "inactive"}, False),
        (False, {"active_state": "activating"}, False),
        (False, None, False),
        (None, {"active_state": "active"}, True),
        (None, {"active_state": "inactive"}, None),
        (None, None, None),
    ],
)
def test_scheduler_state_combines_api_flag_and_service(api_flag, service_state, expected):
    assert scheduler_enabled_state(api_flag, service_state) is expected


def test_scheduler_service_is_monitored_but_not_required() -> None:
    assert SCHEDULER_SERVICE in MONITORED_SERVICES
    assert SCHEDULER_SERVICE not in REQUIRED_SERVICES
    assert MONITORED_SERVICES[: len(REQUIRED_SERVICES)] == REQUIRED_SERVICES


def test_collect_sample_reports_standalone_scheduler(monkeypatch, tmp_path: Path) -> None:
    states = {
        "eden-api": {"active_state": "active", "restarts": 0},
        "nginx": {"active_state": "active", "restarts": 0},
        "eden-scheduler": {"active_state": "active", "restarts": 1},
    }
    monkeypatch.setattr(soak, "_service_state", lambda service: states[service])
    monkeypatch.setattr(soak, "_readiness_state", lambda: (True, False))
    monkeypatch.setattr(soak, "_key_value_file", lambda path: {})
    monkeypatch.setattr(
        Path, "read_text", lambda self, *args, **kwargs: "boot\n", raising=True
    )

    sample = collect_sample(now=datetime(2026, 9, 16, tzinfo=UTC), release_path=tmp_path)

    assert sample["readiness"] is True
    assert sample["scheduler_enabled"] is True
    assert set(sample["services"]) == set(MONITORED_SERVICES)
    assert sample["services"]["eden-scheduler"]["restarts"] == 1


def _sample(sampled_at: datetime, *, scheduler_state: str, scheduler_restarts: int) -> dict:
    return {
        "sampled_at": sampled_at.isoformat(),
        "boot_id": "boot",
        "release": "/opt/eden/releases/split",
        "readiness": True,
        "scheduler_enabled": scheduler_state == "active",
        "services": {
            "eden-api": {"active_state": "active", "restarts": 0},
            "nginx": {"active_state": "active", "restarts": 0},
            "eden-scheduler": {
                "active_state": scheduler_state,
                "restarts": scheduler_restarts,
                "memory_current_bytes": 300 * 1024 * 1024,
            },
        },
        "memory": {
            "available_bytes": 512 * 1024 * 1024,
            "swap_total_bytes": 1024,
            "swap_free_bytes": 1024,
        },
        "vmstat": {"pswpin": 0, "pswpout": 0},
    }


def test_stopped_scheduler_service_is_not_a_service_failure() -> None:
    started = datetime(2026, 9, 16, tzinfo=UTC)
    samples = [
        _sample(started + timedelta(minutes=5 * index), scheduler_state="inactive",
                scheduler_restarts=0)
        for index in range(4)
    ]

    result = evaluate_samples(samples, now=started + timedelta(minutes=15))

    assert "service_inactive" not in result["violations"]
    assert "scheduler_disabled" not in result["violations"]
    assert result["service_memory_peak_bytes"]["eden-scheduler"] == 300 * 1024 * 1024


def test_scheduler_service_restart_counts_as_service_restart() -> None:
    started = datetime(2026, 9, 16, tzinfo=UTC)
    samples = [
        _sample(started, scheduler_state="active", scheduler_restarts=0),
        _sample(started + timedelta(minutes=5), scheduler_state="active", scheduler_restarts=1),
    ]

    result = evaluate_samples(
        samples, now=started + timedelta(minutes=5), require_scheduler_enabled=True
    )

    assert "service_restart" in result["violations"]
    assert "scheduler_disabled" not in result["violations"]


@pytest.mark.parametrize("legacy_mariadb", [False, True])
def test_remote_database_needs_no_local_service(legacy_mariadb: bool) -> None:
    started = datetime(2026, 9, 16, tzinfo=UTC)
    samples = [
        _sample(started + timedelta(minutes=5 * index), scheduler_state="active",
                scheduler_restarts=0)
        for index in range(2)
    ]
    if legacy_mariadb:
        for index, sample in enumerate(samples):
            sample["services"]["mariadb"] = {
                "active_state": "inactive", "restarts": index,
            }

    result = evaluate_samples(
        samples, now=started + timedelta(minutes=5), required_seconds=300,
        require_scheduler_enabled=True,
    )

    assert result["status"] == "passed"
    assert result["violations"] == []
    assert "mariadb" not in MONITORED_SERVICES


@pytest.mark.parametrize("service", ["eden-api", "nginx"])
@pytest.mark.parametrize("missing", [False, True])
def test_remote_database_still_requires_local_api_and_proxy(service: str, missing: bool) -> None:
    started = datetime(2026, 9, 16, tzinfo=UTC)
    sample = _sample(started, scheduler_state="active", scheduler_restarts=0)
    if missing:
        del sample["services"][service]
    else:
        sample["services"][service]["active_state"] = "inactive"

    result = evaluate_samples([sample], now=started)

    assert result["status"] == "failed"
    assert "service_inactive" in result["violations"]


@pytest.mark.parametrize(
    "field,value,violation",
    [
        ("readiness", False, "readiness_failure"),
        ("database_disconnects", 1, "database_disconnect"),
        ("scheduler_enabled", False, "scheduler_disabled"),
    ],
)
def test_remote_database_preserves_health_checks(field, value, violation) -> None:
    started = datetime(2026, 9, 16, tzinfo=UTC)
    sample = _sample(started, scheduler_state="active", scheduler_restarts=0)
    sample[field] = value

    result = evaluate_samples([sample], now=started, require_scheduler_enabled=True)

    assert result["status"] == "failed"
    assert violation in result["violations"]
