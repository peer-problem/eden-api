from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.enums import Availability
from app.products.formulas import adjusted_forecast, adjusted_forecast_index

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
        grid_source = str(value.get("grid_source") or "area_center")
    else:
        return None
    assert value is not None
    return {
        "temperature_c": value.get("temperature_c"),
        "precipitation_probability_pct": value.get("precipitation_probability_pct"),
        "condition": value.get("condition"),
        "grid_source": grid_source,
        "nx": value.get("nx"),
        "ny": value.get("ny"),
        "availability": "available",
        "reason": None,
    }


def _base_forecast(rows: list[dict[str, Any]], place_name: str | None) -> dict[str, Any] | None:
    candidates = [
        row
        for row in rows
        if row.get("source_id") == "SRC_KTO_VISITOR_FORECAST"
        and isinstance(row.get("source_forecast"), dict)
        and (place_name is None or row["source_forecast"].get("place_name") == place_name)
    ]
    if not candidates:
        return None
    selected = min(
        candidates,
        key=lambda row: (
            str(row["source_forecast"].get("place_name") or ""),
            str(row.get("place_id") or ""),
            int(row.get("input_id") or 0),
        ),
    )
    return selected["source_forecast"]


def _weather_value(rows: list[dict[str, Any]], scope: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [row["weather"] for row in rows if isinstance(row.get("weather"), dict)]
    requested_nx = scope.get("nx")
    requested_ny = scope.get("ny")
    if requested_nx is not None and requested_ny is not None:
        return next(
            (
                value
                for value in candidates
                if value.get("nx") == requested_nx and value.get("ny") == requested_ny
            ),
            None,
        )
    return (
        min(
            candidates,
            key=lambda value: (
                1 if value.get("grid_source") == "parent_area" else 0,
                int(value.get("nx") or 0),
                int(value.get("ny") or 0),
                str(value.get("condition") or ""),
            ),
        )
        if candidates
        else None
    )


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
    any_holiday_evidence = False
    any_festival_evidence = False
    available_days = 0
    complete_days = 0
    source_ids: set[str] = set()
    for offset in range(days):
        target = today + timedelta(days=offset)
        rows = by_date.get(target, [])
        base = _base_forecast(rows, place_name)
        weather_value = _weather_value(rows, scope)
        weather = _weather_block(weather_value, scope) if "weather" in include else None
        festival_values = [row.get("festivals") for row in rows if row.get("festivals") is not None]
        festivals = (
            sorted(
                {
                    item.get("name")
                    for values in festival_values
                    for item in values
                    if item.get("name")
                }
            )
            if festival_values
            else None
        )
        holiday_values = [
            row.get("holiday") for row in rows if isinstance(row.get("holiday"), dict)
        ]
        is_holiday = any(item.get("is_holiday") for item in holiday_values)
        factors: dict[str, float] = {}
        if weather and weather.get("availability") == "available":
            condition = str(weather.get("condition") or "").casefold()
            factors["weather"] = (
                -0.1 if condition in {"rain", "rain_or_snow", "snow", "shower"} else 0.0
            )
            all_weather.append(weather)
        if festival_values and "festivals" in include:
            factors["festival"] = 0.1 if festivals else 0.0
            all_festivals.extend(festivals or [])
            any_festival_evidence = True
        if holiday_values and "holidays" in include:
            factors["holiday"] = 0.05 if is_holiday else 0.0
            any_holiday = any_holiday or is_holiday
            any_holiday_evidence = True

        concentration = base.get("concentration_rate") if base else None
        expected = base.get("expected_visitors") if base else None
        weather_factor = factors.get("weather")
        festival_factor = factors.get("festival")
        holiday_factor = factors.get("holiday")
        demand_score, demand_confidence = adjusted_forecast_index(
            float(concentration) if concentration is not None else None,
            weather_factor,
            festival_factor,
            holiday_factor,
        )
        adjusted_visitors, visitor_confidence = adjusted_forecast(
            int(expected) if expected is not None else None,
            weather_factor,
            festival_factor,
            holiday_factor,
        )
        confidence = (
            demand_confidence.value if demand_score is not None else visitor_confidence.value
        )
        has_base = concentration is not None or expected is not None
        if has_base:
            available_days += 1
            source_ids.add("SRC_KTO_VISITOR_FORECAST")
        missing_adjustments: list[str] = []
        if "weather" in include and (
            weather is None or weather.get("availability") != "available"
        ):
            missing_adjustments.append("weather")
        if "festivals" in include and not festival_values:
            missing_adjustments.append("festivals")
        if "holidays" in include and not holiday_values:
            missing_adjustments.append("holidays")
        if has_base and not missing_adjustments:
            complete_days += 1
        day_availability = (
            "available"
            if has_base and not missing_adjustments
            else "partial"
            if has_base
            else "unavailable"
        )
        day_reason = None
        if not has_base:
            day_reason = "해당 날짜의 권위적 원천 예측이 없습니다."
        elif missing_adjustments:
            day_reason = "요청한 일부 조정 원천이 없습니다: " + ", ".join(
                missing_adjustments
            )
        source_ids.update(str(row["source_id"]) for row in rows)
        daily.append(
            {
                "date": target.isoformat(),
                "source_concentration_rate": concentration,
                "demand_score": demand_score,
                "expected_visitors": adjusted_visitors,
                "confidence": confidence,
                "weather": weather,
                "festivals": festivals if "festivals" in include else None,
                "holiday": (
                    is_holiday if "holidays" in include and holiday_values else None
                ),
                "adjustment_factors": factors,
                "availability": day_availability,
                "reason": day_reason,
            }
        )
    availability = (
        Availability.AVAILABLE
        if complete_days == days
        else (Availability.PARTIAL if available_days else Availability.UNAVAILABLE)
    )
    if availability == Availability.AVAILABLE:
        reason = None
    elif available_days < days:
        reason = f"요청 {days}일 중 {available_days}일의 권위적 예측만 있습니다."
    else:
        reason = "일부 날짜에서 요청한 조정 원천이 없습니다."
    return (
        {
            "area_code": product["area_code"],
            "eden_area_id": product.get("eden_area_id"),
            "place_name": place_name,
            "horizon_days": days,
            "daily": daily,
            "weather": (all_weather or None) if "weather" in include else None,
            "festivals": (
                sorted(set(all_festivals)) if any_festival_evidence else None
            )
            if "festivals" in include
            else None,
            "holiday": (
                any_holiday if any_holiday_evidence else None
            )
            if "holidays" in include
            else None,
            "sources": sorted(source_ids),
        },
        availability,
        reason,
    )
