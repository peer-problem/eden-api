from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.enums import Availability
from app.products.snapshots import (
    MAX_NEW_SNAPSHOT_UNCOMPRESSED_BYTES,
    SnapshotCandidate,
    SnapshotPayloadTooLarge,
    SnapshotPublisher,
    SnapshotRollbackUnavailable,
    restore_previous_snapshot,
    retain_snapshots,
)
from app.readmodels.repository import MariaDBReadRepository
from app.repositories.models import (
    ProvenanceEdge,
    ReadModelHead,
    ReadModelPayload,
    ReadModelSnapshot,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    for table in (
        ReadModelPayload.__table__,
        ReadModelSnapshot.__table__,
        ReadModelHead.__table__,
        ProvenanceEdge.__table__,
    ):
        table.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _candidate(
    value: int,
    calculated_at: datetime,
    *,
    metadata_version: int | None = None,
) -> SnapshotCandidate:
    observed_at = datetime(2026, 8, 1, tzinfo=UTC)
    return SnapshotCandidate(
        endpoint="example_product",
        lookup_key='{"scope":"global"}',
        data={"value": value},
        metadata={"version": metadata_version if metadata_version is not None else value},
        input_watermarks={"SRC_EXAMPLE": observed_at},
        formula_versions={"value": "value_v1"},
        observed_at=observed_at,
        source_updated_at=observed_at,
        ingested_at=observed_at,
        calculated_at=calculated_at,
        availability=Availability.AVAILABLE,
    )


def test_publication_deduplicates_payload_and_switches_head(
    session_factory: sessionmaker[Session],
) -> None:
    publisher = SnapshotPublisher(session_factory)
    first = publisher.publish(
        _candidate(7, datetime(2026, 8, 29, 1, tzinfo=UTC), metadata_version=1)
    )
    second = publisher.publish(
        _candidate(7, datetime(2026, 8, 29, 2, tzinfo=UTC), metadata_version=2)
    )

    assert first.snapshot_id != second.snapshot_id
    assert first.payload_id == second.payload_id
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ReadModelPayload)) == 1
        head = session.get(
            ReadModelHead,
            {
                "endpoint": second.endpoint,
                "lookup_key_hash": second.lookup_key_hash,
            },
        )
        assert head is not None
        assert head.snapshot_id == second.snapshot_id
        assert session.get(ReadModelSnapshot, first.snapshot_id).state == "retired"
        current = session.get(ReadModelSnapshot, second.snapshot_id)
        assert current.state == "ready"
        assert current.data is None

        hydrated = MariaDBReadRepository(session_factory)._current_snapshot(
            session,
            second.endpoint,
            second.lookup_key,
        )
        assert hydrated is not None
        assert hydrated.data == {"value": 7}


def test_failed_staging_never_changes_current_head(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = SnapshotPublisher(session_factory)
    current = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))
    candidate = _candidate(2, datetime(2026, 8, 29, 2, tzinfo=UTC))

    def fail_provenance(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("provenance failed")

    monkeypatch.setattr(publisher, "_insert_provenance", fail_provenance)
    with pytest.raises(RuntimeError, match="provenance failed"):
        publisher.publish(candidate)

    with session_factory() as session:
        head = session.get(
            ReadModelHead,
            {
                "endpoint": current.endpoint,
                "lookup_key_hash": current.lookup_key_hash,
            },
        )
        assert head is not None
        assert head.snapshot_id == current.snapshot_id
        staged = session.scalar(
            select(ReadModelSnapshot).where(
                ReadModelSnapshot.snapshot_version != current.snapshot_version
            )
        )
        assert staged is not None
        assert staged.state == "staging"


def test_oversized_snapshot_cannot_stage_or_replace_current_head(
    session_factory: sessionmaker[Session],
) -> None:
    publisher = SnapshotPublisher(session_factory)
    current = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))
    oversized = replace(
        _candidate(2, datetime(2026, 8, 29, 2, tzinfo=UTC)),
        data={"value": "x" * MAX_NEW_SNAPSHOT_UNCOMPRESSED_BYTES},
    )

    with pytest.raises(
        SnapshotPayloadTooLarge,
        match=f"limit is {MAX_NEW_SNAPSHOT_UNCOMPRESSED_BYTES}",
    ):
        publisher.publish(oversized)

    with session_factory() as session:
        head = session.get(
            ReadModelHead,
            {
                "endpoint": current.endpoint,
                "lookup_key_hash": current.lookup_key_hash,
            },
        )
        assert head is not None
        assert head.snapshot_id == current.snapshot_id
        assert session.scalar(select(func.count()).select_from(ReadModelSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(ReadModelPayload)) == 1


def test_retention_dry_runs_then_cleans_abandoned_staging(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = SnapshotPublisher(session_factory)
    current = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))

    def fail_provenance(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("provenance failed")

    monkeypatch.setattr(publisher, "_insert_provenance", fail_provenance)
    with pytest.raises(RuntimeError, match="provenance failed"):
        publisher.publish(_candidate(2, datetime(2026, 8, 29, 2, tzinfo=UTC)))

    with session_factory() as session:
        staged = session.scalar(
            select(ReadModelSnapshot).where(ReadModelSnapshot.state == "staging")
        )
        assert staged is not None
        staged_id = staged.snapshot_id
        staged_payload_id = staged.payload_id
    with session_factory.begin() as session:
        session.add(
            ProvenanceEdge(
                provenance_id=1,
                output_type="read_model_snapshot",
                output_id=staged_id,
                raw_record_id=1,
                formula_version="value_v1",
                created_at=datetime(2026, 8, 29, 2),
            )
        )

    cutoff = datetime(2026, 8, 29, 3, tzinfo=UTC)
    dry_run = retain_snapshots(session_factory, older_than=cutoff)
    assert dry_run.dry_run is True
    assert dry_run.candidate_snapshot_ids == (staged_id,)
    assert dry_run.provenance_rows == 1

    result = retain_snapshots(session_factory, older_than=cutoff, dry_run=False)
    assert result.deleted_snapshots == 1
    assert result.deleted_provenance_rows == 1
    assert result.deleted_payloads == 1
    with session_factory() as session:
        assert session.get(ReadModelSnapshot, staged_id) is None
        assert session.get(ReadModelPayload, staged_payload_id) is None
        head = session.get(
            ReadModelHead,
            {
                "endpoint": current.endpoint,
                "lookup_key_hash": current.lookup_key_hash,
            },
        )
        assert head is not None
        assert head.snapshot_id == current.snapshot_id
        assert session.get(ReadModelSnapshot, current.snapshot_id) is not None


def test_retention_never_deletes_a_head_even_if_state_is_staging(
    session_factory: sessionmaker[Session],
) -> None:
    publisher = SnapshotPublisher(session_factory)
    current = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))
    with session_factory.begin() as session:
        snapshot = session.get(ReadModelSnapshot, current.snapshot_id)
        assert snapshot is not None
        snapshot.state = "staging"

    result = retain_snapshots(
        session_factory,
        older_than=datetime(2026, 8, 30, tzinfo=UTC),
        dry_run=False,
    )

    assert result.candidate_snapshot_ids == ()
    assert result.deleted_snapshots == 0
    with session_factory() as session:
        assert session.get(ReadModelSnapshot, current.snapshot_id) is not None


def test_reader_requires_a_current_head_and_content_addressed_payload(
    session_factory: sessionmaker[Session],
) -> None:
    prepared = SnapshotPublisher(session_factory).publish(
        _candidate(3, datetime(2026, 8, 29, 1, tzinfo=UTC))
    )
    with session_factory.begin() as session:
        session.query(ReadModelHead).delete()

    with session_factory() as session:
        loaded = MariaDBReadRepository(session_factory)._current_snapshot(
            session,
            prepared.endpoint,
            prepared.lookup_key,
        )
        assert loaded is None


def test_retention_preserves_current_and_two_rollback_snapshots(
    session_factory: sessionmaker[Session],
) -> None:
    publisher = SnapshotPublisher(session_factory)
    first = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))
    second = publisher.publish(_candidate(2, datetime(2026, 8, 29, 2, tzinfo=UTC)))
    publisher.publish(_candidate(3, datetime(2026, 8, 29, 3, tzinfo=UTC)))
    current = publisher.publish(_candidate(4, datetime(2026, 8, 29, 4, tzinfo=UTC)))
    cutoff = datetime(2026, 8, 30, tzinfo=UTC)
    with session_factory.begin() as session:
        session.add(
            ProvenanceEdge(
                provenance_id=1,
                output_type="read_model_snapshot",
                output_id=first.snapshot_id,
                raw_record_id=1,
                formula_version="value_v1",
                created_at=datetime(2026, 8, 29, 1),
            )
        )

    dry_run = retain_snapshots(session_factory, older_than=cutoff)
    assert dry_run.dry_run is True
    assert dry_run.candidate_snapshot_ids == (first.snapshot_id,)
    assert dry_run.provenance_rows == 1

    result = retain_snapshots(session_factory, older_than=cutoff, dry_run=False)
    assert result.deleted_snapshots == 1
    assert result.deleted_provenance_rows == 1
    with session_factory() as session:
        assert session.get(ReadModelSnapshot, first.snapshot_id) is None
        assert session.scalar(select(func.count()).select_from(ProvenanceEdge)) == 0
        assert session.get(ReadModelSnapshot, second.snapshot_id) is not None
        assert session.get(ReadModelSnapshot, current.snapshot_id) is not None
        head = session.get(
            ReadModelHead,
            {
                "endpoint": current.endpoint,
                "lookup_key_hash": current.lookup_key_hash,
            },
        )
        assert head is not None
        assert head.snapshot_id == current.snapshot_id


def test_retention_bounds_total_provenance_deletes_per_invocation(
    session_factory: sessionmaker[Session],
) -> None:
    publisher = SnapshotPublisher(session_factory)
    retired = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))
    rollback = publisher.publish(_candidate(2, datetime(2026, 8, 29, 2, tzinfo=UTC)))
    publisher.publish(_candidate(3, datetime(2026, 8, 29, 3, tzinfo=UTC)))
    current = publisher.publish(_candidate(4, datetime(2026, 8, 29, 4, tzinfo=UTC)))
    with session_factory.begin() as session:
        session.add_all(
            [
                ProvenanceEdge(
                    provenance_id=index,
                    output_type="read_model_snapshot",
                    output_id=retired.snapshot_id,
                    raw_record_id=index,
                    formula_version="value_v1",
                    created_at=datetime(2026, 8, 29, 1),
                )
                for index in range(1, 4)
            ]
        )

    cutoff = datetime(2026, 8, 30, tzinfo=UTC)
    first = retain_snapshots(
        session_factory,
        older_than=cutoff,
        dry_run=False,
        provenance_batch_size=2,
        provenance_delete_limit=2,
    )

    assert first.deleted_provenance_rows == 2
    assert first.deleted_snapshots == 0
    with session_factory() as session:
        assert session.get(ReadModelSnapshot, retired.snapshot_id) is not None
        assert session.scalar(select(func.count()).select_from(ProvenanceEdge)) == 1

    second = retain_snapshots(
        session_factory,
        older_than=cutoff,
        dry_run=False,
        provenance_batch_size=2,
        provenance_delete_limit=2,
    )

    assert second.deleted_provenance_rows == 1
    assert second.deleted_snapshots == 1
    with session_factory() as session:
        assert session.get(ReadModelSnapshot, retired.snapshot_id) is None
        assert session.get(ReadModelSnapshot, rollback.snapshot_id) is not None
        assert session.get(ReadModelSnapshot, current.snapshot_id) is not None
        assert session.scalar(select(func.count()).select_from(ProvenanceEdge)) == 0


def test_corrupt_current_snapshot_can_atomically_restore_previous_head(
    session_factory: sessionmaker[Session],
) -> None:
    publisher = SnapshotPublisher(session_factory)
    previous = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))
    current = publisher.publish(_candidate(2, datetime(2026, 8, 29, 2, tzinfo=UTC)))
    with session_factory.begin() as session:
        payload = session.get(ReadModelPayload, current.payload_id)
        assert payload is not None
        payload.payload_blob = b"corrupt"

    restored_id = restore_previous_snapshot(
        session_factory,
        endpoint=current.endpoint,
        lookup_key=current.lookup_key,
    )

    assert restored_id == previous.snapshot_id
    with session_factory() as session:
        head = session.get(
            ReadModelHead,
            {"endpoint": current.endpoint, "lookup_key_hash": current.lookup_key_hash},
        )
        assert head is not None
        assert head.snapshot_id == previous.snapshot_id
        assert session.get(ReadModelSnapshot, previous.snapshot_id).state == "ready"
        assert session.get(ReadModelSnapshot, current.snapshot_id).state == "retired"
        loaded = MariaDBReadRepository(session_factory)._current_snapshot(
            session,
            current.endpoint,
            current.lookup_key,
        )
        assert loaded is not None
        assert loaded.data == {"value": 1}


def test_reader_reuses_immutable_payload_from_bounded_cache(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = SnapshotPublisher(session_factory).publish(
        _candidate(9, datetime(2026, 8, 29, 1, tzinfo=UTC))
    )
    repository = MariaDBReadRepository(session_factory)
    from app.readmodels import repository as repository_module

    original_decode = repository_module.decode_payload
    decode_count = 0

    def counted_decode(*args: object, **kwargs: object) -> object:
        nonlocal decode_count
        decode_count += 1
        return original_decode(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository_module, "decode_payload", counted_decode)

    with session_factory() as session:
        first = repository._current_snapshot(session, current.endpoint, current.lookup_key)
    with session_factory() as session:
        second = repository._current_snapshot(session, current.endpoint, current.lookup_key)

    assert first is not None and first.data == {"value": 9}
    assert second is not None and second.data == {"value": 9}
    assert decode_count == 1


def test_snapshot_restore_keeps_head_when_no_valid_rollback_exists(
    session_factory: sessionmaker[Session],
) -> None:
    current = SnapshotPublisher(session_factory).publish(
        _candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC))
    )

    with pytest.raises(SnapshotRollbackUnavailable):
        restore_previous_snapshot(
            session_factory,
            endpoint=current.endpoint,
            lookup_key=current.lookup_key,
        )

    with session_factory() as session:
        head = session.get(
            ReadModelHead,
            {"endpoint": current.endpoint, "lookup_key_hash": current.lookup_key_hash},
        )
        assert head is not None
        assert head.snapshot_id == current.snapshot_id


def test_republishing_current_head_skips_staging_and_provenance(session_factory, monkeypatch):
    publisher = SnapshotPublisher(session_factory)
    candidate = _candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC))
    first = publisher.publish(candidate)

    def unexpected(*args):
        raise AssertionError("identical head must not write payload or provenance")

    monkeypatch.setattr(publisher, "_stage_payload", unexpected)
    monkeypatch.setattr(publisher, "_insert_provenance", unexpected)
    assert publisher.publish(candidate).snapshot_id == first.snapshot_id


def test_retention_cleans_abandoned_ready_and_respects_pause(session_factory):
    publisher = SnapshotPublisher(session_factory)
    first = publisher.publish(_candidate(1, datetime(2026, 8, 29, 1, tzinfo=UTC)))
    current = publisher.publish(_candidate(2, datetime(2026, 8, 29, 2, tzinfo=UTC)))
    with session_factory.begin() as session:
        session.get(ReadModelSnapshot, first.snapshot_id).state = "ready"
    cutoff = datetime(2026, 8, 30, tzinfo=UTC)
    paused = retain_snapshots(
        session_factory, older_than=cutoff, dry_run=False, pause_reason=lambda: "api_p95"
    )
    assert paused.deleted_snapshots == 0
    done = retain_snapshots(session_factory, older_than=cutoff, dry_run=False)
    assert done.deleted_snapshots == 1
    with session_factory() as session:
        assert session.get(ReadModelSnapshot, current.snapshot_id) is not None
