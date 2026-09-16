import hashlib
import importlib
import json
import tracemalloc
import zlib
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

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


def test_streamed_encoding_preserves_legacy_bytes_and_snapshot_identity() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    data = {
        "rows": [{"value": Decimal("1.25"), "name": "서울", "time": now}] * 2000,
        "availability": Availability.PARTIAL,
    }
    normalized = {
        "rows": [{"value": 1.25, "name": "서울", "time": now.isoformat()}] * 2000,
        "availability": "partial",
    }
    legacy_bytes = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    encoded = encode_payload(data)
    assert encoded.payload_blob == zlib.compress(legacy_bytes, level=6)
    assert encoded.payload_hash == hashlib.sha256(legacy_bytes).hexdigest()
    candidate = _candidate(data=data, calculated_at=now)
    prepared = prepare_snapshot(candidate)
    version_document = {
        "endpoint": candidate.endpoint,
        "lookup_key": candidate.lookup_key,
        "data": normalized,
        "metadata": candidate.metadata,
        "input_watermarks": {"SRC_EXAMPLE": now.isoformat()},
        "formula_versions": candidate.formula_versions,
        "as_of": now.isoformat(),
        "availability": "available",
        "quality_flags": [],
    }
    legacy_version = hashlib.sha256(json.dumps(
        version_document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()
    assert prepared.snapshot_version == legacy_version
    assert prepared.payload_hash == encoded.payload_hash


def test_tiny_payload_decode_does_not_allocate_the_64_mib_ceiling() -> None:
    payload = encode_payload({"value": 1})
    tracemalloc.start()
    try:
        assert decode_payload(
            payload.payload_blob,
            encoding=payload.encoding,
            uncompressed_bytes=payload.uncompressed_bytes,
            compressed_bytes=payload.compressed_bytes,
        ) == {"value": 1}
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


def test_decode_uses_declared_size_to_bound_expansion() -> None:
    blob = zlib.compress(b'["' + b"x" * (2 * 1024 * 1024) + b'"]')
    tracemalloc.start()
    try:
        with pytest.raises(SnapshotPayloadError, match="size"):
            decode_payload(
                blob,
                encoding="json-zlib-v1",
                uncompressed_bytes=10,
                compressed_bytes=len(blob),
            )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


@pytest.mark.parametrize("corruption", ["truncated", "trailing", "second_stream", "overdeclared"])
def test_decode_rejects_incomplete_or_extra_data(corruption: str) -> None:
    payload = encode_payload({"value": 1})
    blob = payload.payload_blob
    declared_size = payload.uncompressed_bytes
    if corruption == "truncated":
        blob = blob[:-1]
    elif corruption == "trailing":
        blob += b"trailing"
    elif corruption == "second_stream":
        blob += zlib.compress(b"null")
    else:
        declared_size += 1
    with pytest.raises(SnapshotPayloadError):
        decode_payload(
            blob,
            encoding=payload.encoding,
            uncompressed_bytes=declared_size,
            compressed_bytes=len(blob),
        )


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
