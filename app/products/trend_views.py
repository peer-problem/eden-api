from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from app.api.v1.schemas import normalize_keyword
from app.domain.enums import Availability
from app.products.formulas import (
    INTEREST_FORMULA_VERSION,
    RISING_KEYWORD_FORMULA_VERSION,
    bounded_index,
    interest_index,
    minmax_score,
)
from app.sources.social import REQUESTABLE_SOCIAL_SOURCES

PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}
SOURCE_NAMES = REQUESTABLE_SOCIAL_SOURCES
ALWAYS_INCLUDED_SOURCES = {"SRC_NAVER_TREND", "SRC_KTO_RESOURCE_DEMAND"}
RISING_MIN_OBSERVATIONS_PER_WINDOW = 2


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _sum_optional(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _single_source_sum(
    rows: list[dict[str, Any]],
    field: str,
    *,
    excluded_sources: set[str] | None = None,
) -> int | None:
    excluded = excluded_sources or set()
    contributing_sources = {
        str(row["source_id"])
        for row in rows
        if row.get("source_id") not in excluded and row.get(field) is not None
    }
    if len(contributing_sources) != 1:
        return None
    source_id = next(iter(contributing_sources))
    return _sum_optional(
        [
            int(row[field])
            for row in rows
            if row.get("source_id") == source_id and row.get(field) is not None
        ]
    )


def _group_time(value: datetime, grain: str) -> datetime:
    value = value.astimezone(UTC)
    if grain == "month":
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if grain == "week":
        value -= timedelta(days=value.weekday())
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _signal(rows: list[dict[str, Any]]) -> float | None:
    ratios = [float(row["search_ratio"]) for row in rows if row.get("search_ratio") is not None]
    if ratios:
        return mean(ratios)
    for field in ("view_count", "post_count", "reaction_count"):
        total = _sum_optional(
            [int(row[field]) if row.get(field) is not None else None for row in rows]
        )
        if total is not None:
            return float(total)
    scores = [float(row["source_score"]) for row in rows if row.get("source_score") is not None]
    return mean(scores) if scores else None


def _source_bucket_scores(
    rows: list[dict[str, Any]],
    time_unit: str,
) -> tuple[dict[tuple[str, datetime], float | None], dict[str, float | None]]:
    grouped: dict[tuple[str, datetime], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["source_id"], _group_time(_timestamp(row["bucket_start"]), time_unit))].append(
            row
        )
    signals = {key: _signal(values) for key, values in grouped.items()}
    bucket_scores: dict[tuple[str, datetime], float | None] = {}
    source_scores: dict[str, float | None] = {}
    for source_id in sorted({key[0] for key in grouped}):
        source_values = [
            value
            for (candidate_source, _bucket), value in signals.items()
            if candidate_source == source_id and value is not None
        ]
        pre_normalized = source_id in {
            "SRC_NAVER_TREND",
            "SRC_KTO_RESOURCE_DEMAND",
        }
        values_for_period: list[float] = []
        for key, value in signals.items():
            if key[0] != source_id:
                continue
            score = (
                round(value, 4)
                if pre_normalized and value is not None and 0 <= value <= 100
                else minmax_score(
                    value,
                    source_values,
                    f"{source_id.lower()}_time_minmax_v1",
                ).value
            )
            bucket_scores[key] = score
            if score is not None:
                values_for_period.append(score)
        source_scores[source_id] = round(mean(values_for_period), 4) if values_for_period else None
    return bucket_scores, source_scores


def _change_rate(series: list[dict[str, Any]]) -> float | None:
    scores = [float(row["interest_index"]) for row in series if row["interest_index"] is not None]
    if len(scores) < 2:
        return None
    split = len(scores) // 2
    previous = mean(scores[:split])
    current = mean(scores[split:])
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 4)


def _combined_score(scores: dict[str, float | None], selected_source_ids: set[str]) -> float | None:
    result = interest_index(
        {source_id: scores.get(source_id) for source_id in selected_source_ids},
        {source_id: 1.0 for source_id in selected_source_ids},
    )
    return result.value


def _rising_keywords(
    observations: list[dict[str, Any]],
    scope: dict[str, Any],
    selected_source_ids: set[str],
    latest: datetime,
) -> list[dict[str, Any]]:
    period_days = PERIOD_DAYS[scope["period"]]
    current_start = latest - timedelta(days=period_days - 1)
    previous_start = current_start - timedelta(days=period_days)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        if not isinstance(row, dict) or row.get("source_id") not in selected_source_ids:
            continue
        if scope.get("country") != "all" and row.get("country") != scope.get("country"):
            continue
        if scope.get("area_code") is not None and row.get("area_id") != scope.get("area_code"):
            continue
        keyword = str(row.get("keyword") or "").strip()
        if not keyword or keyword.casefold() == str(scope["keyword"]).casefold():
            continue
        bucket = _timestamp(row["bucket_start"])
        window = (
            "current"
            if current_start <= bucket <= latest
            else "previous"
            if previous_start <= bucket < current_start
            else "outside"
        )
        if window != "outside":
            grouped[(keyword, str(row["source_id"]), window)].append(row)

    keyword_scores: dict[str, list[float]] = defaultdict(list)
    keyword_sources = sorted({(keyword, source_id) for keyword, source_id, _ in grouped})
    for keyword, source_id in keyword_sources:
        current_rows = grouped.get((keyword, source_id, "current"), [])
        previous_rows = grouped.get((keyword, source_id, "previous"), [])
        if (
            len(current_rows) < RISING_MIN_OBSERVATIONS_PER_WINDOW
            or len(previous_rows) < RISING_MIN_OBSERVATIONS_PER_WINDOW
        ):
            continue
        current = _signal(current_rows)
        previous = _signal(previous_rows)
        if current is None or previous is None or previous <= 0:
            continue
        growth = (current - previous) / previous * 100
        if growth > 0:
            keyword_scores[keyword].append(bounded_index(growth) or 0.0)

    ranked = [
        {"keyword": keyword, "score": round(mean(scores), 4)}
        for keyword, scores in keyword_scores.items()
        if scores
    ]
    ranked.sort(key=lambda row: (-float(row["score"]), str(row["keyword"])))
    return ranked[: int(scope.get("limit", 10))]


def build_trend_view(
    product: dict[str, Any],
    scope: dict[str, Any],
) -> tuple[dict[str, Any] | None, Availability, str | None]:
    observations = product.get("observations")
    if not isinstance(observations, list):
        return None, Availability.UNAVAILABLE, "게시된 social signal 관측이 없습니다."
    requested_sources = set(scope.get("social_sources") or ["youtube"])
    selected_source_ids = {SOURCE_NAMES[name] for name in requested_sources if name in SOURCE_NAMES}
    keyword = normalize_keyword(str(scope["keyword"]))
    candidates = [
        row
        for row in observations
        if isinstance(row, dict)
        and normalize_keyword(str(row.get("keyword", ""))).casefold() == keyword.casefold()
        and row.get("source_id") in selected_source_ids
        and (scope.get("country") == "all" or row.get("country") == scope.get("country"))
        and (scope.get("area_code") is None or row.get("area_id") == scope.get("area_code"))
    ]
    if not candidates:
        return None, Availability.UNAVAILABLE, "요청 범위와 일치하는 social signal이 없습니다."
    latest = max(_timestamp(row["bucket_start"]) for row in candidates)
    cutoff = latest - timedelta(days=PERIOD_DAYS[scope["period"]] - 1)
    rows = [row for row in candidates if _timestamp(row["bucket_start"]) >= cutoff]
    if not rows:
        return None, Availability.UNAVAILABLE, "요청 기간의 social signal이 없습니다."

    bucket_scores, source_scores = _source_bucket_scores(rows, scope["time_unit"])
    grouped_time: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    grouped_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_time[_group_time(_timestamp(row["bucket_start"]), scope["time_unit"])].append(row)
        grouped_source[row["source_id"]].append(row)

    series: list[dict[str, Any]] = []
    for bucket, bucket_rows in sorted(grouped_time.items()):
        scores = {
            source_id: bucket_scores.get((source_id, bucket)) for source_id in selected_source_ids
        }
        series.append(
            {
                "timestamp": bucket,
                "search_ratio": (
                    round(mean(ratios), 4)
                    if (
                        ratios := [
                            float(row["search_ratio"])
                            for row in bucket_rows
                            if row.get("search_ratio") is not None
                        ]
                    )
                    else None
                ),
                "youtube_views": _sum_optional(
                    [
                        int(row["view_count"])
                        if row.get("source_id") == "SRC_YOUTUBE"
                        and row.get("view_count") is not None
                        else None
                        for row in bucket_rows
                    ]
                ),
                "sns_mentions": _single_source_sum(
                    bucket_rows,
                    "post_count",
                    excluded_sources={
                        "SRC_YOUTUBE",
                        "SRC_NAVER_TREND",
                        "SRC_KTO_RESOURCE_DEMAND",
                    },
                ),
                "destination_searches": None,
                "interest_index": _combined_score(scores, selected_source_ids),
            }
        )

    source_metrics: list[dict[str, Any]] = []
    source_availability: dict[str, dict[str, str | None]] = {}
    for source_id in sorted(selected_source_ids):
        source_rows = grouped_source.get(source_id, [])
        if not source_rows:
            source_availability[source_id] = {
                "availability": "unavailable",
                "reason": "요청 키워드 및 범위의 관측이 없습니다.",
            }
            continue
        score = source_scores.get(source_id)
        source_reason = (
            "최근 공개 동영상 최대 20건의 표본이며 지역 필터는 시청자 거주 국가가 "
            "아닌 재생 가능 지역입니다."
            if source_id == "SRC_YOUTUBE"
            else None
            if score is not None
            else "정규화 모집단이 부족합니다."
        )
        source_metrics.append(
            {
                "source_id": source_id,
                "observed_at": max(_timestamp(row["bucket_start"]) for row in source_rows),
                "posts": _sum_optional([row.get("post_count") for row in source_rows]),
                "views": _sum_optional([row.get("view_count") for row in source_rows]),
                "reactions": _sum_optional([row.get("reaction_count") for row in source_rows]),
                "search_ratio": (
                    round(mean(ratios), 4)
                    if (
                        ratios := [
                            float(row["search_ratio"])
                            for row in source_rows
                            if row.get("search_ratio") is not None
                        ]
                    )
                    else None
                ),
                "score": score,
                "availability": "available" if score is not None else "partial",
                "reason": source_reason,
            }
        )
        source_availability[source_id] = {
            "availability": "available" if score is not None else "partial",
            "reason": source_reason,
        }

    period_score = _combined_score(source_scores, selected_source_ids)
    availability = (
        Availability.AVAILABLE
        if selected_source_ids == set(grouped_source) and period_score is not None
        else Availability.PARTIAL
    )
    data = {
        "keyword": keyword,
        "area_code": scope.get("area_code"),
        "country": scope.get("country", "all"),
        "period": scope["period"],
        "time_unit": scope["time_unit"],
        "interest_index": period_score,
        "change_rate": _change_rate(series),
        "source_metrics": source_metrics,
        "source_availability": source_availability,
        "series": series,
        "rising_keywords": _rising_keywords(
            observations,
            scope,
            selected_source_ids,
            latest,
        ),
        "sources": sorted(grouped_source),
    }
    return (
        data,
        availability,
        None
        if availability == Availability.AVAILABLE
        else "일부 social signal 원천 또는 정규화 점수를 제공할 수 없습니다.",
    )


__all__ = [
    "INTEREST_FORMULA_VERSION",
    "RISING_KEYWORD_FORMULA_VERSION",
    "build_trend_view",
]
