from __future__ import annotations

import json
from sys import getsizeof

import pytest

import app.readmodels.repository as repository
from app.readmodels.repository import MariaDBReadRepository, _payload_memory_bytes


def test_cache_budget_counts_decoded_objects_instead_of_json_bytes(monkeypatch) -> None:
    encoded = json.dumps([{"value": index} for index in range(200)])
    first = json.loads(encoded)
    charge = _payload_memory_bytes(first, 1_000_000) + getsizeof("a") + 256
    monkeypatch.setattr(repository, "PAYLOAD_CACHE_MAX_BYTES", charge * 2)
    cache = MariaDBReadRepository(None)
    for key in ("a", "b", "c"):
        cache._store_payload(key, json.loads(encoded), len(encoded))
    assert cache._payload_cache_bytes == charge * 2
    assert list(cache._payload_cache) == ["b", "c"]
    assert cache._cached_payload("a") == (False, None)
    assert cache._cached_payload("b") == (True, first)


def test_cache_rejects_json_that_expands_beyond_memory_budget(monkeypatch) -> None:
    encoded = json.dumps([{"value": index} for index in range(200)])
    monkeypatch.setattr(repository, "PAYLOAD_CACHE_MAX_BYTES", len(encoded) * 2)
    cache = MariaDBReadRepository(None)
    cache._store_payload("large", json.loads(encoded), len(encoded))
    assert not cache._payload_cache
    assert cache._payload_cache_bytes == 0


def test_tiny_payload_versions_are_bounded_and_recent_hits_are_preserved(monkeypatch) -> None:
    monkeypatch.setattr(repository, "PAYLOAD_CACHE_MAX_ENTRIES", 2)
    cache = MariaDBReadRepository(None)
    cache._store_payload("a", None, 4)
    cache._store_payload("b", None, 4)
    assert cache._cached_payload("a") == (True, None)
    cache._store_payload("c", None, 4)
    assert list(cache._payload_cache) == ["a", "c"]
    old_size = cache._payload_cache_bytes
    cache._store_payload("c", None, 4)
    assert cache._payload_cache_bytes == old_size


def test_memory_measurement_counts_shared_values_once_and_stops_at_limit() -> None:
    shared = {"value": "test"}
    data = [shared, shared]
    expected = getsizeof(data) + getsizeof(shared) + getsizeof("value") + getsizeof("test")
    assert _payload_memory_bytes(data, expected) == expected
    assert _payload_memory_bytes(data, 0) == getsizeof(data)


@pytest.mark.parametrize("size", [-1, repository.PAYLOAD_CACHE_MAX_BYTES + 1])
def test_invalid_or_oversized_encoded_payload_is_not_cached(size: int) -> None:
    cache = MariaDBReadRepository(None)
    cache._store_payload("invalid", None, size)
    assert cache._payload_cache_bytes == 0
