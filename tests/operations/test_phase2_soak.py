from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import scripts.phase2_soak as phase2_soak
from app.observability.soak import (
    PHASE1_SOAK_REQUIRED_SECONDS,
    PHASE2_SOAK_REQUIRED_SECONDS,
    evaluate_phase2_samples,
)


def _sample(
    sampled_at: datetime,
    *,
    retention_enabled: bool = True,
    capacity_allowed: bool = True,
    readiness: bool = True,
) -> dict[str, Any]:
    endpoints = (
        "trends",
        "region_insights",
        "place_detail",
        "visitor_forecast",
        "visitor_timeseries",
        "inbound_markets",
        "market_alerts",
        "recommendations",
    )
    return {
        "sampled_at": sampled_at.isoformat(),
        "boot_id": "phase2-boot",
        "release": "/opt/eden/releases/phase2",
        "readiness": readiness,
        "scheduler_enabled": True,
        "retention_enabled": retention_enabled,
        "services": {
            service: {"active_state": "active", "restarts": 0}
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
                "status_code": 200,
                "duration_ms": 100,
                "response_bytes": 1024,
                "schema_valid": True,
            }
            for endpoint in endpoints
        ],
        "database_disconnects": 0,
        "database_capacity": {
            "derived_daily_growth_bytes": 50 * 1024 * 1024,
            "derived_table_bytes": 1024,
            "disk_used_percent": 50.0,
            "capacity_reference_available": capacity_allowed,
            "product_writes_allowed": capacity_allowed,
            "source_writes_allowed": capacity_allowed,
            "capacity_reasons": [] if capacity_allowed else ["capacity_reference_unavailable"],
        },
        "database_runtime": {
            "connection_utilization": 0.2,
            "slow_queries": 0,
            "aborted_connects": 0,
        },
    }


def test_phase2_soak_is_independent_and_requires_seven_days() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    samples = [
        _sample(started + timedelta(minutes=5 * index))
        for index in range((PHASE2_SOAK_REQUIRED_SECONDS // 300) + 1)
    ]

    result = evaluate_phase2_samples(
        samples,
        now=started + timedelta(seconds=PHASE2_SOAK_REQUIRED_SECONDS),
    )

    assert PHASE2_SOAK_REQUIRED_SECONDS == 7 * 24 * 60 * 60
    assert PHASE1_SOAK_REQUIRED_SECONDS == 24 * 60 * 60
    assert result["status"] == "passed"
    assert result["required_seconds"] == PHASE2_SOAK_REQUIRED_SECONDS
    assert result["violations"] == []


def test_phase2_soak_rejects_disabled_retention_and_blocked_capacity() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    completed_at = started + timedelta(seconds=PHASE2_SOAK_REQUIRED_SECONDS)
    samples = [
        _sample(started, retention_enabled=False, capacity_allowed=True),
        _sample(completed_at, retention_enabled=False, capacity_allowed=False),
    ]

    result = evaluate_phase2_samples(samples, now=completed_at)

    assert result["status"] == "failed"
    assert {"retention_disabled", "capacity_gate_blocked"} <= set(
        result["violations"]
    )


def test_phase2_soak_waits_for_capacity_reference_before_starting() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    samples = [_sample(started, capacity_allowed=False)]

    result = evaluate_phase2_samples(samples, now=started)

    assert result["status"] == "in_progress"
    assert result["elapsed_seconds"] == 0
    assert result["violations"] == []
    assert result["reason"] == "Waiting for the first usable capacity reference."


def test_phase2_soak_starts_at_first_usable_capacity_reference() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    capacity_ready = started + timedelta(hours=20)
    completed_at = capacity_ready + timedelta(seconds=PHASE2_SOAK_REQUIRED_SECONDS)
    samples = [
        _sample(started, capacity_allowed=False),
        *(
            _sample(capacity_ready + timedelta(minutes=5 * index))
            for index in range((PHASE2_SOAK_REQUIRED_SECONDS // 300) + 1)
        ),
    ]

    result = evaluate_phase2_samples(samples, now=completed_at)

    assert result["status"] == "passed"
    assert result["started_at"] == capacity_ready.isoformat()
    assert result["violations"] == []


def test_phase2_soak_evaluates_the_latest_complete_seven_day_window() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    completed_at = started + timedelta(days=8)
    samples = [
        _sample(
            started + timedelta(minutes=5 * index),
            readiness=index != 0,
        )
        for index in range((8 * 24 * 60 // 5) + 1)
    ]

    result = evaluate_phase2_samples(samples, now=completed_at)

    assert result["status"] == "passed"
    assert result["started_at"] == (started + timedelta(days=1)).isoformat()
    assert result["elapsed_seconds"] == PHASE2_SOAK_REQUIRED_SECONDS
    assert result["readiness_failures"] == 0


def test_phase2_soak_keeps_failures_inside_the_latest_window() -> None:
    started = datetime(2026, 8, 29, tzinfo=UTC)
    completed_at = started + timedelta(days=8)
    failed_at = completed_at - timedelta(days=6)
    samples = [
        _sample(
            started + timedelta(minutes=5 * index),
            readiness=started + timedelta(minutes=5 * index) != failed_at,
        )
        for index in range((8 * 24 * 60 // 5) + 1)
    ]

    result = evaluate_phase2_samples(samples, now=completed_at)

    assert result["status"] == "failed"
    assert result["readiness_failures"] == 1
    assert "readiness_failure" in result["violations"]


def test_phase2_soak_keeps_boundary_gap_and_stale_sample_safeguards() -> None:
    completed_at = datetime(2026, 9, 6, tzinfo=UTC)
    boundary_sample = completed_at - timedelta(days=7, minutes=5)
    after_gap = completed_at - timedelta(days=7) + timedelta(minutes=20)
    samples_with_boundary_gap = [
        _sample(boundary_sample),
        *(
            _sample(after_gap + timedelta(minutes=5 * index))
            for index in range(
                int((completed_at - after_gap).total_seconds() // 300) + 1
            )
        ),
    ]

    gap_result = evaluate_phase2_samples(
        samples_with_boundary_gap,
        now=completed_at,
    )
    complete_samples = [
        _sample(completed_at - timedelta(days=7) + timedelta(minutes=5 * index))
        for index in range((PHASE2_SOAK_REQUIRED_SECONDS // 300) + 1)
    ]
    stale_result = evaluate_phase2_samples(
        complete_samples,
        now=completed_at + timedelta(minutes=16),
    )

    assert gap_result["status"] == "failed"
    assert "sampling_gap" in gap_result["violations"]
    assert stale_result["status"] == "failed"
    assert "sampling_gap" in stale_result["violations"]


def test_phase2_soak_keeps_missing_and_incomplete_evidence_in_progress_or_failed() -> None:
    capacity_ready = datetime(2026, 8, 30, tzinfo=UTC)
    incomplete_at = capacity_ready + timedelta(days=6)
    incomplete_samples = [
        _sample(capacity_ready - timedelta(days=1), capacity_allowed=False),
        *(
            _sample(capacity_ready + timedelta(minutes=5 * index))
            for index in range((6 * 24 * 60 // 5) + 1)
        ),
    ]

    incomplete_result = evaluate_phase2_samples(
        incomplete_samples,
        now=incomplete_at,
    )
    complete_samples = [
        _sample(capacity_ready + timedelta(minutes=5 * index))
        for index in range((PHASE2_SOAK_REQUIRED_SECONDS // 300) + 1)
    ]
    missing_result = evaluate_phase2_samples(
        complete_samples[::2],
        now=capacity_ready + timedelta(days=7),
    )

    assert incomplete_result["status"] == "in_progress"
    assert incomplete_result["elapsed_seconds"] == 6 * 24 * 60 * 60
    assert incomplete_result["violations"] == []
    assert missing_result["status"] == "failed"
    assert "sampling_coverage" in missing_result["violations"]


def test_phase2_collector_uses_a_dedicated_evidence_path() -> None:
    assert Path("/opt/eden/phase2-evidence/soak.jsonl") == phase2_soak.DEFAULT_EVIDENCE_PATH
    assert Path("/opt/eden/phase1-evidence/soak.jsonl") != phase2_soak.DEFAULT_EVIDENCE_PATH
