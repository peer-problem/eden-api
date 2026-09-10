from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.enums import Availability
from app.products.trends import build_trend_snapshot
from app.readmodels.keys import lookup_key
from app.readmodels.repository import MariaDBReadRepository
from app.repositories.models import Base, Country, SocialObservation


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_integer(
    _type: BigInteger,
    _compiler: object,
    **_kwargs: object,
) -> str:
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(
    _type: LONGTEXT,
    _compiler: object,
    **_kwargs: object,
) -> str:
    return "TEXT"


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _social_row(
    observation_id: int,
    raw_record_id: int,
    *,
    count: int | None,
    updated_at: datetime,
    availability: Availability = Availability.AVAILABLE,
    source_id: str = "SRC_INSTAGRAM",
    bucket_start: datetime | None = None,
) -> SocialObservation:
    return SocialObservation(
        observation_id=observation_id,
        raw_record_id=raw_record_id,
        keyword="서울",
        country_id="eden_country_kr",
        area_id=None,
        bucket_start=bucket_start or datetime(2026, 8, 28),
        bucket_grain="day",
        post_count=count,
        view_count=None,
        reaction_count=None,
        search_ratio=None,
        source_score=None,
        observed_at=updated_at,
        source_updated_at=updated_at,
        ingested_at=updated_at,
        calculated_at=updated_at,
        source_id=source_id,
        availability=availability,
        quality_flags=(
            ["query_market_proxy", "aggregate_only", "source_tombstone"]
            if availability == Availability.UNAVAILABLE
            else ["query_market_proxy", "aggregate_only"]
        ),
    )


def _seed_country(factory: sessionmaker[Session], now: datetime) -> None:
    with factory.begin() as session:
        session.add(
            Country(
                eden_country_id="eden_country_kr",
                iso_alpha2="KR",
                name_ko="대한민국",
                name_en="South Korea",
                default_language="ko",
                default_currency="KRW",
                created_at=now,
                updated_at=now,
            )
        )


def test_trend_product_keeps_only_latest_semantic_aggregate_version() -> None:
    factory = _factory()
    now = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None)
    _seed_country(factory, now)
    with factory.begin() as session:
        session.add_all(
            [
                _social_row(1, 101, count=10, updated_at=now - timedelta(hours=1)),
                _social_row(2, 102, count=25, updated_at=now),
            ]
        )

    result = build_trend_snapshot(factory)

    assert result.observation_count == 1
    repository = MariaDBReadRepository(factory)
    with factory() as session:
        snapshot = repository._current_snapshot(
            session,
            "social_signal",
            lookup_key(scope="global"),
        )
        assert snapshot is not None
        assert snapshot.data["observations"][0]["post_count"] == 25


def test_latest_social_tombstone_removes_prior_value_from_current_product() -> None:
    factory = _factory()
    now = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None)
    _seed_country(factory, now)
    with factory.begin() as session:
        session.add_all(
            [
                _social_row(1, 101, count=10, updated_at=now - timedelta(hours=1)),
                _social_row(
                    2,
                    102,
                    count=None,
                    updated_at=now,
                    availability=Availability.UNAVAILABLE,
                ),
            ]
        )

    result = build_trend_snapshot(factory)

    assert result.observation_count == 0
    repository = MariaDBReadRepository(factory)
    with factory() as session:
        snapshot = repository._current_snapshot(
            session,
            "social_signal",
            lookup_key(scope="global"),
        )
        assert snapshot is not None
        assert snapshot.availability == Availability.UNAVAILABLE
        assert snapshot.data == {"observations": []}
        assert snapshot.quality_flags == ["all_latest_observations_tombstoned"]


def test_trend_product_excludes_preexisting_removed_platform_observations() -> None:
    factory = _factory()
    now = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None)
    _seed_country(factory, now)
    with factory.begin() as session:
        session.add_all(
            [
                _social_row(1, 101, count=25, updated_at=now),
                _social_row(
                    2,
                    102,
                    count=999,
                    updated_at=now,
                    source_id="SRC_TIKTOK",
                    bucket_start=datetime(2026, 9, 1),
                ),
            ]
        )

    result = build_trend_snapshot(factory)

    assert result.observation_count == 1
    repository = MariaDBReadRepository(factory)
    with factory() as session:
        snapshot = repository._current_snapshot(
            session,
            "social_signal",
            lookup_key(scope="global"),
        )
        assert snapshot is not None
        assert {row["source_id"] for row in snapshot.data["observations"]} == {"SRC_INSTAGRAM"}
