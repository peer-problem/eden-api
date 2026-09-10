from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.normalization import public_data
from app.repositories.models import Area, AreaSourceMap, Place


@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (Area, AreaSourceMap, Place):
        table.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _area(
    area_id: str,
    name: str,
    *,
    parent_id: str | None = None,
    active: bool = True,
) -> Area:
    now = datetime(2026, 9, 10)
    return Area(
        eden_area_id=area_id,
        legal_code=None,
        administrative_code=None,
        name_ko=name,
        name_en=None,
        parent_area_id=parent_id,
        level="sido" if parent_id is None else "sigungu",
        center_lat=None,
        center_lng=None,
        valid_from=None,
        valid_to=None,
        active=active,
        created_at=now,
        updated_at=now,
    )


def _place(
    place_id: str,
    area_id: str,
    lat: str,
    lng: str,
    *,
    merge_status: str = "active",
) -> Place:
    now = datetime(2026, 9, 10)
    return Place(
        eden_place_id=place_id,
        area_id=area_id,
        category=None,
        lat=Decimal(lat),
        lng=Decimal(lng),
        merge_status=merge_status,
        canonical_place_id=None,
        created_at=now,
        updated_at=now,
    )


def test_address_uses_unique_most_specific_descendant() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add_all(
            [
                _area("gyeonggi", "경기도"),
                _area("ansan", "안산시", parent_id="gyeonggi"),
                _area("sangnok", "상록구", parent_id="ansan"),
            ]
        )

    with factory() as session:
        assert (
            public_data._resolve_area_id(
                session,
                "SRC_TOUR_KO",
                {"addr1": "경기도 안산시 상록구 본삼로 41 (본오동)"},
            )
            == "sangnok"
        )


def test_duplicate_district_needs_an_explicit_matching_parent() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add_all(
            [
                _area("seoul", "서울특별시"),
                _area("busan", "부산광역시"),
                _area("seoul-jung", "중구", parent_id="seoul"),
                _area("busan-jung", "중구", parent_id="busan"),
            ]
        )

    with factory() as session:
        with pytest.raises(ValueError, match="unresolvable address"):
            public_data._resolve_area_id(
                session,
                "SRC_TOUR_KO",
                {"addr1": "중구 세종대로 110"},
            )

        assert (
            public_data._resolve_area_id(
                session,
                "SRC_TOUR_KO",
                {"addr1": "서울특별시 중구 세종대로 110"},
            )
            == "seoul-jung"
        )


def test_duplicate_district_at_different_depths_stays_unresolved() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add_all(
            [
                _area("seoul", "서울특별시"),
                _area("busan", "부산광역시"),
                _area("busan-center", "부산중심권", parent_id="busan"),
                _area("seoul-jung", "중구", parent_id="seoul"),
                _area("busan-jung", "중구", parent_id="busan-center"),
            ]
        )

    with factory() as session, pytest.raises(ValueError, match="unresolvable address"):
        public_data._resolve_area_id(
            session,
            "SRC_TOUR_KO",
            {"addr1": "중구 중앙로"},
        )


def test_address_with_multiple_region_hierarchies_stays_unresolved() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add_all(
            [
                _area("seoul", "서울특별시"),
                _area("busan", "부산광역시"),
                _area("seoul-jung", "중구", parent_id="seoul"),
                _area("busan-jung", "중구", parent_id="busan"),
            ]
        )

    with factory() as session, pytest.raises(ValueError, match="unresolvable address"):
        public_data._resolve_area_id(
            session,
            "SRC_TOUR_KO",
            {"addr1": "서울특별시 부산광역시 중구 순환 관광"},
        )


def test_province_only_address_stays_unresolved() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add(_area("gyeonggi", "경기도"))

    with factory() as session, pytest.raises(ValueError, match="unresolvable address"):
        public_data._resolve_area_id(
            session,
            "SRC_TOUR_KO",
            {"addr1": "경기도 일대"},
        )


def test_unknown_code_address_results_do_not_poison_source_map_cache() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add_all(
            [
                _area("seoul", "서울특별시"),
                _area("busan", "부산광역시"),
                _area("jongno", "종로구", parent_id="seoul"),
                _area("haeundae", "해운대구", parent_id="busan"),
            ]
        )

    with factory() as session:
        assert (
            public_data._resolve_area_id(
                session,
                "SRC_TOUR_KO",
                {"areaCode": "unknown", "addr1": "서울특별시 종로구"},
            )
            == "jongno"
        )
        assert (
            public_data._resolve_area_id(
                session,
                "SRC_TOUR_KO",
                {"areaCode": "unknown", "addr1": "부산광역시 해운대구"},
            )
            == "haeundae"
        )
        assert ("SRC_TOUR_KO", "unknown") not in session.info["eden_area_source_map"]


def test_authoritative_mapping_still_wins_over_fallback_evidence() -> None:
    factory = _factory()
    now = datetime(2026, 9, 10)
    with factory.begin() as session:
        session.add_all(
            [
                _area("seoul", "서울특별시"),
                _area("busan", "부산광역시"),
                AreaSourceMap(
                    source_id="SRC_TOUR_ZH_CN",
                    external_area_code="1",
                    eden_area_id="seoul",
                    spatial_resolution="sido",
                    created_at=now,
                    updated_at=now,
                ),
                _place("busan-place", "busan", "35.1", "129.1"),
            ]
        )

    with factory() as session:
        assert (
            public_data._resolve_area_id(
                session,
                "SRC_TOUR_ZH_CN",
                {
                    "areaCode": "1",
                    "addr1": "釜山广域市",
                    "mapx": "129.1",
                    "mapy": "35.1",
                },
            )
            == "seoul"
        )
        assert session.info["eden_area_source_map"][("SRC_TOUR_ZH_CN", "1")] == "seoul"


def test_tour_exact_stored_coordinates_resolve_when_active_places_agree() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add(_area("seoul", "서울특별시"))
        session.add_all(
            [
                _place("place-ko", "seoul", "37.1234568", "126.9876543"),
                _place("place-en", "seoul", "37.1234568", "126.9876543"),
            ]
        )

    with factory() as session:
        assert (
            public_data._resolve_area_id(
                session,
                "SRC_TOUR_ZH_CN",
                {
                    "addr1": "无法匹配的翻译地址",
                    "mapx": "126.9876543491",
                    "mapy": "37.1234567501",
                },
            )
            == "seoul"
        )


def test_tour_exact_coordinates_reject_conflicting_or_inactive_areas() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add_all(
            [
                _area("seoul", "서울특별시"),
                _area("busan", "부산광역시"),
                _area("inactive", "비활성구", parent_id="seoul", active=False),
                _place("place-seoul", "seoul", "37.5", "127.1"),
                _place("place-busan", "busan", "37.5", "127.1"),
                _place("place-inactive", "inactive", "36.5", "128.1"),
            ]
        )

    with factory() as session:
        for mapx, mapy in (("127.1", "37.5"), ("128.1", "36.5")):
            with pytest.raises(ValueError, match="unresolvable address"):
                public_data._resolve_area_id(
                    session,
                    "SRC_TOUR_ZH_CN",
                    {"addr1": "无法匹配", "mapx": mapx, "mapy": mapy},
                )


@pytest.mark.parametrize(
    ("mapx", "mapy"),
    [
        (None, "37.5"),
        ("null", "37.5"),
        ("127.1", "NaN"),
        ("Infinity", "37.5"),
        ("181", "37.5"),
        ("127.1", "91"),
    ],
)
def test_invalid_tour_coordinates_do_not_resolve(mapx: object, mapy: object) -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add(_area("seoul", "서울특별시"))
        session.add(_place("place-seoul", "seoul", "37.5", "127.1"))

    with factory() as session, pytest.raises(ValueError, match="unresolvable address"):
        public_data._resolve_area_id(
            session,
            "SRC_TOUR_ZH_CN",
            {"addr1": "无法匹配", "mapx": mapx, "mapy": mapy},
        )


def test_non_tour_source_never_uses_coordinate_fallback() -> None:
    factory = _factory()
    with factory.begin() as session:
        session.add(_area("seoul", "서울특별시"))
        session.add(_place("place-seoul", "seoul", "37.5", "127.1"))

    with factory() as session, pytest.raises(ValueError, match="unresolvable address"):
        public_data._resolve_area_id(
            session,
            public_data.REGIONAL_DEMAND_SOURCE,
            {"addr1": "无法匹配", "mapx": "127.1", "mapy": "37.5"},
        )
