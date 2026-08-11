from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean

INTEREST_FORMULA_VERSION = "interest_index_v1"
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


def minmax_score(value: float | None, population: list[float], formula_version: str) -> ScoreResult:
    if value is None:
        return ScoreResult(None, "unavailable", "input_missing", formula_version)
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
    available = {
        key: value for key, value in scores.items() if value is not None and key in weights
    }
    if len(available) < minimum_available:
        return ScoreResult(None, "unavailable", "insufficient_available_inputs", formula_version)
    weight_sum = sum(weights[key] for key in available)
    if weight_sum <= 0:
        return ScoreResult(None, "unavailable", "available_weight_sum_zero", formula_version)
    value = sum(float(score) * weights[key] for key, score in available.items()) / weight_sum
    availability = "partial" if len(available) < len(scores) else "available"
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
    factors = [
        item for item in (weather_factor, festival_factor, holiday_factor) if item is not None
    ]
    multiplier = 1 + (mean(factors) if factors else 0)
    adjusted = max(0, round(base_visitors * multiplier))
    confidence = 80 - 10 * (3 - len(factors))
    return adjusted, ScoreResult(
        _bounded(confidence),
        "available" if len(factors) == 3 else "partial",
        None if len(factors) == 3 else "some_adjustment_inputs_unavailable",
        FORECAST_FORMULA_VERSION,
    )


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
