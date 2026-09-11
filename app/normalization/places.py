from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import String, and_, cast, func, select, tuple_
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.ids import stable_eden_id
from app.normalization.public_data import (
    _add_dead_letter,
    _database_time,
    _date,
    _decimal,
    _document,
    _finish_run,
    _is_raw_record_replay,
    _provenance,
    _resolve_area_id,
    _run_records,
    _strict_public_data_items,
    _text,
)
from app.repositories.models import (
    Area,
    NearbyShop,
    Place,
    PlaceLocalization,
    PlaceRelation,
    PlaceSourceMap,
    ProvenanceEdge,
    RawRecord,
)

TOUR_LANGUAGES = {
    "SRC_TOUR_KO": "ko",
    "SRC_TOUR_EN": "en",
    "SRC_TOUR_JA": "ja",
    "SRC_TOUR_ZH_CN": "zh-CN",
}
HUB_SOURCE = "SRC_KTO_PLACE_HUB"
RELATED_SOURCE = "SRC_KTO_PLACE_RELATED"
SHOP_SOURCE = "SRC_SEMAS_SHOPS"
SHOP_WRITE_BATCH_SIZE = 100
TOUR_REPLAY_LOOKUP_BATCH_SIZE = 1000
TOUR_REPLAY_PROVENANCE_BATCH_SIZE = 100


def _coordinates(row: dict[str, Any], x: str, y: str) -> tuple[Decimal | None, Decimal | None]:
    if (x, y) == ("mapx", "mapy"):
        row = {
            **row,
            **{
                key: None
                for key in (x, y)
                if isinstance(row.get(key), str) and row[key].strip().lower() == "null"
            },
        }
    lng = _decimal(row, x)
    lat = _decimal(row, y)
    if lng is None or lat is None:
        return None, None
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError("place coordinates are outside WGS84 bounds")
    return lat, lng


def _positive_rank(row: dict[str, Any], *names: str) -> int:
    value = _decimal(row, *names, required=True)
    assert value is not None
    if value < 1 or value != value.to_integral_value():
        raise ValueError(f"positive integer rank required: {'/'.join(names)}")
    return int(value)


def _existing_place_id(
    session: Session,
    source_id: str,
    external_id: str,
    lat: Decimal | None,
    lng: Decimal | None,
    namespace: str,
) -> str:
    source_map_cache = session.info.setdefault("eden_place_source_map", {})
    source_key = (source_id, external_id)
    if source_key not in source_map_cache:
        source_map_cache[source_key] = session.scalar(
            select(PlaceSourceMap.eden_place_id).where(
                PlaceSourceMap.source_id == source_id,
                PlaceSourceMap.external_content_id == external_id,
            )
        )
    place_id = source_map_cache[source_key]
    if place_id is not None:
        return place_id
    if source_id in TOUR_LANGUAGES:
        tour_cache = session.info.setdefault("eden_tour_content_map", {})
        if external_id not in tour_cache:
            tour_cache[external_id] = session.scalar(
                select(PlaceSourceMap.eden_place_id)
                .where(
                    PlaceSourceMap.source_id.in_(tuple(TOUR_LANGUAGES)),
                    PlaceSourceMap.external_content_id == external_id,
                )
                .limit(1)
            )
        place_id = tour_cache[external_id]
        if place_id is not None:
            return place_id
    # Coordinates alone cannot establish facility identity.
    return stable_eden_id("place", namespace, external_id)


def _upsert_place(
    session: Session,
    raw: RawRecord,
    source_id: str,
    external_id: str,
    area_id: str,
    title: str,
    language: str,
    category: str | None,
    lat: Decimal | None,
    lng: Decimal | None,
    address: str | None,
    overview: str | None,
    namespace: str,
) -> str:
    now = datetime.now(UTC).replace(tzinfo=None)
    place_id = _existing_place_id(session, source_id, external_id, lat, lng, namespace)
    existing_place = session.get(Place, place_id)
    incoming_area = session.get(Area, area_id)
    if existing_place is not None and incoming_area is not None and not incoming_area.active:
        current_area = session.get(Area, existing_place.area_id)
        if current_area is not None and current_area.active:
            # An unchanged source identity may still carry a retired area code.
            # Keep its already verified active assignment across source refreshes.
            area_id = existing_place.area_id
    source_updated_at = getattr(raw, "source_updated_at", None)
    incoming_timestamp = (
        _database_time(source_updated_at) if source_updated_at is not None else None
    )
    place_values = {
        "eden_place_id": place_id,
        "area_id": area_id,
        "category": category,
        "lat": lat,
        "lng": lng,
        "merge_status": "active",
        "created_at": now,
        "updated_at": now,
    }
    place_is_current = _output_accepts_raw(
        session,
        "place",
        place_id,
        incoming_timestamp,
    )
    if place_is_current:
        session.execute(
            insert(Place)
            .values(**place_values)
            .on_duplicate_key_update(
                area_id=area_id,
                category=category,
                lat=lat,
                lng=lng,
                merge_status="active",
                updated_at=now,
            )
        )
    session.execute(
        insert(PlaceSourceMap)
        .values(
            source_id=source_id,
            external_content_id=external_id,
            eden_place_id=place_id,
            created_at=now,
            updated_at=now,
        )
        .on_duplicate_key_update(eden_place_id=place_id, updated_at=now)
    )
    localization_id = _existing_localization_id(session, place_id, language)
    localization_is_current = localization_id is None or _output_accepts_raw(
        session,
        "place_localization",
        str(localization_id),
        incoming_timestamp,
    )
    if localization_is_current:
        session.execute(
            insert(PlaceLocalization)
            .values(
                eden_place_id=place_id,
                language=language,
                title=title[:500],
                address=address[:1000] if address else None,
                overview=overview,
                is_fallback=False,
                created_at=now,
                updated_at=now,
            )
            .on_duplicate_key_update(
                title=title[:500],
                address=address[:1000] if address else None,
                overview=overview,
                is_fallback=False,
                updated_at=now,
            )
        )
    if localization_id is None:
        localization_id = session.scalar(
            select(PlaceLocalization.id).where(
                PlaceLocalization.eden_place_id == place_id,
                PlaceLocalization.language == language,
            )
        )
        session.info.setdefault("eden_place_localization", {})[(place_id, language)] = (
            localization_id
        )
    if localization_id is None:
        raise RuntimeError("place localization was not resolved")
    _place_provenance(session, place_id, localization_id, raw.raw_record_id)
    session.info.setdefault("eden_place_source_map", {})[(source_id, external_id)] = place_id
    if source_id in TOUR_LANGUAGES:
        session.info.setdefault("eden_tour_content_map", {})[external_id] = place_id
    if place_is_current and lat is not None and lng is not None:
        session.info.setdefault("eden_place_coordinate_map", {})[(lat, lng)] = place_id
    return place_id


def _existing_localization_id(
    session: Session,
    place_id: str,
    language: str,
) -> int | None:
    cache = session.info.setdefault("eden_place_localization", {})
    cache_key = (place_id, language)
    if cache_key not in cache:
        cache[cache_key] = session.scalar(
            select(PlaceLocalization.id).where(
                PlaceLocalization.eden_place_id == place_id,
                PlaceLocalization.language == language,
            )
        )
    return cache[cache_key]


def _output_accepts_raw(
    session: Session,
    output_type: str,
    output_id: str,
    incoming_timestamp: datetime | None,
) -> bool:
    if incoming_timestamp is None:
        return True
    latest_timestamp = session.scalar(
        select(func.max(RawRecord.source_updated_at))
        .select_from(ProvenanceEdge)
        .join(RawRecord, RawRecord.raw_record_id == ProvenanceEdge.raw_record_id)
        .where(
            ProvenanceEdge.output_type == output_type,
            ProvenanceEdge.output_id == output_id,
            ProvenanceEdge.formula_version == "place_identity_v1",
        )
    )
    return latest_timestamp is None or latest_timestamp <= incoming_timestamp


def _place_provenance(
    session: Session, place_id: str, localization_id: int, raw_record_id: int
) -> None:
    from app.repositories.models import ProvenanceEdge

    now = datetime.now(UTC).replace(tzinfo=None)
    for output_type, output_id in (
        ("place", place_id),
        ("place_localization", str(localization_id)),
    ):
        session.execute(
            insert(ProvenanceEdge)
            .values(
                output_type=output_type,
                output_id=output_id,
                raw_record_id=raw_record_id,
                formula_version="place_identity_v1",
                created_at=now,
            )
            .on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id)
        )


def _successfully_provenanced_tour_content_ids(
    session: Session,
    source_id: str,
    raw_record_id: int,
) -> set[str]:
    rows = session.execute(
        select(
            PlaceSourceMap.eden_place_id,
            PlaceSourceMap.external_content_id,
        )
        .join(
            ProvenanceEdge,
            and_(
                ProvenanceEdge.output_id == PlaceSourceMap.eden_place_id,
                ProvenanceEdge.output_type == "place",
                ProvenanceEdge.formula_version == "place_identity_v1",
                ProvenanceEdge.raw_record_id == raw_record_id,
            ),
        )
        .where(PlaceSourceMap.source_id == source_id)
    ).all()
    content_ids_by_place: dict[str, set[str]] = {}
    for place_id, external_content_id in rows:
        content_ids_by_place.setdefault(place_id, set()).add(external_content_id)
    return {
        next(iter(content_ids))
        for content_ids in content_ids_by_place.values()
        if len(content_ids) == 1
    }


def _stale_tour_replay_outputs(
    session: Session,
    source_id: str,
    language: str,
    content_ids: set[str],
    source_updated_at: datetime | None,
) -> dict[str, tuple[str, str]]:
    if not content_ids or source_updated_at is None:
        return {}
    incoming_timestamp = _database_time(source_updated_at)
    canonical_latest = (
        select(func.max(RawRecord.source_updated_at))
        .select_from(ProvenanceEdge)
        .join(RawRecord, RawRecord.raw_record_id == ProvenanceEdge.raw_record_id)
        .where(
            ProvenanceEdge.output_type == "place",
            ProvenanceEdge.output_id == PlaceSourceMap.eden_place_id,
            ProvenanceEdge.formula_version == "place_identity_v1",
        )
        .correlate(PlaceSourceMap)
        .scalar_subquery()
    )
    localization_latest = (
        select(func.max(RawRecord.source_updated_at))
        .select_from(ProvenanceEdge)
        .join(RawRecord, RawRecord.raw_record_id == ProvenanceEdge.raw_record_id)
        .where(
            ProvenanceEdge.output_type == "place_localization",
            ProvenanceEdge.output_id == cast(PlaceLocalization.id, String(128)),
            ProvenanceEdge.formula_version == "place_identity_v1",
        )
        .correlate(PlaceLocalization)
        .scalar_subquery()
    )
    resolved: dict[str, tuple[str, str]] = {}
    ordered_ids = sorted(content_ids)
    for offset in range(0, len(ordered_ids), TOUR_REPLAY_LOOKUP_BATCH_SIZE):
        batch = ordered_ids[offset : offset + TOUR_REPLAY_LOOKUP_BATCH_SIZE]
        rows = session.execute(
            select(
                PlaceSourceMap.external_content_id,
                PlaceSourceMap.eden_place_id,
                PlaceLocalization.id,
                canonical_latest.label("canonical_latest"),
                localization_latest.label("localization_latest"),
            )
            .join(Place, Place.eden_place_id == PlaceSourceMap.eden_place_id)
            .join(
                PlaceLocalization,
                and_(
                    PlaceLocalization.eden_place_id == PlaceSourceMap.eden_place_id,
                    PlaceLocalization.language == language,
                ),
            )
            .where(
                PlaceSourceMap.source_id == source_id,
                PlaceSourceMap.external_content_id.in_(batch),
            )
        ).all()
        for content_id, place_id, localization_id, place_latest, locale_latest in rows:
            if (
                place_latest is not None
                and locale_latest is not None
                and place_latest > incoming_timestamp
                and locale_latest > incoming_timestamp
            ):
                resolved[content_id] = (place_id, str(localization_id))
    return resolved


def _write_tour_replay_provenance(
    session: Session,
    raw_record_id: int,
    outputs: set[tuple[str, str]],
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        {
            "output_type": output_type,
            "output_id": output_id,
            "raw_record_id": raw_record_id,
            "formula_version": "place_identity_v1",
            "created_at": now,
        }
        for output_type, output_id in sorted(outputs)
    ]
    for offset in range(0, len(rows), TOUR_REPLAY_PROVENANCE_BATCH_SIZE):
        batch = rows[offset : offset + TOUR_REPLAY_PROVENANCE_BATCH_SIZE]
        upsert = insert(ProvenanceEdge).values(batch)
        session.execute(upsert.on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id))


def normalize_tour_catalog_run(
    source_id: str,
    session_factory: sessionmaker[Session],
    run_id: str,
) -> int:
    language = TOUR_LANGUAGES[source_id]
    normalized = 0
    with session_factory() as session:
        for raw in _run_records(session, run_id, source_id):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "tour_catalog_schema", exc)
                session.commit()
                continue
            successfully_provenanced_ids = (
                _successfully_provenanced_tour_content_ids(
                    session,
                    source_id,
                    raw.raw_record_id,
                )
                if _is_raw_record_replay()
                else set()
            )
            stale_replay_outputs = (
                _stale_tour_replay_outputs(
                    session,
                    source_id,
                    language,
                    {
                        content_id
                        for row in rows
                        if (content_id := _text(row, "contentid")) is not None
                        and content_id not in successfully_provenanced_ids
                    }
                    - successfully_provenanced_ids,
                    getattr(raw, "source_updated_at", None),
                )
                if _is_raw_record_replay()
                else {}
            )
            replay_provenance: set[tuple[str, str]] = set()
            for row in rows:
                try:
                    external_id = _text(row, "contentid", required=True) or ""
                    title = _text(row, "title", required=True) or ""
                    lat, lng = _coordinates(row, "mapx", "mapy")
                    address = (
                        " ".join(
                            value
                            for value in (_text(row, "addr1"), _text(row, "addr2"))
                            if value
                        )
                        or None
                    )
                    area_id = _resolve_area_id(session, source_id, row)
                    category = _text(row, "lclsSystm1", "cat1")
                    overview = _text(row, "overview")
                    # Validate every row, but reserve savepoints for actual writes.
                    if external_id in successfully_provenanced_ids:
                        normalized += 1
                        continue
                    if stale_outputs := stale_replay_outputs.get(external_id):
                        place_id, localization_id = stale_outputs
                        replay_provenance.update(
                            {
                                ("place", place_id),
                                ("place_localization", localization_id),
                            }
                        )
                        normalized += 1
                        continue
                    with session.begin_nested():
                        _upsert_place(
                            session,
                            raw,
                            source_id,
                            external_id,
                            area_id,
                            title,
                            language,
                            category,
                            lat,
                            lng,
                            address,
                            overview,
                            "KTO_CONTENT",
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "tour_catalog_row_schema", exc)
            _write_tour_replay_provenance(
                session,
                raw.raw_record_id,
                replay_provenance,
            )
            session.commit()
        _finish_run(session, run_id, normalized)
        session.commit()
    return normalized


def _hub_period(row: dict[str, Any]) -> datetime:
    return _date(_text(row, "baseYm", required=True) or "", ("%Y%m",))


def normalize_hub_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        for raw in _run_records(session, run_id, HUB_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "hub_place_schema", exc)
                session.commit()
                continue
            for row in rows:
                try:
                    with session.begin_nested():
                        external_id = _text(row, "hubTatsCd", required=True) or ""
                        title = _text(row, "hubTatsNm", required=True) or ""
                        lat, lng = _coordinates(row, "mapX", "mapY")
                        place_id = _upsert_place(
                            session,
                            raw,
                            HUB_SOURCE,
                            external_id,
                            _resolve_area_id(session, HUB_SOURCE, row),
                            title,
                            "ko",
                            _text(row, "hubCtgryLclsNm"),
                            lat,
                            lng,
                            None,
                            None,
                            "KTO_TATS",
                        )
                        period = _hub_period(row)
                        values = {
                            "from_place_id": place_id,
                            "to_place_id": place_id,
                            "relation_type": "hub",
                            "rank": _positive_rank(row, "hubRank"),
                            "score": None,
                            "observed_at": period,
                            "source_updated_at": _database_time(raw.source_updated_at),
                            "ingested_at": _database_time(raw.ingested_at),
                            "calculated_at": calculated_at,
                            "source_id": HUB_SOURCE,
                            "availability": "available",
                            "quality_flags": [],
                        }
                        session.execute(
                            insert(PlaceRelation).values(**values).on_duplicate_key_update(**values)
                        )
                        relation_id = session.scalar(
                            select(PlaceRelation.relation_id).where(
                                PlaceRelation.from_place_id == place_id,
                                PlaceRelation.to_place_id == place_id,
                                PlaceRelation.relation_type == "hub",
                                PlaceRelation.observed_at == period,
                            )
                        )
                        if relation_id is None:
                            raise RuntimeError("hub relation was not resolved")
                        _provenance(
                            session,
                            "place_relation",
                            relation_id,
                            (raw.raw_record_id,),
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "hub_place_row_schema", exc)
            session.commit()
        _finish_run(session, run_id, normalized)
        session.commit()
    return normalized


def normalize_related_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        for raw in _run_records(session, run_id, RELATED_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "related_place_schema", exc)
                session.commit()
                continue
            for row in rows:
                try:
                    with session.begin_nested():
                        source_area = _resolve_area_id(session, RELATED_SOURCE, row)
                        from_id = _upsert_place(
                            session,
                            raw,
                            RELATED_SOURCE,
                            _text(row, "tAtsCd", required=True) or "",
                            source_area,
                            _text(row, "tAtsNm", required=True) or "",
                            "ko",
                            None,
                            None,
                            None,
                            None,
                            None,
                            "KTO_TATS",
                        )
                        related_row = dict(row)
                        if row.get("rlteSignguCd"):
                            related_row["signguCd"] = row["rlteSignguCd"]
                        to_id = _upsert_place(
                            session,
                            raw,
                            RELATED_SOURCE,
                            _text(row, "rlteTatsCd", required=True) or "",
                            _resolve_area_id(session, RELATED_SOURCE, related_row),
                            _text(row, "rlteTatsNm", required=True) or "",
                            "ko",
                            _text(row, "rlteCtgryLclsNm"),
                            None,
                            None,
                            None,
                            None,
                            "KTO_TATS",
                        )
                        rank = _positive_rank(row, "rlteRank")
                        period = _hub_period(row)
                        score = (Decimal("100") / Decimal(rank)).quantize(Decimal("0.0001"))
                        values = {
                            "from_place_id": from_id,
                            "to_place_id": to_id,
                            "relation_type": "related",
                            "rank": rank,
                            "score": score,
                            "observed_at": period,
                            "source_updated_at": _database_time(raw.source_updated_at),
                            "ingested_at": _database_time(raw.ingested_at),
                            "calculated_at": calculated_at,
                            "source_id": RELATED_SOURCE,
                            "availability": "available",
                            "quality_flags": ["score_derived_from_rank"],
                        }
                        session.execute(
                            insert(PlaceRelation).values(**values).on_duplicate_key_update(**values)
                        )
                        relation_id = session.scalar(
                            select(PlaceRelation.relation_id).where(
                                PlaceRelation.from_place_id == from_id,
                                PlaceRelation.to_place_id == to_id,
                                PlaceRelation.relation_type == "related",
                                PlaceRelation.observed_at == period,
                            )
                        )
                        if relation_id is None:
                            raise RuntimeError("related-place relation was not resolved")
                        _provenance(
                            session,
                            "place_relation",
                            relation_id,
                            (raw.raw_record_id,),
                            "inverse_rank_v1",
                        )
                    normalized += 1
                except Exception as exc:
                    _add_dead_letter(session, raw, "related_place_row_schema", exc)
            session.commit()
        _finish_run(session, run_id, normalized)
        session.commit()
    return normalized


def normalize_shop_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        groups: dict[tuple[str, datetime], dict[str, Any]] = {}
        for raw in _run_records(session, run_id, SHOP_SOURCE):
            try:
                rows = _strict_public_data_items(_document(raw))
            except Exception as exc:
                _add_dead_letter(session, raw, "nearby_shop_schema", exc)
                session.commit()
                continue
            for row in rows:
                try:
                    external_id = _text(row, "bizesId", required=True) or ""
                    lat, lng = _coordinates(row, "lon", "lat")
                    if lat is None or lng is None:
                        raise ValueError("shop coordinates are required")
                    source_period = _text(row, "stdrYm")
                    observed = (
                        _date(source_period, ("%Y%m", "%Y%m%d"))
                        if source_period
                        else _database_time(raw.source_updated_at).replace(
                            day=1, hour=0, minute=0, second=0, microsecond=0
                        )
                    )
                    area_id = _resolve_area_id(session, SHOP_SOURCE, row)
                    key = (external_id, observed)
                    group = groups.setdefault(key, {"raw_record_ids": set()})
                    previous = group.get("values")
                    values = {
                        "eden_shop_id": stable_eden_id("shop", "SEMAS", external_id),
                        "external_shop_id": external_id,
                        "place_id": None,
                        "area_id": area_id,
                        "name": _text(row, "bizesNm", required=True),
                        "category": _text(
                            row,
                            "indsSclsNm",
                            "indsMclsNm",
                            "indsLclsNm",
                            required=True,
                        ),
                        "lat": lat,
                        "lng": lng,
                        "distance_m": None,
                        "observed_at": observed,
                        "source_updated_at": _database_time(raw.source_updated_at),
                        "ingested_at": _database_time(raw.ingested_at),
                        "calculated_at": calculated_at,
                        "source_id": SHOP_SOURCE,
                        "availability": "available",
                        "quality_flags": (
                            []
                            if source_period
                            else ["source_period_inferred_from_collection_month"]
                        ),
                    }
                    if previous is not None:
                        for timestamp_name in (
                            "observed_at",
                            "source_updated_at",
                            "ingested_at",
                        ):
                            values[timestamp_name] = max(
                                values[timestamp_name], previous[timestamp_name]
                            )
                        values["quality_flags"] = sorted(
                            {
                                *values["quality_flags"],
                                *previous["quality_flags"],
                            }
                        )
                    group["values"] = values
                    group["raw_record_ids"].add(raw.raw_record_id)
                except Exception as exc:
                    _add_dead_letter(session, raw, "nearby_shop_row_schema", exc)
            session.commit()
        normalized = _write_shop_groups(session, list(groups.values()))
        _finish_run(session, run_id, normalized)
        session.commit()
    return normalized


def _write_shop_groups(session: Session, groups: list[dict[str, Any]]) -> int:
    normalized = 0
    for offset in range(0, len(groups), SHOP_WRITE_BATCH_SIZE):
        batch = groups[offset : offset + SHOP_WRITE_BATCH_SIZE]
        _write_shop_batch(session, batch)
        normalized += len(batch)
        if normalized % 500 == 0:
            session.commit()
    return normalized


def _write_shop_batch(session: Session, groups: list[dict[str, Any]]) -> None:
    if not groups:
        return
    if len(groups) > SHOP_WRITE_BATCH_SIZE:
        raise ValueError(f"shop write batch exceeds {SHOP_WRITE_BATCH_SIZE} rows")

    rows = [group["values"] for group in groups]
    upsert = insert(NearbyShop).values(rows)
    incoming_is_current = NearbyShop.source_updated_at <= upsert.inserted.source_updated_at
    update_values = [
        (
            name,
            func.if_(
                incoming_is_current,
                upsert.inserted[name],
                NearbyShop.__table__.c[name],
            ),
        )
        for name in rows[0]
        if name != "source_updated_at"
    ]
    # MariaDB evaluates assignments from left to right. Keep this assignment last so
    # every condition above compares against the previously stored source timestamp.
    update_values.append(
        (
            "source_updated_at",
            func.if_(
                incoming_is_current,
                upsert.inserted.source_updated_at,
                NearbyShop.source_updated_at,
            ),
        )
    )
    session.execute(upsert.on_duplicate_key_update(update_values))

    keys = [(row["external_shop_id"], row["observed_at"]) for row in rows]
    resolved = session.execute(
        select(
            NearbyShop.external_shop_id,
            NearbyShop.observed_at,
            NearbyShop.id,
        ).where(
            NearbyShop.source_id == SHOP_SOURCE,
            tuple_(NearbyShop.external_shop_id, NearbyShop.observed_at).in_(keys),
        )
    ).all()
    shop_ids = {
        (external_id, observed_at): shop_id for external_id, observed_at, shop_id in resolved
    }

    provenance_rows: list[dict[str, Any]] = []
    for group, row in zip(groups, rows, strict=True):
        key = (row["external_shop_id"], row["observed_at"])
        shop_id = shop_ids.get(key)
        if shop_id is None:
            raise RuntimeError("nearby shop was not resolved")
        created_at = datetime.now(UTC).replace(tzinfo=None)
        provenance_rows.extend(
            {
                "output_type": "nearby_shop",
                "output_id": str(shop_id),
                "raw_record_id": raw_record_id,
                "formula_version": "identity_v1",
                "created_at": created_at,
            }
            for raw_record_id in sorted(set(group["raw_record_ids"]))
        )

    for offset in range(0, len(provenance_rows), SHOP_WRITE_BATCH_SIZE):
        edge_batch = provenance_rows[offset : offset + SHOP_WRITE_BATCH_SIZE]
        edge_upsert = insert(ProvenanceEdge).values(edge_batch)
        session.execute(
            edge_upsert.on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id)
        )
