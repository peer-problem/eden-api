from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite
from statistics import mean

INTEREST_FORMULA_VERSION = "interest_index_v1"
RISING_KEYWORD_FORMULA_VERSION = "rising_keyword_equal_window_v1"
INBOUND_FORMULA_VERSION = "inbound_score_v1"
CROWD_FORMULA_VERSION = "crowd_index_v1"
FORECAST_FORMULA_VERSION = "visitor_forecast_adjustment_v1"
RECOMMENDATION_FORMULA_VERSION = "recommendation_score_v1"


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


def inbound_score(
    visitors: float | None,
    flights: float | None,
    fx: float | None,
    social: float | None,
) -> ScoreResult:
    return weighted_score(
        {"visitors": visitors, "flights": flights, "fx": fx, "social": social},
        {"visitors": 0.4, "flights": 0.25, "fx": 0.15, "social": 0.2},
        INBOUND_FORMULA_VERSION,
        minimum_available=2,
    )


def crowd_index(value: float | None, reference_population: list[float]) -> ScoreResult:
    if value is None:
        return ScoreResult(None, "unavailable", "input_missing", CROWD_FORMULA_VERSION)
    finite = sorted(item for item in reference_population if isfinite(item))
    if len(finite) < 5:
        return ScoreResult(None, "unavailable", "population_too_small", CROWD_FORMULA_VERSION)
    less_or_equal = sum(item <= value for item in finite)
    return ScoreResult(
        _bounded(less_or_equal / len(finite) * 100),
        "available",
        None,
        CROWD_FORMULA_VERSION,
    )


def adjusted_forecast(
    base_visitors: int | None,
    weather_factor: float | None,
    festival_factor: float | None,
    holiday_factor: float | None,
) -> tuple[int | None, ScoreResult]:
    if base_visitors is None:
        return None, ScoreResult(
            None, "unavailable", "source_forecast_missing", FORECAST_FORMULA_VERSION
        )
    factors = _forecast_factors(weather_factor, festival_factor, holiday_factor)
    multiplier = max(0.0, 1 + (mean(factors) if factors else 0.0))
    adjusted = max(0, round(base_visitors * multiplier))
    confidence = _forecast_confidence(len(factors))
    return adjusted, ScoreResult(
        confidence,
        "available" if len(factors) == 3 else "partial",
        None if len(factors) == 3 else "some_adjustment_inputs_unavailable",
        FORECAST_FORMULA_VERSION,
    )


def adjusted_forecast_index(
    base_index: float | None,
    weather_factor: float | None,
    festival_factor: float | None,
    holiday_factor: float | None,
) -> tuple[float | None, ScoreResult]:
    """Apply the forecast adjustment to a source index without replacing it."""
    if base_index is None:
        return None, ScoreResult(
            None, "unavailable", "source_forecast_missing", FORECAST_FORMULA_VERSION
        )
    if not isfinite(base_index):
        raise ValueError("Score inputs must be finite")
    factors = _forecast_factors(weather_factor, festival_factor, holiday_factor)
    multiplier = max(0.0, 1 + (mean(factors) if factors else 0.0))
    return _bounded(base_index * multiplier), ScoreResult(
        _forecast_confidence(len(factors)),
        "available" if len(factors) == 3 else "partial",
        None if len(factors) == 3 else "some_adjustment_inputs_unavailable",
        FORECAST_FORMULA_VERSION,
    )


def _forecast_factors(*factors: float | None) -> list[float]:
    available = [float(value) for value in factors if value is not None]
    if any(not isfinite(value) for value in available):
        raise ValueError("Forecast adjustment factors must be finite")
    return available


def _forecast_confidence(available_factor_count: int) -> float:
    # The authoritative forecast contributes 0.5. Each observed adjustment
    # family contributes another 0.1, up to the documented three families.
    return round(min(0.8, 0.5 + 0.1 * available_factor_count), 4)


def recommendation_score(features: dict[str, float | None]) -> ScoreResult:
    return weighted_score(
        features,
        {
            "theme_match": 0.35,
            "demand": 0.2,
            "market_affinity": 0.15,
            "budget_fit": 0.15,
            "crowd_fit": 0.15,
        },
        RECOMMENDATION_FORMULA_VERSION,
        minimum_available=2,
    )
