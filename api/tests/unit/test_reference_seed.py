from __future__ import annotations

from app.reference import preserve_collection_cursor


def test_reseeding_keeps_the_scheduler_rotation_cursor() -> None:
    seeded = {"cadence_tier": "daily", "refresh_scope": {"operations": []}}

    assert preserve_collection_cursor(seeded, {"collection_cursor": 7, "stale": True}) == {
        **seeded,
        "collection_cursor": 7,
    }
    assert preserve_collection_cursor(seeded, {"cadence_tier": "hourly"}) == seeded
    assert preserve_collection_cursor(seeded, None) == seeded
    assert preserve_collection_cursor(seeded, "not-a-dict") == seeded
