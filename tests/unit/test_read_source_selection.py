from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from app.readmodels.repository import _request_source_ids, _selected_snapshot_as_of
from app.repositories.models import ReadModelSnapshot


def test_region_freshness_uses_only_included_blocks() -> None:
    assert _request_source_ids(
        "region_insights",
        {"include": ["visitors"]},
    ) == ("SRC_KTO_REGIONAL_VISITORS",)


def test_forecast_freshness_excludes_unrequested_adjustments() -> None:
    assert _request_source_ids(
        "visitor_forecast",
        {"include": ["weather"]},
    ) == ("SRC_KMA_FORECAST", "SRC_KTO_VISITOR_FORECAST")


def test_inbound_freshness_uses_selected_blocks_and_social_sources() -> None:
    assert _request_source_ids(
        "inbound_markets",
        {
            "include": ["visitors", "social_interest"],
            "social_sources": ["reddit"],
        },
    ) == ("SRC_KTO_INBOUND_STATS", "SRC_REDDIT")

    assert _request_source_ids(
        "inbound_markets",
        {"include": ["social_interest"], "social_sources": ["youtube"]},
    ) == ("SRC_YOUTUBE",)


def test_place_freshness_keeps_language_fallback_and_selected_relation() -> None:
    assert _request_source_ids(
        "place_detail",
        {"lang": "ja", "include": ["related"]},
    ) == ("SRC_KTO_PLACE_RELATED", "SRC_TOUR_JA", "SRC_TOUR_KO")


def test_alert_freshness_uses_only_requested_scope() -> None:
    assert _request_source_ids(
        "market_alerts",
        {"source_scope": "local"},
    ) == ("SRC_EMBASSY_NOTICE",)


def test_excluded_source_watermark_does_not_age_the_response() -> None:
    snapshot = cast(
        ReadModelSnapshot,
        cast(
            Any,
            SimpleNamespace(
                input_watermarks={
                    "SRC_KTO_VISITOR_FORECAST": "2026-08-29T00:00:00+00:00",
                    "SRC_HOLIDAY": "2025-01-01T00:00:00+00:00",
                },
                as_of=datetime(2025, 1, 1),
            ),
        ),
    )

    assert _selected_snapshot_as_of(
        (snapshot,),
        ("SRC_KTO_VISITOR_FORECAST",),
    ) == datetime(2026, 8, 29, tzinfo=UTC)
