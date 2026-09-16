"""Observation retention must release its streaming cursor whenever it stops early."""
from __future__ import annotations

from datetime import datetime

from app.ingestion.retention import retain_observations


class FakeReferences:
    """Stands in for the ScalarResult over snapshot normalized_references."""

    def __init__(self, rows: list[dict[str, list[int]] | None]) -> None:
        self.rows = rows
        self.closed = 0
        self.consumed = 0

    def __iter__(self):
        for row in self.rows:
            self.consumed += 1
            yield row

    def close(self) -> None:
        self.closed += 1


class FakeSession:
    def __init__(self, references: FakeReferences) -> None:
        self.references = references

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *_args) -> None:
        # Closing the session after the cursor is the order the pool relies on.
        assert self.references.closed == 1, "cursor must be closed before the session"

    def scalars(self, _statement):
        return self.references


class FakeFactory:
    def __init__(self, references: FakeReferences) -> None:
        self.references = references
        self.begun = 0

    def __call__(self) -> FakeSession:
        return FakeSession(self.references)

    def begin(self):
        self.begun += 1
        raise AssertionError("no deletion session may open after an incomplete scan")


def test_early_exit_closes_the_streaming_cursor_and_deletes_nothing() -> None:
    references = FakeReferences([{"nearby_shop": [1, 2]}, {"nearby_shop": [3]}, None])
    factory = FakeFactory(references)
    calls = iter([None, "memory_write_pause"])

    result = retain_observations(
        factory,
        pause_reason=lambda: next(calls, "memory_write_pause"),
        now=datetime(2026, 9, 16),
    )

    assert result == {}
    assert references.closed == 1
    assert references.consumed == 2
    assert factory.begun == 0


def test_completed_scan_still_closes_the_cursor_before_deleting() -> None:
    references = FakeReferences([{"nearby_shop": [1]}, {"place_relation": [2]}])
    factory = FakeFactory(references)
    seen = {"count": 0}

    def pause_after_scan() -> str | None:
        seen["count"] += 1
        return None if seen["count"] <= len(references.rows) else "capacity_sample_stale"

    result = retain_observations(factory, pause_reason=pause_after_scan, now=datetime(2026, 9, 16))

    assert result == {}
    assert references.closed == 1
    assert references.consumed == 2
    assert factory.begun == 0
