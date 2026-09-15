from __future__ import annotations

from datetime import date

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


def test_forecast_base_selection_is_deterministic_for_the_same_rows() -> None:
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
    assert forward[0]["daily"][0]["source_concentration_rate"] == 20.0


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


def test_weekday_proxy_midrank_never_invents_people_or_confidence():
    from datetime import timedelta

    today = date(2026, 9, 11)
    visits = [
        {
            "period_start": (today - timedelta(days=30 + offset)).isoformat(),
            "grain": "day",
            "subject_type": "area",
            "visitor_type": "all",
            "visitor_count": 100,
        }
        for offset in range(28)
    ]
    data, status, _ = build_forecast_view(
        {"area_code": "11", "visits": visits}, {"days": 30}, today=today
    )
    assert status == Availability.PARTIAL
    assert len(data["daily"]) == 30
    for row in data["daily"]:
        assert row["demand_score"] == 50
        assert row["method"] == "historical_weekday_proxy"
        assert row["expected_visitors"] is None
        assert row["confidence"] is None
        assert row["sample_count"] == 28
    assert "7일 이후" in data["daily"][7]["basis"]
    assert data["daily"][0]["weather"] is None
    VisitorForecastData.model_validate(data)


def test_weekday_proxy_requires_28_days_and_expires_after_60_days():
    from datetime import timedelta

    from app.products.forecast_views import historical_weekday_proxy

    today = date(2026, 9, 11)

    def visits(count, lag):
        return [
            {
                "period_start": (today - timedelta(days=lag + offset)).isoformat(),
                "grain": "day",
                "subject_type": "area",
                "visitor_type": "all",
                "visitor_count": offset,
            }
            for offset in range(count)
        ]

    assert historical_weekday_proxy(visits(27, 30), today, today) is None
    assert historical_weekday_proxy(visits(28, 60), today, today) is not None
    assert historical_weekday_proxy(visits(28, 61), today, today) is None


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
