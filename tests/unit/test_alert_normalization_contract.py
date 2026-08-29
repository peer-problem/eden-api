from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.domain.enums import RunStatus
from app.normalization.alerts import normalize_official_alert_run
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
