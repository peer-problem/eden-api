from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Availability
from app.readmodels.keys import lookup_key_hash
from app.repositories.models import ProvenanceEdge, ReadModelSnapshot


class SnapshotPublishBusy(RuntimeError):
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
class PreparedSnapshot:
    snapshot_id: str
    endpoint: str
    lookup_key: str
    lookup_key_hash: str
    snapshot_version: str
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
    serialized = json.dumps(
        {
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
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    version = hashlib.sha256(serialized.encode()).hexdigest()
    snapshot_id = f"snap_{version[:59]}"
    return PreparedSnapshot(
        snapshot_id=snapshot_id,
        endpoint=candidate.endpoint,
        lookup_key=candidate.lookup_key,
        lookup_key_hash=lookup_key_hash(candidate.lookup_key),
        snapshot_version=version,
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


class SnapshotPublisher:
    """Atomically switches a lookup key to one reproducible snapshot."""

    def __init__(
        self, session_factory: sessionmaker[Session], lock_timeout_seconds: int = 10
    ) -> None:
        self.session_factory = session_factory
        self.lock_timeout_seconds = lock_timeout_seconds

    def publish(self, candidate: SnapshotCandidate) -> PreparedSnapshot:
        prepared = prepare_snapshot(candidate)
        lock_name = f"eden:publish:{prepared.lookup_key_hash[:45]}"
        with self.session_factory() as session:
            acquired = session.scalar(
                text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
                {"lock_name": lock_name, "timeout_seconds": self.lock_timeout_seconds},
            )
            session.commit()
            if acquired != 1:
                raise SnapshotPublishBusy(
                    f"Could not acquire the snapshot publish lock for {prepared.endpoint}"
                )
            try:
                with session.begin():
                    self._publish_in_transaction(session, prepared)
            finally:
                session.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": lock_name},
                )
                session.commit()
        return prepared

    @staticmethod
    def _publish_in_transaction(session: Session, snapshot: PreparedSnapshot) -> None:
        existing = session.scalar(
            select(ReadModelSnapshot.snapshot_id).where(
                ReadModelSnapshot.endpoint == snapshot.endpoint,
                ReadModelSnapshot.lookup_key_hash == snapshot.lookup_key_hash,
                ReadModelSnapshot.lookup_key == snapshot.lookup_key,
                ReadModelSnapshot.snapshot_version == snapshot.snapshot_version,
            )
        )
        if existing is None:
            session.add(
                ReadModelSnapshot(
                    snapshot_id=snapshot.snapshot_id,
                    endpoint=snapshot.endpoint,
                    lookup_key=snapshot.lookup_key,
                    lookup_key_hash=snapshot.lookup_key_hash,
                    snapshot_version=snapshot.snapshot_version,
                    data=snapshot.data,
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
                    published=False,
                )
            )
            session.flush()

        session.execute(
            update(ReadModelSnapshot)
            .where(
                ReadModelSnapshot.endpoint == snapshot.endpoint,
                ReadModelSnapshot.lookup_key_hash == snapshot.lookup_key_hash,
                ReadModelSnapshot.lookup_key == snapshot.lookup_key,
                ReadModelSnapshot.snapshot_id != snapshot.snapshot_id,
                ReadModelSnapshot.published.is_(True),
            )
            .values(published=False)
        )
        session.execute(
            update(ReadModelSnapshot)
            .where(ReadModelSnapshot.snapshot_id == snapshot.snapshot_id)
            .values(published=True)
        )

        provenance_version = (
            "|".join(sorted(set(snapshot.formula_versions.values())))[:100] or "identity_v1"
        )
        for raw_record_id in snapshot.raw_record_ids:
            session.execute(
                insert(ProvenanceEdge)
                .values(
                    output_type="read_model_snapshot",
                    output_id=snapshot.snapshot_id,
                    raw_record_id=raw_record_id,
                    formula_version=provenance_version,
                    created_at=snapshot.calculated_at,
                )
                .on_duplicate_key_update(provenance_id=ProvenanceEdge.provenance_id)
            )
