from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.ids import stable_eden_id
from app.repositories.models import (
    InboundVisitorObservation,
    IngestionRun,
    ProvenanceEdge,
    RawRecord,
)
from app.sources.kto_inbound import (
    SOURCE_ID,
    _month_data_as_of,
    aggregate_kto_inbound_rows,
)


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    run_id: str
    normalized_count: int
    raw_record_ids: tuple[int, ...]


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def normalize_kto_inbound_run(
    session_factory: sessionmaker[Session],
    run_id: str,
) -> NormalizationResult:
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    normalized_count = 0
    raw_record_ids: list[int] = []
    with session_factory.begin() as session:
        records = session.scalars(
            select(RawRecord)
            .where(RawRecord.run_id == run_id, RawRecord.source_id == SOURCE_ID)
            .order_by(RawRecord.raw_record_id)
        ).all()
        for raw in records:
            body = raw.body_json
            if not isinstance(body, dict):
                continue
            country_iso = body.get("country_iso")
            if not isinstance(country_iso, str):
                continue
            totals = aggregate_kto_inbound_rows(body)
            country_id = stable_eden_id("country", "ISO3166", country_iso)
            for month, visitor_count in sorted(totals.items()):
                period_start = datetime(int(month[:4]), int(month[4:]), 1)
                source_updated_at = _database_time(_month_data_as_of(month))
                values = {
                    "country_id": country_id,
                    "period_start": period_start,
                    "visitor_count": visitor_count,
                    "observed_at": _database_time(raw.observed_at),
                    "source_updated_at": source_updated_at,
                    "ingested_at": _database_time(raw.ingested_at),
                    "calculated_at": calculated_at,
                    "source_id": SOURCE_ID,
                    "availability": "available",
                    "quality_flags": [],
                }
                session.execute(
                    insert(InboundVisitorObservation)
                    .values(**values)
                    .on_duplicate_key_update(**values)
                )
                observation_id = session.scalar(
                    select(InboundVisitorObservation.observation_id).where(
                        InboundVisitorObservation.source_id == SOURCE_ID,
                        InboundVisitorObservation.country_id == country_id,
                        InboundVisitorObservation.period_start == period_start,
                    )
                )
                if observation_id is None:
                    raise RuntimeError("Normalized inbound observation could not be resolved")
                session.execute(
                    insert(ProvenanceEdge)
                    .values(
                        output_type="inbound_visitor_observation",
                        output_id=str(observation_id),
                        raw_record_id=raw.raw_record_id,
                        formula_version="identity_v1",
                        created_at=calculated_at,
                    )
                    .on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id)
                )
                normalized_count += 1
            raw_record_ids.append(raw.raw_record_id)
        session.query(IngestionRun).filter(IngestionRun.run_id == run_id).update(
            {"normalized_count": normalized_count}
        )
    return NormalizationResult(
        run_id=run_id,
        normalized_count=normalized_count,
        raw_record_ids=tuple(raw_record_ids),
    )
