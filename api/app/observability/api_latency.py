"""Read the separate API process's pressure metric over the deployment's loopback port."""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import Lock
from time import monotonic

import httpx

_METRICS_URL = "http://127.0.0.1:8000/internal/metrics"
_CACHE_SECONDS = 5.0
_MAX_RESPONSE_BYTES = 1024 * 1024
_READ_DEADLINE_SECONDS = 1.0
_CACHE_LOCK = Lock()
_cached_at = float("-inf")


@dataclass(frozen=True)
class ApiLatency:
    available: bool
    p95_seconds: float | None = None


_cached_latency = ApiLatency(available=False)


def _read_api_latency() -> ApiLatency:
    # No proxy, redirect, or arbitrary host: this is the existing production API listener.
    deadline = monotonic() + _READ_DEADLINE_SECONDS
    payload = bytearray()
    try:
        with httpx.stream(
            "GET", _METRICS_URL, timeout=0.5, trust_env=False, follow_redirects=False,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if monotonic() > deadline or len(payload) + len(chunk) > _MAX_RESPONSE_BYTES:
                    return ApiLatency(available=False)
                payload.extend(chunk)
        for line in payload.splitlines():
            if line.startswith(b"eden_api_recent_p95_seconds "):
                value = float(line.split()[1])
                if math.isnan(value):
                    # The API is reachable but idle or has fewer than 20 fresh samples.
                    return ApiLatency(available=True)
                if math.isfinite(value) and value >= 0:
                    return ApiLatency(available=True, p95_seconds=value)
                break
    except (httpx.HTTPError, ValueError, IndexError):
        pass
    return ApiLatency(available=False)


def read_api_latency() -> ApiLatency:
    """Cache success and failure briefly so per-row pressure checks stay inexpensive."""
    global _cached_at, _cached_latency
    with _CACHE_LOCK:
        if monotonic() - _cached_at >= _CACHE_SECONDS:
            _cached_latency = _read_api_latency()
            _cached_at = monotonic()
        return _cached_latency
