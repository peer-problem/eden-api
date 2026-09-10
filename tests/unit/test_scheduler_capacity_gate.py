from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import app.scheduler.capacity as capacity_module
from app.observability.capacity import CapacityDecision, CapacitySample
from app.scheduler.capacity import SchedulerCapacityGate


def test_capacity_gate_fails_closed_before_first_sample() -> None:
    gate = SchedulerCapacityGate()

    assert gate.source_pause_reason() == "capacity_sample_unavailable"
    assert gate.product_pause_reason() == "capacity_sample_unavailable"


def test_capacity_gate_keeps_fail_closed_decision_when_sampling_fails() -> None:
    gate = SchedulerCapacityGate()
    settings = Mock()
    settings.RELEASE_ROOT.exists.return_value = False
    factory = Mock()
    factory.begin.side_effect = RuntimeError("information schema unavailable")

    decision = gate.refresh(settings, factory)

    assert decision is not None
    assert not decision.source_writes_allowed
    assert not decision.product_writes_allowed
    assert decision.reasons == ("capacity_sample_unavailable",)


def _allowing_gate(sampled_at: datetime) -> SchedulerCapacityGate:
    gate = SchedulerCapacityGate()
    gate._sample = CapacitySample(  # noqa: SLF001 - focused admission-gate fixture
        sampled_at=sampled_at,
        disk_used_percent=50,
        tables=(),
        system_memory_used_percent=50,
    )
    gate._decision = CapacityDecision(  # noqa: SLF001 - focused admission-gate fixture
        disk_used_percent=50,
        derived_daily_growth_bytes=0,
        warning=False,
        product_writes_allowed=True,
        source_writes_allowed=True,
        reasons=(),
    )
    return gate


def test_capacity_gate_checks_fresh_memory_at_each_write_admission(
    monkeypatch,
) -> None:
    gate = _allowing_gate(datetime.now(UTC))
    monkeypatch.setattr(capacity_module, "system_memory_used_percent", lambda: 85.0)

    assert gate.source_pause_reason() == "memory_write_pause"
    assert gate.product_pause_reason() == "memory_write_pause"


def test_capacity_gate_rejects_a_decision_older_than_ten_minutes(monkeypatch) -> None:
    gate = _allowing_gate(datetime.now(UTC) - timedelta(minutes=11))
    monkeypatch.setattr(capacity_module, "system_memory_used_percent", lambda: 50.0)

    assert gate.source_pause_reason() == "capacity_sample_stale"
    assert gate.product_pause_reason() == "capacity_sample_stale"


def test_sampling_failure_replaces_a_previous_allow_decision() -> None:
    gate = _allowing_gate(datetime.now(UTC))
    settings = Mock()
    settings.RELEASE_ROOT.exists.return_value = False
    factory = Mock()
    factory.begin.side_effect = RuntimeError("information schema unavailable")

    decision = gate.refresh(settings, factory)

    assert decision is not None
    assert decision.reasons == ("capacity_sample_unavailable",)
    assert gate.source_pause_reason().startswith("capacity_sample_unavailable")
