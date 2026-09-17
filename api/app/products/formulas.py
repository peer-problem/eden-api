from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite

INTEREST_FORMULA_VERSION = "interest_index_v1"
RISING_KEYWORD_FORMULA_VERSION = "rising_keyword_equal_window_v1"
FORECAST_FORMULA_VERSION = "official_forecast_v1"


@dataclass(frozen=True, slots=True)
class ScoreResult:
    value: float | None
    availability: str
    reason: str | None
    formula_version: str


def _bounded(value: float) -> float:
    if not isfinite(value):
        raise ValueError("Score inputs must be finite")
    return round(max(0.0, min(100.0, value)), 4)


def bounded_index(value: float | None) -> float | None:
    """Clamp a present numeric index to the public 0-to-100 contract."""
    return None if value is None else _bounded(float(value))


def minmax_score(value: float | None, population: list[float], formula_version: str) -> ScoreResult:
    if value is None:
        return ScoreResult(None, "unavailable", "input_missing", formula_version)
    if not isfinite(value):
        raise ValueError("Score inputs must be finite")
    finite = [item for item in population if isfinite(item)]
    if len(finite) < 2:
        return ScoreResult(None, "unavailable", "population_too_small", formula_version)
    low, high = min(finite), max(finite)
    if low == high:
        return ScoreResult(None, "unavailable", "normalization_range_zero", formula_version)
    return ScoreResult(
        _bounded((value - low) / (high - low) * 100), "available", None, formula_version
    )


def weighted_score(
    scores: dict[str, float | None],
    weights: dict[str, float],
    formula_version: str,
    minimum_available: int = 1,
) -> ScoreResult:
    invalid_weights = [key for key, weight in weights.items() if not isfinite(weight) or weight < 0]
    if invalid_weights:
        raise ValueError("Score weights must be finite and non-negative")
    expected = {key for key in scores if key in weights and weights[key] > 0}
    available = {
        key: float(value) for key, value in scores.items() if value is not None and key in expected
    }
    if any(not isfinite(value) for value in available.values()):
        raise ValueError("Score inputs must be finite")
    if len(available) < minimum_available:
        return ScoreResult(None, "unavailable", "insufficient_available_inputs", formula_version)
    available_keys = sorted(available)
    weight_sum = fsum(weights[key] for key in available_keys)
    if weight_sum <= 0:
        return ScoreResult(None, "unavailable", "available_weight_sum_zero", formula_version)
    value = fsum(available[key] * weights[key] for key in available_keys) / weight_sum
    availability = "partial" if len(available) < len(expected) else "available"
    reason = "some_inputs_unavailable" if availability == "partial" else None
    return ScoreResult(_bounded(value), availability, reason, formula_version)


def interest_index(scores: dict[str, float | None], weights: dict[str, float]) -> ScoreResult:
    return weighted_score(scores, weights, INTEREST_FORMULA_VERSION)
