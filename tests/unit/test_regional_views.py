from __future__ import annotations

import pytest

from app.api.v1.schemas import RegionInsightData, VisitorTimeseriesData
from app.domain.enums import Availability
from app.products.regional_views import (
    _sum,
    _visitor_totals,
    build_region_insight_view,
    build_visitor_timeseries_view,
)


def _visit(
    visitor_type: str,
    count: int | None,
    *,
    period_start: str = "2026-08-29",
    grain: str = "day",
    subject_type: str = "area",
    subject_key: str = "area",
) -> dict[str, object]:
    return {
        "period_start": period_start,
        "grain": grain,
        "subject_type": subject_type,
        "subject_key": subject_key,
        "visitor_type": visitor_type,
        "visitor_count": count,
        "completeness_ratio": 1.0,
    }


def _area() -> dict[str, str]:
    return {
        "area_code": "11",
        "eden_area_id": "eden_area_11",
        "name": "서울",
        "spatial_resolution": "sido",
    }


def test_regional_sum_preserves_all_missing_but_not_numeric_zero() -> None:
    assert _sum([None, None]) is None
    assert _sum([None, 0]) == 0


def test_visitor_totals_only_derive_all_within_the_same_scope_and_grain() -> None:
    rows = [
        _visit("all", 100),
        _visit("domestic", 40),
        _visit("foreign", 60),
        _visit("all", 500, subject_key="other"),
        _visit("domestic", 20, subject_key="other"),
        _visit("foreign", 80, grain="month"),
    ]

    assert _visitor_totals(rows) == {"all": 600, "domestic": 60, "foreign": 140}


def test_visitor_totals_reject_mismatch_at_the_same_scope_and_grain() -> None:
    with pytest.raises(ValueError, match="all must equal domestic"):
        _visitor_totals([_visit("all", 99), _visit("domestic", 40), _visit("foreign", 60)])


def test_region_insight_ignores_unselected_and_attraction_dates() -> None:
    product = {
        "area": _area(),
        "visits": [
            _visit("all", 10, period_start="2026-01-01"),
            _visit(
                "all",
                999,
                period_start="2026-02-01",
                grain="month",
                subject_type="attraction",
                subject_key="광화문",
            ),
        ],
        "demand": [{"period_start": "2026-03-01", "stay_index": 50.0}],
        "diversity": [],
    }

    data, availability, reason = build_region_insight_view(
        product, {"include": ["visitors"], "period": "7d"}
    )

    assert data["visitors"]["total"] == 10
    assert data["demand"] is None
    assert data["sources"] == ["SRC_KTO_REGIONAL_VISITORS"]
    assert availability == Availability.PARTIAL
    assert reason is not None
    RegionInsightData.model_validate(data)


def test_region_indices_are_bounded_and_partial_diversity_edit_is_preserved() -> None:
    product = {
        "area": _area(),
        "visits": [],
        "demand": [
            {
                "period_start": "2026-08-29",
                "stay_index": 120.0,
                "spend_index": -20.0,
            }
        ],
        "diversity": [{"period_start": "2026-08-29", "age_index": 55.0}],
    }

    data, availability, _reason = build_region_insight_view(
        product, {"include": ["demand", "diversity"], "period": "7d"}
    )

    assert data["demand"]["stay_index"] == 100.0
    assert data["demand"]["spend_index"] == 0.0
    assert data["demand"]["availability"] == "partial"
    assert data["demand"]["reason"] == "일부 수요 차원이 없습니다."
    assert data["diversity"]["availability"] == "partial"
    assert data["diversity"]["nationality_index"] is None
    assert availability == Availability.PARTIAL


def test_previous_year_comparison_uses_calendar_dates_across_leap_day() -> None:
    product = {
        "area": _area(),
        "visits": [
            _visit("all", 100, period_start="2023-02-28"),
            _visit("all", 120, period_start="2024-02-29"),
        ],
        "demand": [],
        "diversity": [],
    }

    data, _availability, _reason = build_region_insight_view(
        product,
        {
            "include": ["visitors"],
            "period": "7d",
            "compare": "previous_year",
        },
    )

    assert data["comparison"]["baseline_start"] == "2023-02-23"
    assert data["comparison"]["baseline_end"] == "2023-02-28"
    assert data["comparison"]["change_rate"] is None  # Incomplete windows cannot be compared.


def test_attraction_monthly_observation_is_not_relabelled_as_daily() -> None:
    product = {
        "area": _area(),
        "visits": [
            _visit(
                "all",
                100,
                period_start="2026-08-01",
                grain="month",
                subject_type="attraction",
                subject_key="광화문",
            )
        ],
    }
    daily_scope = {
        "period": "30d",
        "granularity": "day",
        "attraction_name": "광화문",
    }
    month_scope = {**daily_scope, "granularity": "month"}

    daily, daily_availability, _reason = build_visitor_timeseries_view(product, daily_scope)
    monthly, monthly_availability, _reason = build_visitor_timeseries_view(product, month_scope)

    assert daily is None
    assert daily_availability == Availability.UNAVAILABLE
    assert monthly is not None
    assert all(point["grain"] == "month" for point in monthly["series"])
    assert [point["completeness_ratio"] for point in monthly["series"]] == [0.0, 1.0]
    assert monthly_availability == Availability.PARTIAL
    VisitorTimeseriesData.model_validate(monthly)


def test_visitor_type_projects_region_and_timeseries_counts() -> None:
    product = {
        "area": _area(),
        "visits": [
            _visit("all", 100),
            _visit("domestic", 40),
            _visit("foreign", 60),
        ],
        "demand": [],
        "diversity": [],
    }

    region, _, _ = build_region_insight_view(
        product,
        {"include": ["visitors"], "period": "7d", "visitor_type": "foreign"},
    )
    timeseries, _, _ = build_visitor_timeseries_view(
        product,
        {"period": "7d", "granularity": "day", "visitor_type": "domestic"},
    )

    assert region["visitors"] == {
        "total": 60,
        "domestic": None,
        "foreign": 60,
        "change_rate": None,
        "availability": "partial",
        "reason": "일부 날짜의 방문 관측이 없습니다.",
        "completeness_ratio": 1 / 7,
    }
    assert timeseries is not None
    observed = timeseries["series"][-1]
    assert observed["total"] == 40
    assert observed["domestic"] == 40
    assert observed["foreign"] is None
    assert timeseries["summary"]["total"] == 40
    assert timeseries["summary"]["domestic"] == 40
    assert timeseries["summary"]["foreign"] is None


def test_timeseries_missing_requested_buckets_reduce_completeness() -> None:
    product = {
        "area": _area(),
        "visits": [_visit("all", 10, period_start="2026-08-29")],
    }

    data, availability, reason = build_visitor_timeseries_view(
        product,
        {"period": "7d", "granularity": "day", "visitor_type": "all"},
    )

    assert data is not None
    assert len(data["series"]) == 7
    assert data["summary"]["completeness_ratio"] == round(1 / 7, 6)
    assert availability == Availability.PARTIAL
    assert reason == "시계열의 일부 날짜가 없습니다."
