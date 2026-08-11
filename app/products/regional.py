from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.products.snapshots import SnapshotCandidate, SnapshotPublisher
from app.readmodels.keys import lookup_key
from app.repositories.models import (
    Area,
    ProvenanceEdge,
    RegionalDemandObservation,
    RegionalDiversityObservation,
    RegionalVisitObservation,
)

REGIONAL_PRODUCT_VERSION = "regional_product_v1"
REGIONAL_MAX_AGE_SECONDS = 3 * 24 * 3600


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


def _raw_record_ids(session: Session, output_ids: dict[str, list[int]]) -> tuple[int, ...]:
    raw_ids: set[int] = set()
    for output_type, ids in output_ids.items():
        if not ids:
            continue
        raw_ids.update(
            session.scalars(
                select(ProvenanceEdge.raw_record_id).where(
                    ProvenanceEdge.output_type == output_type,
                    ProvenanceEdge.output_id.in_([str(value) for value in ids]),
                )
            ).all()
        )
    return tuple(sorted(raw_ids))


def build_regional_snapshots(
    session_factory: sessionmaker[Session],
) -> RegionalProductResult:
    with session_factory() as session:
        area_ids = sorted(
            set(
                session.scalars(select(RegionalVisitObservation.area_id).distinct()).all()
            )
            | set(
                session.scalars(select(RegionalDemandObservation.area_id).distinct()).all()
            )
            | set(
                session.scalars(select(RegionalDiversityObservation.area_id).distinct()).all()
            )
        )

    publisher = SnapshotPublisher(session_factory)
    published: list[str] = []
    for area_id in area_ids:
        with session_factory() as session:
            area = session.get(Area, area_id)
            if area is None:
                continue
            visits = list(
                session.scalars(
                    select(RegionalVisitObservation)
                    .where(RegionalVisitObservation.area_id == area_id)
                    .order_by(
                        RegionalVisitObservation.period_start,
                        RegionalVisitObservation.visitor_type,
                    )
                ).all()
            )
            demand = list(
                session.scalars(
                    select(RegionalDemandObservation)
                    .where(RegionalDemandObservation.area_id == area_id)
                    .order_by(RegionalDemandObservation.period_start)
                ).all()
            )
            diversity = list(
                session.scalars(
                    select(RegionalDiversityObservation)
                    .where(RegionalDiversityObservation.area_id == area_id)
                    .order_by(RegionalDiversityObservation.period_start)
                ).all()
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
            raw_ids = _raw_record_ids(
                session,
                {
                    "regional_visit_observation": [row.observation_id for row in visits],
                    "regional_demand_observation": [row.observation_id for row in demand],
                    "regional_diversity_observation": [
                        row.observation_id for row in diversity
                    ],
                },
            )

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
                raw_record_ids=raw_ids,
            )
        )
        published.append(area_id)
    return RegionalProductResult(len(published), tuple(published))
