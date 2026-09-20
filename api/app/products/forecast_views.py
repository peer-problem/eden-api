from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean
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
    official = [
        row
        for row in rows
        if row.get("source_id") == "SRC_KTO_VISITOR_FORECAST"
        and isinstance(row.get("source_forecast"), dict)
    ]
    if place_name is not None:
        candidates = [
            row for row in official if row["source_forecast"].get("place_name") == place_name
        ]
    else:
        candidates = [
            row
            for row in official
            if not row.get("place_id") and not row["source_forecast"].get("place_name")
        ]
        if not candidates:
            # The official forecast is published per attraction. An area request
            # gets the mean of its attractions' rates and says how many were
            # averaged, never one arbitrary attraction's value.
            rates = [
                float(row["source_forecast"]["concentration_rate"])
                for row in official
                if row["source_forecast"].get("concentration_rate") is not None
            ]
            if not rates:
                return None
            return {
                "place_name": None,
                "concentration_rate": round(mean(rates), 4),
                "expected_visitors": None,
                "sample_count": len(rates),
                "basis": f"지역 내 관광지 {len(rates)}곳의 공식 집중률 평균",
            }
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
    days = int(scope.get("days", 7))
    include = set(scope.get("include") or ["weather", "festivals", "holidays"])
    place_name = scope.get("place_name")
    by_date: dict[date, list[dict[str, Any]]] = {}
    for row in product.get("inputs", []):
        by_date.setdefault(_date(str(row["forecast_date"])), []).append(row)
    coverage = product.get("reference_coverage") or {}

    def covered(source_id: str, target: date) -> bool:
        window = coverage.get(source_id) if isinstance(coverage, dict) else None
        if not isinstance(window, dict):
            return False
        try:
            return (
                date.fromisoformat(str(window.get("from", target.isoformat())))
                <= target
                <= date.fromisoformat(str(window["through"]))
            )
        except (KeyError, TypeError, ValueError):
            return False

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
        festival_known = bool(festival_values) or covered("SRC_FESTIVAL", target)
        festivals = (
            sorted(
                {
                    item.get("name")
                    for values in festival_values
                    for item in values
                    if item.get("name")
                }
            )
            if festival_known
            else None
        )
        holiday_values = [
            row.get("holiday") for row in rows if isinstance(row.get("holiday"), dict)
        ]
        holiday_known = bool(holiday_values) or covered("SRC_HOLIDAY", target)
        is_holiday = any(item.get("is_holiday") for item in holiday_values)
        factors: dict[str, float] = {}
        if weather and weather.get("availability") == "available":
            all_weather.append(weather)
        if festival_known and "festivals" in include:
            all_festivals.extend(festivals or [])
            any_festival_evidence = True
        if holiday_known and "holidays" in include:
            any_holiday = any_holiday or is_holiday
            any_holiday_evidence = True
        concentration = base.get("concentration_rate") if base else None
        expected = base.get("expected_visitors") if base else None
        has_official = concentration is not None or expected is not None
        demand_score = concentration
        adjusted_visitors = expected
        confidence = None
        has_base = has_official
        if has_base:
            available_days += 1
            source_ids.add("SRC_KTO_VISITOR_FORECAST")
        missing_adjustments: list[str] = []
        if "weather" in include and (weather is None or weather.get("availability") != "available"):
            missing_adjustments.append("weather")
        if "festivals" in include and not festival_known:
            missing_adjustments.append("festivals")
        if "holidays" in include and not holiday_known:
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
            day_reason = "요청 날짜와 범위의 공식 방문 전망이 없습니다."
        elif missing_adjustments:
            day_reason = "일부 참고 정보가 없습니다: " + ", ".join(missing_adjustments)
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
                "holiday": (is_holiday if "holidays" in include and holiday_known else None),
                "adjustment_factors": factors,
                "method": "official" if has_official else None,
                "basis_period": None,
                "sample_count": base.get("sample_count") if has_official and base else None,
                "basis": base.get("basis") if has_official and base else None,
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
        reason = f"요청 {days}일 중 {available_days}일의 전망만 있습니다."
    else:
        reason = "일부 날짜에서 요청한 참고 원천이 없습니다."
    return (
        {
            "area_code": product["area_code"],
            "requested_area_code": scope.get("requested_area_code"),
            "data_area_code": product["area_code"],
            "spatial_resolution": product.get("spatial_resolution"),
            "eden_area_id": product.get("eden_area_id"),
            "place_name": place_name,
            "horizon_days": days,
            "daily": daily,
            "weather": (all_weather or None) if "weather" in include else None,
            "festivals": (sorted(set(all_festivals)) if any_festival_evidence else None)
            if "festivals" in include
            else None,
            "holiday": (any_holiday if any_holiday_evidence else None)
            if "holidays" in include
            else None,
            "sources": sorted(source_ids),
        },
        availability,
        reason,
    )
