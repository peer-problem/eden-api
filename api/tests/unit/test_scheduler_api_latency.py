from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager

import httpx
import pytest

from app.observability import api_latency


def _mock_metrics(monkeypatch, payload: str, *, status_code: int = 200) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, text=payload)

    @contextmanager
    def stream(method, url, **kwargs):
        assert kwargs == {"timeout": 0.5, "trust_env": False, "follow_redirects": False}
        with (
            httpx.Client(transport=httpx.MockTransport(handle)) as client,
            client.stream(method, url) as response,
        ):
            yield response

    monkeypatch.setattr(api_latency.httpx, "stream", stream)
    return requests


def test_reads_latency_exported_by_a_separate_api_process(monkeypatch) -> None:
    payload = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "\n".join([
            "from app.observability.metrics import record_http_request",
            "from prometheus_client import generate_latest",
            "for _ in range(20): record_http_request('GET', '/v1/trends', 200, 1.5)",
            "print(generate_latest().decode())",
        ])],
        check=True, capture_output=True, text=True,
    ).stdout
    requests = _mock_metrics(monkeypatch, payload)

    assert api_latency._read_api_latency() == api_latency.ApiLatency(True, 1.5)
    assert str(requests[0].url) == "http://127.0.0.1:8000/internal/metrics"


@pytest.mark.parametrize("value", ["NaN", "0.0", "0.2"])
def test_reachable_api_without_pressure_allows_work(monkeypatch, value) -> None:
    _mock_metrics(monkeypatch, f"eden_api_recent_p95_seconds {value}\n")
    result = api_latency._read_api_latency()
    assert result.available
    assert result.p95_seconds == (None if value == "NaN" else float(value))


@pytest.mark.parametrize("payload", ["", "eden_api_recent_p95_seconds invalid\n",
                                    "eden_api_recent_p95_seconds +Inf\n",
                                    "eden_api_recent_p95_seconds -1\n"])
def test_missing_or_invalid_metric_is_unavailable(monkeypatch, payload) -> None:
    _mock_metrics(monkeypatch, payload)
    assert not api_latency._read_api_latency().available


def test_response_size_and_status_are_bounded(monkeypatch) -> None:
    _mock_metrics(monkeypatch, "x" * (api_latency._MAX_RESPONSE_BYTES + 1))
    assert not api_latency._read_api_latency().available
    _mock_metrics(monkeypatch, "eden_api_recent_p95_seconds 0.0\n", status_code=503)
    assert not api_latency._read_api_latency().available


def test_metrics_timeout_is_unavailable(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("loopback API unavailable")

    monkeypatch.setattr(api_latency.httpx, "stream", timeout)
    assert not api_latency._read_api_latency().available


def test_response_read_deadline_is_bounded(monkeypatch) -> None:
    _mock_metrics(monkeypatch, "eden_api_recent_p95_seconds 0.0\n")
    times = iter([0.0, 2.0])
    monkeypatch.setattr(api_latency, "monotonic", lambda: next(times))
    assert not api_latency._read_api_latency().available


def test_caches_failures_briefly_and_recovers_after_refresh(monkeypatch) -> None:
    monkeypatch.setattr(api_latency, "_cached_at", float("-inf"))
    monkeypatch.setattr(api_latency, "_cached_latency", api_latency.ApiLatency(False))
    now = [0.0]
    monkeypatch.setattr(api_latency, "monotonic", lambda: now[0])
    requests = _mock_metrics(monkeypatch, "", status_code=503)

    assert not api_latency.read_api_latency().available
    for _ in range(100):
        assert not api_latency.read_api_latency().available
    assert len(requests) == 1

    requests = _mock_metrics(monkeypatch, "eden_api_recent_p95_seconds 0.1\n")
    now[0] = api_latency._CACHE_SECONDS
    assert api_latency.read_api_latency() == api_latency.ApiLatency(True, 0.1)
    for _ in range(100):
        assert api_latency.read_api_latency() == api_latency.ApiLatency(True, 0.1)
    assert len(requests) == 1
