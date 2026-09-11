from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any

from app.domain.enums import Availability
from app.products.formulas import bounded_index

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


def _previous_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _visitor_totals(rows: list[dict[str, Any]]) -> dict[str, int | None]:
    grouped: dict[tuple[str, str, str, str], dict[str, list[int | None]]] = defaultdict(
        lambda: {"all": [], "domestic": [], "foreign": []}
    )
    for row in rows:
        visitor_type = str(row.get("visitor_type"))
        if visitor_type not in {"all", "domestic", "foreign"}:
            continue
        key = (
            str(row.get("period_start")),
            str(row.get("grain")),
            str(row.get("subject_type")),
            str(row.get("subject_key")),
        )
        grouped[key][visitor_type].append(
            int(row["visitor_count"]) if row.get("visitor_count") is not None else None
        )

    totals: dict[str, list[int | None]] = {
        "all": [],
        "domestic": [],
        "foreign": [],
    }
    for counts in grouped.values():
        scoped = {visitor_type: _sum(values) for visitor_type, values in counts.items()}
        domestic = scoped["domestic"]
        foreign = scoped["foreign"]
        reported_total = scoped["all"]
        if domestic is not None and foreign is not None:
            derived_total = domestic + foreign
            if reported_total is not None and reported_total != derived_total:
                raise ValueError("all must equal domestic + foreign at the same scope and grain")
            scoped["all"] = derived_total
        for visitor_type in totals:
            totals[visitor_type].append(scoped[visitor_type])
    return {visitor_type: _sum(values) for visitor_type, values in totals.items()}


def _project_visitor_totals(
    totals: dict[str, int | None],
    visitor_type: str,
) -> dict[str, int | None]:
    if visitor_type == "domestic":
        return {"all": totals["domestic"], "domestic": totals["domestic"], "foreign": None}
    if visitor_type == "foreign":
        return {"all": totals["foreign"], "domestic": None, "foreign": totals["foreign"]}
    return totals


def _window_rows(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    return [row for row in rows if start <= _day(str(row["period_start"])) <= end]


def _latest_month(rows):
    if not rows:
        return []
    latest = max(str(row["period_start"])[:7] for row in rows)
    return [row for row in rows if str(row["period_start"])[:7] == latest]


def build_region_insight_view(
    product: dict[str, Any], scope: dict[str, Any]
) -> tuple[dict[str, Any], Availability, str | None]:
    selected = set(scope.get("include") or ["visitors", "demand", "diversity"])
    period = str(scope.get("period", "30d"))
    days = PERIOD_DAYS[period]
    area_visit_rows = [
        row for row in product.get("visits", []) if row.get("subject_type") == "area"
    ]
    selected_collections = {
        "visitors": area_visit_rows,
        "demand": product.get("demand", []),
        "diversity": product.get("diversity", []),
    }
    all_dated_rows = [
        row
        for collection in selected
        for row in selected_collections[collection]
        if row.get("period_start")
    ]
    if not all_dated_rows:
        all_dated_rows = [
            row
            for collection in selected_collections.values()
            for row in collection
            if row.get("period_start")
        ]
    if not all_dated_rows:
        raise ValueError("regional product contains no dated observations")
    anchor_rows = area_visit_rows if "visitors" in selected and area_visit_rows else all_dated_rows
    end = max(_day(str(row["period_start"])) for row in anchor_rows)
    start = end - timedelta(days=days - 1)
    states: list[Availability] = []

    visitors = None
    comparison = None
    if "visitors" in selected:
        visit_rows = _window_rows(area_visit_rows, start, end)
        visitor_type = str(scope.get("visitor_type", "all"))
        totals = _project_visitor_totals(_visitor_totals(visit_rows), visitor_type)
        visitor_available = totals["all"] is not None
        observed_days = len(
            {
                _day(str(row["period_start"]))
                for row in visit_rows
                if row.get("visitor_count") is not None
            }
        )
        completeness = min(1.0, observed_days / days)
        visitor_state = (
            Availability.AVAILABLE
            if visitor_available and completeness == 1
            else Availability.PARTIAL
            if visitor_available
            else Availability.UNAVAILABLE
        )
        states.append(visitor_state)
        visitors = {
            "total": totals["all"],
            "domestic": totals["domestic"],
            "foreign": totals["foreign"],
            "change_rate": None,
            "availability": visitor_state.value,
            "completeness_ratio": completeness,
            "reason": (
                None
                if completeness == 1
                else "일부 날짜의 방문 관측이 없습니다."
                if visitor_available
                else "요청 기간의 방문 관측이 없습니다."
            ),
        }
        compare = scope.get("compare")
        if compare:
            if compare == "previous_period":
                baseline_end = start - timedelta(days=1)
                baseline_start = baseline_end - timedelta(days=days - 1)
            else:
                baseline_start = _previous_year(start)
                baseline_end = _previous_year(end)
            baseline_rows = _window_rows(area_visit_rows, baseline_start, baseline_end)
            baseline_totals = _visitor_totals(baseline_rows)
            baseline_days = len(
                {
                    _day(str(row["period_start"]))
                    for row in baseline_rows
                    if row.get("visitor_count") is not None
                }
            )
            change = _change_rate(
                totals["all"] if observed_days == days and baseline_days == days else None,
                _project_visitor_totals(baseline_totals, visitor_type)["all"],
            )
            visitors["change_rate"] = change
            comparison = {
                "type": compare,
                "baseline_start": baseline_start.isoformat(),
                "baseline_end": baseline_end.isoformat(),
                "change_rate": change,
            }

    demand = None
    if "demand" in selected:
        rows = _latest_month(product.get("demand", []))
        stay = [_number(row.get("stay_index")) for row in rows]
        spend = [_number(row.get("spend_index")) for row in rows]
        nights = [_number(row.get("avg_stay_nights")) for row in rows]
        stay_values = [value for value in stay if value is not None]
        spend_values = [value for value in spend if value is not None]
        night_values = [value for value in nights if value is not None]
        present_dimensions = sum(
            bool(values) for values in (stay_values, spend_values, night_values)
        )
        state = (
            Availability.AVAILABLE
            if present_dimensions == 3
            else Availability.PARTIAL
            if present_dimensions
            else Availability.UNAVAILABLE
        )
        states.append(state)
        demand = {
            "data_period": str(rows[0]["period_start"])[:7] if rows else None,
            "stay_index": bounded_index(mean(stay_values)) if stay_values else None,
            "spend_index": bounded_index(mean(spend_values)) if spend_values else None,
            "avg_stay_nights": round(mean(night_values), 3) if night_values else None,
            "availability": state.value,
            "reason": (
                None
                if state == Availability.AVAILABLE
                else "일부 수요 차원이 없습니다."
                if state == Availability.PARTIAL
                else "요청 기간의 수요 관측이 없습니다."
            ),
        }

    diversity = None
    if "diversity" in selected:
        rows = _latest_month(product.get("diversity", []))
        ages = [value for row in rows if (value := _number(row.get("age_index"))) is not None]
        nationalities = [
            value for row in rows if (value := _number(row.get("nationality_index"))) is not None
        ]
        has_any_dimension = bool(ages or nationalities)
        state = (
            Availability.AVAILABLE
            if ages and nationalities
            else Availability.PARTIAL
            if has_any_dimension
            else Availability.UNAVAILABLE
        )
        states.append(state)
        diversity = {
            "data_period": str(rows[0]["period_start"])[:7] if rows else None,
            "age_index": bounded_index(mean(ages)) if ages else None,
            "nationality_index": (bounded_index(mean(nationalities)) if nationalities else None),
            "availability": state.value,
            "reason": (
                None
                if state == Availability.AVAILABLE
                else (
                    "연령 다양성 원천이 없어 국적 다양성만 제공합니다."
                    if not ages
                    else "국적 다양성 원천이 없어 연령 다양성만 제공합니다."
                )
                if state == Availability.PARTIAL
                else "요청 기간의 다양성 관측이 없습니다."
            ),
        }

    available_count = sum(state == Availability.AVAILABLE for state in states)
    availability = (
        Availability.AVAILABLE
        if states and available_count == len(states)
        else Availability.PARTIAL
        if any(state != Availability.UNAVAILABLE for state in states)
        else Availability.UNAVAILABLE
    )
    reason = None if availability == Availability.AVAILABLE else "일부 지역 데이터 블록이 없습니다."
    sources = []
    if "visitors" in selected and area_visit_rows:
        sources.append("SRC_KTO_REGIONAL_VISITORS")
    if "demand" in selected and product.get("demand"):
        sources.append("SRC_KTO_DEMAND_INTENSITY")
    if "diversity" in selected and product.get("diversity"):
        sources.append("SRC_KTO_DIVERSITY")
    return (
        {
            "area": product["area"],
            "reference_information": product.get("reference_information"),
            "period": period,
            "basis_period": {"start": start.isoformat(), "end": end.isoformat()},
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


def _expected_buckets(start: date, end: date, granularity: str) -> list[date]:
    current = _bucket_start(start, granularity)
    buckets: list[date] = []
    while current <= end:
        buckets.append(current)
        if granularity == "day":
            current += timedelta(days=1)
        elif granularity == "week":
            current += timedelta(days=7)
        else:
            current = (
                current.replace(year=current.year + 1, month=1)
                if current.month == 12
                else current.replace(month=current.month + 1)
            )
    return buckets


def _source_grain(rows: list[dict[str, Any]], granularity: str) -> str | None:
    rank = {"day": 0, "week": 1, "month": 2}
    target_rank = rank[granularity]
    compatible = {
        str(row.get("grain"))
        for row in rows
        if str(row.get("grain")) in rank and rank[str(row.get("grain"))] <= target_rank
    }
    return min(compatible, key=rank.__getitem__) if compatible else None


def _bucket_completeness(
    rows: list[dict[str, Any]],
    source_grain: str,
    granularity: str,
    bucket: date,
    start: date,
    end: date,
) -> float:
    coverage_by_period: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        value = _number(row.get("completeness_ratio"))
        coverage_by_period[_day(str(row["period_start"]))].append(
            min(1.0, max(0.0, value if value is not None else 1.0))
        )
    period_coverage = [max(values) for values in coverage_by_period.values()]
    if source_grain == granularity:
        return round(mean(period_coverage), 6) if period_coverage else 0.0
    expected_days = _bucket_expected_days(bucket, granularity, start, end)
    return round(min(1.0, sum(period_coverage) / expected_days), 6)


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
    granularity = str(scope.get("granularity", "day"))
    source_grain = _source_grain(visitor_rows, granularity)
    if source_grain is None:
        return (
            None,
            Availability.UNAVAILABLE,
            "요청 grain보다 세밀한 방문 시계열 관측이 없습니다.",
        )
    visitor_rows = [row for row in visitor_rows if row.get("grain") == source_grain]
    end = max(_day(str(row["period_start"])) for row in visitor_rows)
    start = end - timedelta(days=days - 1)
    selected = _window_rows(visitor_rows, start, end)
    buckets: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        buckets[_bucket_start(_day(str(row["period_start"])), granularity)].append(row)

    series: list[dict[str, Any]] = []
    visitor_type = str(scope.get("visitor_type", "all"))
    for bucket in _expected_buckets(start, end, granularity):
        rows = buckets.get(bucket, [])
        totals = _project_visitor_totals(_visitor_totals(rows), visitor_type)
        concentrations = [
            value for row in rows if (value := _number(row.get("concentration_rate"))) is not None
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
                    bounded_index(mean(concentrations)) if concentrations else None
                ),
                "completeness_ratio": _bucket_completeness(
                    rows, source_grain, granularity, bucket, start, end
                ),
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
    availability = Availability.AVAILABLE if completeness == 1 else Availability.PARTIAL
    return (
        {
            "area": product["area"],
            "period": period,
            "basis_period": {"start": start.isoformat(), "end": end.isoformat()},
            "granularity": granularity,
            "visitor_type": visitor_type,
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
            "sources": ["SRC_TOURISM_ADMISSION" if attraction else "SRC_KTO_REGIONAL_VISITORS"],
        },
        availability,
        None if availability == Availability.AVAILABLE else "시계열의 일부 날짜가 없습니다.",
    )
