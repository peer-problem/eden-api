from __future__ import annotations

import hashlib
import json
import zlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from time import monotonic
from typing import Any

from sqlalchemy import delete, exists, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.domain.enums import Availability
from app.observability.metrics import record_snapshot_publish
from app.readmodels.keys import lookup_key_hash
from app.repositories.models import (
    ProvenanceEdge,
    ReadModelHead,
    ReadModelPayload,
    ReadModelSnapshot,
)

PAYLOAD_ENCODING = "json-zlib-v1"
MAX_SNAPSHOT_DECODE_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_UNCOMPRESSED_BYTES = MAX_SNAPSHOT_DECODE_BYTES
MAX_NEW_SNAPSHOT_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
PROVENANCE_BATCH_SIZE = 500
MAX_RETENTION_PROVENANCE_ROWS = 50_000


class SnapshotPublishBusy(RuntimeError):
    pass


class SnapshotPayloadError(ValueError):
    pass


class SnapshotPayloadTooLarge(SnapshotPayloadError):
    pass


class SnapshotRollbackUnavailable(SnapshotPayloadError):
    pass


@dataclass(frozen=True, slots=True)
class SnapshotCandidate:
    endpoint: str
    lookup_key: str
    data: dict[str, Any] | list[Any] | None
    metadata: dict[str, Any]
    input_watermarks: dict[str, datetime]
    formula_versions: dict[str, str]
    observed_at: datetime
    source_updated_at: datetime
    ingested_at: datetime
    calculated_at: datetime
    availability: Availability
    quality_flags: tuple[str, ...] = ()
    raw_record_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EncodedPayload:
    payload_id: str
    payload_hash: str
    encoding: str
    payload_blob: bytes
    uncompressed_bytes: int
    compressed_bytes: int


@dataclass(frozen=True, slots=True)
class PreparedSnapshot:
    snapshot_id: str
    endpoint: str
    lookup_key: str
    lookup_key_hash: str
    snapshot_version: str
    payload_id: str
    payload_hash: str
    payload_encoding: str
    payload_blob: bytes
    uncompressed_bytes: int
    compressed_bytes: int
    data: dict[str, Any] | list[Any] | None
    metadata_json: dict[str, Any]
    input_watermarks: dict[str, str]
    formula_versions: dict[str, str]
    observed_at: datetime
    source_updated_at: datetime
    ingested_at: datetime
    calculated_at: datetime
    as_of: datetime
    availability: str
    quality_flags: list[str]
    raw_record_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RetentionResult:
    dry_run: bool
    candidate_snapshot_ids: tuple[str, ...]
    provenance_rows: int
    deleted_snapshots: int = 0
    deleted_provenance_rows: int = 0
    deleted_payloads: int = 0


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _database_time(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return _aware_utc(value, "JSON datetime").isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Snapshot JSON contains unsupported value: {type(value).__name__}")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def encode_payload(
    data: dict[str, Any] | list[Any] | None,
    *,
    max_uncompressed_bytes: int = MAX_NEW_SNAPSHOT_UNCOMPRESSED_BYTES,
) -> EncodedPayload:
    if max_uncompressed_bytes < 1:
        raise ValueError("max_uncompressed_bytes must be positive")
    serialized = _canonical_json_bytes(_json_safe(data))
    if len(serialized) > max_uncompressed_bytes:
        raise SnapshotPayloadTooLarge(
            f"Snapshot payload is {len(serialized)} bytes; limit is {max_uncompressed_bytes}"
        )
    payload_hash = hashlib.sha256(serialized).hexdigest()
    compressed = zlib.compress(serialized, level=6)
    return EncodedPayload(
        payload_id=f"payload_{payload_hash[:56]}",
        payload_hash=payload_hash,
        encoding=PAYLOAD_ENCODING,
        payload_blob=compressed,
        uncompressed_bytes=len(serialized),
        compressed_bytes=len(compressed),
    )


def payload_size_limit(endpoint: str) -> int:
    """Return the absolute decode ceiling retained for existing snapshot payloads."""
    del endpoint
    return MAX_SNAPSHOT_DECODE_BYTES


def publish_payload_size_limit(endpoint: str) -> int:
    """Return the uniform ceiling for newly published snapshot payloads."""
    del endpoint
    return MAX_NEW_SNAPSHOT_UNCOMPRESSED_BYTES


def decode_payload(
    payload_blob: bytes,
    *,
    encoding: str,
    uncompressed_bytes: int,
    compressed_bytes: int,
    max_uncompressed_bytes: int = MAX_SNAPSHOT_UNCOMPRESSED_BYTES,
    expected_hash: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    if encoding != PAYLOAD_ENCODING:
        raise SnapshotPayloadError(f"Unsupported snapshot payload encoding: {encoding}")
    if uncompressed_bytes < 0 or uncompressed_bytes > max_uncompressed_bytes:
        raise SnapshotPayloadTooLarge(
            f"Declared snapshot size {uncompressed_bytes} exceeds limit {max_uncompressed_bytes}"
        )
    if compressed_bytes != len(payload_blob):
        raise SnapshotPayloadError("Compressed snapshot size does not match its metadata")

    decompressor = zlib.decompressobj()
    try:
        decoded = decompressor.decompress(payload_blob, max_uncompressed_bytes + 1)
        if len(decoded) > max_uncompressed_bytes or decompressor.unconsumed_tail:
            raise SnapshotPayloadTooLarge("Decompressed snapshot exceeds the configured limit")
        decoded += decompressor.flush(max_uncompressed_bytes + 1 - len(decoded))
    except zlib.error as exc:
        raise SnapshotPayloadError("Snapshot payload is not valid zlib data") from exc
    if len(decoded) > max_uncompressed_bytes:
        raise SnapshotPayloadTooLarge("Decompressed snapshot exceeds the configured limit")
    if not decompressor.eof or decompressor.unused_data:
        raise SnapshotPayloadError("Snapshot payload contains an incomplete or trailing stream")
    if len(decoded) != uncompressed_bytes:
        raise SnapshotPayloadError("Uncompressed snapshot size does not match its metadata")
    if expected_hash is not None and hashlib.sha256(decoded).hexdigest() != expected_hash:
        raise SnapshotPayloadError("Snapshot payload hash mismatch")
    try:
        value = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotPayloadError("Snapshot payload is not valid UTF-8 JSON") from exc
    if value is not None and not isinstance(value, (dict, list)):
        raise SnapshotPayloadError("Snapshot payload root must be an object, array, or null")
    return value


def prepare_snapshot(candidate: SnapshotCandidate) -> PreparedSnapshot:
    if not candidate.endpoint or len(candidate.endpoint) > 64:
        raise ValueError("endpoint must contain 1-64 characters")
    if not candidate.lookup_key or len(candidate.lookup_key) > 1000:
        raise ValueError("lookup_key must contain 1-1000 characters")
    if candidate.availability != Availability.UNAVAILABLE and candidate.data is None:
        raise ValueError("available or partial snapshots must contain data")

    observed_at = _aware_utc(candidate.observed_at, "observed_at")
    source_updated_at = _aware_utc(candidate.source_updated_at, "source_updated_at")
    ingested_at = _aware_utc(candidate.ingested_at, "ingested_at")
    calculated_at = _aware_utc(candidate.calculated_at, "calculated_at")
    if calculated_at < max(observed_at, source_updated_at, ingested_at):
        raise ValueError("calculated_at cannot precede an input audit timestamp")

    watermarks = {
        source_id: _aware_utc(value, f"input_watermarks[{source_id}]")
        for source_id, value in sorted(candidate.input_watermarks.items())
    }
    as_of = min(watermarks.values(), default=source_updated_at)
    if as_of > calculated_at:
        raise ValueError("snapshot as_of cannot be later than calculated_at")

    data = _json_safe(candidate.data)
    metadata = _json_safe(candidate.metadata)
    formula_versions = dict(sorted(candidate.formula_versions.items()))
    version_document = {
        "endpoint": candidate.endpoint,
        "lookup_key": candidate.lookup_key,
        "data": data,
        "metadata": metadata,
        "input_watermarks": {
            source_id: value.isoformat() for source_id, value in watermarks.items()
        },
        "formula_versions": formula_versions,
        "as_of": as_of.isoformat(),
        "availability": candidate.availability.value,
        "quality_flags": sorted(set(candidate.quality_flags)),
    }
    version = hashlib.sha256(_canonical_json_bytes(version_document)).hexdigest()
    payload = encode_payload(
        data,
        max_uncompressed_bytes=publish_payload_size_limit(candidate.endpoint),
    )
    snapshot_id = f"snap_{version[:59]}"
    return PreparedSnapshot(
        snapshot_id=snapshot_id,
        endpoint=candidate.endpoint,
        lookup_key=candidate.lookup_key,
        lookup_key_hash=lookup_key_hash(candidate.lookup_key),
        snapshot_version=version,
        payload_id=payload.payload_id,
        payload_hash=payload.payload_hash,
        payload_encoding=payload.encoding,
        payload_blob=payload.payload_blob,
        uncompressed_bytes=payload.uncompressed_bytes,
        compressed_bytes=payload.compressed_bytes,
        data=data,
        metadata_json=metadata,
        input_watermarks={source_id: value.isoformat() for source_id, value in watermarks.items()},
        formula_versions=formula_versions,
        observed_at=_database_time(observed_at),
        source_updated_at=_database_time(source_updated_at),
        ingested_at=_database_time(ingested_at),
        calculated_at=_database_time(calculated_at),
        as_of=_database_time(as_of),
        availability=candidate.availability.value,
        quality_flags=sorted(set(candidate.quality_flags)),
        raw_record_ids=tuple(sorted(set(candidate.raw_record_ids))),
    )


def _is_mysql(session: Session) -> bool:
    return session.get_bind().dialect.name in {"mysql", "mariadb"}


class SnapshotPublisher:
    """Stages payload and provenance before a short, atomic head switch."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        lock_timeout_seconds: int = 10,
        *,
        provenance_batch_size: int = PROVENANCE_BATCH_SIZE,
    ) -> None:
        if not 1 <= provenance_batch_size <= PROVENANCE_BATCH_SIZE:
            raise ValueError(f"provenance_batch_size must be between 1 and {PROVENANCE_BATCH_SIZE}")
        self.session_factory = session_factory
        self.lock_timeout_seconds = lock_timeout_seconds
        self.provenance_batch_size = provenance_batch_size

    def publish(self, candidate: SnapshotCandidate) -> PreparedSnapshot:
        prepared = prepare_snapshot(candidate)
        lock_name = f"eden:publish:{prepared.lookup_key_hash[:45]}"
        bind = self.session_factory.kw.get("bind")
        try:
            if isinstance(bind, Engine):
                # An externally-owned connection keeps MariaDB GET_LOCK attached to the
                # same physical connection across the bounded intermediate commits.
                with (
                    bind.connect() as connection,
                    self.session_factory(bind=connection) as session,
                ):
                    self._publish_with_lock(session, prepared, lock_name)
            else:
                with self.session_factory() as session:
                    self._publish_with_lock(session, prepared, lock_name)
        except SnapshotPublishBusy:
            record_snapshot_publish(prepared.endpoint, "busy")
            raise
        except Exception:
            record_snapshot_publish(prepared.endpoint, "error")
            raise
        record_snapshot_publish(
            prepared.endpoint,
            "success",
            uncompressed_bytes=prepared.uncompressed_bytes,
            compressed_bytes=prepared.compressed_bytes,
        )
        return prepared

    def _publish_with_lock(
        self,
        session: Session,
        prepared: PreparedSnapshot,
        lock_name: str,
    ) -> None:
        acquired = self._acquire_lock(session, lock_name, self.lock_timeout_seconds)
        if not acquired:
            raise SnapshotPublishBusy(
                f"Could not acquire the snapshot publish lock for {prepared.endpoint}"
            )
        try:
            with session.begin():
                current_id = session.scalar(
                    select(ReadModelHead.snapshot_id).where(
                        ReadModelHead.endpoint == prepared.endpoint,
                        ReadModelHead.lookup_key_hash == prepared.lookup_key_hash,
                    )
                )
            if current_id == prepared.snapshot_id:
                return
            self._stage_payload(session, prepared)
            self._stage_snapshot(session, prepared)
            self._insert_provenance(session, prepared)
            self._mark_ready(session, prepared.snapshot_id)
            self._switch_head(session, prepared)
        finally:
            self._release_lock(session, lock_name)

    @staticmethod
    def _acquire_lock(session: Session, lock_name: str, timeout_seconds: int) -> bool:
        if not _is_mysql(session):
            return True
        acquired = session.scalar(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            {"lock_name": lock_name, "timeout_seconds": timeout_seconds},
        )
        session.commit()
        return acquired == 1

    @staticmethod
    def _release_lock(session: Session, lock_name: str) -> None:
        if not _is_mysql(session):
            return
        session.execute(text("SELECT RELEASE_LOCK(:lock_name)"), {"lock_name": lock_name})
        session.commit()

    @staticmethod
    def _stage_payload(session: Session, snapshot: PreparedSnapshot) -> None:
        with session.begin():
            if _is_mysql(session):
                session.execute(
                    mysql_insert(ReadModelPayload)
                    .values(
                        payload_id=snapshot.payload_id,
                        payload_hash=snapshot.payload_hash,
                        encoding=snapshot.payload_encoding,
                        payload_blob=snapshot.payload_blob,
                        uncompressed_bytes=snapshot.uncompressed_bytes,
                        compressed_bytes=snapshot.compressed_bytes,
                        created_at=snapshot.calculated_at,
                    )
                    .on_duplicate_key_update(payload_id=ReadModelPayload.payload_id)
                )
                return
            existing = session.scalar(
                select(ReadModelPayload).where(
                    ReadModelPayload.payload_hash == snapshot.payload_hash
                )
            )
            if existing is None:
                session.add(
                    ReadModelPayload(
                        payload_id=snapshot.payload_id,
                        payload_hash=snapshot.payload_hash,
                        encoding=snapshot.payload_encoding,
                        payload_blob=snapshot.payload_blob,
                        uncompressed_bytes=snapshot.uncompressed_bytes,
                        compressed_bytes=snapshot.compressed_bytes,
                        created_at=snapshot.calculated_at,
                    )
                )

    def _stage_snapshot(self, session: Session, snapshot: PreparedSnapshot) -> None:
        with session.begin():
            existing = session.get(ReadModelSnapshot, snapshot.snapshot_id)
            if existing is None:
                session.add(
                    ReadModelSnapshot(
                        snapshot_id=snapshot.snapshot_id,
                        endpoint=snapshot.endpoint,
                        lookup_key=snapshot.lookup_key,
                        lookup_key_hash=snapshot.lookup_key_hash,
                        snapshot_version=snapshot.snapshot_version,
                        payload_id=snapshot.payload_id,
                        metadata_json=snapshot.metadata_json,
                        input_watermarks=snapshot.input_watermarks,
                        formula_versions=snapshot.formula_versions,
                        observed_at=snapshot.observed_at,
                        source_updated_at=snapshot.source_updated_at,
                        ingested_at=snapshot.ingested_at,
                        calculated_at=snapshot.calculated_at,
                        as_of=snapshot.as_of,
                        availability=snapshot.availability,
                        quality_flags=snapshot.quality_flags,
                        state="staging",
                    )
                )
                return
            existing.payload_id = snapshot.payload_id
            if existing.state == "retired":
                existing.state = "staging"

    def _insert_provenance(self, session: Session, snapshot: PreparedSnapshot) -> None:
        provenance_version = (
            "|".join(sorted(set(snapshot.formula_versions.values())))[:100] or "identity_v1"
        )
        for offset in range(0, len(snapshot.raw_record_ids), self.provenance_batch_size):
            raw_record_ids = snapshot.raw_record_ids[offset : offset + self.provenance_batch_size]
            with session.begin():
                if _is_mysql(session):
                    rows = [
                        {
                            "output_type": "read_model_snapshot",
                            "output_id": snapshot.snapshot_id,
                            "raw_record_id": raw_record_id,
                            "formula_version": provenance_version,
                            "created_at": snapshot.calculated_at,
                        }
                        for raw_record_id in raw_record_ids
                    ]
                    session.execute(
                        mysql_insert(ProvenanceEdge)
                        .values(rows)
                        .on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id)
                    )
                    continue
                for raw_record_id in raw_record_ids:
                    present = session.scalar(
                        select(ProvenanceEdge.provenance_id).where(
                            ProvenanceEdge.output_type == "read_model_snapshot",
                            ProvenanceEdge.output_id == snapshot.snapshot_id,
                            ProvenanceEdge.raw_record_id == raw_record_id,
                            ProvenanceEdge.formula_version == provenance_version,
                        )
                    )
                    if present is None:
                        session.add(
                            ProvenanceEdge(
                                output_type="read_model_snapshot",
                                output_id=snapshot.snapshot_id,
                                raw_record_id=raw_record_id,
                                formula_version=provenance_version,
                                created_at=snapshot.calculated_at,
                            )
                        )

    @staticmethod
    def _mark_ready(session: Session, snapshot_id: str) -> None:
        with session.begin():
            session.execute(
                update(ReadModelSnapshot)
                .where(ReadModelSnapshot.snapshot_id == snapshot_id)
                .values(state="ready")
            )

    @staticmethod
    def _switch_head(session: Session, snapshot: PreparedSnapshot) -> None:
        """Only this bounded transaction changes what readers can observe."""
        with session.begin():
            new_state = session.scalar(
                select(ReadModelSnapshot.state)
                .where(ReadModelSnapshot.snapshot_id == snapshot.snapshot_id)
                .with_for_update()
            )
            if new_state != "ready":
                raise RuntimeError("Snapshot head can only switch to a ready snapshot")
            old_snapshot_id = session.scalar(
                select(ReadModelHead.snapshot_id)
                .where(
                    ReadModelHead.endpoint == snapshot.endpoint,
                    ReadModelHead.lookup_key_hash == snapshot.lookup_key_hash,
                )
                .with_for_update()
            )
            if _is_mysql(session):
                session.execute(
                    mysql_insert(ReadModelHead)
                    .values(
                        endpoint=snapshot.endpoint,
                        lookup_key_hash=snapshot.lookup_key_hash,
                        lookup_key=snapshot.lookup_key,
                        snapshot_id=snapshot.snapshot_id,
                        updated_at=snapshot.calculated_at,
                    )
                    .on_duplicate_key_update(
                        lookup_key=snapshot.lookup_key,
                        snapshot_id=snapshot.snapshot_id,
                        updated_at=snapshot.calculated_at,
                    )
                )
            else:
                head = session.get(
                    ReadModelHead,
                    {"endpoint": snapshot.endpoint, "lookup_key_hash": snapshot.lookup_key_hash},
                )
                if head is None:
                    session.add(
                        ReadModelHead(
                            endpoint=snapshot.endpoint,
                            lookup_key_hash=snapshot.lookup_key_hash,
                            lookup_key=snapshot.lookup_key,
                            snapshot_id=snapshot.snapshot_id,
                            updated_at=snapshot.calculated_at,
                        )
                    )
                else:
                    head.lookup_key = snapshot.lookup_key
                    head.snapshot_id = snapshot.snapshot_id
                    head.updated_at = snapshot.calculated_at
            if old_snapshot_id is not None and old_snapshot_id != snapshot.snapshot_id:
                session.execute(
                    update(ReadModelSnapshot)
                    .where(ReadModelSnapshot.snapshot_id == old_snapshot_id)
                    .values(state="retired")
                )


def restore_previous_snapshot(
    session_factory: sessionmaker[Session],
    *,
    endpoint: str,
    lookup_key: str,
) -> str:
    """Validate and atomically restore the newest usable retired snapshot."""
    key_hash = lookup_key_hash(lookup_key)
    lock_name = f"eden:publish:{key_hash[:45]}"
    bind = session_factory.kw.get("bind")
    if isinstance(bind, Engine):
        with bind.connect() as connection, session_factory(bind=connection) as session:
            return _restore_previous_with_lock(
                session,
                endpoint=endpoint,
                lookup_key=lookup_key,
                key_hash=key_hash,
                lock_name=lock_name,
            )
    with session_factory() as session:
        return _restore_previous_with_lock(
            session,
            endpoint=endpoint,
            lookup_key=lookup_key,
            key_hash=key_hash,
            lock_name=lock_name,
        )


def _restore_previous_with_lock(
    session: Session,
    *,
    endpoint: str,
    lookup_key: str,
    key_hash: str,
    lock_name: str,
) -> str:
    if not SnapshotPublisher._acquire_lock(session, lock_name, 10):
        raise SnapshotPublishBusy(f"Could not acquire the snapshot restore lock for {endpoint}")
    try:
        head = session.get(
            ReadModelHead,
            {"endpoint": endpoint, "lookup_key_hash": key_hash},
        )
        if head is None or head.lookup_key != lookup_key:
            raise SnapshotRollbackUnavailable("Current snapshot head does not exist")
        current_snapshot_id = head.snapshot_id
        candidates = list(
            session.scalars(
                select(ReadModelSnapshot)
                .where(
                    ReadModelSnapshot.endpoint == endpoint,
                    ReadModelSnapshot.lookup_key_hash == key_hash,
                    ReadModelSnapshot.lookup_key == lookup_key,
                    ReadModelSnapshot.snapshot_id != current_snapshot_id,
                    ReadModelSnapshot.state == "retired",
                )
                .order_by(
                    ReadModelSnapshot.calculated_at.desc(),
                    ReadModelSnapshot.snapshot_id.desc(),
                )
                .limit(5)
            )
        )
        restored: ReadModelSnapshot | None = None
        for candidate in candidates:
            if candidate.payload_id is None:
                continue
            payload = session.get(ReadModelPayload, candidate.payload_id)
            if payload is None:
                continue
            try:
                decoded = decode_payload(
                    payload.payload_blob,
                    encoding=payload.encoding,
                    uncompressed_bytes=payload.uncompressed_bytes,
                    compressed_bytes=payload.compressed_bytes,
                    max_uncompressed_bytes=payload_size_limit(endpoint),
                )
                decoded_hash = hashlib.sha256(_canonical_json_bytes(decoded)).hexdigest()
                if decoded_hash != payload.payload_hash:
                    continue
            except SnapshotPayloadError:
                continue
            restored = candidate
            break
        if restored is None:
            raise SnapshotRollbackUnavailable("No valid retired snapshot is available")
        restored_snapshot_id = restored.snapshot_id
        # The validation reads above autobegin a transaction. End that read
        # transaction before opening the bounded, atomic head-switch transaction.
        # MariaDB named locks are connection-scoped, so rollback does not release it.
        session.rollback()
        now = datetime.now(UTC).replace(tzinfo=None)
        with session.begin():
            locked_head = session.scalar(
                select(ReadModelHead)
                .where(
                    ReadModelHead.endpoint == endpoint,
                    ReadModelHead.lookup_key_hash == key_hash,
                )
                .with_for_update()
            )
            if (
                locked_head is None
                or locked_head.lookup_key != lookup_key
                or locked_head.snapshot_id != current_snapshot_id
            ):
                raise SnapshotPublishBusy("Snapshot head changed during restore")
            rollback_state = session.scalar(
                select(ReadModelSnapshot.state)
                .where(ReadModelSnapshot.snapshot_id == restored_snapshot_id)
                .with_for_update()
            )
            if rollback_state != "retired":
                raise SnapshotPublishBusy("Rollback snapshot changed during restore")
            session.execute(
                update(ReadModelSnapshot)
                .where(ReadModelSnapshot.snapshot_id == locked_head.snapshot_id)
                .values(state="retired")
            )
            session.execute(
                update(ReadModelSnapshot)
                .where(ReadModelSnapshot.snapshot_id == restored_snapshot_id)
                .values(state="ready")
            )
            locked_head.snapshot_id = restored_snapshot_id
            locked_head.lookup_key = lookup_key
            locked_head.updated_at = now
        return restored_snapshot_id
    finally:
        SnapshotPublisher._release_lock(session, lock_name)


def _retention_candidate_ids(
    session: Session,
    *,
    older_than: datetime,
    limit: int,
    candidate_id: str | None = None,
) -> tuple[str, ...]:
    if older_than.tzinfo is not None:
        older_than = _database_time(older_than)
    newer = aliased(ReadModelSnapshot)
    is_current_head = exists(
        select(ReadModelHead.snapshot_id).where(
            ReadModelHead.snapshot_id == ReadModelSnapshot.snapshot_id
        )
    )
    # Two newer retired rows are sufficient. Counting the entire history for
    # every candidate made a small cleanup spend seconds scanning old versions.
    has_newer_retired = (
        select(newer.snapshot_id)
        .where(
            newer.endpoint == ReadModelSnapshot.endpoint,
            newer.lookup_key_hash == ReadModelSnapshot.lookup_key_hash,
            newer.state == "retired",
            (newer.calculated_at > ReadModelSnapshot.calculated_at)
            | (
                (newer.calculated_at == ReadModelSnapshot.calculated_at)
                & (newer.snapshot_id > ReadModelSnapshot.snapshot_id)
            ),
        )
        .offset(1)
        .limit(1)
        .exists()
    )
    is_retired_cleanup = (ReadModelSnapshot.state == "retired") & has_newer_retired
    is_abandoned_staging = ReadModelSnapshot.state.in_(("staging", "ready"))
    return tuple(
        session.scalars(
            select(ReadModelSnapshot.snapshot_id)
            .where(
                is_retired_cleanup | is_abandoned_staging,
                ReadModelSnapshot.snapshot_id == candidate_id if candidate_id else True,
                ReadModelSnapshot.calculated_at < older_than,
                ~is_current_head,
            )
            .order_by(ReadModelSnapshot.calculated_at, ReadModelSnapshot.snapshot_id)
            .limit(limit)
        ).all()
    )


@contextmanager
def _retention_session(factory, snapshot_id):
    # Keep GET_LOCK attached to a physical connection across DELETE commits.
    bind = factory.kw.get("bind")
    with bind.connect() as connection, factory(bind=connection) as session:
        with session.begin():
            snapshot = session.get(ReadModelSnapshot, snapshot_id)
            key_hash = snapshot.lookup_key_hash if snapshot else None
        lock_name = f"eden:publish:{key_hash[:45]}" if key_hash else None
        acquired = bool(lock_name and SnapshotPublisher._acquire_lock(session, lock_name, 0))
        try:
            yield session if acquired else None
        finally:
            if acquired:
                SnapshotPublisher._release_lock(session, lock_name)


def retain_snapshots(
    session_factory: sessionmaker[Session],
    *,
    older_than: datetime,
    dry_run: bool = True,
    snapshot_batch_size: int = 100,
    provenance_batch_size: int = PROVENANCE_BATCH_SIZE,
    provenance_delete_limit: int = MAX_RETENTION_PROVENANCE_ROWS,
    max_seconds: float = 5.0,
    pause_reason: Callable[[], str | None] | None = None,
) -> RetentionResult:
    """Deletes bounded retired or abandoned staging rows without touching safe points.

    A snapshot with remaining provenance is kept for a later invocation.
    """
    if not 1 <= snapshot_batch_size <= 500:
        raise ValueError("snapshot_batch_size must be between 1 and 500")
    if not 1 <= provenance_batch_size <= PROVENANCE_BATCH_SIZE:
        raise ValueError(f"provenance_batch_size must be between 1 and {PROVENANCE_BATCH_SIZE}")
    if not 1 <= provenance_delete_limit <= MAX_RETENTION_PROVENANCE_ROWS:
        raise ValueError(
            f"provenance_delete_limit must be between 1 and {MAX_RETENTION_PROVENANCE_ROWS}"
        )

    deadline = monotonic() + max_seconds
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    with session_factory() as session:
        candidate_ids = _retention_candidate_ids(
            session,
            older_than=older_than,
            limit=snapshot_batch_size,
        )
        provenance_rows = len(
            tuple(
                session.scalars(
                    select(ProvenanceEdge.provenance_id)
                    .where(
                        ProvenanceEdge.output_type == "read_model_snapshot",
                        ProvenanceEdge.output_id.in_(candidate_ids),
                    )
                    .limit(provenance_delete_limit if dry_run else provenance_batch_size)
                )
            )
        )
    if dry_run or not candidate_ids:
        return RetentionResult(
            dry_run=dry_run,
            candidate_snapshot_ids=candidate_ids,
            provenance_rows=provenance_rows,
        )

    deleted_snapshots = 0
    deleted_provenance_rows = 0
    deleted_payloads = 0
    for snapshot_id in candidate_ids:
        if monotonic() >= deadline or (pause_reason and pause_reason()):
            break
        with _retention_session(session_factory, snapshot_id) as session:
            if session is None:
                continue
            with session.begin():
                eligible = _retention_candidate_ids(
                    session, older_than=older_than, limit=1, candidate_id=snapshot_id
                )
            if snapshot_id not in eligible:
                continue
            while deleted_provenance_rows < provenance_delete_limit:
                if monotonic() >= deadline or (pause_reason and pause_reason()):
                    break
                with session.begin():
                    still_deletable = session.scalar(
                        select(ReadModelSnapshot.snapshot_id).where(
                            ReadModelSnapshot.snapshot_id == snapshot_id,
                            ReadModelSnapshot.state.in_(("retired", "staging", "ready")),
                            ~exists(
                                select(ReadModelHead.snapshot_id).where(
                                    ReadModelHead.snapshot_id == snapshot_id
                                )
                            ),
                        )
                    )
                    if still_deletable is None:
                        break
                    provenance_ids = tuple(
                        session.scalars(
                            select(ProvenanceEdge.provenance_id)
                            .where(
                                ProvenanceEdge.output_type == "read_model_snapshot",
                                ProvenanceEdge.output_id == snapshot_id,
                            )
                            .order_by(ProvenanceEdge.provenance_id)
                            .limit(
                                min(
                                    provenance_batch_size,
                                    provenance_delete_limit - deleted_provenance_rows,
                                )
                            )
                        ).all()
                    )
                    if not provenance_ids:
                        break
                    result = session.execute(
                        delete(ProvenanceEdge).where(
                            ProvenanceEdge.provenance_id.in_(provenance_ids)
                        )
                    )
                    deleted_provenance_rows += result.rowcount or 0

            if monotonic() >= deadline or (pause_reason and pause_reason()):
                break
            with session.begin():
                provenance_remains = bool(
                    session.scalar(
                        select(
                            exists().where(
                                ProvenanceEdge.output_type == "read_model_snapshot",
                                ProvenanceEdge.output_id == snapshot_id,
                            )
                        )
                    )
                )
                if provenance_remains:
                    continue
                payload_id = session.scalar(
                    select(ReadModelSnapshot.payload_id).where(
                        ReadModelSnapshot.snapshot_id == snapshot_id,
                        ReadModelSnapshot.state.in_(("retired", "staging", "ready")),
                        ~exists(
                            select(ReadModelHead.snapshot_id).where(
                                ReadModelHead.snapshot_id == snapshot_id
                            )
                        ),
                    )
                )
                result = session.execute(
                    delete(ReadModelSnapshot).where(
                        ReadModelSnapshot.snapshot_id == snapshot_id,
                        ReadModelSnapshot.state.in_(("retired", "staging", "ready")),
                        ~exists(
                            select(ReadModelHead.snapshot_id).where(
                                ReadModelHead.snapshot_id == snapshot_id
                            )
                        ),
                    )
                )
                deleted_snapshots += result.rowcount or 0
                payload_in_use = payload_id is not None and session.scalar(
                    select(exists().where(ReadModelSnapshot.payload_id == payload_id))
                )
                if payload_id is not None and not payload_in_use:
                    payload_result = session.execute(
                        delete(ReadModelPayload).where(ReadModelPayload.payload_id == payload_id)
                    )
                    deleted_payloads += payload_result.rowcount or 0
        if deleted_provenance_rows >= provenance_delete_limit:
            break

    return RetentionResult(
        dry_run=False,
        candidate_snapshot_ids=candidate_ids,
        provenance_rows=provenance_rows,
        deleted_snapshots=deleted_snapshots,
        deleted_provenance_rows=deleted_provenance_rows,
        deleted_payloads=deleted_payloads,
    )
