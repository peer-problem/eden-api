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
from app.normalization.raw_content import decoded_raw_json
from app.repositories.models import (
    Area,
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


@dataclass(slots=True)
class _VisitorAggregate:
    values: dict[str, Any]
    raw_record_ids: set[int] = field(default_factory=set)


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _document(raw: RawRecord) -> dict[str, Any] | list[Any]:
    body = decoded_raw_json(raw)
    if not isinstance(body, (dict, list)):
        raise ValueError("raw record does not contain a structured public-data response")
    if isinstance(body, dict) and "response" in body:
        response = body["response"]
        if isinstance(response, (dict, list)):
            return response
    return body


def _contains_field(value: Any, field_name: str) -> bool:
    if isinstance(value, dict):
        return field_name in value or any(
            _contains_field(child, field_name) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_field(child, field_name) for child in value)
    return False


def _strict_public_data_items(
    document: dict[str, Any] | list[Any],
) -> list[dict[str, Any]]:
    """Separate a documented empty result from an unrecognized response shape."""
    if not _contains_field(document, "items"):
        raise ValueError("public-data response does not contain an items container")
    return public_data_items(document)


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
    for name in ("signguCode", "signguCd"):
        value = _text(row, name)
        if value not in {None, "0", "_"}:
            return value
    legal_region = _text(row, "lDongRegnCd")
    legal_sigungu = _text(row, "lDongSignguCd")
    if legal_region and legal_sigungu:
        return f"{legal_region}{legal_sigungu.zfill(3)}"
    return _text(row, "areaCode", "areaCd", "areacode", required=True) or ""


def _resolve_area_id(session: Session, source_id: str, row: dict[str, Any]) -> str:
    code = _area_code(row)
    area_map_cache = session.info.setdefault("eden_area_source_map", {})
    cache_key = (source_id, code)
    cached_area_id = area_map_cache.get(cache_key)
    if cached_area_id is not None:
        return cached_area_id
    area_id = session.scalar(
        select(AreaSourceMap.eden_area_id).where(
            AreaSourceMap.source_id == source_id,
            AreaSourceMap.external_area_code == code,
        )
    )
    if area_id is None:
        address = _text(row, "addr1", "rdnmadr", "lnmadr")
        if address:
            cached = session.info.get("eden_active_area_names")
            if cached is None:
                cached = session.execute(
                    select(Area.eden_area_id, Area.name_ko, Area.parent_area_id).where(
                        Area.active.is_(True)
                    )
                ).all()
                session.info["eden_active_area_names"] = cached
            parent_names = {item.eden_area_id: item.name_ko for item in cached}
            matches = [
                item
                for item in cached
                if item.name_ko and item.name_ko in address and item.parent_area_id is not None
            ]
            parent_matches = [
                item for item in matches if parent_names.get(item.parent_area_id, "") in address
            ]
            resolved = parent_matches if len(parent_matches) == 1 else matches
            if len(resolved) == 1:
                area_id = resolved[0].eden_area_id
    if area_id is None:
        raise ValueError(f"unmapped {source_id} area code: {code}")
    area_map_cache[cache_key] = area_id
    return area_id


def _visitor_type(row: dict[str, Any]) -> str:
    label = (_text(row, "touDivNm") or "").replace(" ", "")
    if "외국" in label:
        return "foreign"
    if any(token in label for token in ("내국", "현지인", "외지인")):
        return "domestic"
    if any(token in label for token in ("전체", "합계", "총")):
        return "all"
    raise ValueError(f"unknown tourist division: {label or '<missing>'}")


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.0001"))


def _add_dead_letter(session: Session, raw: RawRecord, code: str, exc: Exception) -> None:
    existing = session.scalar(
        select(DeadLetter).where(
            DeadLetter.raw_record_id == raw.raw_record_id,
            DeadLetter.error_code == code,
            DeadLetter.reprocess_status.in_(("pending", "retrying")),
        )
    )
    detail = f"{type(exc).__name__}: {str(exc)[:1900]}"
    if existing is None:
        session.add(
            DeadLetter(
                raw_record_id=raw.raw_record_id,
                error_code=code,
                error_detail=detail,
                created_at=datetime.now(UTC).replace(tzinfo=None),
                reprocess_status="pending",
                reprocessed_at=None,
            )
        )
    else:
        existing.error_detail = detail
        existing.reprocess_status = "pending"
        existing.reprocessed_at = None


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
    dead_letter_count = (
        session.scalar(
            select(func.count())
            .select_from(DeadLetter)
            .join(RawRecord, RawRecord.raw_record_id == DeadLetter.raw_record_id)
            .where(
                RawRecord.run_id == run_id,
                DeadLetter.reprocess_status == "pending",
            )
        )
        or 0
    )
    run.normalized_count = count
    if dead_letter_count:
        run.status = RunStatus.PARTIAL if count else RunStatus.FAILED


def _public_rows(
    session: Session,
    raw: RawRecord,
    error_code: str,
) -> list[dict[str, Any]]:
    try:
        return _strict_public_data_items(_document(raw))
    except Exception as exc:
        _add_dead_letter(session, raw, error_code, exc)
        return []


def normalize_regional_visitors_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        groups: dict[tuple[str, str, datetime], _VisitorAggregate] = {}
        for raw in _run_records(session, run_id, REGIONAL_VISIT_SOURCE):
            for row in _public_rows(session, raw, "regional_visit_schema"):
                try:
                    area_id = _resolve_area_id(session, REGIONAL_VISIT_SOURCE, row)
                    period_start = _date(_text(row, "baseYmd", required=True) or "", ("%Y%m%d",))
                    visitor_type = _visitor_type(row)
                    count_value = _decimal(row, "touNum", required=True)
                    assert count_value is not None
                    if count_value < 0:
                        raise ValueError("visitor count cannot be negative")
                    rounded = int(count_value.to_integral_value(rounding=ROUND_HALF_UP))
                    key = (area_id, visitor_type, period_start)
                    aggregate = groups.get(key)
                    if aggregate is None:
                        aggregate = _VisitorAggregate(
                            values={
                                "area_id": area_id,
                                "subject_type": "area",
                                "subject_key": "area",
                                "visitor_type": visitor_type,
                                "grain": "day",
                                "period_start": period_start,
                                "visitor_count": 0,
                                "concentration_rate": None,
                                "completeness_ratio": Decimal("1"),
                                "observed_at": _database_time(raw.observed_at),
                                "source_updated_at": _database_time(raw.source_updated_at),
                                "ingested_at": _database_time(raw.ingested_at),
                                "calculated_at": calculated_at,
                                "source_id": REGIONAL_VISIT_SOURCE,
                                "availability": "available",
                                "quality_flags": [],
                            }
                        )
                        groups[key] = aggregate
                    aggregate.values["visitor_count"] += rounded
                    aggregate.values["observed_at"] = max(
                        aggregate.values["observed_at"],
                        _database_time(raw.observed_at),
                    )
                    aggregate.values["ingested_at"] = max(
                        aggregate.values["ingested_at"],
                        _database_time(raw.ingested_at),
                    )
                    aggregate.values["source_updated_at"] = max(
                        aggregate.values["source_updated_at"],
                        _database_time(raw.source_updated_at),
                    )
                    if count_value != rounded:
                        aggregate.values["quality_flags"] = ["source_estimate_rounded"]
                    aggregate.raw_record_ids.add(raw.raw_record_id)
                except Exception as exc:
                    _add_dead_letter(session, raw, "regional_visit_row_schema", exc)
            session.commit()
        for aggregate in groups.values():
            values = aggregate.values
            session.execute(
                insert(RegionalVisitObservation).values(**values).on_duplicate_key_update(**values)
            )
            observation_id = session.scalar(
                select(RegionalVisitObservation.observation_id).where(
                    RegionalVisitObservation.source_id == REGIONAL_VISIT_SOURCE,
                    RegionalVisitObservation.area_id == values["area_id"],
                    RegionalVisitObservation.subject_type == "area",
                    RegionalVisitObservation.subject_key == "area",
                    RegionalVisitObservation.visitor_type == values["visitor_type"],
                    RegionalVisitObservation.grain == "day",
                    RegionalVisitObservation.period_start == values["period_start"],
                )
            )
            if observation_id is None:
                raise RuntimeError("regional visitor observation was not resolved")
            _provenance(
                session,
                "regional_visit_observation",
                observation_id,
                aggregate.raw_record_ids,
            )
            normalized += 1
            if normalized % 500 == 0:
                session.commit()
        _finish_run(session, run_id, normalized)
        session.commit()
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
                value = _decimal(row, value_field, required=True)
                assert value is not None
                if value < 0:
                    raise ValueError("source index cannot be negative")
                area_id = _resolve_area_id(session, source_id, row)
                period = _date(_text(row, "baseYm", required=True) or "", ("%Y%m",))
                key = (area_id, period, value_field)
                aggregate = groups.setdefault(
                    key,
                    _Aggregate(
                        observed_at=_database_time(raw.observed_at),
                        source_updated_at=_database_time(raw.source_updated_at),
                        ingested_at=_database_time(raw.ingested_at),
                    ),
                )
                aggregate.values.append(value)
                aggregate.raw_record_ids.add(raw.raw_record_id)
                aggregate.observed_at = max(aggregate.observed_at, _database_time(raw.observed_at))
                aggregate.source_updated_at = max(
                    aggregate.source_updated_at,
                    _database_time(raw.source_updated_at),
                )
                aggregate.ingested_at = max(aggregate.ingested_at, _database_time(raw.ingested_at))
            except Exception as exc:
                _add_dead_letter(session, raw, f"{source_id.lower()}_row_schema", exc)
    return groups


def _national_month_level_scores(
    session: Session,
    groups: dict[tuple[str, datetime, str], _Aggregate],
) -> dict[tuple[str, datetime, str], Decimal | None]:
    """Normalize source-relative indices within month and spatial level."""
    area_ids = {area_id for area_id, _period, _field in groups}
    levels = dict(
        session.execute(
            select(Area.eden_area_id, Area.level).where(Area.eden_area_id.in_(area_ids))
        ).all()
    )
    averages = {key: _mean(aggregate.values) for key, aggregate in groups.items()}
    populations: dict[tuple[datetime, str, str], list[Decimal]] = defaultdict(list)
    for (area_id, period, field_name), value in averages.items():
        if value is not None and area_id in levels:
            populations[(period, field_name, levels[area_id])].append(value)
    scores: dict[tuple[str, datetime, str], Decimal | None] = {}
    for key, value in averages.items():
        area_id, period, field_name = key
        population = populations.get((period, field_name, levels.get(area_id, "")), [])
        if value is None or len(population) < 2:
            scores[key] = None
            continue
        low, high = min(population), max(population)
        scores[key] = (
            None
            if low == high
            else ((value - low) / (high - low) * Decimal("100")).quantize(Decimal("0.0001"))
        )
    return scores


def _diversity_dimensions(
    scores: dict[tuple[str, datetime, str], Decimal | None],
    area_id: str,
    period: datetime,
) -> tuple[None, Decimal | None]:
    """Map only dimensions whose official semantics match the public contract."""
    return None, scores.get((area_id, period, "intlDivIxVal"))


def normalize_regional_demand_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    normalized = 0
    with session_factory.begin() as session:
        groups = _aggregate_index_rows(
            session,
            _run_records(session, run_id, REGIONAL_DEMAND_SOURCE),
            REGIONAL_DEMAND_SOURCE,
            ("tarSjrnDsIxVal", "tarExpDsIxVal"),
        )
        scores = _national_month_level_scores(session, groups)
        combined: dict[tuple[str, datetime], dict[str, _Aggregate]] = defaultdict(dict)
        for (area_id, period, field_name), aggregate in groups.items():
            combined[(area_id, period)][field_name] = aggregate
        for (area_id, period), metrics in combined.items():
            audit = list(metrics.values())
            stay_index = scores.get((area_id, period, "tarSjrnDsIxVal"))
            spend_index = scores.get((area_id, period, "tarExpDsIxVal"))
            available_dimensions = sum(value is not None for value in (stay_index, spend_index))
            quality_flags = ["national_month_spatial_level_minmax_v1"]
            if stay_index is None:
                quality_flags.append("stay_index_unavailable")
            if spend_index is None:
                quality_flags.append("spend_index_unavailable")
            values = {
                "area_id": area_id,
                "period_start": period,
                "stay_index": stay_index,
                "spend_index": spend_index,
                "lodging_index": None,
                "avg_stay_nights": None,
                "observed_at": max(item.observed_at for item in audit),
                "source_updated_at": max(item.source_updated_at for item in audit),
                "ingested_at": max(item.ingested_at for item in audit),
                "calculated_at": calculated_at,
                "source_id": REGIONAL_DEMAND_SOURCE,
                "availability": (
                    "available"
                    if available_dimensions == 2
                    else "partial"
                    if available_dimensions
                    else "unavailable"
                ),
                "quality_flags": quality_flags,
            }
            session.execute(
                insert(RegionalDemandObservation).values(**values).on_duplicate_key_update(**values)
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
                "national_month_spatial_level_minmax_v1",
            )
            normalized += 1
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_regional_diversity_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    normalized = 0
    with session_factory.begin() as session:
        groups = _aggregate_index_rows(
            session,
            _run_records(session, run_id, REGIONAL_DIVERSITY_SOURCE),
            REGIONAL_DIVERSITY_SOURCE,
            ("touDivIxVal", "expDivIxVal", "intlDivIxVal"),
        )
        scores = _national_month_level_scores(session, groups)
        combined: dict[tuple[str, datetime], dict[str, _Aggregate]] = defaultdict(dict)
        for (area_id, period, field_name), aggregate in groups.items():
            combined[(area_id, period)][field_name] = aggregate
        for (area_id, period), metrics in combined.items():
            public_inputs = [
                metrics[field_name]
                for field_name in ("touDivIxVal", "intlDivIxVal")
                if field_name in metrics
            ]
            if not public_inputs:
                continue
            audit = list(metrics.values())
            age_index, nationality_index = _diversity_dimensions(scores, area_id, period)
            values = {
                "area_id": area_id,
                "period_start": period,
                # The official field is labelled "관광객 다양성". It does not
                # establish an age distribution, so never expose it as age_index.
                "age_index": age_index,
                "nationality_index": nationality_index,
                "observed_at": max(item.observed_at for item in audit),
                "source_updated_at": max(item.source_updated_at for item in audit),
                "ingested_at": max(item.ingested_at for item in audit),
                "calculated_at": calculated_at,
                "source_id": REGIONAL_DIVERSITY_SOURCE,
                "availability": ("partial" if nationality_index is not None else "unavailable"),
                "quality_flags": [
                    "national_month_spatial_level_minmax_v1",
                    "age_dimension_not_available",
                    "tourist_diversity_retained_as_raw_only",
                    "expenditure_diversity_retained_as_raw_only",
                    *([] if nationality_index is not None else ["nationality_index_unavailable"]),
                ],
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
                "national_month_spatial_level_minmax_v1",
            )
            normalized += 1
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_resource_demand_run(session_factory: sessionmaker[Session], run_id: str) -> int:
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
                        period = _date(_text(row, "baseYm", required=True) or "", ("%Y%m",))
                        source_score = _decimal(row, value_field, required=True)
                        assert source_score is not None
                        if source_score < 0:
                            raise ValueError("official demand index cannot be negative")
                        values = {
                            "raw_record_id": raw.raw_record_id,
                            "keyword": _text(row, name_field, code_field, required=True),
                            "country_id": None,
                            "area_id": _resolve_area_id(session, RESOURCE_DEMAND_SOURCE, row),
                            "bucket_start": period,
                            "bucket_grain": "month",
                            "post_count": None,
                            "view_count": None,
                            "reaction_count": None,
                            "search_ratio": None,
                            "source_score": source_score,
                            "observed_at": _database_time(raw.observed_at),
                            "source_updated_at": _database_time(raw.source_updated_at),
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
                            raise RuntimeError("resource demand observation was not resolved")
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
