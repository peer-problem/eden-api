"""Match KTO hub/related places (KTO_TATS ids) to TourAPI places (KTO_CONTENT ids).

The hub and related-place APIs identify attractions with the KTO TATS code, while
the place detail endpoint serves TourAPI content ids. Without
a bridge, hub ranks and related places only ever attach to separate TATS-minted
place rows that no public request reaches. A TATS row is treated as the same
facility as a TourAPI place when the normalized Korean name is identical inside
the same province, the match is unambiguous, and any coordinates on both sides
agree within one kilometre. Coordinates alone never merge identities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.ids import stable_eden_id
from app.repositories.models import (
    Area,
    Place,
    PlaceLocalization,
    PlaceSourceMap,
    ProvenanceEdge,
)

TATS_SOURCES = ("SRC_KTO_PLACE_HUB", "SRC_KTO_PLACE_RELATED")
TOUR_CONTENT_SOURCE = "SRC_TOUR_KO"
MAX_COORDINATE_DISTANCE_M = 1000.0
CROSSWALK_FORMULA_VERSION = "place_crosswalk_v1"
_TITLE_NOISE = re.compile(r"\(.*?\)|\[.*?\]|[\s·・.,'\"\-_/&]+")


@dataclass(frozen=True, slots=True)
class TourCandidate:
    place_id: str
    area_id: str
    lat: Decimal | None
    lng: Decimal | None


def normalized_title(title: str | None) -> str:
    return _TITLE_NOISE.sub("", title or "").lower()


def tats_place_id(external_id: str) -> str:
    return stable_eden_id("place", "KTO_TATS", external_id)


def _distance_m(a: TourCandidate | tuple[Any, Any], b: tuple[Any, Any]) -> float | None:
    lat1, lng1 = (a.lat, a.lng) if isinstance(a, TourCandidate) else a
    lat2, lng2 = b
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return None
    phi1, phi2 = radians(float(lat1)), radians(float(lat2))
    d_phi = radians(float(lat2) - float(lat1))
    d_lambda = radians(float(lng2) - float(lng1))
    value = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 6_371_008.8 * 2 * asin(sqrt(value))


def _province(session: Session, area_id: str) -> str | None:
    cache = session.info.setdefault("eden_area_province", {})
    if area_id not in cache:
        code = session.scalar(select(Area.administrative_code).where(Area.eden_area_id == area_id))
        cache[area_id] = code[:2] if code else None
    return cache[area_id]


def _province_title_index(session: Session, province: str) -> dict[str, list[TourCandidate]]:
    cache = session.info.setdefault("eden_tour_title_index", {})
    if province not in cache:
        rows = session.execute(
            select(
                Place.eden_place_id,
                Place.area_id,
                Place.lat,
                Place.lng,
                PlaceLocalization.title,
            )
            .join(Area, Area.eden_area_id == Place.area_id)
            .join(
                PlaceLocalization,
                (PlaceLocalization.eden_place_id == Place.eden_place_id)
                & (PlaceLocalization.language == "ko"),
            )
            .where(
                Place.merge_status == "active",
                Place.canonical_place_id.is_(None),
                func.substr(Area.administrative_code, 1, 2) == province,
                select(PlaceSourceMap.id)
                .where(
                    PlaceSourceMap.eden_place_id == Place.eden_place_id,
                    PlaceSourceMap.source_id == TOUR_CONTENT_SOURCE,
                )
                .exists(),
            )
        ).all()
        index: dict[str, list[TourCandidate]] = {}
        for place_id, area_id, lat, lng, title in rows:
            key = normalized_title(title)
            if key:
                index.setdefault(key, []).append(TourCandidate(place_id, area_id, lat, lng))
        cache[province] = index
    return cache[province]


def match_tour_place(
    session: Session,
    title: str | None,
    area_id: str,
    lat: Decimal | None,
    lng: Decimal | None,
) -> str | None:
    """Return the TourAPI place this TATS row names, or None when unsure."""
    key = normalized_title(title)
    if not key:
        return None
    province = _province(session, area_id)
    if province is None:
        return None
    candidates = _province_title_index(session, province).get(key, [])
    if lat is not None and lng is not None:
        candidates = [
            candidate
            for candidate in candidates
            if (distance := _distance_m(candidate, (lat, lng))) is None
            or distance <= MAX_COORDINATE_DISTANCE_M
        ]
    if len(candidates) > 1:
        same_area = [candidate for candidate in candidates if candidate.area_id == area_id]
        candidates = same_area or candidates
    if len(candidates) != 1:
        return None
    return candidates[0].place_id


def attach_place_alias(
    session: Session,
    source_id: str,
    external_id: str,
    canonical_id: str,
    raw_record_id: int | None,
) -> str:
    """Point a TATS source identity at its TourAPI place and retire the TATS-minted row."""
    now = datetime.now(UTC).replace(tzinfo=None)
    mapping = session.scalar(
        select(PlaceSourceMap).where(
            PlaceSourceMap.source_id == source_id,
            PlaceSourceMap.external_content_id == external_id,
        )
    )
    previous_id = mapping.eden_place_id if mapping is not None else None
    if mapping is None:
        session.add(
            PlaceSourceMap(
                source_id=source_id,
                external_content_id=external_id,
                eden_place_id=canonical_id,
                created_at=now,
                updated_at=now,
            )
        )
    elif mapping.eden_place_id != canonical_id:
        mapping.eden_place_id = canonical_id
        mapping.updated_at = now
    minted_id = tats_place_id(external_id)
    for retired_id in {previous_id, minted_id} - {None, canonical_id}:
        retired = session.get(Place, retired_id)
        if retired is None or retired.canonical_place_id is not None:
            continue
        if retired_id != minted_id:
            # Only rows this crosswalk minted are safe to retire; other
            # identities keep their own history.
            continue
        retired.merge_status = "merged"
        retired.canonical_place_id = canonical_id
        retired.updated_at = now
    if raw_record_id is not None:
        localization_id = session.scalar(
            select(PlaceLocalization.id).where(
                PlaceLocalization.eden_place_id == canonical_id,
                PlaceLocalization.language == "ko",
            )
        )
        outputs = [("place", canonical_id)]
        if localization_id is not None:
            outputs.append(("place_localization", str(localization_id)))
        for output_type, output_id in outputs:
            exists = session.scalar(
                select(ProvenanceEdge.provenance_id).where(
                    ProvenanceEdge.output_type == output_type,
                    ProvenanceEdge.output_id == output_id,
                    ProvenanceEdge.raw_record_id == raw_record_id,
                    ProvenanceEdge.formula_version == CROSSWALK_FORMULA_VERSION,
                )
            )
            if exists is None:
                session.add(
                    ProvenanceEdge(
                        output_type=output_type,
                        output_id=output_id,
                        raw_record_id=raw_record_id,
                        formula_version=CROSSWALK_FORMULA_VERSION,
                        created_at=now,
                    )
                )
    session.flush()
    session.info.setdefault("eden_place_source_map", {})[(source_id, external_id)] = canonical_id
    return canonical_id


def alias_place_ids(session: Session, place_id: str) -> list[str]:
    """The place id plus every retired row that now points at it."""
    aliases = list(
        session.scalars(
            select(Place.eden_place_id).where(Place.canonical_place_id == place_id)
        ).all()
    )
    return [place_id, *sorted(aliases)]


def crosswalk_tats_places(
    session: Session,
    *,
    limit: int = 3000,
    deadline: float | None = None,
    clock=None,
) -> dict[str, int]:
    """Retire already-minted TATS places whose TourAPI twin can be identified."""
    from time import monotonic

    clock = clock or monotonic
    examined = matched = 0
    rows = session.execute(
        select(
            PlaceSourceMap.source_id,
            PlaceSourceMap.external_content_id,
            Place.eden_place_id,
            Place.area_id,
            Place.lat,
            Place.lng,
            PlaceLocalization.title,
        )
        .join(Place, Place.eden_place_id == PlaceSourceMap.eden_place_id)
        .outerjoin(
            PlaceLocalization,
            (PlaceLocalization.eden_place_id == Place.eden_place_id)
            & (PlaceLocalization.language == "ko"),
        )
        .where(
            PlaceSourceMap.source_id.in_(TATS_SOURCES),
            Place.canonical_place_id.is_(None),
            Place.merge_status == "active",
        )
        .order_by(PlaceSourceMap.id)
        .limit(limit)
    ).all()
    for source_id, external_id, place_id, area_id, lat, lng, title in rows:
        if deadline is not None and clock() >= deadline:
            break
        if place_id != tats_place_id(external_id):
            # Already attached to a TourAPI place (or another identity).
            continue
        examined += 1
        canonical_id = match_tour_place(session, title, area_id, lat, lng)
        if canonical_id is None or canonical_id == place_id:
            continue
        attach_place_alias(session, source_id, external_id, canonical_id, None)
        matched += 1
    return {"examined": examined, "matched": matched}
