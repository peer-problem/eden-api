from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.keys import lookup_key
from app.repositories.models import (
    Area,
    Place,
    RegionalDemandObservation,
    RegionalDiversityObservation,
    RegionalVisitObservation,
)
from app.sources.essential import essential_place_ids

REGIONAL_PRODUCT_VERSION = "regional_product_v1"
REGIONAL_MAX_AGE_SECONDS = 3 * 24 * 3600
REGIONAL_VISIT_HISTORY_DAYS = 456
REGIONAL_METRIC_HISTORY_DAYS = 731


@dataclass(frozen=True, slots=True)
class RegionalProductResult:
    published_count: int
    area_ids: tuple[str, ...]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _audit_rows(rows: list[Any]) -> tuple[datetime, datetime, datetime]:
    return (
        max(_aware(row.observed_at) for row in rows),
        max(_aware(row.source_updated_at) for row in rows),
        max(_aware(row.ingested_at) for row in rows),
    )


def build_regional_snapshots(
    session_factory: sessionmaker[Session],
) -> RegionalProductResult:
    with session_factory() as session:
        province_ids = set(
            session.scalars(
                select(Area.eden_area_id).where(Area.active.is_(True), Area.level == "sido")
            )
        )
        selected_places = essential_place_ids(session)
        category_counts: dict[str, dict[str, int]] = {}
        for parent_id, area_id, category in session.execute(
            select(Area.parent_area_id, Place.area_id, Place.category)
            .join(Area, Area.eden_area_id == Place.area_id)
            .where(Place.eden_place_id.in_(selected_places))
        ):
            counts = category_counts.setdefault(parent_id or area_id, {})
            counts[category] = counts.get(category, 0) + 1
        visit_latest = {
            (area_id, subject_type): latest
            for area_id, subject_type, latest in session.execute(
                select(
                    RegionalVisitObservation.area_id,
                    RegionalVisitObservation.subject_type,
                    func.max(RegionalVisitObservation.period_start),
                ).group_by(
                    RegionalVisitObservation.area_id,
                    RegionalVisitObservation.subject_type,
                )
            ).all()
        }
        demand_latest = dict(
            session.execute(
                select(
                    RegionalDemandObservation.area_id,
                    func.max(RegionalDemandObservation.period_start),
                ).group_by(RegionalDemandObservation.area_id)
            ).all()
        )
        diversity_latest = dict(
            session.execute(
                select(
                    RegionalDiversityObservation.area_id,
                    func.max(RegionalDiversityObservation.period_start),
                ).group_by(RegionalDiversityObservation.area_id)
            ).all()
        )
        area_ids = sorted(
            province_ids
            & (
                {area_id for area_id, _subject_type in visit_latest}
                | set(demand_latest)
                | set(diversity_latest)
            )
        )

    publisher = SnapshotPublisher(session_factory)
    published: list[str] = []
    for area_id in area_ids:
        with session_factory() as session:
            area = session.get(Area, area_id)
            if area is None:
                continue
            visit_windows = [
                and_(
                    RegionalVisitObservation.subject_type == subject_type,
                    RegionalVisitObservation.period_start
                    >= latest - timedelta(days=REGIONAL_VISIT_HISTORY_DAYS - 1),
                    RegionalVisitObservation.period_start <= latest,
                )
                for (candidate_area_id, subject_type), latest in visit_latest.items()
                if candidate_area_id == area_id
            ]
            visits = (
                list(
                    session.scalars(
                        select(RegionalVisitObservation)
                        .where(
                            RegionalVisitObservation.area_id == area_id,
                            or_(*visit_windows),
                        )
                        .order_by(
                            RegionalVisitObservation.period_start,
                            RegionalVisitObservation.visitor_type,
                        )
                    ).all()
                )
                if visit_windows
                else []
            )
            latest_demand = demand_latest.get(area_id)
            demand = (
                list(
                    session.scalars(
                        select(RegionalDemandObservation)
                        .where(
                            RegionalDemandObservation.area_id == area_id,
                            RegionalDemandObservation.period_start
                            >= latest_demand - timedelta(days=REGIONAL_METRIC_HISTORY_DAYS - 1),
                            RegionalDemandObservation.period_start <= latest_demand,
                        )
                        .order_by(RegionalDemandObservation.period_start)
                    ).all()
                )
                if latest_demand is not None
                else []
            )
            latest_diversity = diversity_latest.get(area_id)
            diversity = (
                list(
                    session.scalars(
                        select(RegionalDiversityObservation)
                        .where(
                            RegionalDiversityObservation.area_id == area_id,
                            RegionalDiversityObservation.period_start
                            >= latest_diversity - timedelta(days=REGIONAL_METRIC_HISTORY_DAYS - 1),
                            RegionalDiversityObservation.period_start <= latest_diversity,
                        )
                        .order_by(RegionalDiversityObservation.period_start)
                    ).all()
                )
                if latest_diversity is not None
                else []
            )
            all_rows: list[Any] = [*visits, *demand, *diversity]
            if not all_rows:
                continue
            observed_at, source_updated_at, ingested_at = _audit_rows(all_rows)
            input_watermarks: dict[str, datetime] = {}
            for row in all_rows:
                watermark = _aware(row.source_updated_at)
                input_watermarks[row.source_id] = max(
                    input_watermarks.get(row.source_id, watermark), watermark
                )
            normalized_references = {
                "regional_visit_observation": [str(row.observation_id) for row in visits],
                "regional_demand_observation": [str(row.observation_id) for row in demand],
                "regional_diversity_observation": [str(row.observation_id) for row in diversity],
            }

        missing = [
            name
            for name, rows in (
                ("visitors", visits),
                ("demand", demand),
                ("diversity", diversity),
            )
            if not rows
        ]
        availability = Availability.PARTIAL if missing else Availability.AVAILABLE
        calculated_at = datetime.now(UTC)
        publisher.publish(
            SnapshotCandidate(
                endpoint="regional_product",
                lookup_key=lookup_key(area_code=area_id),
                data={
                    "area": {
                        "area_code": area.administrative_code or area.eden_area_id,
                        "eden_area_id": area.eden_area_id,
                        "name": area.name_ko,
                        "spatial_resolution": area.level,
                    },
                    "reference_information": {
                        "place_category_counts": category_counts.get(area_id, {}),
                        "scope": (
                            "현재 선정한 한국어 관광지의 실제 분류별 수. "
                            "공식 수요 또는 다양성 지수가 아닙니다."
                        ),
                    },
                    "visits": [
                        {
                            "observation_id": row.observation_id,
                            "period_start": _aware(row.period_start).isoformat(),
                            "visitor_type": row.visitor_type,
                            "grain": row.grain,
                            "subject_type": row.subject_type,
                            "subject_key": row.subject_key,
                            "visitor_count": row.visitor_count,
                            "concentration_rate": row.concentration_rate,
                            "completeness_ratio": row.completeness_ratio,
                        }
                        for row in visits
                    ],
                    "demand": [
                        {
                            "observation_id": row.observation_id,
                            "period_start": _aware(row.period_start).isoformat(),
                            "stay_index": row.stay_index,
                            "spend_index": row.spend_index,
                            "lodging_index": row.lodging_index,
                            "avg_stay_nights": row.avg_stay_nights,
                            "availability": row.availability,
                        }
                        for row in demand
                    ],
                    "diversity": [
                        {
                            "observation_id": row.observation_id,
                            "period_start": _aware(row.period_start).isoformat(),
                            "age_index": row.age_index,
                            "nationality_index": row.nationality_index,
                            "availability": row.availability,
                        }
                        for row in diversity
                    ],
                },
                metadata={
                    "max_acceptable_age_seconds": REGIONAL_MAX_AGE_SECONDS,
                    "normalized_references": normalized_references,
                    "spatial_resolution": area.level,
                    "reason": (
                        f"누락된 지역 데이터 블록: {', '.join(missing)}" if missing else None
                    ),
                    "missing_blocks": missing,
                },
                input_watermarks=input_watermarks,
                formula_versions={"regional_product": REGIONAL_PRODUCT_VERSION},
                observed_at=observed_at,
                source_updated_at=source_updated_at,
                ingested_at=ingested_at,
                calculated_at=calculated_at,
                availability=availability,
                quality_flags=tuple(f"missing_{name}" for name in missing),
                raw_record_ids=(),
            )
        )
        published.append(area_id)
    return RegionalProductResult(len(published), tuple(published))
