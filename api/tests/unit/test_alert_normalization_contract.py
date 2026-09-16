from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import BigInteger, create_engine, func, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.domain.enums import RunStatus
from app.normalization.alerts import _add_dead_letter, normalize_official_alert_run
from app.repositories.models import (
    AlertDocument,
    DeadLetter,
    IngestionRun,
    RawRecord,
)


@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def _compile_longtext_as_text(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


def test_alert_document_identity_includes_market_country() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in AlertDocument.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert (
        "source_id",
        "country_id",
        "canonical_url_hash",
    ) in unique_columns


def test_invalid_alert_record_is_dead_lettered_and_fails_run() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    IngestionRun.__table__.create(engine)
    RawRecord.__table__.create(engine)
    DeadLetter.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 29, 1, 0, 0)
    with factory.begin() as session:
        session.add(
            IngestionRun(
                run_id="run-alert-invalid",
                job_id="ingest:SRC_OFFICIAL_TEST",
                source_id="SRC_OFFICIAL_TEST",
                idempotency_key="alert-invalid",
                status=RunStatus.SUCCEEDED,
                request_scope={},
                started_at=now,
                finished_at=now,
                raw_count=1,
                normalized_count=0,
                error_summary=None,
                test_run_id=None,
            )
        )
        session.add(
            RawRecord(
                raw_record_id=1,
                source_id="SRC_OFFICIAL_TEST",
                external_key="broken",
                observed_at=now,
                source_updated_at=now,
                ingested_at=now,
                content_type="application/json",
                body_json={"title": "missing required fields"},
                body_text=None,
                content_hash="hash",
                run_id="run-alert-invalid",
                tombstone=False,
            )
        )

    assert normalize_official_alert_run(
        factory,
        "run-alert-invalid",
        "SRC_OFFICIAL_TEST",
    ) == 0
    with factory() as session:
        run = session.get(IngestionRun, "run-alert-invalid")
        dead_letter = session.scalar(select(DeadLetter))
        assert run is not None
        assert run.status == RunStatus.FAILED
        assert run.normalized_count == 0
        assert dead_letter is not None
        assert dead_letter.error_code == "alert_schema_drift"
        assert dead_letter.reprocess_status == "pending"


def _alert_dead_letter_factory() -> sessionmaker:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RawRecord.__table__.create(engine)
    DeadLetter.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 29, 1, 0, 0)
    with factory.begin() as session:
        session.add(
            RawRecord(
                raw_record_id=1,
                source_id="SRC_OFFICIAL_TEST",
                external_key="broken",
                observed_at=now,
                source_updated_at=now,
                ingested_at=now,
                content_type="application/json",
                body_json={"title": "missing required fields"},
                body_text=None,
                content_hash="hash",
                run_id="run-alert-invalid",
                tombstone=False,
            )
        )
    return factory


@pytest.mark.parametrize("terminal_status", ["resolved", "quarantined"])
def test_alert_dead_letter_replay_preserves_terminal_record(
    terminal_status: str,
) -> None:
    factory = _alert_dead_letter_factory()
    now = datetime(2026, 8, 29, 1, 0, 0)
    reprocessed_at = now - timedelta(hours=1)
    with factory.begin() as session:
        session.add(
            DeadLetter(
                dead_letter_id=1,
                raw_record_id=1,
                error_code="alert_schema_drift",
                error_detail="original terminal failure",
                created_at=now,
                reprocess_status=terminal_status,
                reprocessed_at=reprocessed_at,
                attempt_count=4,
                next_attempt_at=None,
                last_attempt_at=now - timedelta(hours=2),
            )
        )

    with factory.begin() as session:
        raw = session.get(RawRecord, 1)
        assert raw is not None
        _add_dead_letter(session, raw, "alert_schema_drift", "still invalid")

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DeadLetter)) == 1
        terminal = session.get(DeadLetter, 1)
        assert terminal is not None
        assert terminal.reprocess_status == terminal_status
        assert terminal.error_detail == "original terminal failure"
        assert terminal.reprocessed_at == reprocessed_at
        assert terminal.attempt_count == 4
        assert terminal.last_attempt_at == now - timedelta(hours=2)


def test_alert_dead_letter_prefers_claimed_retrying_duplicate() -> None:
    factory = _alert_dead_letter_factory()
    now = datetime(2026, 8, 29, 1, 0, 0)
    with factory.begin() as session:
        session.add_all(
            [
                DeadLetter(
                    dead_letter_id=1,
                    raw_record_id=1,
                    error_code="alert_schema_drift",
                    error_detail="older pending failure",
                    created_at=now - timedelta(minutes=1),
                    reprocess_status="pending",
                    reprocessed_at=None,
                    attempt_count=0,
                    next_attempt_at=None,
                    last_attempt_at=None,
                ),
                DeadLetter(
                    dead_letter_id=2,
                    raw_record_id=1,
                    error_code="alert_schema_drift",
                    error_detail="claimed failure",
                    created_at=now,
                    reprocess_status="retrying",
                    reprocessed_at=now - timedelta(hours=1),
                    attempt_count=2,
                    next_attempt_at=None,
                    last_attempt_at=now,
                ),
            ]
        )

    with factory.begin() as session:
        raw = session.get(RawRecord, 1)
        assert raw is not None
        _add_dead_letter(session, raw, "alert_schema_drift", "latest failure")

    with factory() as session:
        older_pending = session.get(DeadLetter, 1)
        claimed = session.get(DeadLetter, 2)
        assert older_pending is not None
        assert claimed is not None
        assert older_pending.error_detail == "older pending failure"
        assert claimed.reprocess_status == "pending"
        assert claimed.error_detail == "ValueError: latest failure"
        assert claimed.reprocessed_at is None
