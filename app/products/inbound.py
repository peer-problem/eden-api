from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.products.formulas import INBOUND_FORMULA_VERSION, inbound_score, minmax_score
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.keys import lookup_key
from app.repositories.models import (
    Country,
    FlightObservation,
    FxObservation,
    InboundVisitorObservation,
    ProvenanceEdge,
    SocialObservation,
    TourismBalanceObservation,
)
from app.sources.kto_inbound import SOURCE_ID
from app.sources.social import REQUESTABLE_SOCIAL_SOURCES

PERIOD_MONTHS = {"3m": 3, "6m": 6, "12m": 12, "24m": 24}
INBOUND_HISTORY_MONTHS = max(PERIOD_MONTHS.values()) * 2
SOCIAL_HISTORY_MONTHS = max(PERIOD_MONTHS.values())
MAX_AGE_SECONDS = 38 * 24 * 3600
SOCIAL_SOURCE_NAMES = {
    source_id: source_name for source_name, source_id in REQUESTABLE_SOCIAL_SOURCES.items()
}
COUNTRY_PROXY_FLAGS = frozenset({"query_market_proxy", "query_language_market_proxy"})


@dataclass(frozen=True, slots=True)
class InboundProductResult:
    published_count: int
    countries: tuple[str, ...]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _month_ordinal(value: datetime) -> int:
    return value.year * 12 + value.month - 1


def _month_start(ordinal: int) -> datetime:
    return datetime(ordinal // 12, ordinal % 12 + 1, 1)


def _history_windows(column: object, end_ordinals: set[int], months: int) -> object:
    return or_(
        *(
            and_(
                column >= _month_start(end_ordinal - months + 1),
                column < _month_start(end_ordinal + 1),
            )
            for end_ordinal in sorted(end_ordinals)
        )
    )


def _window(
    rows: list[InboundVisitorObservation],
    end_ordinal: int,
    months: int,
) -> list[InboundVisitorObservation]:
    start_ordinal = end_ordinal - months + 1
    return [row for row in rows if start_ordinal <= _month_ordinal(row.period_start) <= end_ordinal]


def _change_rate(current: int | None, previous: int | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return round((current - previous) / previous * 100, 4)


def build_inbound_snapshots(
    session_factory: sessionmaker[Session],
) -> InboundProductResult:
    with session_factory() as session:
        countries = session.execute(
            select(
                Country.eden_country_id,
                Country.iso_alpha2,
                func.max(InboundVisitorObservation.period_start),
            )
            .join(
                InboundVisitorObservation,
                InboundVisitorObservation.country_id == Country.eden_country_id,
            )
            .where(InboundVisitorObservation.source_id == SOURCE_ID)
            .group_by(Country.eden_country_id, Country.iso_alpha2)
            .order_by(Country.iso_alpha2)
        ).all()

    if not countries:
        return InboundProductResult(0, ())
    end_ordinals = {_month_ordinal(latest_period) for _, _, latest_period in countries}
    with session_factory() as session:
        all_visitor_rows = list(
            session.scalars(
                select(InboundVisitorObservation)
                .where(
                    InboundVisitorObservation.source_id == SOURCE_ID,
                    _history_windows(
                        InboundVisitorObservation.period_start,
                        end_ordinals,
                        INBOUND_HISTORY_MONTHS,
                    ),
                )
                .order_by(InboundVisitorObservation.period_start)
            ).all()
        )
        all_flight_rows = list(
            session.scalars(
                select(FlightObservation)
                .where(
                    FlightObservation.grain == "month",
                    _history_windows(
                        FlightObservation.period_start,
                        end_ordinals,
                        INBOUND_HISTORY_MONTHS,
                    ),
                )
                .order_by(FlightObservation.period_start)
            ).all()
        )
        ranked_fx = (
            select(
                FxObservation.observation_id.label("observation_id"),
                func.row_number()
                .over(
                    partition_by=FxObservation.currency,
                    order_by=(
                        FxObservation.rate_date.desc(),
                        FxObservation.observation_id.desc(),
                    ),
                )
                .label("currency_rank"),
            )
            .subquery()
        )
        all_fx_rows = list(
            session.scalars(
                select(FxObservation)
                .join(
                    ranked_fx,
                    ranked_fx.c.observation_id == FxObservation.observation_id,
                )
                .where(ranked_fx.c.currency_rank <= 2)
                .order_by(
                    FxObservation.currency,
                    FxObservation.rate_date.desc(),
                    FxObservation.observation_id.desc(),
                )
            ).all()
        )
        all_social_rows = list(
            session.scalars(
                select(SocialObservation)
                .where(
                    SocialObservation.source_id.in_(SOCIAL_SOURCE_NAMES),
                    _history_windows(
                        SocialObservation.bucket_start,
                        end_ordinals,
                        SOCIAL_HISTORY_MONTHS,
                    ),
                )
                .order_by(SocialObservation.bucket_start)
            ).all()
        )

    visitor_rows_by_country: dict[str, list[InboundVisitorObservation]] = {}
    for row in all_visitor_rows:
        visitor_rows_by_country.setdefault(row.country_id, []).append(row)

    fx_history: dict[str, list[FxObservation]] = {}
    for row in all_fx_rows:
        fx_history.setdefault(row.currency, [])
        if len(fx_history[row.currency]) < 2:
            fx_history[row.currency].append(row)
    fx_change_population = [
        change
        for rows_by_currency in fx_history.values()
        if len(rows_by_currency) > 1
        and (
            change := _change_rate_decimal(
                float(rows_by_currency[0].krw_rate),
                float(rows_by_currency[1].krw_rate),
            )
        )
        is not None
    ]

    publisher = SnapshotPublisher(session_factory)
    published_count = 0
    published_countries: list[str] = []
    for country_id, country_iso, latest_period in countries:
        with session_factory() as session:
            country = session.get(Country, country_id)
            if country is None:
                continue
            schedule = session.scalar(
                select(FlightObservation)
                .where(
                    FlightObservation.country_id == country_id,
                    FlightObservation.grain == "7d_schedule",
                )
                .order_by(FlightObservation.period_start.desc())
                .limit(1)
            )
            fx_rows = fx_history.get(country.default_currency, [])
            balance = session.scalar(
                select(TourismBalanceObservation)
                .order_by(TourismBalanceObservation.period_start.desc())
                .limit(1)
            )
        rows = visitor_rows_by_country.get(country_id, [])
        flight_rows = [row for row in all_flight_rows if row.country_id == country_id]
        if not rows:
            continue
        published_countries.append(country_iso)
        end_ordinal = _month_ordinal(latest_period)
        for period, month_count in PERIOD_MONTHS.items():
            current_rows = _window(rows, end_ordinal, month_count)
            previous_rows = _window(rows, end_ordinal - month_count, month_count)
            current_flights = _window_flights(flight_rows, end_ordinal, month_count)
            previous_flights = _window_flights(flight_rows, end_ordinal - month_count, month_count)
            social_population_rows = _window_social(all_social_rows, end_ordinal, month_count)
            social_rows = [row for row in social_population_rows if row.country_id == country_id]
            current_total = _sum_optional([row.visitor_count for row in current_rows])
            previous_total = _sum_optional([row.visitor_count for row in previous_rows])
            arriving_flights = _sum_optional([row.arriving_flights for row in current_flights])
            previous_arriving_flights = _sum_optional(
                [row.arriving_flights for row in previous_flights]
            )
            available_month_count = len(
                {
                    _month_ordinal(row.period_start)
                    for row in current_rows
                    if row.visitor_count is not None
                }
            )
            completeness = available_month_count / month_count
            availability = (
                Availability.AVAILABLE
                if completeness == 1
                else Availability.PARTIAL
                if completeness > 0
                else Availability.UNAVAILABLE
            )
            reason = None
            quality_flags: tuple[str, ...] = ()
            if completeness < 1:
                reason = (
                    f"요청 {month_count}개월 중 {available_month_count}개월의 공식 통계만 있습니다."
                )
                quality_flags = ("incomplete_period",)
            input_rows = [
                *current_rows,
                *previous_rows,
                *current_flights,
                *previous_flights,
                *([schedule] if schedule else []),
                *(row for history in fx_history.values() for row in history),
                *([balance] if balance else []),
                *social_population_rows,
            ]
            raw_record_ids = _raw_record_ids(
                session_factory,
                {
                    "inbound_visitor_observation": [
                        row.observation_id for row in [*current_rows, *previous_rows]
                    ],
                    "flight_observation": [
                        row.observation_id
                        for row in [
                            *current_flights,
                            *previous_flights,
                            *([schedule] if schedule else []),
                        ]
                    ],
                    "fx_observation": [
                        row.observation_id for history in fx_history.values() for row in history
                    ],
                    "tourism_balance_observation": ([balance.observation_id] if balance else []),
                    "social_observation": [row.observation_id for row in social_population_rows],
                },
            )
            visitor_population = _component_population(
                all_visitor_rows, end_ordinal, month_count, "visitor_count"
            )
            flight_population = _component_population(
                all_flight_rows, end_ordinal, month_count, "arriving_flights"
            )
            visitor_component = minmax_score(
                float(current_total) if current_total is not None else None,
                visitor_population,
                "inbound_visitors_minmax_v1",
            )
            flight_component = minmax_score(
                float(arriving_flights) if arriving_flights is not None else None,
                flight_population,
                "inbound_flights_minmax_v1",
            )
            social_interest, social_component = _social_interest(
                social_rows,
                _social_source_scores(social_population_rows, country_id),
            )
            fx = fx_rows[0] if fx_rows else None
            previous_fx = fx_rows[1] if len(fx_rows) > 1 else None
            fx_change = _change_rate_decimal(
                float(fx.krw_rate) if fx and fx.krw_rate is not None else None,
                (
                    float(previous_fx.krw_rate)
                    if previous_fx and previous_fx.krw_rate is not None
                    else None
                ),
            )
            fx_component = minmax_score(
                fx_change,
                fx_change_population,
                "inbound_fx_change_minmax_v1",
            )
            score = inbound_score(
                visitor_component.value,
                flight_component.value,
                fx_component.value,
                social_component,
            )
            social_available = any(
                item["availability"] == "available" for item in social_interest.values()
            )
            source_availability = {
                "visitors": {
                    "availability": availability.value,
                    "reason": reason,
                },
                "flights": {
                    "availability": "partial" if current_flights else "unavailable",
                    "reason": (
                        "국가별 도착 운항편 수는 있으나 여객 수는 원천에서 제공하지 않습니다."
                        if current_flights
                        else "월간 국가별 항공 관측이 없습니다."
                    ),
                },
                "flight_schedule": {
                    "availability": "available" if schedule else "unavailable",
                    "reason": None if schedule else "향후 7일 운항 일정이 없습니다.",
                },
                "fx": {
                    "availability": "available" if fx else "unavailable",
                    "reason": None if fx else "해당 통화의 환율 관측이 없습니다.",
                },
                "tourism_balance": {
                    "availability": "available" if balance else "unavailable",
                    "reason": (
                        "한국 전체 일반여행 수지이며 국가별 수지가 아닙니다."
                        if balance
                        else "관광수지 관측이 없습니다."
                    ),
                },
                "social_interest": {
                    "availability": "available" if social_available else "unavailable",
                    "reason": (
                        None
                        if social_available
                        else "실제 국가별 이용자 기준의 SNS 관측이 없습니다."
                    ),
                },
            }
            input_watermarks: dict[str, datetime] = {}
            for input_row in input_rows:
                watermark = _aware_utc(input_row.source_updated_at)
                input_watermarks[input_row.source_id] = max(
                    input_watermarks.get(input_row.source_id, watermark), watermark
                )
            calculated_at = datetime.now(UTC)
            publisher.publish(
                SnapshotCandidate(
                    endpoint="inbound_market_country",
                    lookup_key=lookup_key(country=country_iso, period=period),
                    data={
                        "country": country_iso,
                        "visitors": current_total,
                        "visitor_change_rate": _change_rate(current_total, previous_total),
                        "visitor_completeness_ratio": round(completeness, 6),
                        "arriving_flights": arriving_flights,
                        "flight_change_rate": _change_rate(
                            arriving_flights, previous_arriving_flights
                        ),
                        "passengers": None,
                        "flight_schedule": (
                            {
                                **(schedule.schedule or {}),
                                "flights": schedule.arriving_flights,
                                "change_rate": None,
                                "availability": "available",
                                "reason": None,
                            }
                            if schedule
                            else None
                        ),
                        "fx": (
                            {
                                "currency": fx.currency,
                                "krw_rate": fx.krw_rate,
                                "change_rate": fx_change,
                                "rate_date": fx.rate_date.date().isoformat(),
                                "source_id": fx.source_id,
                                "availability": "available",
                                "reason": None,
                            }
                            if fx
                            else None
                        ),
                        "fx_by_currency": _fx_views(fx_history),
                        "tourism_balance_usd": (balance.balance_usd if balance else None),
                        "tourism_balance_period": (
                            balance.period_start.strftime("%Y-%m") if balance else None
                        ),
                        "tourism_balance_scope": "KR_total" if balance else None,
                        "social_interest": social_interest or None,
                        "source_availability": source_availability,
                        "inbound_score": score.value,
                    },
                    metadata={
                        "max_acceptable_age_seconds": MAX_AGE_SECONDS,
                        "spatial_resolution": "country",
                        "reason": reason,
                    },
                    input_watermarks=input_watermarks,
                    formula_versions={"inbound_score": INBOUND_FORMULA_VERSION},
                    observed_at=max(_aware_utc(row.observed_at) for row in input_rows),
                    source_updated_at=max(_aware_utc(row.source_updated_at) for row in input_rows),
                    ingested_at=max(_aware_utc(row.ingested_at) for row in input_rows),
                    calculated_at=calculated_at,
                    availability=availability,
                    quality_flags=quality_flags,
                    raw_record_ids=raw_record_ids,
                )
            )
            published_count += 1
    return InboundProductResult(published_count, tuple(published_countries))


def _raw_record_ids(
    session_factory: sessionmaker[Session],
    output_ids: dict[str, list[int]],
) -> tuple[int, ...]:
    with session_factory() as session:
        rows: set[int] = set()
        for output_type, ids in output_ids.items():
            if not ids:
                continue
            rows.update(
                session.scalars(
                    select(ProvenanceEdge.raw_record_id).where(
                        ProvenanceEdge.output_type == output_type,
                        ProvenanceEdge.output_id.in_([str(value) for value in ids]),
                    )
                ).all()
            )
    return tuple(sorted(rows))


def _window_flights(
    rows: list[FlightObservation], end_ordinal: int, months: int
) -> list[FlightObservation]:
    start_ordinal = end_ordinal - months + 1
    return [row for row in rows if start_ordinal <= _month_ordinal(row.period_start) <= end_ordinal]


def _window_social(
    rows: list[SocialObservation], end_ordinal: int, months: int
) -> list[SocialObservation]:
    start_ordinal = end_ordinal - months + 1
    return [row for row in rows if start_ordinal <= _month_ordinal(row.bucket_start) <= end_ordinal]


def _sum_optional(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _social_interest(
    rows: list[SocialObservation],
    normalized_scores: dict[str, float | None] | None = None,
) -> tuple[dict[str, dict[str, object]], float | None]:
    grouped: dict[str, list[SocialObservation]] = {}
    for row in rows:
        source_name = SOCIAL_SOURCE_NAMES.get(row.source_id)
        if source_name is not None:
            grouped.setdefault(source_name, []).append(row)

    result: dict[str, dict[str, object]] = {}
    source_scores: list[float] = []
    for source_name, source_rows in sorted(grouped.items()):
        country_rows = [row for row in source_rows if _is_country_social_signal(row)]
        if not country_rows:
            result[source_name] = {
                "posts": None,
                "views": None,
                "reactions": None,
                "score": None,
                "availability": "unavailable",
                "reason": (
                    "YouTube 지역 필터는 재생 가능 지역이며 시청자 거주 국가가 아닙니다."
                    if source_rows[0].source_id == "SRC_YOUTUBE"
                    else "검색어 기반 시장 proxy는 실제 국가별 이용자 관측이 아닙니다."
                ),
            }
            continue
        raw_scores = [
            float(row.source_score) for row in country_rows if row.source_score is not None
        ]
        score = (
            normalized_scores.get(country_rows[0].source_id)
            if normalized_scores is not None
            else (round(mean(raw_scores), 4) if raw_scores else None)
        )
        if score is not None:
            source_scores.append(score)
        result[source_name] = {
            "posts": _sum_optional([row.post_count for row in country_rows]),
            "views": _sum_optional([row.view_count for row in country_rows]),
            "reactions": _sum_optional([row.reaction_count for row in country_rows]),
            "score": score,
            "availability": "available",
            "reason": None,
        }
    return result, round(mean(source_scores), 4) if source_scores else None


def _is_country_social_signal(row: SocialObservation) -> bool:
    flags = set(row.quality_flags or [])
    return row.source_id != "SRC_YOUTUBE" and flags.isdisjoint(COUNTRY_PROXY_FLAGS)


def _social_signal_value(rows: list[SocialObservation]) -> float | None:
    search = [float(row.search_ratio) for row in rows if row.search_ratio is not None]
    if search:
        return mean(search)
    for field_name in ("view_count", "post_count", "reaction_count"):
        values = [getattr(row, field_name) for row in rows]
        total = _sum_optional(values)
        if total is not None:
            return float(total)
    return None


def _social_source_scores(
    rows: list[SocialObservation],
    country_id: str,
) -> dict[str, float | None]:
    grouped: dict[tuple[str, str], list[SocialObservation]] = {}
    for row in rows:
        if (
            row.source_id not in SOCIAL_SOURCE_NAMES
            or row.country_id is None
            or not _is_country_social_signal(row)
        ):
            continue
        grouped.setdefault((row.source_id, row.country_id), []).append(row)
    values = {key: _social_signal_value(source_rows) for key, source_rows in grouped.items()}
    result: dict[str, float | None] = {}
    for source_id in SOCIAL_SOURCE_NAMES:
        population = [
            value
            for (candidate_source, _country), value in values.items()
            if candidate_source == source_id and value is not None
        ]
        current = values.get((source_id, country_id))
        if current is None:
            continue
        result[source_id] = minmax_score(
            current,
            population,
            f"{source_id.lower()}_market_minmax_v1",
        ).value
    return result


def _fx_views(
    history: dict[str, list[FxObservation]],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for currency, rows in sorted(history.items()):
        if not rows:
            continue
        latest = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        result[currency] = {
            "currency": currency,
            "krw_rate": latest.krw_rate,
            "change_rate": _change_rate_decimal(
                float(latest.krw_rate) if latest.krw_rate is not None else None,
                (float(previous.krw_rate) if previous and previous.krw_rate is not None else None),
            ),
            "rate_date": latest.rate_date.date().isoformat(),
            "source_id": latest.source_id,
            "availability": "available",
            "reason": None,
        }
    return result


def _component_population(
    rows: list[InboundVisitorObservation] | list[FlightObservation],
    end_ordinal: int,
    months: int,
    field_name: str,
) -> list[float]:
    grouped: dict[str, float] = {}
    start_ordinal = end_ordinal - months + 1
    for row in rows:
        if not start_ordinal <= _month_ordinal(row.period_start) <= end_ordinal:
            continue
        value = getattr(row, field_name)
        if value is not None:
            grouped[row.country_id] = grouped.get(row.country_id, 0) + float(value)
    return list(grouped.values())


def _change_rate_decimal(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 4)
