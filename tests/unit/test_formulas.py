from __future__ import annotations

import pytest

from app.products.formulas import (
    adjusted_forecast,
    adjusted_forecast_index,
    crowd_index,
    inbound_score,
    weighted_score,
)


def test_weighted_score_renormalizes_only_available_positive_weights() -> None:
    result = weighted_score(
        {"available": 80.0, "missing": None, "ignored": 0.0},
        {"available": 0.25, "missing": 0.75, "ignored": 0.0},
        "test_v1",
    )

    assert result.value == 80.0
    assert result.availability == "partial"
    assert result.reason == "some_inputs_unavailable"


def test_inbound_score_requires_two_available_inputs() -> None:
    unavailable = inbound_score(70.0, None, None, None)
    available = inbound_score(70.0, 30.0, None, None)

    assert unavailable.value is None
    assert unavailable.reason == "insufficient_available_inputs"
    assert available.value == pytest.approx(54.6154)
    assert available.availability == "partial"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-10.0, 0.0), (50.0, 60.0), (500.0, 100.0)],
)
def test_crowd_index_stays_within_public_bounds(value: float, expected: float) -> None:
    result = crowd_index(value, [0.0, 25.0, 50.0, 75.0, 100.0])

    assert result.value == expected
    assert result.value is not None
    assert 0 <= result.value <= 100


def test_forecast_adjustment_is_deterministic_and_confidence_uses_zero_to_one_scale() -> None:
    adjusted, confidence = adjusted_forecast(100, -0.1, 0.1, 0.05)
    adjusted_index, index_confidence = adjusted_forecast_index(50.0, -0.1, 0.1, 0.05)

    assert adjusted == 102
    assert adjusted_index == 50.8333
    assert confidence.value == 0.8
    assert index_confidence == confidence


def test_forecast_adjustment_preserves_missing_authoritative_base() -> None:
    adjusted, confidence = adjusted_forecast(None, -0.1, 0.1, 0.05)
    adjusted_index, index_confidence = adjusted_forecast_index(None, -0.1, 0.1, 0.05)

    assert adjusted is None
    assert adjusted_index is None
    assert confidence.value is None
    assert index_confidence.value is None
    assert confidence.reason == "source_forecast_missing"
