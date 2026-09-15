import importlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.enums import Availability
from app.products.snapshots import (
    SnapshotCandidate,
    SnapshotPayloadError,
    SnapshotPayloadTooLarge,
    decode_payload,
    encode_payload,
    prepare_snapshot,
)


def _candidate(*, data: dict[str, object], calculated_at: datetime) -> SnapshotCandidate:
    observed_at = datetime(2026, 8, 29, tzinfo=UTC)
    return SnapshotCandidate(
        endpoint="example",
        lookup_key='{"scope":"global"}',
        data=data,
        metadata={"spatial_resolution": "none"},
        input_watermarks={"SRC_EXAMPLE": observed_at},
        formula_versions={"score": "score_v1"},
        observed_at=observed_at,
        source_updated_at=observed_at,
        ingested_at=observed_at,
        calculated_at=calculated_at,
        availability=Availability.AVAILABLE,
    )


def test_payload_hash_is_canonical_and_round_trips() -> None:
    first = encode_payload({"한글": [1, 2], "nested": {"b": True, "a": None}})
    second = encode_payload({"nested": {"a": None, "b": True}, "한글": [1, 2]})

    assert first.payload_id == second.payload_id
    assert first.payload_hash == second.payload_hash
    assert decode_payload(
        first.payload_blob,
        encoding=first.encoding,
        uncompressed_bytes=first.uncompressed_bytes,
        compressed_bytes=first.compressed_bytes,
    ) == {"nested": {"a": None, "b": True}, "한글": [1, 2]}


def test_payload_hash_excludes_snapshot_audit_metadata() -> None:
    first = prepare_snapshot(
        _candidate(data={"value": 7}, calculated_at=datetime(2026, 8, 29, 1, tzinfo=UTC))
    )
    second = prepare_snapshot(
        _candidate(data={"value": 7}, calculated_at=datetime(2026, 8, 29, 2, tzinfo=UTC))
    )

    assert first.payload_hash == second.payload_hash
    assert first.payload_id == second.payload_id


def test_backfill_uses_the_same_payload_contract() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260829_0006_snapshot_payload_backfill"
    )
    current = encode_payload({"nested": {"b": 2, "a": 1}})
    payload_id, payload_hash, blob, uncompressed_bytes, compressed_bytes = (
        migration._encoded_payload({"nested": {"a": 1, "b": 2}})
    )

    assert payload_id == current.payload_id
    assert payload_hash == current.payload_hash
    assert blob == current.payload_blob
    assert uncompressed_bytes == current.uncompressed_bytes
    assert compressed_bytes == current.compressed_bytes


def test_payload_size_and_integrity_are_bounded() -> None:
    with pytest.raises(SnapshotPayloadTooLarge):
        encode_payload({"value": "too large"}, max_uncompressed_bytes=4)

    payload = encode_payload({"value": 1})
    with pytest.raises(SnapshotPayloadError, match="size"):
        decode_payload(
            payload.payload_blob + b"trailing",
            encoding=payload.encoding,
            uncompressed_bytes=payload.uncompressed_bytes,
            compressed_bytes=payload.compressed_bytes,
        )
    with pytest.raises(SnapshotPayloadTooLarge):
        decode_payload(
            payload.payload_blob,
            encoding=payload.encoding,
            uncompressed_bytes=payload.uncompressed_bytes,
            compressed_bytes=payload.compressed_bytes,
            max_uncompressed_bytes=payload.uncompressed_bytes - 1,
        )


def test_prepare_snapshot_rejects_naive_or_out_of_order_times() -> None:
    calculated_at = datetime(2026, 8, 29, 1, tzinfo=UTC)
    candidate = _candidate(data={"value": 1}, calculated_at=calculated_at)
    with pytest.raises(ValueError, match="timezone"):
        prepare_snapshot(replace(candidate, observed_at=datetime(2026, 8, 29)))

    with pytest.raises(ValueError, match="cannot precede"):
        prepare_snapshot(
            _candidate(
                data={"value": 1},
                calculated_at=datetime(2026, 8, 28, tzinfo=UTC),
            )
        )
