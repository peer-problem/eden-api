from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import RunStatus
from app.repositories.models import (
    AreaSourceMap,
    DeadLetter,
    IngestionRun,
    ProvenanceEdge,
    RawRecord,
    RegionalDemandObservation,
    RegionalDiversityObservation,
    RegionalVisitObservation,
    SocialObservation,
)
from app.sources.public_data import public_data_items

REGIONAL_VISIT_SOURCE = "SRC_KTO_REGIONAL_VISITORS"
REGIONAL_DEMAND_SOURCE = "SRC_KTO_DEMAND_INTENSITY"
REGIONAL_DIVERSITY_SOURCE = "SRC_KTO_DIVERSITY"
RESOURCE_DEMAND_SOURCE = "SRC_KTO_RESOURCE_DEMAND"


@dataclass(slots=True)
class _Aggregate:
    observed_at: datetime
    source_updated_at: datetime
    ingested_at: datetime
    values: list[Decimal] = field(default_factory=list)
    raw_record_ids: set[int] = field(default_factory=set)


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _document(raw: RawRecord) -> dict[str, Any] | list[Any]:
    body = raw.body_json
    if not isinstance(body, (dict, list)):
        raise ValueError("raw record does not contain a structured public-data response")
    if isinstance(body, dict) and "response" in body:
        response = body["response"]
        if isinstance(response, (dict, list)):
            return response
    return body


def _text(row: dict[str, Any], *names: str, required: bool = False) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    if required:
        raise ValueError(f"missing required field: {'/'.join(names)}")
    return None


def _decimal(row: dict[str, Any], *names: str, required: bool = False) -> Decimal | None:
    raw = _text(row, *names, required=required)
    if raw is None:
        return None
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal field: {'/'.join(names)}") from exc
    if not value.is_finite():
        raise ValueError(f"non-finite decimal field: {'/'.join(names)}")
    return value


def _bounded_index(row: dict[str, Any], *names: str) -> Decimal:
    value = _decimal(row, *names, required=True)
    assert value is not None
    if value < 0 or value > 100:
        raise ValueError(f"0-100 source index out of range: {'/'.join(names)}")
    return value


def _date(value: str, formats: Iterable[str]) -> datetime:
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    raise ValueError(f"unsupported source date: {value[:32]}")


def _area_code(row: dict[str, Any]) -> str:
    return _text(
        row,
        "signguCode",
        "signguCd",
        "signguCode",
        "areaCode",
        "areaCd",
        "areacode",
        required=True,
    ) or ""


def _resolve_area_id(session: Session, source_id: str, row: dict[str, Any]) -> str:
    code = _area_code(row)
    area_id = session.scalar(
        select(AreaSourceMap.eden_area_id).where(
            AreaSourceMap.source_id == source_id,
            AreaSourceMap.external_area_code == code,
        )
    )
    if area_id is None:
        raise ValueError(f"unmapped {source_id} area code: {code}")
    return area_id


def _visitor_type(row: dict[str, Any]) -> str:
    label = (_text(row, "touDivNm") or "").replace(" ", "")
    if "외국" in label:
        return "foreign"
    if "내국" in label:
        return "domestic"
    if any(token in label for token in ("전체", "합계", "총")):
        return "all"
    raise ValueError(f"unknown tourist division: {label or '<missing>'}")


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.0001"))


def _add_dead_letter(session: Session, raw: RawRecord, code: str, exc: Exception) -> None:
    exists = session.scalar(
        select(DeadLetter.dead_letter_id).where(
            DeadLetter.raw_record_id == raw.raw_record_id,
            DeadLetter.error_code == code,
            DeadLetter.reprocess_status == "pending",
        )
    )
    if exists is None:
        session.add(
            DeadLetter(
                raw_record_id=raw.raw_record_id,
                error_code=code,
                error_detail=f"{type(exc).__name__}: {str(exc)[:1900]}",
                created_at=datetime.now(UTC).replace(tzinfo=None),
                reprocess_status="pending",
                reprocessed_at=None,
            )
        )


def _provenance(
    session: Session,
    output_type: str,
    output_id: int,
    raw_record_ids: Iterable[int],
    formula_version: str = "identity_v1",
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for raw_record_id in sorted(set(raw_record_ids)):
        session.execute(
            insert(ProvenanceEdge)
            .values(
                output_type=output_type,
                output_id=str(output_id),
                raw_record_id=raw_record_id,
                formula_version=formula_version,
                created_at=now,
            )
            .on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id)
        )


def _run_records(session: Session, run_id: str, source_id: str) -> list[RawRecord]:
    return list(
        session.scalars(
            select(RawRecord)
            .where(RawRecord.run_id == run_id, RawRecord.source_id == source_id)
            .order_by(RawRecord.raw_record_id)
        ).all()
    )


def _finish_run(session: Session, run_id: str, count: int) -> None:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise ValueError(f"ingestion run does not exist: {run_id}")
    dead_letter_count = session.scalar(
        select(func.count())
        .select_from(DeadLetter)
        .join(RawRecord, RawRecord.raw_record_id == DeadLetter.raw_record_id)
        .where(
            RawRecord.run_id == run_id,
            DeadLetter.reprocess_status == "pending",
        )
    ) or 0
    run.normalized_count = count
    if dead_letter_count and run.status in {
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
    }:
        run.status = RunStatus.PARTIAL if count else RunStatus.FAILED


def _public_rows(
    session: Session,
    raw: RawRecord,
    error_code: str,
) -> list[dict[str, Any]]:
    try:
        return public_data_items(_document(raw))
    except Exception as exc:
        _add_dead_letter(session, raw, error_code, exc)
        return []


def normalize_regional_visitors_run(
    session_factory: sessionmaker[Session], run_id: str
) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, REGIONAL_VISIT_SOURCE):
            for row in _public_rows(session, raw, "regional_visit_schema"):
                try:
                    with session.begin_nested():
                        area_id = _resolve_area_id(session, REGIONAL_VISIT_SOURCE, row)
                        period_start = _date(
                            _text(row, "baseYmd", required=True) or "", ("%Y%m%d",)
                        )
                        count_value = _decimal(row, "touNum", required=True)
                        assert count_value is not None
                        if count_value < 0:
                            raise ValueError("visitor count cannot be negative")
                        rounded = int(count_value.to_integral_value(rounding=ROUND_HALF_UP))
                        quality_flags = (
                            ["source_estimate_rounded"] if count_value != rounded else []
                        )
                        values = {
                            "area_id": area_id,
                            "subject_type": "area",
                            "subject_key": "area",
                            "visitor_type": _visitor_type(row),
                            "grain": "day",
                            "period_start": period_start,
                            "visitor_count": rounded,
                            "concentration_rate": None,
                            "completeness_ratio": Decimal("1"),
                            "observed_at": _database_time(raw.observed_at),
                            "source_updated_at": period_start,
                            "ingested_at": _database_time(raw.ingested_at),
                            "calculated_at": calculated_at,
                            "source_id": REGIONAL_VISIT_SOURCE,
                            "availability": "available",
                            "quality_flags": quality_flags,
                        }
                        session.execute(
                            insert(RegionalVisitObservation)
                            .values(**values)
                            .on_duplicate_key_update(**values)
                        )
                        observation_id = session.scalar(
                            select(RegionalVisitObservation.observation_id).where(
                                RegionalVisitObservation.source_id
                                == REGIONAL_VISIT_SOURCE,
                                RegionalVisitObservation.area_id == area_id,
                                RegionalVisitObservation.subject_type == "area",
                                RegionalVisitObservation.subject_key == "area",
                                RegionalVisitObservation.visitor_type
                                == values["visitor_type"],
                                RegionalVisitObservation.grain == "day",
                                RegionalVisitObservation.period_start == period_start,
                            )
                        )
                        if observation_id is None:
                            raise RuntimeError(
                                "regional visitor observation was not resolved"
                            )
                        _provenance(
                            session,
                            "regional_visit_observation",
                            observation_id,
                            (raw.raw_record_id,),
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "regional_visit_row_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def _aggregate_index_rows(
    session: Session,
    records: list[RawRecord],
    source_id: str,
    value_fields: tuple[str, ...],
) -> dict[tuple[str, datetime, str], _Aggregate]:
    groups: dict[tuple[str, datetime, str], _Aggregate] = {}
    for raw in records:
        for row in _public_rows(session, raw, f"{source_id.lower()}_schema"):
            try:
                value_field = next(
                    (name for name in value_fields if row.get(name) not in (None, "")),
                    None,
                )
                if value_field is None:
                    raise ValueError("documented index value field is missing")
                value = _bounded_index(row, value_field)
                area_id = _resolve_area_id(session, source_id, row)
                period = _date(_text(row, "baseYm", required=True) or "", ("%Y%m",))
                key = (area_id, period, value_field)
                aggregate = groups.setdefault(
                    key,
                    _Aggregate(
                        observed_at=_database_time(raw.observed_at),
                        source_updated_at=period,
                        ingested_at=_database_time(raw.ingested_at),
                    ),
                )
                aggregate.values.append(value)
                aggregate.raw_record_ids.add(raw.raw_record_id)
                aggregate.observed_at = max(aggregate.observed_at, _database_time(raw.observed_at))
                aggregate.ingested_at = max(aggregate.ingested_at, _database_time(raw.ingested_at))
            except Exception as exc:
                _add_dead_letter(session, raw, f"{source_id.lower()}_row_schema", exc)
    return groups


def normalize_regional_demand_run(
    session_factory: sessionmaker[Session], run_id: str
) -> int:
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    normalized = 0
    with session_factory.begin() as session:
        groups = _aggregate_index_rows(
            session,
            _run_records(session, run_id, REGIONAL_DEMAND_SOURCE),
            REGIONAL_DEMAND_SOURCE,
            ("tarSjrnDsIxVal", "tarExpDsIxVal"),
        )
        combined: dict[tuple[str, datetime], dict[str, _Aggregate]] = defaultdict(dict)
        for (area_id, period, field_name), aggregate in groups.items():
            combined[(area_id, period)][field_name] = aggregate
        for (area_id, period), metrics in combined.items():
            audit = list(metrics.values())
            values = {
                "area_id": area_id,
                "period_start": period,
                "stay_index": _mean(
                    metrics.get("tarSjrnDsIxVal", _Aggregate(period, period, period)).values
                ),
                "spend_index": _mean(
                    metrics.get("tarExpDsIxVal", _Aggregate(period, period, period)).values
                ),
                "lodging_index": None,
                "avg_stay_nights": None,
                "observed_at": max(item.observed_at for item in audit),
                "source_updated_at": max(item.source_updated_at for item in audit),
                "ingested_at": max(item.ingested_at for item in audit),
                "calculated_at": calculated_at,
                "source_id": REGIONAL_DEMAND_SOURCE,
                "availability": "available",
                "quality_flags": [],
            }
            session.execute(
                insert(RegionalDemandObservation)
                .values(**values)
                .on_duplicate_key_update(**values)
            )
            observation_id = session.scalar(
                select(RegionalDemandObservation.observation_id).where(
                    RegionalDemandObservation.source_id == REGIONAL_DEMAND_SOURCE,
                    RegionalDemandObservation.area_id == area_id,
                    RegionalDemandObservation.period_start == period,
                )
            )
            if observation_id is None:
                raise RuntimeError("regional demand observation was not resolved")
            _provenance(
                session,
                "regional_demand_observation",
                observation_id,
                (raw_id for item in audit for raw_id in item.raw_record_ids),
                "documented_subindex_mean_v1",
            )
            normalized += 1
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_regional_diversity_run(
    session_factory: sessionmaker[Session], run_id: str
) -> int:
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    normalized = 0
    with session_factory.begin() as session:
        groups = _aggregate_index_rows(
            session,
            _run_records(session, run_id, REGIONAL_DIVERSITY_SOURCE),
            REGIONAL_DIVERSITY_SOURCE,
            ("touDivIxVal", "intlDivIxVal"),
        )
        combined: dict[tuple[str, datetime], dict[str, _Aggregate]] = defaultdict(dict)
        for (area_id, period, field_name), aggregate in groups.items():
            combined[(area_id, period)][field_name] = aggregate
        for (area_id, period), metrics in combined.items():
            audit = list(metrics.values())
            values = {
                "area_id": area_id,
                "period_start": period,
                # The current source names this value "tourist diversity"; it
                # is not semantically identical to age diversity.  Preserve
                # the input through provenance but do not relabel it.
                "age_index": None,
                "nationality_index": _mean(
                    metrics.get("intlDivIxVal", _Aggregate(period, period, period)).values
                ),
                "observed_at": max(item.observed_at for item in audit),
                "source_updated_at": max(item.source_updated_at for item in audit),
                "ingested_at": max(item.ingested_at for item in audit),
                "calculated_at": calculated_at,
                "source_id": REGIONAL_DIVERSITY_SOURCE,
                "availability": "partial",
                "quality_flags": ["age_dimension_not_identified"],
            }
            session.execute(
                insert(RegionalDiversityObservation)
                .values(**values)
                .on_duplicate_key_update(**values)
            )
            observation_id = session.scalar(
                select(RegionalDiversityObservation.observation_id).where(
                    RegionalDiversityObservation.source_id == REGIONAL_DIVERSITY_SOURCE,
                    RegionalDiversityObservation.area_id == area_id,
                    RegionalDiversityObservation.period_start == period,
                )
            )
            if observation_id is None:
                raise RuntimeError("regional diversity observation was not resolved")
            _provenance(
                session,
                "regional_diversity_observation",
                observation_id,
                (raw_id for item in audit for raw_id in item.raw_record_ids),
                "documented_subindex_mean_v1",
            )
            normalized += 1
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_resource_demand_run(
    session_factory: sessionmaker[Session], run_id: str
) -> int:
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    normalized = 0
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, RESOURCE_DEMAND_SOURCE):
            for row in _public_rows(session, raw, "resource_demand_schema"):
                try:
                    with session.begin_nested():
                        is_service = row.get("tarSvcDemIxVal") not in (None, "")
                        value_field = "tarSvcDemIxVal" if is_service else "culResDemIxVal"
                        name_field = "tarSvcDemIxNm" if is_service else "culResDemIxNm"
                        code_field = "tarSvcDemIxCd" if is_service else "culResDemIxCd"
                        period = _date(
                            _text(row, "baseYm", required=True) or "", ("%Y%m",)
                        )
                        values = {
                            "raw_record_id": raw.raw_record_id,
                            "keyword": _text(row, name_field, code_field, required=True),
                            "country_id": None,
                            "area_id": _resolve_area_id(
                                session, RESOURCE_DEMAND_SOURCE, row
                            ),
                            "bucket_start": period,
                            "bucket_grain": "month",
                            "post_count": None,
                            "view_count": None,
                            "reaction_count": None,
                            "search_ratio": None,
                            "source_score": _bounded_index(row, value_field),
                            "observed_at": _database_time(raw.observed_at),
                            "source_updated_at": period,
                            "ingested_at": _database_time(raw.ingested_at),
                            "calculated_at": calculated_at,
                            "source_id": RESOURCE_DEMAND_SOURCE,
                            "availability": "available",
                            "quality_flags": ["official_demand_index"],
                        }
                        session.execute(
                            insert(SocialObservation)
                            .values(**values)
                            .on_duplicate_key_update(**values)
                        )
                        observation_id = session.scalar(
                            select(SocialObservation.observation_id).where(
                                SocialObservation.source_id == RESOURCE_DEMAND_SOURCE,
                                SocialObservation.raw_record_id == raw.raw_record_id,
                                SocialObservation.keyword == values["keyword"],
                                SocialObservation.area_id == values["area_id"],
                                SocialObservation.bucket_start == period,
                            )
                        )
                        if observation_id is None:
                            raise RuntimeError(
                                "resource demand observation was not resolved"
                            )
                        _provenance(
                            session,
                            "social_observation",
                            observation_id,
                            (raw.raw_record_id,),
                            "official_source_index_v1",
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "resource_demand_row_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized
