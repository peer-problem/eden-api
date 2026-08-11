from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
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
    _provenance,
    _resolve_area_id,
    _run_records,
    _text,
)
from app.repositories.models import (
    NearbyShop,
    Place,
    PlaceLocalization,
    PlaceRelation,
    PlaceSourceMap,
    RawRecord,
)
from app.sources.public_data import public_data_items

TOUR_LANGUAGES = {
    "SRC_TOUR_KO": "ko",
    "SRC_TOUR_EN": "en",
    "SRC_TOUR_JA": "ja",
    "SRC_TOUR_ZH_CN": "zh-CN",
}
HUB_SOURCE = "SRC_KTO_PLACE_HUB"
RELATED_SOURCE = "SRC_KTO_PLACE_RELATED"
SHOP_SOURCE = "SRC_SEMAS_SHOPS"


def _coordinates(row: dict[str, Any], x: str, y: str) -> tuple[Decimal | None, Decimal | None]:
    lng = _decimal(row, x)
    lat = _decimal(row, y)
    if lng is None or lat is None:
        return None, None
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError("place coordinates are outside WGS84 bounds")
    return lat, lng


def _existing_place_id(
    session: Session,
    source_id: str,
    external_id: str,
    lat: Decimal | None,
    lng: Decimal | None,
    namespace: str,
) -> str:
    place_id = session.scalar(
        select(PlaceSourceMap.eden_place_id).where(
            PlaceSourceMap.source_id == source_id,
            PlaceSourceMap.external_content_id == external_id,
        )
    )
    if place_id is not None:
        return place_id
    if source_id in TOUR_LANGUAGES:
        place_id = session.scalar(
            select(PlaceSourceMap.eden_place_id)
            .where(
                PlaceSourceMap.source_id.in_(tuple(TOUR_LANGUAGES)),
                PlaceSourceMap.external_content_id == external_id,
            )
            .limit(1)
        )
        if place_id is not None:
            return place_id
    if lat is not None and lng is not None:
        place_id = session.scalar(
            select(Place.eden_place_id)
            .where(Place.lat == lat, Place.lng == lng, Place.merge_status == "active")
            .order_by(Place.eden_place_id)
            .limit(1)
        )
        if place_id is not None:
            return place_id
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
    place_id = _existing_place_id(
        session, source_id, external_id, lat, lng, namespace
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
    localization_id = session.scalar(
        select(PlaceLocalization.id).where(
            PlaceLocalization.eden_place_id == place_id,
            PlaceLocalization.language == language,
        )
    )
    if localization_id is None:
        raise RuntimeError("place localization was not resolved")
    _place_provenance(session, place_id, localization_id, raw.raw_record_id)
    return place_id


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


def normalize_tour_catalog_run(
    source_id: str,
    session_factory: sessionmaker[Session],
    run_id: str,
) -> int:
    language = TOUR_LANGUAGES[source_id]
    normalized = 0
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, source_id):
            try:
                for row in public_data_items(_document(raw)):
                    external_id = _text(row, "contentid", required=True) or ""
                    title = _text(row, "title", required=True) or ""
                    lat, lng = _coordinates(row, "mapx", "mapy")
                    address = " ".join(
                        value
                        for value in (_text(row, "addr1"), _text(row, "addr2"))
                        if value
                    ) or None
                    _upsert_place(
                        session,
                        raw,
                        source_id,
                        external_id,
                        _resolve_area_id(session, source_id, row),
                        title,
                        language,
                        _text(row, "lclsSystm1", "cat1"),
                        lat,
                        lng,
                        address,
                        _text(row, "overview"),
                        "KTO_CONTENT",
                    )
                    normalized += 1
            except Exception as exc:
                _add_dead_letter(session, raw, "tour_catalog_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def _hub_period(row: dict[str, Any]) -> datetime:
    return _date(_text(row, "baseYm", required=True) or "", ("%Y%m",))


def normalize_hub_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, HUB_SOURCE):
            try:
                for row in public_data_items(_document(raw)):
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
                        "rank": int(_decimal(row, "hubRank", required=True) or 0),
                        "score": None,
                        "observed_at": period,
                        "source_updated_at": period,
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
                _add_dead_letter(session, raw, "hub_place_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_related_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, RELATED_SOURCE):
            try:
                for row in public_data_items(_document(raw)):
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
                    rank = int(_decimal(row, "rlteRank", required=True) or 0)
                    if rank < 1:
                        raise ValueError("related-place rank must be positive")
                    period = _hub_period(row)
                    score = (Decimal("100") / Decimal(rank)).quantize(Decimal("0.0001"))
                    values = {
                        "from_place_id": from_id,
                        "to_place_id": to_id,
                        "relation_type": "related",
                        "rank": rank,
                        "score": score,
                        "observed_at": period,
                        "source_updated_at": period,
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
                _add_dead_letter(session, raw, "related_place_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized


def normalize_shop_run(session_factory: sessionmaker[Session], run_id: str) -> int:
    normalized = 0
    calculated_at = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        for raw in _run_records(session, run_id, SHOP_SOURCE):
            try:
                for row in public_data_items(_document(raw)):
                    external_id = _text(row, "bizesId", required=True) or ""
                    lat, lng = _coordinates(row, "lon", "lat")
                    if lat is None or lng is None:
                        raise ValueError("shop coordinates are required")
                    observed = _date(
                        _text(row, "stdrYm", required=True) or "", ("%Y%m", "%Y%m%d")
                    )
                    values = {
                        "eden_shop_id": stable_eden_id("shop", "SEMAS", external_id),
                        "external_shop_id": external_id,
                        "place_id": None,
                        "area_id": _resolve_area_id(session, SHOP_SOURCE, row),
                        "name": _text(row, "bizesNm", required=True),
                        "category": _text(
                            row, "indsSclsNm", "indsMclsNm", "indsLclsNm", required=True
                        ),
                        "lat": lat,
                        "lng": lng,
                        "distance_m": None,
                        "observed_at": observed,
                        "source_updated_at": observed,
                        "ingested_at": _database_time(raw.ingested_at),
                        "calculated_at": calculated_at,
                        "source_id": SHOP_SOURCE,
                        "availability": "available",
                        "quality_flags": [],
                    }
                    session.execute(
                        insert(NearbyShop).values(**values).on_duplicate_key_update(**values)
                    )
                    shop_id = session.scalar(
                        select(NearbyShop.id).where(
                            NearbyShop.source_id == SHOP_SOURCE,
                            NearbyShop.external_shop_id == external_id,
                            NearbyShop.observed_at == observed,
                        )
                    )
                    if shop_id is None:
                        raise RuntimeError("nearby shop was not resolved")
                    _provenance(
                        session, "nearby_shop", shop_id, (raw.raw_record_id,)
                    )
                    normalized += 1
            except Exception as exc:
                _add_dead_letter(session, raw, "nearby_shop_schema", exc)
        _finish_run(session, run_id, normalized)
    return normalized
