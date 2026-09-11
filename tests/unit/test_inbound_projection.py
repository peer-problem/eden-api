from __future__ import annotations

from app.products.inbound import _social_interest, _sum_optional
from app.readmodels.repository import (
    _included_inbound_score,
    _selected_inbound_social_interest,
)
from app.repositories.models import SocialObservation


def test_inbound_optional_sum_preserves_null_and_numeric_zero() -> None:
    assert _sum_optional([None, None]) is None
    assert _sum_optional([None, 0]) == 0


def test_inbound_score_is_hidden_when_component_blocks_are_excluded() -> None:
    snapshot = {"inbound_score": 72.5}

    assert _included_inbound_score(snapshot, {"visitors", "flights"}) is None
    assert (
        _included_inbound_score(
            snapshot,
            {"visitors", "flights", "fx", "social_interest"},
        )
        == 72.5
    )


def test_stale_inbound_snapshot_cannot_leak_excluded_social_inputs() -> None:
    social_interest, excluded_contributed = _selected_inbound_social_interest(
        {
            "youtube": {"score": 20.0, "views": 1000, "availability": "available"},
            "tiktok": {"score": 100.0},
        },
        set(),
    )

    assert set(social_interest) == {"youtube"}
    assert social_interest["youtube"]["score"] is None
    assert social_interest["youtube"]["views"] is None
    assert social_interest["youtube"]["availability"] == "unavailable"
    assert excluded_contributed
    assert (
        _included_inbound_score(
            {"inbound_score": 88.0},
            {"visitors", "flights", "fx", "social_interest"},
            excluded_social_contributed=excluded_contributed,
        )
        is None
    )


def test_youtube_query_sample_is_not_used_as_country_interest() -> None:
    interest, score = _social_interest(
        [
            SocialObservation(
                source_id="SRC_YOUTUBE",
                post_count=50,
                view_count=1000,
                reaction_count=30,
                source_score=90,
                quality_flags=["query_language_market_proxy", "aggregate_only"],
            )
        ],
        {"SRC_YOUTUBE": 100.0},
    )

    assert score == 100.0
    assert interest["youtube"]["posts"] == 50
    assert interest["youtube"]["views"] == 1000
    assert interest["youtube"]["semantics"] == "search_sample_interest"
    assert "실제 국적별 시청자 수가 아닙니다" in interest["youtube"]["reason"]


def test_inbound_reader_preserves_explicit_youtube_sample_metrics():
    value, excluded = _selected_inbound_social_interest(
        {
            "youtube": {
                "posts": 20,
                "views": 1234,
                "score": None,
                "availability": "available",
                "semantics": "search_sample_interest",
            },
        },
        {"youtube"},
    )
    assert value["youtube"]["views"] == 1234
    assert value["youtube"]["score"] is None
    assert excluded is False
