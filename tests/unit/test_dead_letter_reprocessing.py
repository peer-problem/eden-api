from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.normalization.dead_letters import reprocess_dead_letters
from app.repositories.models import DeadLetter, RawRecord


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


@pytest.fixture
def dead_letter_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RawRecord.__table__.create(engine)
    DeadLetter.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 29, 0, 0, 0)
    with factory.begin() as session:
        for index in range(3):
            raw_id = index + 1
            session.add(
                RawRecord(
                    raw_record_id=raw_id,
                    source_id="SRC_NAVER_TREND",
                    external_key=f"key-{raw_id}",
                    observed_at=now,
                    source_updated_at=now,
                    ingested_at=now,
                    content_type="application/json",
                    body_json={"value": raw_id},
                    body_text=None,
                    content_hash=f"hash-{raw_id}",
                    run_id=f"run-{raw_id}",
                    tombstone=False,
                )
            )
            session.add(
                DeadLetter(
                    dead_letter_id=raw_id,
                    raw_record_id=raw_id,
                    error_code="schema_drift",
                    error_detail="missing field",
                    created_at=now + timedelta(seconds=index),
                    reprocess_status="pending",
                    reprocessed_at=None,
                    attempt_count=0,
                    next_attempt_at=None,
                    last_attempt_at=None,
                )
            )
    return factory


def test_reprocessor_claims_at_most_one_hundred_and_is_resumable(dead_letter_factory) -> None:
    normalized: list[tuple[str, str]] = []
    dirtied: list[tuple[str, str]] = []
    now = datetime(2026, 8, 29, 1, 0, 0)

    result = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=2,
        normalizer=lambda source_id, _factory, run_id: normalized.append(
            (source_id, run_id)
        ),
        dirty_marker=lambda source_id, _factory, *, watermark: dirtied.append(
            (source_id, watermark)
        ),
        clock=lambda: now,
    )

    assert result.claimed_count == 2
    assert result.resolved_count == 2
    assert normalized == [
        ("SRC_NAVER_TREND", "run-1"),
        ("SRC_NAVER_TREND", "run-2"),
    ]
    assert dirtied == [
        ("SRC_NAVER_TREND", "dead-letter:1"),
        ("SRC_NAVER_TREND", "dead-letter:2"),
    ]
    with dead_letter_factory() as session:
        statuses = list(
            session.execute(
                select(DeadLetter.dead_letter_id, DeadLetter.reprocess_status).order_by(
                    DeadLetter.dead_letter_id
                )
            )
        )
    assert statuses == [(1, "resolved"), (2, "resolved"), (3, "pending")]


def test_failed_attempt_records_backoff_then_quarantine(dead_letter_factory) -> None:
    now = datetime(2026, 8, 29, 1, 0, 0)
    with dead_letter_factory.begin() as session:
        session.get(DeadLetter, 1).attempt_count = 3
        session.get(DeadLetter, 2).attempt_count = 4

    def fail(_source_id, _factory, _run_id):
        raise ValueError("still invalid")

    result = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=2,
        max_attempts=5,
        retry_base_seconds=60,
        normalizer=fail,
        dirty_marker=lambda *_args, **_kwargs: None,
        clock=lambda: now,
    )

    assert result.retry_count == 1
    assert result.quarantined_count == 1
    with dead_letter_factory() as session:
        retry = session.get(DeadLetter, 1)
        quarantined = session.get(DeadLetter, 2)
        assert retry.reprocess_status == "pending"
        assert retry.attempt_count == 4
        assert retry.last_attempt_at == now
        assert retry.next_attempt_at == now + timedelta(minutes=8)
        assert quarantined.reprocess_status == "quarantined"
        assert quarantined.attempt_count == 5
        assert quarantined.next_attempt_at is None


def test_normalizer_rejected_record_counts_as_retry_without_raising(
    dead_letter_factory,
) -> None:
    now = datetime(2026, 8, 29, 1, 0, 0)
    dirtied: list[str] = []

    def reject_record(_source_id, factory, _run_id) -> None:
        with factory.begin() as session:
            row = session.get(DeadLetter, 1)
            row.reprocess_status = "pending"
            row.error_detail = "ValueError: still missing field"

    result = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=1,
        normalizer=reject_record,
        dirty_marker=lambda *_args, **_kwargs: dirtied.append("dirty"),
        clock=lambda: now,
    )

    assert result.resolved_count == 0
    assert result.retry_count == 1
    assert dirtied == []
    with dead_letter_factory() as session:
        row = session.get(DeadLetter, 1)
        assert row.reprocess_status == "pending"
        assert row.attempt_count == 1
        assert row.next_attempt_at == now + timedelta(minutes=1)


def test_capacity_pause_releases_claims_without_consuming_attempt(dead_letter_factory) -> None:
    now = datetime(2026, 8, 29, 1, 0, 0)
    checks = iter([None, "disk_source_pause"])

    result = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=2,
        normalizer=lambda *_args: pytest.fail("normalizer must not run while paused"),
        dirty_marker=lambda *_args, **_kwargs: None,
        pause_reason=lambda: next(checks),
        clock=lambda: now,
    )

    assert result.paused
    assert result.pause_reason == "disk_source_pause"
    with dead_letter_factory() as session:
        rows = list(
            session.scalars(
                select(DeadLetter).where(DeadLetter.dead_letter_id.in_([1, 2]))
            )
        )
        assert all(row.reprocess_status == "pending" for row in rows)
        assert all(row.attempt_count == 0 for row in rows)
        assert all(row.next_attempt_at == now for row in rows)
