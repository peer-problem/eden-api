from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any

from app.domain.enums import Availability

PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "12m": 365}


def _day(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _sum(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _change_rate(current: int | None, baseline: int | None) -> float | None:
    if current is None or baseline in (None, 0):
        return None
    return round((current - baseline) / baseline * 100, 4)


def _visitor_totals(rows: list[dict[str, Any]]) -> dict[str, int | None]:
    values = {
        visitor_type: _sum(
            [
                int(row["visitor_count"]) if row.get("visitor_count") is not None else None
                for row in rows
                if row.get("visitor_type") == visitor_type
            ]
        )
        for visitor_type in ("all", "domestic", "foreign")
    }
    if values["domestic"] is not None and values["foreign"] is not None:
        values["all"] = values["domestic"] + values["foreign"]
    return values


def _window_rows(
    rows: list[dict[str, Any]], start: date, end: date
) -> list[dict[str, Any]]:
    return [row for row in rows if start <= _day(str(row["period_start"])) <= end]


def build_region_insight_view(
    product: dict[str, Any], scope: dict[str, Any]
) -> tuple[dict[str, Any], Availability, str | None]:
    selected = set(scope.get("include") or ["visitors", "demand", "diversity"])
    period = str(scope.get("period", "30d"))
    days = PERIOD_DAYS[period]
    all_dated_rows = [
        row
        for collection in ("visits", "demand", "diversity")
        for row in product.get(collection, [])
        if row.get("period_start")
    ]
    if not all_dated_rows:
        raise ValueError("regional product contains no dated observations")
    end = max(_day(str(row["period_start"])) for row in all_dated_rows)
    start = end - timedelta(days=days - 1)
    states: list[Availability] = []

    visitors = None
    comparison = None
    if "visitors" in selected:
        visit_rows = _window_rows(product.get("visits", []), start, end)
        totals = _visitor_totals(visit_rows)
        visitor_available = any(value is not None for value in totals.values())
        visitor_state = (
            Availability.AVAILABLE if visitor_available else Availability.UNAVAILABLE
        )
        states.append(visitor_state)
        visitors = {
            "total": totals["all"],
            "domestic": totals["domestic"],
            "foreign": totals["foreign"],
            "change_rate": None,
            "availability": visitor_state.value,
            "reason": None if visitor_available else "요청 기간의 방문 관측이 없습니다.",
        }
        compare = scope.get("compare")
        if compare:
            if compare == "previous_period":
                baseline_end = start - timedelta(days=1)
            else:
                baseline_end = end - timedelta(days=365)
            baseline_start = baseline_end - timedelta(days=days - 1)
            baseline_rows = _window_rows(
                product.get("visits", []), baseline_start, baseline_end
            )
            baseline_totals = _visitor_totals(baseline_rows)
            visitor_type = str(scope.get("visitor_type", "all"))
            change = _change_rate(totals[visitor_type], baseline_totals[visitor_type])
            visitors["change_rate"] = change
            comparison = {
                "type": compare,
                "baseline_start": baseline_start.isoformat(),
                "baseline_end": baseline_end.isoformat(),
                "change_rate": change,
            }

    demand = None
    if "demand" in selected:
        rows = _window_rows(product.get("demand", []), start, end)
        stay = [_number(row.get("stay_index")) for row in rows]
        spend = [_number(row.get("spend_index")) for row in rows]
        nights = [_number(row.get("avg_stay_nights")) for row in rows]
        stay_values = [value for value in stay if value is not None]
        spend_values = [value for value in spend if value is not None]
        night_values = [value for value in nights if value is not None]
        block_available = bool(stay_values or spend_values or night_values)
        state = Availability.AVAILABLE if block_available else Availability.UNAVAILABLE
        states.append(state)
        demand = {
            "stay_index": round(mean(stay_values), 4) if stay_values else None,
            "spend_index": round(mean(spend_values), 4) if spend_values else None,
            "avg_stay_nights": round(mean(night_values), 3) if night_values else None,
            "availability": state.value,
            "reason": None if block_available else "요청 기간의 수요 관측이 없습니다.",
        }

    diversity = None
    if "diversity" in selected:
        rows = _window_rows(product.get("diversity", []), start, end)
        ages = [value for row in rows if (value := _number(row.get("age_index"))) is not None]
        nationalities = [
            value
            for row in rows
            if (value := _number(row.get("nationality_index"))) is not None
        ]
        block_available = bool(ages or nationalities)
        state = Availability.AVAILABLE if block_available else Availability.UNAVAILABLE
        states.append(state)
        diversity = {
            "age_index": round(mean(ages), 4) if ages else None,
            "nationality_index": round(mean(nationalities), 4) if nationalities else None,
            "availability": state.value,
            "reason": None if block_available else "요청 기간의 다양성 관측이 없습니다.",
        }

    available_count = sum(state == Availability.AVAILABLE for state in states)
    availability = (
        Availability.AVAILABLE
        if states and available_count == len(states)
        else Availability.PARTIAL
    )
    reason = None if availability == Availability.AVAILABLE else "일부 지역 데이터 블록이 없습니다."
    sources = []
    if product.get("visits"):
        sources.append("SRC_KTO_REGIONAL_VISITORS")
    if product.get("demand"):
        sources.append("SRC_KTO_DEMAND_INTENSITY")
    if product.get("diversity"):
        sources.append("SRC_KTO_DIVERSITY")
    return (
        {
            "area": product["area"],
            "period": period,
            "visitors": visitors,
            "demand": demand,
            "diversity": diversity,
            "comparison": comparison,
            "sources": sources,
        },
        availability,
        reason,
    )


def _bucket_start(value: date, granularity: str) -> date:
    if granularity == "day":
        return value
    if granularity == "week":
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def _bucket_expected_days(bucket: date, granularity: str, start: date, end: date) -> int:
    if granularity == "day":
        bucket_end = bucket
    elif granularity == "week":
        bucket_end = bucket + timedelta(days=6)
    else:
        bucket_end = bucket.replace(day=calendar.monthrange(bucket.year, bucket.month)[1])
    return max(1, (min(bucket_end, end) - max(bucket, start)).days + 1)


def build_visitor_timeseries_view(
    product: dict[str, Any], scope: dict[str, Any]
) -> tuple[dict[str, Any] | None, Availability, str | None]:
    period = str(scope.get("period", "30d"))
    days = PERIOD_DAYS[period]
    visitor_rows = list(product.get("visits", []))
    attraction = scope.get("attraction_name")
    if attraction:
        visitor_rows = [
            row
            for row in visitor_rows
            if row.get("subject_type") == "attraction" and row.get("subject_key") == attraction
        ]
    else:
        visitor_rows = [row for row in visitor_rows if row.get("subject_type") == "area"]
    if not visitor_rows:
        return None, Availability.UNAVAILABLE, "요청 범위의 방문 시계열 관측이 없습니다."
    end = max(_day(str(row["period_start"])) for row in visitor_rows)
    start = end - timedelta(days=days - 1)
    selected = _window_rows(visitor_rows, start, end)
    granularity = str(scope.get("granularity", "day"))
    buckets: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        buckets[_bucket_start(_day(str(row["period_start"])), granularity)].append(row)

    series: list[dict[str, Any]] = []
    for bucket, rows in sorted(buckets.items()):
        totals = _visitor_totals(rows)
        observed_days = len({_day(str(row["period_start"])) for row in rows})
        expected_days = _bucket_expected_days(bucket, granularity, start, end)
        concentrations = [
            value
            for row in rows
            if (value := _number(row.get("concentration_rate"))) is not None
        ]
        series.append(
            {
                "period_start": bucket.isoformat(),
                "grain": granularity,
                "subject_type": "attraction" if attraction else "area",
                "total": totals["all"],
                "domestic": totals["domestic"],
                "foreign": totals["foreign"],
                "concentration_rate": (
                    round(mean(concentrations), 4) if concentrations else None
                ),
                "completeness_ratio": round(min(1, observed_days / expected_days), 6),
            }
        )
    if not series:
        return None, Availability.UNAVAILABLE, "요청 기간의 방문 시계열 관측이 없습니다."
    total = _sum([point["total"] for point in series])
    domestic = _sum([point["domestic"] for point in series])
    foreign = _sum([point["foreign"] for point in series])
    concentration_values = [
        float(point["concentration_rate"])
        for point in series
        if point["concentration_rate"] is not None
    ]
    completeness = round(mean(point["completeness_ratio"] for point in series), 6)
    availability = (
        Availability.AVAILABLE if completeness == 1 else Availability.PARTIAL
    )
    return (
        {
            "area": product["area"],
            "period": period,
            "granularity": granularity,
            "visitor_type": scope.get("visitor_type", "all"),
            "attraction_name": attraction,
            "summary": {
                "total": total,
                "domestic": domestic,
                "foreign": foreign,
                "peak_visitors": max(
                    (point["total"] for point in series if point["total"] is not None),
                    default=None,
                ),
                "peak_concentration_rate": max(concentration_values, default=None),
                "completeness_ratio": completeness,
            },
            "series": series,
            "sources": ["SRC_KTO_REGIONAL_VISITORS"],
        },
        availability,
        None if availability == Availability.AVAILABLE else "시계열의 일부 날짜가 없습니다.",
    )
