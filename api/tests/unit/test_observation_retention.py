"""Observation retention must release its streaming cursor whenever it stops early."""
from __future__ import annotations

from datetime import datetime
from time import monotonic

from sqlalchemy import Column, DateTime, Integer, MetaData, Table, create_engine, event

from app.ingestion.retention import _unprotected_candidates, retain_observations


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


def test_large_protected_set_uses_bounded_sql_and_pages_past_protected_rows() -> None:
    engine = create_engine("sqlite://")
    metadata = MetaData()
    facts = Table(
        "facts", metadata,
        Column("id", Integer, primary_key=True),
        Column("observed_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)
    old = datetime(2024, 1, 1)
    with engine.begin() as connection:
        connection.execute(facts.insert(), [
            {"id": identifier, "observed_at": old}
            for identifier in [*range(1, 551), 70_001, 70_002, 70_003]
        ])
    queries = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_query(_conn, _cursor, statement, parameters, _context, _many):
        queries.append((statement, len(parameters)))

    with engine.connect() as connection:
        result = _unprotected_candidates(
            connection, facts.c.id, facts.c.observed_at, datetime(2025, 1, 1),
            {str(value) for value in range(1, 70_001)},
            batch_size=2, deadline=monotonic() + 5, pause_reason=None,
        )
    assert result == [70_001, 70_002]
    assert len(queries) == 2
    assert all("NOT IN" not in sql and count <= 6 for sql, count in queries)
    engine.dispose()


def test_candidate_scan_discards_partial_batch_when_paused() -> None:
    class Session:
        def scalars(self, _statement):
            return self

        def all(self):
            # The first page has one deletable row and 499 protected rows.
            return list(range(500))

    facts = Table(
        "facts", MetaData(), Column("id", Integer), Column("observed_at", DateTime),
    )
    reasons = iter([None, "capacity_sample_stale"])
    assert _unprotected_candidates(
        Session(), facts.c.id, facts.c.observed_at, datetime(2025, 1, 1),
        {str(value) for value in range(1, 500)},
        batch_size=2, deadline=monotonic() + 5, pause_reason=lambda: next(reasons),
    ) == []
