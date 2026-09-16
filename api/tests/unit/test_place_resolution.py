from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.readmodels.keys import lookup_key
from app.readmodels.repository import MariaDBReadRepository
from app.repositories.models import (
    Place,
    PlaceLocalization,
    PlaceSourceMap,
    RefreshPolicy,
    SourceRegistry,
    SourceState,
)


@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


def _factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        SourceRegistry,
        RefreshPolicy,
        SourceState,
        Place,
        PlaceSourceMap,
        PlaceLocalization,
    ):
        table.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _place(place_id: str, *, canonical_place_id: str | None = None) -> Place:
    now = datetime(2026, 8, 29, 1, 0, 0)
    return Place(
        eden_place_id=place_id,
        area_id="area-1",
        category=None,
        lat=None,
        lng=None,
        merge_status="merged" if canonical_place_id else "active",
        canonical_place_id=canonical_place_id,
        created_at=now,
        updated_at=now,
    )


def test_place_alias_collision_is_ambiguous_until_aliases_share_canonical_id() -> None:
    factory = _factory()
    now = datetime(2026, 8, 29, 1, 0, 0)
    with factory.begin() as session:
        session.add_all([_place("place-a"), _place("place-b")])
        session.add_all(
            [
                PlaceSourceMap(
                    id=1,
                    source_id="SRC_TOUR_KO",
                    external_content_id="shared-id",
                    eden_place_id="place-a",
                    created_at=now,
                    updated_at=now,
                ),
                PlaceSourceMap(
                    id=2,
                    source_id="SRC_TOUR_EN",
                    external_content_id="shared-id",
                    eden_place_id="place-b",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )

    repository = MariaDBReadRepository(factory)
    assert not repository.resolve_place("shared-id").exists

    with factory.begin() as session:
        alias = session.get(Place, "place-b")
        assert alias is not None
        alias.canonical_place_id = "place-a"
        alias.merge_status = "merged"

    resolution = repository.resolve_place("shared-id")
    assert resolution.exists
    assert resolution.eden_place_id == "place-a"
    assert repository.resolve_place("place-b").eden_place_id == "place-a"


def test_legacy_foreign_only_place_returns_its_actual_language() -> None:
    factory = _factory()
    now = datetime(2026, 8, 29, 1, 0, 0)
    with factory.begin() as session:
        session.add(_place("place-a"))
        session.add(
            PlaceLocalization(
                id=1,
                eden_place_id="place-a",
                language="ja",
                title="日本語だけ",
                address=None,
                overview=None,
                is_fallback=False,
                created_at=now,
                updated_at=now,
            )
        )

    result = MariaDBReadRepository(factory).fetch(
        "place_detail",
        lookup_key(
            content_id="place-a",
            lang="en",
            radius_m=1000,
            related_limit=5,
            include=["shops"],
            # Fixture has no coordinates, so nearby data stays unavailable.
        ),
    )

    assert result.data["language"] == "ja"
    assert result.data["requested_language"] == "en"
    assert result.data["fallback"] is True
