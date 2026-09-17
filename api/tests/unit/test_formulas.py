from __future__ import annotations

from app.products.formulas import (
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
