from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.products import registry
from app.products.refresh_requests import (
    CLEAN,
    PENDING,
    claim_product_refresh,
    complete_product_refresh,
    defer_product_refresh,
    mark_products_dirty,
)
from app.products.registry import ProductFamily, product_families_for_source
from app.repositories.models import ProductRefreshRequest
from app.sources.social import EXCLUDED_SOCIAL_SOURCE_IDS


@pytest.fixture
def refresh_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ProductRefreshRequest.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_four_sources_coalesce_to_one_request_per_family(refresh_factory) -> None:
    requested_at = datetime(2026, 8, 29, 1, 2, 3)
    source_ids = ["SRC_YOUTUBE", "SRC_INSTAGRAM", "SRC_REDDIT", "SRC_FACEBOOK"]

    for index, source_id in enumerate(source_ids):
        mark_products_dirty(
            source_id,
            refresh_factory,
            watermark=f"run-{index}",
            requested_at=requested_at + timedelta(seconds=index),
        )

    with refresh_factory() as session:
        rows = {row.family: row for row in session.scalars(select(ProductRefreshRequest)).all()}
    assert set(rows) == {ProductFamily.INBOUND.value, ProductFamily.TRENDS.value}
    assert rows[ProductFamily.TRENDS.value].source_ids == sorted(source_ids)
    assert rows[ProductFamily.INBOUND.value].source_ids == sorted(
        source_ids
    )
    assert all(row.status == PENDING for row in rows.values())


def test_four_dirty_sources_trigger_one_family_build(monkeypatch, refresh_factory) -> None:
    source_ids = ["SRC_YOUTUBE", "SRC_INSTAGRAM", "SRC_REDDIT", "SRC_FACEBOOK"]
    for source_id in source_ids:
        mark_products_dirty(source_id, refresh_factory)
    builds: list[object] = []
    monkeypatch.setattr(
        registry,
        "build_inbound_snapshots",
        lambda _factory: builds.append(object()) or object(),
    )

    claim = claim_product_refresh(ProductFamily.INBOUND, refresh_factory)
    assert claim is not None
    registry.refresh_product_family(ProductFamily.INBOUND, refresh_factory)
    assert complete_product_refresh(claim, refresh_factory)

    assert len(builds) == 1
    assert claim_product_refresh(ProductFamily.INBOUND, refresh_factory) is None


def test_excluded_social_sources_do_not_dirty_products(refresh_factory) -> None:
    for source_id in EXCLUDED_SOCIAL_SOURCE_IDS:
        mark_products_dirty(source_id, refresh_factory)

    with refresh_factory() as session:
        assert session.scalars(select(ProductRefreshRequest)).all() == []


def test_new_dirty_watermark_survives_an_in_progress_build(refresh_factory) -> None:
    started_at = datetime(2026, 8, 29, 1, 0, 0)
    mark_products_dirty(
        "SRC_NAVER_TREND",
        refresh_factory,
        watermark="run-old",
        requested_at=started_at,
    )
    claim = claim_product_refresh(
        ProductFamily.TRENDS,
        refresh_factory,
        claimed_at=started_at + timedelta(minutes=1),
    )
    assert claim is not None

    mark_products_dirty(
        "SRC_YOUTUBE",
        refresh_factory,
        watermark="run-new",
        requested_at=started_at + timedelta(minutes=2),
    )

    assert not complete_product_refresh(
        claim,
        refresh_factory,
        completed_at=started_at + timedelta(minutes=3),
    )
    next_claim = claim_product_refresh(
        ProductFamily.TRENDS,
        refresh_factory,
        claimed_at=started_at + timedelta(minutes=4),
    )
    assert next_claim is not None
    assert next_claim.request_watermark == {
        "SRC_NAVER_TREND": "run-old",
        "SRC_YOUTUBE": "run-new",
    }
    assert complete_product_refresh(next_claim, refresh_factory)
    with refresh_factory() as session:
        row = session.get(ProductRefreshRequest, ProductFamily.TRENDS.value)
        assert row is not None
        assert row.status == CLEAN


def test_abandoned_product_claim_is_resumable(refresh_factory) -> None:
    started_at = datetime(2026, 8, 29, 1, 0, 0)
    mark_products_dirty(
        "SRC_NAVER_TREND",
        refresh_factory,
        requested_at=started_at,
    )
    abandoned = claim_product_refresh(
        ProductFamily.TRENDS,
        refresh_factory,
        claimed_at=started_at,
    )
    resumed = claim_product_refresh(
        ProductFamily.TRENDS,
        refresh_factory,
        claimed_at=started_at + timedelta(minutes=31),
        stale_after=timedelta(minutes=30),
    )
    assert abandoned is not None
    assert resumed is not None
    assert resumed.claim_token != abandoned.claim_token


def test_failed_product_refresh_waits_for_exponential_backoff(refresh_factory) -> None:
    started_at = datetime(2026, 8, 29, 1, 0, 0)
    mark_products_dirty(
        "SRC_NAVER_TREND",
        refresh_factory,
        requested_at=started_at,
    )
    first = claim_product_refresh(
        ProductFamily.TRENDS,
        refresh_factory,
        claimed_at=started_at,
    )
    assert first is not None
    assert defer_product_refresh(
        first,
        refresh_factory,
        "build failed",
        deferred_at=started_at,
        retry_base_seconds=60,
    )
    assert (
        claim_product_refresh(
            ProductFamily.TRENDS,
            refresh_factory,
            claimed_at=started_at + timedelta(seconds=59),
        )
        is None
    )
    second = claim_product_refresh(
        ProductFamily.TRENDS,
        refresh_factory,
        claimed_at=started_at + timedelta(seconds=60),
    )
    assert second is not None
    assert second.attempt_count == 2
    assert defer_product_refresh(
        second,
        refresh_factory,
        "build failed again",
        deferred_at=started_at + timedelta(seconds=60),
        retry_base_seconds=60,
    )
    assert (
        claim_product_refresh(
            ProductFamily.TRENDS,
            refresh_factory,
            claimed_at=started_at + timedelta(seconds=179),
        )
        is None
    )
    assert (
        claim_product_refresh(
            ProductFamily.TRENDS,
            refresh_factory,
            claimed_at=started_at + timedelta(seconds=180),
        )
        is not None
    )


def test_family_dispatch_builds_only_the_requested_product(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        registry,
        "build_trend_snapshot",
        lambda _factory: calls.append("trends") or object(),
    )
    monkeypatch.setattr(
        registry,
        "build_inbound_snapshots",
        lambda _factory: calls.append("inbound") or object(),
    )

    registry.refresh_product_family(ProductFamily.TRENDS, object())

    assert calls == ["trends"]
    assert product_families_for_source("SRC_KTO_INBOUND_STATS") == (ProductFamily.INBOUND,)
