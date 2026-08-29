from __future__ import annotations

from unittest.mock import Mock

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
