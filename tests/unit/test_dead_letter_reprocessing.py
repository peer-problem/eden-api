from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine, func, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.normalization import registry
from app.normalization.dead_letters import reprocess_dead_letters
from app.normalization.public_data import (
    _REPLAY_RAW_RECORD_IDS,
    _add_dead_letter,
    _finish_run,
    _raw_record_replay_scope,
)
from app.repositories.models import DeadLetter, IngestionRun, RawRecord


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@pytest.fixture
def dead_letter_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    IngestionRun.__table__.create(engine)
    RawRecord.__table__.create(engine)
    DeadLetter.__table__.create(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    now = datetime(2026, 8, 29, 0, 0, 0)
    with factory.begin() as session:
        session.add(
            IngestionRun(
                run_id="run-1",
                job_id="job-1",
                source_id="SRC_YOUTUBE",
                idempotency_key="run-1",
                status="failed",
                request_scope={},
                started_at=now,
                finished_at=now,
                raw_count=2,
                normalized_count=7,
                error_summary=None,
                test_run_id=None,
            )
        )
        for index in range(3):
            raw_id = index + 1
            session.add(
                RawRecord(
                    raw_record_id=raw_id,
                    source_id="SRC_YOUTUBE",
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


def test_reprocessor_claims_only_one_run_per_batch_and_is_resumable(
    dead_letter_factory,
) -> None:
    normalized: list[tuple[str, str]] = []
    dirtied: list[tuple[str, str]] = []
    now = datetime(2026, 8, 29, 1, 0, 0)

    result = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=2,
        normalizer=lambda source_id, _factory, run_id, _raw_ids: normalized.append(
            (source_id, run_id)
        ),
        dirty_marker=lambda source_id, _factory, *, watermark: dirtied.append(
            (source_id, watermark)
        ),
        clock=lambda: now,
    )

    assert result.claimed_count == 1
    assert result.resolved_count == 1
    assert normalized == [("SRC_YOUTUBE", "run-1")]
    assert dirtied == [("SRC_YOUTUBE", "dead-letter:1")]
    with dead_letter_factory() as session:
        statuses = list(
            session.execute(
                select(DeadLetter.dead_letter_id, DeadLetter.reprocess_status).order_by(
                    DeadLetter.dead_letter_id
                )
            )
        )
    assert statuses == [(1, "resolved"), (2, "pending"), (3, "pending")]

    resumed = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=2,
        normalizer=lambda source_id, _factory, run_id, _raw_ids: normalized.append(
            (source_id, run_id)
        ),
        dirty_marker=lambda source_id, _factory, *, watermark: dirtied.append(
            (source_id, watermark)
        ),
        clock=lambda: now,
    )

    assert resumed.claimed_count == 1
    assert resumed.resolved_count == 1
    assert normalized[-1] == ("SRC_YOUTUBE", "run-2")
    with dead_letter_factory() as session:
        statuses = list(
            session.execute(
                select(DeadLetter.dead_letter_id, DeadLetter.reprocess_status).order_by(
                    DeadLetter.dead_letter_id
                )
            )
        )
    assert statuses == [(1, "resolved"), (2, "resolved"), (3, "pending")]


def test_reprocessor_claims_up_to_batch_size_from_the_selected_run(
    dead_letter_factory,
) -> None:
    with dead_letter_factory.begin() as session:
        session.get(RawRecord, 2).run_id = "run-1"

    normalized: list[tuple[str, str]] = []
    result = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=2,
        normalizer=lambda source_id, _factory, run_id, _raw_ids: normalized.append(
            (source_id, run_id)
        ),
        dirty_marker=lambda *_args, **_kwargs: None,
        clock=lambda: datetime(2026, 8, 29, 1, 0, 0),
    )

    assert result.claimed_count == 2
    assert result.resolved_count == 2
    assert normalized == [("SRC_YOUTUBE", "run-1")]
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
        session.get(RawRecord, 2).run_id = "run-1"
        session.get(DeadLetter, 1).attempt_count = 3
        session.get(DeadLetter, 2).attempt_count = 4

    def fail(_source_id, _factory, _run_id, _raw_ids):
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

    def reject_record(_source_id, factory, _run_id, _raw_ids) -> None:
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
    with dead_letter_factory.begin() as session:
        session.get(RawRecord, 2).run_id = "run-1"

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


@pytest.mark.parametrize("terminal_status", ["resolved", "quarantined"])
def test_full_run_replay_preserves_other_raw_terminal_dead_letter(
    dead_letter_factory,
    terminal_status: str,
) -> None:
    now = datetime(2026, 8, 29, 1, 0, 0)
    terminal_reprocessed_at = now - timedelta(hours=1)
    with dead_letter_factory.begin() as session:
        raw_2 = session.get(RawRecord, 2)
        raw_2.run_id = "run-1"
        terminal = session.get(DeadLetter, 2)
        terminal.reprocess_status = terminal_status
        terminal.error_detail = "original terminal failure"
        terminal.reprocessed_at = terminal_reprocessed_at
        terminal.attempt_count = 4
        terminal.last_attempt_at = now - timedelta(hours=2)

    replayed_raw_ids: list[tuple[int, ...]] = []

    def replay_entire_run(_source_id, factory, run_id, raw_record_ids) -> None:
        replayed_raw_ids.append(raw_record_ids)
        with factory.begin() as session:
            raw_records = list(
                session.scalars(
                    select(RawRecord)
                    .where(
                        RawRecord.run_id == run_id,
                        RawRecord.raw_record_id.in_(raw_record_ids),
                    )
                    .order_by(RawRecord.raw_record_id)
                )
            )
            assert [raw.raw_record_id for raw in raw_records] == [1]
            for raw in raw_records:
                _add_dead_letter(
                    session,
                    raw,
                    "schema_drift",
                    ValueError(f"still invalid raw {raw.raw_record_id}"),
                )

    result = reprocess_dead_letters(
        dead_letter_factory,
        batch_size=1,
        normalizer=replay_entire_run,
        dirty_marker=lambda *_args, **_kwargs: None,
        clock=lambda: now,
    )

    assert result.claimed_count == 1
    assert result.resolved_count == 0
    assert result.retry_count == 1
    assert replayed_raw_ids == [(1,)]
    with dead_letter_factory() as session:
        assert session.scalar(select(func.count()).select_from(DeadLetter)) == 3
        claimed = session.get(DeadLetter, 1)
        terminal = session.get(DeadLetter, 2)
        assert claimed.reprocess_status == "pending"
        assert claimed.attempt_count == 1
        assert claimed.next_attempt_at == now + timedelta(minutes=1)
        assert terminal.reprocess_status == terminal_status
        assert terminal.error_detail == "original terminal failure"
        assert terminal.reprocessed_at == terminal_reprocessed_at
        assert terminal.attempt_count == 4
        assert terminal.last_attempt_at == now - timedelta(hours=2)


def test_add_dead_letter_prefers_claimed_retrying_duplicate(
    dead_letter_factory,
) -> None:
    now = datetime(2026, 8, 29, 1, 0, 0)
    with dead_letter_factory.begin() as session:
        original_pending = session.get(DeadLetter, 1)
        original_pending.error_detail = "older pending failure"
        session.add(
            DeadLetter(
                dead_letter_id=4,
                raw_record_id=1,
                error_code="schema_drift",
                error_detail="claimed failure",
                created_at=now,
                reprocess_status="retrying",
                reprocessed_at=now - timedelta(hours=1),
                attempt_count=2,
                next_attempt_at=None,
                last_attempt_at=now,
            )
        )

    with dead_letter_factory.begin() as session:
        raw = session.get(RawRecord, 1)
        _add_dead_letter(session, raw, "schema_drift", ValueError("latest failure"))

    with dead_letter_factory() as session:
        older_pending = session.get(DeadLetter, 1)
        claimed = session.get(DeadLetter, 4)
        assert older_pending.reprocess_status == "pending"
        assert older_pending.error_detail == "older pending failure"
        assert claimed.reprocess_status == "pending"
        assert claimed.error_detail == "ValueError: latest failure"
        assert claimed.reprocessed_at is None


def test_add_dead_letter_records_a_genuinely_new_raw_failure(
    dead_letter_factory,
) -> None:
    with dead_letter_factory.begin() as session:
        raw = session.get(RawRecord, 1)
        _add_dead_letter(session, raw, "new_schema_drift", ValueError("new failure"))

    with dead_letter_factory() as session:
        added = session.scalar(
            select(DeadLetter).where(DeadLetter.error_code == "new_schema_drift")
        )
        assert added is not None
        assert added.raw_record_id == 1
        assert added.reprocess_status == "pending"
        assert added.error_detail == "ValueError: new failure"


def test_add_dead_letter_deduplicates_repeated_failure_with_autoflush_disabled(
    dead_letter_factory,
) -> None:
    with dead_letter_factory.begin() as session:
        raw = session.get(RawRecord, 1)
        for index in range(100):
            _add_dead_letter(
                session,
                raw,
                "repeated_schema_drift",
                ValueError(f"failure {index}"),
            )

    with dead_letter_factory() as session:
        rows = list(
            session.scalars(
                select(DeadLetter).where(
                    DeadLetter.raw_record_id == 1,
                    DeadLetter.error_code == "repeated_schema_drift",
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].error_detail == "ValueError: failure 99"


def test_targeted_replay_preserves_original_run_totals(dead_letter_factory) -> None:
    with dead_letter_factory.begin() as session, _raw_record_replay_scope((1,)):
        _finish_run(session, "run-1", 1)

    with dead_letter_factory() as session:
        run = session.get(IngestionRun, "run-1")
        assert run.normalized_count == 7
        assert run.status == "failed"


def test_registry_targets_only_row_identity_normalizers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, frozenset[int] | None]] = []

    def capture(source_id, _factory, _run_id):
        observed.append((source_id, _REPLAY_RAW_RECORD_IDS.get()))

    monkeypatch.setattr(registry, "_normalize_run", capture)

    registry.reprocess_normalization_run(
        "SRC_SEMAS_SHOPS",
        object(),
        "run-shops",
        (4, 7),
    )
    registry.reprocess_normalization_run(
        "SRC_KTO_REGIONAL_VISITORS",
        lambda: nullcontext(SimpleNamespace(get=lambda *_args: None)),
        "run-aggregate",
        (9,),
    )

    assert observed == [
        ("SRC_SEMAS_SHOPS", frozenset({4, 7})),
        ("SRC_KTO_REGIONAL_VISITORS", None),
    ]
