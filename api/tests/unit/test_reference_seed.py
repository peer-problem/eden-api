from __future__ import annotations

from app.reference import preserve_collection_cursor, source_enabled_by_code
from app.sources.essential import DISABLED_SOURCES


def test_reseeding_keeps_the_scheduler_rotation_cursor() -> None:
    seeded = {"cadence_tier": "daily", "refresh_scope": {"operations": []}}

    assert preserve_collection_cursor(seeded, {"collection_cursor": 7, "stale": True}) == {
        **seeded,
        "collection_cursor": 7,
    }
    assert preserve_collection_cursor(seeded, {"cadence_tier": "hourly"}) == seeded
    assert preserve_collection_cursor(seeded, None) == seeded
    assert preserve_collection_cursor(seeded, "not-a-dict") == seeded


def test_place_sources_are_enabled_again_and_social_sources_stay_off() -> None:
    for source_id in (
        "SRC_TOUR_EN",
        "SRC_TOUR_JA",
        "SRC_TOUR_ZH_CN",
        "SRC_KTO_PLACE_HUB",
        "SRC_KTO_PLACE_RELATED",
    ):
        assert source_id not in DISABLED_SOURCES
        assert source_enabled_by_code(source_id) is True
    for source_id in ("SRC_NAVER_TREND", "SRC_INSTAGRAM", "SRC_TOURISM_ADMISSION"):
        assert source_enabled_by_code(source_id) is False
