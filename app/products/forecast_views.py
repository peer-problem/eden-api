from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.enums import Availability

SEOUL = ZoneInfo("Asia/Seoul")


def _date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _weather_block(
    value: dict[str, Any] | None,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    requested_nx = scope.get("nx")
    requested_ny = scope.get("ny")
    if requested_nx is not None and requested_ny is not None:
        if not value or value.get("nx") != requested_nx or value.get("ny") != requested_ny:
            return {
                "temperature_c": None,
                "precipitation_probability_pct": None,
                "condition": None,
                "grid_source": "request",
                "nx": requested_nx,
                "ny": requested_ny,
                "availability": "unavailable",
                "reason": "요청 격자의 사전 수집된 기상 데이터가 없습니다.",
            }
        grid_source = "request"
    elif value:
        grid_source = "area_center"
    else:
        return None
    assert value is not None
    return {
        "temperature_c": value.get("temperature_c"),
        "precipitation_probability_pct": value.get(
            "precipitation_probability_pct"
        ),
        "condition": value.get("condition"),
        "grid_source": grid_source,
        "nx": value.get("nx"),
        "ny": value.get("ny"),
        "availability": "available",
        "reason": None,
    }


def build_forecast_view(
    product: dict[str, Any],
    scope: dict[str, Any],
    *,
    today: date | None = None,
) -> tuple[dict[str, Any], Availability, str | None]:
    today = today or datetime.now(SEOUL).date()
    days = int(scope.get("days", 14))
    include = set(scope.get("include") or ["weather", "festivals", "holidays"])
    place_name = scope.get("place_name")
    by_date: dict[date, list[dict[str, Any]]] = {}
    for row in product.get("inputs", []):
        by_date.setdefault(_date(str(row["forecast_date"])), []).append(row)

    daily: list[dict[str, Any]] = []
    all_weather: list[dict[str, Any]] = []
    all_festivals: list[str] = []
    any_holiday = False
    available_days = 0
    source_ids: set[str] = set()
    for offset in range(days):
        target = today + timedelta(days=offset)
        rows = by_date.get(target, [])
        base_rows = [
            row
            for row in rows
            if row.get("source_id") == "SRC_KTO_VISITOR_FORECAST"
            and (
                place_name is None
                or (row.get("source_forecast") or {}).get("place_name") == place_name
            )
        ]
        base = base_rows[0].get("source_forecast") if base_rows else None
        weather_value = next(
            (row.get("weather") for row in rows if row.get("weather")), None
        )
        weather = _weather_block(weather_value, scope) if "weather" in include else None
        festivals = [
            item.get("name")
            for row in rows
            for item in (row.get("festivals") or [])
            if item.get("name")
        ]
        holiday_values = [row.get("holiday") for row in rows if row.get("holiday")]
        is_holiday = any(item.get("is_holiday") for item in holiday_values)
        factors: dict[str, float] = {}
        if weather and weather.get("availability") == "available":
            condition = weather.get("condition")
            factors["weather"] = -0.1 if condition in {"rain", "snow", "shower"} else 0.0
            all_weather.append(weather)
        if festivals and "festivals" in include:
            factors["festival"] = 0.1
            all_festivals.extend(festivals)
        if is_holiday and "holidays" in include:
            factors["holiday"] = 0.05
            any_holiday = True
        has_base = bool(base)
        if has_base:
            available_days += 1
            source_ids.add("SRC_KTO_VISITOR_FORECAST")
        source_ids.update(str(row["source_id"]) for row in rows)
        daily.append(
            {
                "date": target.isoformat(),
                "source_concentration_rate": (
                    base.get("concentration_rate") if base else None
                ),
                "demand_score": None,
                "expected_visitors": base.get("expected_visitors") if base else None,
                "confidence": None,
                "weather": weather,
                "festivals": festivals if "festivals" in include else None,
                "holiday": is_holiday if "holidays" in include else None,
                "adjustment_factors": factors,
                "availability": "available" if has_base else "unavailable",
                "reason": None if has_base else "해당 날짜의 권위적 원천 예측이 없습니다.",
            }
        )
    availability = (
        Availability.AVAILABLE
        if available_days == days
        else (Availability.PARTIAL if available_days else Availability.UNAVAILABLE)
    )
    reason = (
        None
        if availability == Availability.AVAILABLE
        else f"요청 {days}일 중 {available_days}일의 권위적 예측만 있습니다."
    )
    return (
        {
            "area_code": product["area_code"],
            "place_name": place_name,
            "horizon_days": days,
            "daily": daily,
            "weather": all_weather if "weather" in include else None,
            "festivals": sorted(set(all_festivals)) if "festivals" in include else None,
            "holiday": any_holiday if "holidays" in include else None,
            "sources": sorted(source_ids),
        },
        availability,
        reason,
    )
