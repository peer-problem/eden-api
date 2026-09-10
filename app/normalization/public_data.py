from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import RunStatus
from app.normalization.raw_content import decoded_raw_json
from app.repositories.models import (
    Area,
    AreaSourceMap,
    DeadLetter,
    IngestionRun,
    Place,
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
REGIONAL_VISIT_WRITE_BATCH_SIZE = 100
TOUR_AREA_SOURCES = frozenset(
    {"SRC_TOUR_KO", "SRC_TOUR_EN", "SRC_TOUR_JA", "SRC_TOUR_ZH_CN"}
)
_STORED_COORDINATE_QUANTUM = Decimal("0.0000001")

_REPLAY_RAW_RECORD_IDS: ContextVar[frozenset[int] | None] = ContextVar(
    "eden_replay_raw_record_ids",
    default=None,
)


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


def _area_code(row: dict[str, Any]) -> str | None:
    for name in ("signguCode", "signguCd"):
        value = _text(row, name)
        if value not in {None, "0", "_"}:
            return value
    legal_region = _text(row, "lDongRegnCd")
    legal_sigungu = _text(row, "lDongSignguCd")
    if legal_region and legal_sigungu:
        if (
            legal_region == legal_sigungu
            and len(legal_sigungu) == 5
            and legal_sigungu.isdigit()
        ):
            return legal_sigungu
        return f"{legal_region}{legal_sigungu.zfill(3)}"
    return _text(row, "areaCode", "areaCd", "areacode")


def _active_areas(session: Session) -> list[Any]:
    cached = session.info.get("eden_active_area_names")
    if cached is None:
        cached = session.execute(
            select(Area.eden_area_id, Area.name_ko, Area.parent_area_id).where(
                Area.active.is_(True)
            )
        ).all()
        session.info["eden_active_area_names"] = cached
    return cached


def _resolve_area_from_address(session: Session, address: str | None) -> str | None:
    if not address:
        return None
    areas = _active_areas(session)
    areas_by_id = {item.eden_area_id: item for item in areas}
    matches = [item for item in areas if item.name_ko and item.name_ko in address]
    if not matches:
        return None

    matched_names = {item.name_ko for item in matches}

    def lineage(item: Any) -> list[Any]:
        resolved = [item]
        seen = {item.eden_area_id}
        parent_id = item.parent_area_id
        while parent_id is not None:
            if parent_id in seen:
                return []
            seen.add(parent_id)
            parent = areas_by_id.get(parent_id)
            if parent is None:
                break
            resolved.append(parent)
            parent_id = parent.parent_area_id
        return resolved

    evidence = []
    for item in matches:
        candidate_lineage = lineage(item)
        lineage_names = {candidate.name_ko for candidate in candidate_lineage}
        if (
            candidate_lineage
            and item.parent_area_id is not None
            and matched_names.issubset(lineage_names)
        ):
            evidence.append(item)
    if len(evidence) != 1:
        return None
    return evidence[0].eden_area_id


def _tour_coordinates(row: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    try:
        lng = _decimal(row, "mapx")
        lat = _decimal(row, "mapy")
    except ValueError:
        return None
    if lng is None or lat is None:
        return None
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    try:
        return (
            lat.quantize(_STORED_COORDINATE_QUANTUM, rounding=ROUND_HALF_UP),
            lng.quantize(_STORED_COORDINATE_QUANTUM, rounding=ROUND_HALF_UP),
        )
    except InvalidOperation:
        return None


def _resolve_tour_area_from_coordinates(
    session: Session,
    source_id: str,
    row: dict[str, Any],
) -> str | None:
    if source_id not in TOUR_AREA_SOURCES:
        return None
    coordinates = _tour_coordinates(row)
    if coordinates is None:
        return None
    lat, lng = coordinates
    matches = session.execute(
        select(Place.area_id, Area.active)
        .join(Area, Area.eden_area_id == Place.area_id)
        .where(
            Place.lat == lat,
            Place.lng == lng,
            Place.merge_status == "active",
        )
    ).all()
    if not matches or any(not item.active for item in matches):
        return None
    area_ids = {item.area_id for item in matches}
    if len(area_ids) != 1:
        return None
    return next(iter(area_ids))


def _resolve_area_id(session: Session, source_id: str, row: dict[str, Any]) -> str:
    code = _area_code(row)
    area_map_cache = session.info.setdefault("eden_area_source_map", {})
    cache_key = (source_id, code) if code is not None else None
    if cache_key is not None:
        cached_area_id = area_map_cache.get(cache_key)
        if cached_area_id is not None:
            return cached_area_id
        area_id = session.scalar(
            select(AreaSourceMap.eden_area_id).where(
                AreaSourceMap.source_id == source_id,
                AreaSourceMap.external_area_code == code,
            )
        )
        if area_id is not None:
            area_map_cache[cache_key] = area_id
            return area_id
    else:
        area_id = None
    if area_id is None:
        address = _text(row, "addr1", "rdnmadr", "lnmadr")
        area_id = _resolve_area_from_address(session, address)
    if area_id is None:
        area_id = _resolve_tour_area_from_coordinates(session, source_id, row)
    if area_id is None:
        if code is None:
            raise ValueError(f"missing {source_id} area code and unresolvable address")
        raise ValueError(f"unmapped {source_id} area code: {code}")
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
    cache = session.info.setdefault("eden_dead_letter_by_raw_error", {})
    cache_key = (raw.raw_record_id, code)
    if cache_key not in cache:
        cache[cache_key] = session.scalar(
            select(DeadLetter)
            .where(
                DeadLetter.raw_record_id == raw.raw_record_id,
                DeadLetter.error_code == code,
            )
            .order_by(
                case(
                    (DeadLetter.reprocess_status == "retrying", 0),
                    (DeadLetter.reprocess_status == "pending", 1),
                    else_=2,
                ),
                DeadLetter.dead_letter_id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
    existing = cache[cache_key]
    detail = f"{type(exc).__name__}: {str(exc)[:1900]}"
    if existing is None:
        existing = DeadLetter(
            raw_record_id=raw.raw_record_id,
            error_code=code,
            error_detail=detail,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            reprocess_status="pending",
            reprocessed_at=None,
        )
        session.add(existing)
        cache[cache_key] = existing
    elif existing.reprocess_status in {"pending", "retrying"}:
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


@contextmanager
def _raw_record_replay_scope(raw_record_ids: Iterable[int]):
    token = _REPLAY_RAW_RECORD_IDS.set(frozenset(raw_record_ids))
    try:
        yield
    finally:
        _REPLAY_RAW_RECORD_IDS.reset(token)


def _is_raw_record_replay() -> bool:
    return _REPLAY_RAW_RECORD_IDS.get() is not None


def _run_records(session: Session, run_id: str, source_id: str) -> list[RawRecord]:
    statement = select(RawRecord).where(
        RawRecord.run_id == run_id,
        RawRecord.source_id == source_id,
    )
    replay_raw_ids = _REPLAY_RAW_RECORD_IDS.get()
    if replay_raw_ids is not None:
        statement = statement.where(RawRecord.raw_record_id.in_(replay_raw_ids))
    return list(
        session.scalars(
            statement.order_by(RawRecord.raw_record_id)
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
    replay_raw_ids = _REPLAY_RAW_RECORD_IDS.get()
    if replay_raw_ids is None:
        run.normalized_count = count
    if replay_raw_ids is None and dead_letter_count:
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
        normalized = _write_regional_visit_groups(session, list(groups.values()))
        _finish_run(session, run_id, normalized)
        session.commit()
    return normalized


def _write_regional_visit_groups(
    session: Session,
    groups: list[_VisitorAggregate],
) -> int:
    normalized = 0
    for offset in range(0, len(groups), REGIONAL_VISIT_WRITE_BATCH_SIZE):
        batch = groups[offset : offset + REGIONAL_VISIT_WRITE_BATCH_SIZE]
        _write_regional_visit_batch(session, batch)
        normalized += len(batch)
        if normalized % 500 == 0:
            session.commit()
    return normalized


def _write_regional_visit_batch(
    session: Session,
    groups: list[_VisitorAggregate],
) -> None:
    if not groups:
        return
    if len(groups) > REGIONAL_VISIT_WRITE_BATCH_SIZE:
        raise ValueError(
            f"regional visitor write batch exceeds {REGIONAL_VISIT_WRITE_BATCH_SIZE} rows"
        )

    rows = [group.values for group in groups]
    upsert = insert(RegionalVisitObservation).values(rows)
    incoming_is_newest = (
        upsert.inserted.source_updated_at
        > RegionalVisitObservation.source_updated_at
    ) | (
        (
            upsert.inserted.source_updated_at
            == RegionalVisitObservation.source_updated_at
        )
        & (upsert.inserted.ingested_at >= RegionalVisitObservation.ingested_at)
    )
    update_fields = (
        "visitor_count",
        "concentration_rate",
        "completeness_ratio",
        "observed_at",
        "calculated_at",
        "availability",
        "quality_flags",
        "ingested_at",
        "source_updated_at",
    )
    update_values = [
        (
            name,
            case(
                (incoming_is_newest, upsert.inserted[name]),
                else_=getattr(RegionalVisitObservation, name),
            ),
        )
        for name in update_fields
    ]
    session.execute(upsert.on_duplicate_key_update(update_values))

    key_fields = (
        "source_id",
        "area_id",
        "subject_type",
        "subject_key",
        "visitor_type",
        "grain",
        "period_start",
    )
    keys = [tuple(row[name] for name in key_fields) for row in rows]
    resolved = session.execute(
        select(
            RegionalVisitObservation.source_id,
            RegionalVisitObservation.area_id,
            RegionalVisitObservation.subject_type,
            RegionalVisitObservation.subject_key,
            RegionalVisitObservation.visitor_type,
            RegionalVisitObservation.grain,
            RegionalVisitObservation.period_start,
            RegionalVisitObservation.observation_id,
        ).where(
            tuple_(
                RegionalVisitObservation.source_id,
                RegionalVisitObservation.area_id,
                RegionalVisitObservation.subject_type,
                RegionalVisitObservation.subject_key,
                RegionalVisitObservation.visitor_type,
                RegionalVisitObservation.grain,
                RegionalVisitObservation.period_start,
            ).in_(keys)
        )
    ).all()
    observation_ids = {
        tuple(result[:7]): result[7]
        for result in resolved
    }

    provenance_rows: list[dict[str, Any]] = []
    created_at = datetime.now(UTC).replace(tzinfo=None)
    for aggregate, key in zip(groups, keys, strict=True):
        observation_id = observation_ids.get(key)
        if observation_id is None:
            raise RuntimeError("regional visitor observation was not resolved")
        provenance_rows.extend(
            {
                "output_type": "regional_visit_observation",
                "output_id": str(observation_id),
                "raw_record_id": raw_record_id,
                "formula_version": "identity_v1",
                "created_at": created_at,
            }
            for raw_record_id in sorted(aggregate.raw_record_ids)
        )

    for offset in range(0, len(provenance_rows), REGIONAL_VISIT_WRITE_BATCH_SIZE):
        edge_batch = provenance_rows[offset : offset + REGIONAL_VISIT_WRITE_BATCH_SIZE]
        edge_upsert = insert(ProvenanceEdge).values(edge_batch)
        session.execute(
            edge_upsert.on_duplicate_key_update(
                provenance_id=ProvenanceEdge.provenance_id
            )
        )


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
