from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import scripts.phase1_soak as phase1_soak
from app.observability.soak import PHASE1_SOAK_REQUIRED_SECONDS, evaluate_samples

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _sample(
    sampled_at: datetime,
    *,
    duration_ms: float = 100,
    status_code: int = 200,
    response_bytes: int = 1024,
    restarts: int = 0,
    database_disconnects: int = 0,
    derived_daily_growth_bytes: int | None = 50 * 1024 * 1024,
    scheduler_enabled: bool = True,
) -> dict[str, Any]:
    return {
        "sampled_at": sampled_at.isoformat(),
        "boot_id": "phase1-boot",
        "release": "/opt/eden/releases/phase1",
        "readiness": True,
        "scheduler_enabled": scheduler_enabled,
        "services": {
            service: {"active_state": "active", "restarts": restarts}
            for service in ("eden-api", "mariadb", "nginx")
        },
        "memory": {
            "available_bytes": 512 * 1024 * 1024,
            "swap_total_bytes": 1024,
            "swap_free_bytes": 1024,
        },
        "vmstat": {"pswpin": 0, "pswpout": 0},
        "public_route_probes": [
            {
                "endpoint": endpoint,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "response_bytes": response_bytes,
                "schema_valid": True,
            }
            for endpoint in (
                "trends",
                "region_insights",
                "place_detail",
                "visitor_forecast",
                "visitor_timeseries",
                "inbound_markets",
                "market_alerts",
                "recommendations",
            )
        ],
        "database_disconnects": database_disconnects,
        "database_capacity": {
            "derived_daily_growth_bytes": derived_daily_growth_bytes,
            "derived_table_bytes": 1024,
            "disk_used_percent": 50.0,
        },
        "database_runtime": {
            "connection_utilization": 0.2,
            "slow_queries": 0,
            "aborted_connects": 0,
        },
    }


def test_phase1_soak_passes_only_after_contiguous_24_hour_evidence() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    samples = [
        _sample(started, scheduler_enabled=False),
        *[
            _sample(started + timedelta(minutes=5 * index))
            for index in range(1, 289)
        ],
    ]

    result = evaluate_samples(
        samples,
        now=started + timedelta(hours=24),
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
        require_scheduler_phases=True,
    )

    assert result["status"] == "passed"
    assert result["public_probe_count"] == 289 * 8
    assert result["api_p95_milliseconds"] == 100
    assert set(result["api_route_p95_milliseconds"]) == {
        "trends",
        "region_insights",
        "place_detail",
        "visitor_forecast",
        "visitor_timeseries",
        "inbound_markets",
        "market_alerts",
        "recommendations",
    }
    assert result["max_derived_daily_growth_bytes"] == 50 * 1024 * 1024
    assert result["scheduler_phases_missing"] == []
    assert result["max_database_connection_utilization"] == 0.2
    assert result["violations"] == []


def test_phase1_soak_rejects_api_resource_and_database_failures() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    samples = [
        _sample(started),
        _sample(
            started + timedelta(minutes=5),
            duration_ms=501,
            status_code=500,
            response_bytes=(2 * 1024 * 1024) + 1,
            restarts=1,
            database_disconnects=1,
        ),
    ]

    result = evaluate_samples(
        samples,
        now=started + timedelta(minutes=5),
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
    )

    assert result["status"] == "failed"
    assert set(result["violations"]) >= {
        "service_restart",
        "api_probe_failure",
        "api_p95_exceeded",
        "api_route_p95_exceeded",
        "api_response_too_large",
        "database_disconnect",
    }


def test_phase1_soak_requires_every_route_and_valid_response_schema() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    sample = _sample(started)
    sample["public_route_probes"] = sample["public_route_probes"][:-1]
    sample["public_route_probes"][0]["schema_valid"] = False

    result = evaluate_samples(
        [sample],
        now=started,
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
    )

    assert "api_schema_failure" in result["violations"]
    assert "api_route_coverage_missing" in result["violations"]


def test_phase1_soak_requires_scheduler_off_and_on_measurements_at_gate() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    completed_at = started + timedelta(hours=24)

    result = evaluate_samples(
        [_sample(started), _sample(completed_at)],
        now=completed_at,
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        max_gap_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
        require_scheduler_phases=True,
    )

    assert "api_scheduler_phase_missing" in result["violations"]
    assert result["scheduler_phases_missing"] == ["scheduler_off"]


def test_phase1_soak_rejects_missing_or_excessive_database_growth_evidence() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    completed_at = started + timedelta(hours=24)

    missing = evaluate_samples(
        [
            _sample(started, derived_daily_growth_bytes=None),
            _sample(completed_at, derived_daily_growth_bytes=None),
        ],
        now=completed_at,
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        max_gap_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
    )
    exceeded = evaluate_samples(
        [
            _sample(started),
            _sample(completed_at, derived_daily_growth_bytes=100 * 1024 * 1024 + 1),
        ],
        now=completed_at,
        required_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        max_gap_seconds=PHASE1_SOAK_REQUIRED_SECONDS,
        require_public_probes=True,
    )

    assert "database_growth_missing" in missing["violations"]
    assert "database_growth_budget_exceeded" in exceeded["violations"]


def test_deploy_installs_the_five_minute_phase1_soak_timer() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            REPOSITORY_ROOT / ".ops" / "deploy.sh",
            REPOSITORY_ROOT / "scripts" / "deploy_support.sh",
            REPOSITORY_ROOT / "scripts" / "deploy_remote.sh",
        )
    )

    assert "eden-phase1-soak.service" in source
    assert "scripts/phase1_soak.py sample" in source
    assert "OnUnitActiveSec=5min" in source
    assert "scripts/phase1_soak.py baseline --iterations 20" in source
    assert "20-phase1-baseline.conf" in source
    assert "EnvironmentFile=/opt/eden/phase1-evidence/baseline.env" in source
    assert "eden-phase1-soak.timer" in source


def test_deploy_installs_query_plan_database_and_journal_gates() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            REPOSITORY_ROOT / ".ops" / "deploy.sh",
            REPOSITORY_ROOT / "scripts" / "deploy_support.sh",
            REPOSITORY_ROOT / "scripts" / "deploy_remote.sh",
        )
    )

    assert "scripts/phase1_query_plans.py" in source
    assert "eden-phase1-query-plans.timer" in source
    assert "scripts/phase1_db_maintenance.py" in source
    assert "OnCalendar=Sun *-*-* 03:30:00" in source
    assert "SystemMaxUse=200M" in source
    assert "RuntimeMaxUse=100M" in source
    assert "MaxRetentionSec=14day" in source
    assert "MaxFileSec=1day" in source


def test_scheduler_off_baseline_cli_fails_activation_on_a_threshold_violation(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        phase1_soak,
        "record_baseline",
        lambda _path, *, iterations: {"status": "failed", "iterations": iterations},
    )
    monkeypatch.setattr(sys, "argv", ["phase1_soak.py", "baseline", "--iterations", "20"])

    assert phase1_soak.main() == 1


def test_warmup_cli_fails_when_a_public_route_cannot_be_primed(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        phase1_soak,
        "warm_public_routes",
        lambda: {"status": "failed", "failed_endpoints": ["recommendations"]},
    )
    monkeypatch.setattr(sys, "argv", ["phase1_soak.py", "warmup"])

    assert phase1_soak.main() == 1
