from __future__ import annotations

from datetime import date

import pytest

from app.api.v1.schemas import VisitorForecastData
from app.domain.enums import Availability
from app.products.forecast_views import build_forecast_view


def _scope(*, days: int = 2) -> dict[str, object]:
    return {
        "days": days,
        "include": ["weather", "festivals", "holidays"],
        "place_name": "광화문",
    }


def test_forecast_preserves_official_values_without_arbitrary_adjustments() -> None:
    product = {
        "area_code": "11",
        "inputs": [
            {
                "input_id": 1,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-29T00:00:00+00:00",
                "place_id": "eden_place_1",
                "source_forecast": {
                    "place_name": "광화문",
                    "concentration_rate": 50.0,
                    "expected_visitors": None,
                },
            },
            {
                "input_id": 2,
                "source_id": "SRC_KMA_FORECAST",
                "forecast_date": "2026-08-29T00:00:00+00:00",
                "weather": {
                    "temperature_c": 24.0,
                    "precipitation_probability_pct": 80.0,
                    "condition": "rain",
                    "nx": 60,
                    "ny": 127,
                },
            },
            {
                "input_id": 3,
                "source_id": "SRC_LOCAL_FESTIVALS",
                "forecast_date": "2026-08-29T00:00:00+00:00",
                "festivals": [{"name": "서울축제"}],
            },
            {
                "input_id": 4,
                "source_id": "SRC_KASI_HOLIDAYS",
                "forecast_date": "2026-08-29T00:00:00+00:00",
                "holiday": {"name": "광복절", "is_holiday": True},
            },
            {
                "input_id": 5,
                "source_id": "SRC_KMA_FORECAST",
                "forecast_date": "2026-08-30T00:00:00+00:00",
                "weather": {
                    "temperature_c": 25.0,
                    "precipitation_probability_pct": 0.0,
                    "condition": "clear",
                    "nx": 60,
                    "ny": 127,
                },
            },
        ],
    }

    data, availability, reason = build_forecast_view(product, _scope(), today=date(2026, 8, 29))

    first, outside_source_horizon = data["daily"]
    assert first["source_concentration_rate"] == 50.0
    assert first["demand_score"] == 50.0
    assert first["expected_visitors"] is None
    assert first["confidence"] is None
    assert first["adjustment_factors"] == {}
    assert outside_source_horizon["source_concentration_rate"] is None
    assert outside_source_horizon["demand_score"] is None
    assert outside_source_horizon["expected_visitors"] is None
    assert outside_source_horizon["confidence"] is None
    assert outside_source_horizon["availability"] == "unavailable"
    assert outside_source_horizon["festivals"] is None
    assert outside_source_horizon["holiday"] is None
    assert availability == Availability.PARTIAL
    assert reason == "요청 2일 중 1일의 전망만 있습니다."
    VisitorForecastData.model_validate(data)


def test_forecast_view_adjusts_source_supplied_expected_visitors() -> None:
    product = {
        "area_code": "11",
        "inputs": [
            {
                "input_id": 1,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-29",
                "source_forecast": {
                    "place_name": "광화문",
                    "concentration_rate": 50.0,
                    "expected_visitors": 100,
                },
            },
            {
                "input_id": 2,
                "source_id": "SRC_KMA_FORECAST",
                "forecast_date": "2026-08-29",
                "weather": {"condition": "rain", "nx": 60, "ny": 127},
            },
            {
                "input_id": 3,
                "source_id": "SRC_LOCAL_FESTIVALS",
                "forecast_date": "2026-08-29",
                "festivals": [{"name": "서울축제"}],
            },
            {
                "input_id": 4,
                "source_id": "SRC_KASI_HOLIDAYS",
                "forecast_date": "2026-08-29",
                "holiday": {"is_holiday": True},
            },
        ],
    }

    data, availability, _reason = build_forecast_view(
        product, _scope(days=1), today=date(2026, 8, 29)
    )

    assert data["daily"][0]["expected_visitors"] == 100
    assert availability == Availability.AVAILABLE


def test_forecast_missing_requested_adjustments_are_null_and_partial() -> None:
    product = {
        "area_code": "11",
        "eden_area_id": "eden_area_11",
        "inputs": [
            {
                "input_id": 1,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-29",
                "source_forecast": {
                    "place_name": "광화문",
                    "concentration_rate": 50.0,
                    "expected_visitors": 100,
                },
            }
        ],
    }

    data, availability, reason = build_forecast_view(
        product,
        _scope(days=1),
        today=date(2026, 8, 29),
    )

    day = data["daily"][0]
    assert data["eden_area_id"] == "eden_area_11"
    assert day["weather"] is None
    assert day["festivals"] is None
    assert day["holiday"] is None
    assert day["adjustment_factors"] == {}
    assert day["availability"] == "partial"
    assert availability == Availability.PARTIAL
    assert reason == "일부 날짜에서 요청한 참고 원천이 없습니다."


def test_area_forecast_averages_its_attractions_deterministically() -> None:
    rows = [
        {
            "input_id": 2,
            "source_id": "SRC_KTO_VISITOR_FORECAST",
            "forecast_date": "2026-08-29",
            "place_id": "place_b",
            "source_forecast": {
                "place_name": "B",
                "concentration_rate": 80.0,
                "expected_visitors": None,
            },
        },
        {
            "input_id": 1,
            "source_id": "SRC_KTO_VISITOR_FORECAST",
            "forecast_date": "2026-08-29",
            "place_id": "place_a",
            "source_forecast": {
                "place_name": "A",
                "concentration_rate": 20.0,
                "expected_visitors": None,
            },
        },
    ]
    scope = {"days": 1, "include": []}

    forward = build_forecast_view(
        {"area_code": "11", "inputs": rows}, scope, today=date(2026, 8, 29)
    )
    reverse = build_forecast_view(
        {"area_code": "11", "inputs": list(reversed(rows))},
        scope,
        today=date(2026, 8, 29),
    )

    assert forward == reverse
    # An area request averages its attractions instead of picking one of them.
    day = forward[0]["daily"][0]
    assert day["source_concentration_rate"] == 50.0
    assert day["demand_score"] == 50.0
    assert day["method"] == "official"
    assert day["sample_count"] == 2
    assert day["basis"] == "지역 내 관광지 2곳의 공식 집중률 평균"
    assert day["expected_visitors"] is None
    assert forward[1] == Availability.PARTIAL  # reference sources are not covered

    named, _, _ = build_forecast_view(
        {"area_code": "11", "inputs": rows},
        {**scope, "place_name": "B"},
        today=date(2026, 8, 29),
    )
    assert named["daily"][0]["source_concentration_rate"] == 80.0
    assert named["daily"][0]["method"] == "official"


@pytest.mark.parametrize(
    ("place_id", "place_name", "expected_score"),
    [(None, None, 40.0), (None, "광화문", 40.0), ("place_a", None, 40.0)],
)
def test_area_forecast_uses_area_rows_or_the_attraction_mean(
    place_id: str | None, place_name: str | None, expected_score: float | None
) -> None:
    product = {
        "area_code": "11",
        "inputs": [{
            "source_id": "SRC_KTO_VISITOR_FORECAST",
            "forecast_date": "2026-08-29",
            "place_id": place_id,
            "source_forecast": {"place_name": place_name, "concentration_rate": 40.0},
        }],
    }

    data, _, _ = build_forecast_view(product, {"days": 1}, today=date(2026, 8, 29))

    assert data["daily"][0]["source_concentration_rate"] == expected_score
    assert data["daily"][0]["method"] == ("official" if expected_score is not None else None)


def test_area_forecast_preserves_official_rows_without_place_fields() -> None:
    product = {
        "area_code": "11",
        "inputs": [{
            "source_id": "SRC_KTO_VISITOR_FORECAST",
            "forecast_date": "2026-08-29",
            "source_forecast": {"concentration_rate": 40.0},
        }],
    }

    data, _, _ = build_forecast_view(product, {"days": 1}, today=date(2026, 8, 29))

    assert data["daily"][0]["source_concentration_rate"] == 40.0
    assert data["daily"][0]["method"] == "official"


def test_forecast_uses_the_requested_precollected_weather_grid() -> None:
    product = {
        "area_code": "11",
        "inputs": [
            {
                "input_id": 1,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-29",
                "source_forecast": {
                    "place_name": "광화문",
                    "concentration_rate": 50.0,
                    "expected_visitors": None,
                },
            },
            {
                "input_id": 2,
                "source_id": "SRC_KMA_FORECAST",
                "forecast_date": "2026-08-29",
                "weather": {"condition": "rain", "nx": 1, "ny": 2},
            },
            {
                "input_id": 3,
                "source_id": "SRC_KMA_FORECAST",
                "forecast_date": "2026-08-29",
                "weather": {"condition": "clear", "nx": 60, "ny": 127},
            },
        ],
    }
    scope = {
        "days": 1,
        "include": ["weather"],
        "place_name": "광화문",
        "nx": 60,
        "ny": 127,
    }

    data, _availability, _reason = build_forecast_view(product, scope, today=date(2026, 8, 29))

    assert data["daily"][0]["weather"]["condition"] == "clear"
    assert data["daily"][0]["weather"]["grid_source"] == "request"
    assert data["daily"][0]["adjustment_factors"] == {}


def test_historical_visits_never_replace_missing_official_forecasts():
    from datetime import timedelta

    today = date(2026, 9, 11)
    visits = [
        {"period_start": (today - timedelta(days=offset)).isoformat(),
         "grain": "day", "subject_type": "area", "visitor_type": "all",
         "visitor_count": 100}
        for offset in range(60)
    ]
    data, status, _ = build_forecast_view(
        {"area_code": "11", "visits": visits, "inputs": []},
        {"days": 30}, today=today,
    )
    assert status == Availability.UNAVAILABLE
    assert len(data["daily"]) == 30
    for row in data["daily"]:
        assert row["demand_score"] is None
        assert row["method"] is None
        assert row["expected_visitors"] is None
        assert row["confidence"] is None
        assert row["sample_count"] is None
    VisitorForecastData.model_validate(data)


def test_named_attraction_does_not_receive_area_proxy():
    from datetime import timedelta

    today = date(2026, 9, 11)
    visits = [
        {
            "period_start": (today - timedelta(days=offset)).isoformat(),
            "grain": "day",
            "subject_type": "area",
            "visitor_type": "all",
            "visitor_count": 100,
        }
        for offset in range(30)
    ]
    data, status, _ = build_forecast_view(
        {"area_code": "11", "visits": visits}, {"days": 1, "place_name": "unknown"}, today=today
    )
    assert status == Availability.UNAVAILABLE
    assert data["daily"][0]["demand_score"] is None


def _coverage_product(through: str) -> dict[str, object]:
    return {
        "area_code": "11",
        "eden_area_id": "eden_area_11",
        "reference_coverage": {
            "SRC_FESTIVAL": {"from": "2026-08-29", "through": through},
            "SRC_HOLIDAY": {"from": "2026-08-29", "through": through},
        },
        "inputs": [
            {
                "input_id": 1,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-29",
                "source_forecast": {"place_name": "광화문", "concentration_rate": 50.0},
            },
            {
                "input_id": 2,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-30",
                "source_forecast": {"place_name": "광화문", "concentration_rate": 55.0},
            },
        ],
    }


def test_covered_dates_without_events_are_known_empty_not_missing() -> None:
    scope = {**_scope(days=2), "include": ["festivals", "holidays"]}

    data, availability, reason = build_forecast_view(
        _coverage_product("2026-11-26"), scope, today=date(2026, 8, 29)
    )

    assert [day["festivals"] for day in data["daily"]] == [[], []]
    assert [day["holiday"] for day in data["daily"]] == [False, False]
    assert [day["availability"] for day in data["daily"]] == ["available", "available"]
    assert data["festivals"] == []
    assert data["holiday"] is False
    assert availability == Availability.AVAILABLE
    assert reason is None
    VisitorForecastData.model_validate(data)


def test_dates_past_the_reference_coverage_window_stay_missing() -> None:
    scope = {**_scope(days=2), "include": ["festivals", "holidays"]}

    data, availability, reason = build_forecast_view(
        _coverage_product("2026-08-29"), scope, today=date(2026, 8, 29)
    )

    first, second = data["daily"]
    assert first["festivals"] == [] and first["holiday"] is False
    assert first["availability"] == "available"
    assert second["festivals"] is None and second["holiday"] is None
    assert second["availability"] == "partial"
    assert second["reason"] == "일부 참고 정보가 없습니다: festivals, holidays"
    assert availability == Availability.PARTIAL
    assert reason == "일부 날짜에서 요청한 참고 원천이 없습니다."


def test_event_rows_still_win_over_empty_coverage() -> None:
    product = _coverage_product("2026-11-26")
    product["inputs"].append(
        {
            "input_id": 3,
            "source_id": "SRC_FESTIVAL",
            "forecast_date": "2026-08-30",
            "festivals": [{"name": "서울거리예술축제"}],
        }
    )
    product["inputs"].append(
        {
            "input_id": 4,
            "source_id": "SRC_HOLIDAY",
            "forecast_date": "2026-08-30",
            "holiday": {"name": "임시공휴일", "is_holiday": True},
        }
    )
    scope = {**_scope(days=2), "include": ["festivals", "holidays"]}

    data, _, _ = build_forecast_view(product, scope, today=date(2026, 8, 29))

    assert data["daily"][1]["festivals"] == ["서울거리예술축제"]
    assert data["daily"][1]["holiday"] is True
    assert data["festivals"] == ["서울거리예술축제"]
    assert data["holiday"] is True


def test_malformed_coverage_is_ignored() -> None:
    product = _coverage_product("not-a-date")
    product["reference_coverage"]["SRC_HOLIDAY"] = "garbage"
    scope = {**_scope(days=1), "include": ["festivals", "holidays"]}

    data, availability, _ = build_forecast_view(product, scope, today=date(2026, 8, 29))

    assert data["daily"][0]["festivals"] is None
    assert data["daily"][0]["holiday"] is None
    assert availability == Availability.PARTIAL


def test_forecast_view_echoes_area_codes_and_uses_an_aggregated_province_row() -> None:
    product = {
        "area_code": "1100000000",
        "spatial_resolution": "sido",
        "inputs": [
            {
                "input_id": None,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-29T00:00:00+00:00",
                "place_id": None,
                "source_forecast": {
                    "place_name": None,
                    "concentration_rate": 61.25,
                    "expected_visitors": None,
                    "sample_count": 120,
                    "sigungu_count": 25,
                    "basis": "시도 내 시군구 25곳, 관광지 120곳의 공식 집중률 평균",
                    "aggregated_from": "sigungu",
                },
            },
            {
                "input_id": 7,
                "source_id": "SRC_KTO_VISITOR_FORECAST",
                "forecast_date": "2026-08-29T00:00:00+00:00",
                "place_id": "eden_place_1",
                "source_forecast": {
                    "place_name": "광화문",
                    "concentration_rate": 99.0,
                    "expected_visitors": None,
                },
            },
        ],
    }
    scope = {
        "days": 1,
        "include": ["weather", "festivals", "holidays"],
        "requested_area_code": "11",
    }

    data, availability, _reason = build_forecast_view(product, scope, today=date(2026, 8, 29))

    assert data["requested_area_code"] == "11"
    assert data["data_area_code"] == "1100000000"
    assert data["spatial_resolution"] == "sido"
    (day,) = data["daily"]
    # The province aggregate wins over any single attraction row.
    assert day["source_concentration_rate"] == 61.25
    assert day["method"] == "official"
    assert day["sample_count"] == 120
    assert day["basis"] == "시도 내 시군구 25곳, 관광지 120곳의 공식 집중률 평균"
    assert availability == Availability.PARTIAL
    VisitorForecastData.model_validate(data)
