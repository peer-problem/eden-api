from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.products.formulas import (
    CROWD_FORMULA_VERSION,
    RECOMMENDATION_FORMULA_VERSION,
    crowd_index,
)
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.keys import lookup_key
from app.repositories.models import (
    Area,
    Country,
    Place,
    PlaceLocalization,
    PlaceRelation,
    PlaceSourceMap,
    ProvenanceEdge,
    RegionalDemandObservation,
    RegionalVisitObservation,
)

MAX_AGE_SECONDS = 3 * 24 * 3600
MAX_RECOMMENDATION_FEATURES = 2_000
MAX_RECOMMENDATION_FEATURES_PER_AREA = 50
RECOMMENDATION_CROWD_HISTORY_DAYS = 366


@dataclass(frozen=True, slots=True)
class RecommendationProductResult:
    published_count: int
    place_count: int


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _season(month: int) -> str:
    if month in {3, 4, 5}:
        return "spring"
    if month in {6, 7, 8}:
        return "summer"
    if month in {9, 10, 11}:
        return "autumn"
    return "winter"


def _themes(category: str | None, localizations: list[PlaceLocalization]) -> list[str]:
    text = " ".join(
        [category or "", *(row.title for row in localizations)]
    ).casefold()
    result: list[str] = []
    category_code = (category or "").upper()
    if category_code.startswith("A01") or any(
        token in text for token in ("공원", "산", "바다", "해변", "섬", "숲", "자연")
    ):
        result.append("nature")
    if category_code.startswith("A02") or any(
        token in text for token in ("궁", "박물관", "문화", "사찰", "역사", "미술관")
    ):
        result.append("culture")
    if category_code.startswith("A05") or any(
        token in text for token in ("시장", "음식", "맛집", "카페", "restaurant")
    ):
        result.append("food")
    if any(token in text for token in ("k-pop", "kpop", "케이팝", "한류", "bts")):
        result.append("kpop")
    return result


def _raw_ids(session: Session, output_ids: dict[str, list[str]]) -> tuple[int, ...]:
    result: set[int] = set()
    for output_type, ids in output_ids.items():
        if not ids:
            continue
        result.update(
            session.scalars(
                select(ProvenanceEdge.raw_record_id).where(
                    ProvenanceEdge.output_type == output_type,
                    ProvenanceEdge.output_id.in_(ids),
                )
            ).all()
        )
    return tuple(sorted(result))


def build_recommendation_snapshot(
    session_factory: sessionmaker[Session],
) -> RecommendationProductResult:
    with session_factory() as session:
        place_filter = (
            (Place.merge_status == "active")
            & Place.area_id.is_not(None)
            & Place.lat.is_not(None)
            & Place.lng.is_not(None)
        )
        eligible_place_count = int(
            session.scalar(select(func.count()).select_from(Place).where(place_filter)) or 0
        )
        ranked_places = (
            select(
                Place.eden_place_id.label("place_id"),
                func.row_number()
                .over(
                    partition_by=Place.area_id,
                    order_by=Place.eden_place_id,
                )
                .label("area_rank"),
            )
            .where(place_filter)
            .subquery()
        )
        places = list(
            session.scalars(
                select(Place)
                .join(
                    ranked_places,
                    ranked_places.c.place_id == Place.eden_place_id,
                )
                .where(
                    ranked_places.c.area_rank
                    <= MAX_RECOMMENDATION_FEATURES_PER_AREA
                )
                .order_by(Place.area_id, Place.eden_place_id)
                .limit(MAX_RECOMMENDATION_FEATURES)
            ).all()
        )
        if not places:
            return RecommendationProductResult(0, 0)
        place_ids = [place.eden_place_id for place in places]
        latest_relation_observed = (
            select(
                PlaceRelation.from_place_id.label("from_place_id"),
                func.max(PlaceRelation.observed_at).label("observed_at"),
            )
            .where(
                PlaceRelation.from_place_id.in_(place_ids),
                PlaceRelation.relation_type == "related",
            )
            .group_by(PlaceRelation.from_place_id)
            .subquery()
        )
        relations = list(
            session.scalars(
                select(PlaceRelation)
                .join(
                    latest_relation_observed,
                    (latest_relation_observed.c.from_place_id == PlaceRelation.from_place_id)
                    & (latest_relation_observed.c.observed_at == PlaceRelation.observed_at),
                )
                .where(
                    PlaceRelation.from_place_id.in_(place_ids),
                    PlaceRelation.relation_type == "related",
                )
                .order_by(
                    PlaceRelation.from_place_id,
                    PlaceRelation.observed_at.desc(),
                    PlaceRelation.rank,
                )
            ).all()
        )
        localization_place_ids = sorted(
            {*place_ids, *(row.to_place_id for row in relations)}
        )
        localizations = list(
            session.scalars(
                select(PlaceLocalization)
                .where(PlaceLocalization.eden_place_id.in_(localization_place_ids))
                .order_by(PlaceLocalization.eden_place_id, PlaceLocalization.language)
            ).all()
        )
        source_maps = list(
            session.scalars(
                select(PlaceSourceMap)
                .where(PlaceSourceMap.eden_place_id.in_(place_ids))
                .order_by(PlaceSourceMap.eden_place_id, PlaceSourceMap.source_id)
            ).all()
        )
        ranked_demand = (
            select(
                RegionalDemandObservation.observation_id.label("observation_id"),
                func.row_number()
                .over(
                    partition_by=RegionalDemandObservation.area_id,
                    order_by=(
                        RegionalDemandObservation.period_start.desc(),
                        RegionalDemandObservation.observation_id.desc(),
                    ),
                )
                .label("area_rank"),
            )
            .subquery()
        )
        demand_rows = list(
            session.scalars(
                select(RegionalDemandObservation)
                .join(
                    ranked_demand,
                    ranked_demand.c.observation_id
                    == RegionalDemandObservation.observation_id,
                )
                .where(ranked_demand.c.area_rank == 1)
                .order_by(RegionalDemandObservation.area_id)
            ).all()
        )
        latest_visit_period = session.scalar(
            select(func.max(RegionalVisitObservation.period_start)).where(
                RegionalVisitObservation.subject_type == "area",
                RegionalVisitObservation.visitor_type == "all",
                RegionalVisitObservation.visitor_count.is_not(None),
            )
        )
        visit_rows = list(
            session.scalars(
                select(RegionalVisitObservation).where(
                    RegionalVisitObservation.subject_type == "area",
                    RegionalVisitObservation.visitor_type == "all",
                    RegionalVisitObservation.visitor_count.is_not(None),
                    RegionalVisitObservation.period_start
                    >= (
                        latest_visit_period
                        - timedelta(days=RECOMMENDATION_CROWD_HISTORY_DAYS)
                        if latest_visit_period is not None
                        else datetime.max
                    ),
                )
            ).all()
        )
        selected_area_ids = {str(place.area_id) for place in places}
        areas = {
            row.eden_area_id: row
            for row in session.scalars(
                select(Area).where(Area.eden_area_id.in_(selected_area_ids))
            ).all()
        }
        country_languages = {
            row.iso_alpha2: row.default_language
            for row in session.scalars(select(Country)).all()
        }

        raw_record_ids = _raw_ids(
            session,
            {
                "place": place_ids,
                "place_relation": [str(row.relation_id) for row in relations],
                "regional_demand_observation": [
                    str(row.observation_id) for row in demand_rows
                ],
                "regional_visit_observation": [
                    str(row.observation_id) for row in visit_rows
                ],
            },
        )

    by_place_localizations: dict[str, list[PlaceLocalization]] = {}
    for row in localizations:
        by_place_localizations.setdefault(row.eden_place_id, []).append(row)
    by_place_sources: dict[str, list[PlaceSourceMap]] = {}
    for row in source_maps:
        by_place_sources.setdefault(row.eden_place_id, []).append(row)
    latest_relations: dict[str, list[PlaceRelation]] = {}
    for row in relations:
        current = latest_relations.setdefault(row.from_place_id, [])
        if not current or row.observed_at == current[0].observed_at:
            current.append(row)
    latest_demand: dict[str, RegionalDemandObservation] = {}
    for row in demand_rows:
        latest_demand.setdefault(row.area_id, row)

    season_totals: dict[tuple[str, str], float] = {}
    for row in visit_rows:
        key = (row.area_id, _season(row.period_start.month))
        season_totals[key] = season_totals.get(key, 0) + float(row.visitor_count or 0)
    crowd_by_area: dict[str, dict[str, float | None]] = {}
    for area_id in areas:
        crowd_by_area[area_id] = {}
        for season in ("spring", "summer", "autumn", "winter"):
            population = [
                value
                for (candidate_area, candidate_season), value in season_totals.items()
                if candidate_season == season
            ]
            crowd_by_area[area_id][season] = crowd_index(
                season_totals.get((area_id, season)), population
            ).value

    title_by_place = {
        place_id: next(
            (row.title for row in rows if row.language == "ko"),
            rows[0].title,
        )
        for place_id, rows in by_place_localizations.items()
        if rows
    }
    features: list[dict[str, Any]] = []
    for place in places:
        area = areas.get(place.area_id)
        place_localizations = by_place_localizations.get(place.eden_place_id, [])
        if area is None or not place_localizations:
            continue
        demand = latest_demand.get(place.area_id)
        demand_values = [
            float(value)
            for value in (
                demand.stay_index if demand else None,
                demand.spend_index if demand else None,
            )
            if value is not None
        ]
        related = latest_relations.get(place.eden_place_id, [])[:10]
        features.append(
            {
                "place_id": place.eden_place_id,
                "area": {
                    "area_code": area.administrative_code or area.eden_area_id,
                    "eden_area_id": area.eden_area_id,
                    "name": area.name_ko,
                },
                "category": place.category,
                "lat": place.lat,
                "lng": place.lng,
                "localizations": {
                    row.language: row.title for row in place_localizations
                },
                "themes": _themes(place.category, place_localizations),
                "demand_score": (
                    round(mean(demand_values), 4) if demand_values else None
                ),
                "crowd_by_season": crowd_by_area.get(place.area_id, {}),
                "related_places": [
                    {
                        "content_id": row.to_place_id,
                        "title": title_by_place.get(row.to_place_id),
                        "relation_type": "related",
                        "score": row.score,
                        "score_as_of": _aware(row.source_updated_at).isoformat(),
                    }
                    for row in related
                    if title_by_place.get(row.to_place_id) is not None
                    and row.score is not None
                ],
                "sources": sorted(
                    {
                        *(row.source_id for row in by_place_sources.get(place.eden_place_id, [])),
                        *(row.source_id for row in related),
                        *([demand.source_id] if demand else []),
                    }
                ),
            }
        )
    if not features:
        return RecommendationProductResult(0, 0)

    audit_times = [
        *(_aware(row.updated_at) for row in places),
        *(_aware(row.source_updated_at) for row in relations),
        *(_aware(row.source_updated_at) for row in demand_rows),
        *(_aware(row.source_updated_at) for row in visit_rows),
    ]
    observed_times = [
        *(_aware(row.updated_at) for row in places),
        *(_aware(row.observed_at) for row in relations),
        *(_aware(row.observed_at) for row in demand_rows),
        *(_aware(row.observed_at) for row in visit_rows),
    ]
    ingested_times = [
        *(_aware(row.updated_at) for row in places),
        *(_aware(row.ingested_at) for row in relations),
        *(_aware(row.ingested_at) for row in demand_rows),
        *(_aware(row.ingested_at) for row in visit_rows),
    ]
    input_watermarks: dict[str, datetime] = {}
    for row in source_maps:
        watermark = _aware(row.updated_at)
        input_watermarks[row.source_id] = max(
            input_watermarks.get(row.source_id, watermark), watermark
        )
    for row in [*relations, *demand_rows, *visit_rows]:
        watermark = _aware(row.source_updated_at)
        input_watermarks[row.source_id] = max(
            input_watermarks.get(row.source_id, watermark), watermark
        )

    calculated_at = datetime.now(UTC)
    feature_pool_truncated = eligible_place_count > len(places)
    SnapshotPublisher(session_factory).publish(
        SnapshotCandidate(
            endpoint="recommendation_feature",
            lookup_key=lookup_key(scope="global"),
            data={
                "country_languages": country_languages,
                "features": features,
            },
            metadata={
                "max_acceptable_age_seconds": MAX_AGE_SECONDS,
                "spatial_resolution": "place",
                "reason": "예산 원천은 등록되어 있지 않아 값이 제공되지 않습니다.",
                "candidate_pool": {
                    "eligible_places": eligible_place_count,
                    "selected_places": len(places),
                    "max_places": MAX_RECOMMENDATION_FEATURES,
                    "max_places_per_area": MAX_RECOMMENDATION_FEATURES_PER_AREA,
                },
            },
            input_watermarks=input_watermarks,
            formula_versions={
                "recommendation_score": RECOMMENDATION_FORMULA_VERSION,
                "crowd_index": CROWD_FORMULA_VERSION,
            },
            observed_at=max(observed_times),
            source_updated_at=max(audit_times),
            ingested_at=max(ingested_times),
            calculated_at=calculated_at,
            availability=Availability.PARTIAL,
            quality_flags=(
                "budget_source_unavailable",
                *(("candidate_pool_truncated",) if feature_pool_truncated else ()),
            ),
            raw_record_ids=raw_record_ids,
        )
    )
    return RecommendationProductResult(1, len(features))
