from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.base import FetchReasonCode
from app.sources.http import (
    SecureSourceClient,
    SourceCredentialHttpError,
    SourceHttpError,
    SourceRunBudgetExceeded,
    SourceTransientHttpError,
)
from app.sources.social import NaverTrendAdapter, YouTubeAggregateAdapter


@pytest.mark.parametrize(
    ("addresses", "allowed"),
    [
        (["64:ff9b::808:808", "8.8.8.8"], True),
        (["64:ff9b::808:808"], True),
        (["2606:4700:4700::1111"], True),
        (["64:ff9b::7f00:1"], False),
        (["64:ff9b::a00:1"], False),
        (["64:ff9b::a9fe:a9fe"], False),
        (["64:ff9b::6440:1"], False),
        (["64:ff9b::e000:1"], False),
        (["8.8.8.8", "127.0.0.1"], False),
        (["64:ff9b:1::808:808"], False),
    ],
)
def test_dns64_preserves_public_destination_boundary(
    monkeypatch: pytest.MonkeyPatch,
    addresses: list[str],
    allowed: bool,
) -> None:
    monkeypatch.setattr(
        "app.sources.http.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", (address, 443)) for address in addresses],
    )
    client = SecureSourceClient({"source.example"})
    try:
        if allowed:
            client._validate_url("https://source.example/data")
        else:
            with pytest.raises(SourceHttpError, match="non-public"):
                client._validate_url("https://source.example/data")
    finally:
        client.close()


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> SecureSourceClient:
    client = SecureSourceClient(
        {"source.example"},
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    client._validate_url = lambda _url: None  # type: ignore[method-assign]
    return client


def test_credential_rejection_is_not_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request)

    client = _client(handler)
    with pytest.raises(SourceCredentialHttpError, match="401"):
        client.get("https://source.example/data")
    client.close()

    assert attempts == 1


def test_rate_limit_honors_retry_after_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, content=b"ok", request=request)

    monkeypatch.setattr("app.sources.http.time.sleep", delays.append)
    client = _client(handler)
    body, _, _ = client.get("https://source.example/data")
    client.close()

    assert body == b"ok"
    assert attempts == 2
    assert delays == [0.0]


def test_rate_limit_larger_than_job_timeout_is_not_slept_or_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "30"}, request=request)

    monkeypatch.setattr("app.sources.http.time.sleep", delays.append)
    client = _client(handler)
    with pytest.raises(SourceTransientHttpError, match="429"):
        client.get("https://source.example/data")
    client.close()

    assert attempts == 1
    assert delays == []


def test_server_errors_use_bounded_three_attempt_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    monkeypatch.setattr("app.sources.http.time.sleep", lambda _delay: None)
    client = _client(handler)
    with pytest.raises(SourceTransientHttpError, match="503"):
        client.get("https://source.example/data")
    client.close()

    assert attempts == 3


def test_request_budget_counts_retry_attempts() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    client = SecureSourceClient(
        {"source.example"},
        timeout_seconds=2,
        max_requests=2,
        transport=httpx.MockTransport(handler),
    )
    client._validate_url = lambda _url: None  # type: ignore[method-assign]
    with pytest.raises(SourceRunBudgetExceeded, match="request limit"):
        client.get("https://source.example/data")
    client.close()

    assert attempts == 2
    assert client.request_count == 2


def test_request_budget_counts_redirects() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(302, headers={"Location": "/final"}, request=request)

    client = SecureSourceClient(
        {"source.example"},
        timeout_seconds=2,
        max_requests=1,
        transport=httpx.MockTransport(handler),
    )
    client._validate_url = lambda _url: None  # type: ignore[method-assign]
    with pytest.raises(SourceRunBudgetExceeded, match="request limit"):
        client.get("https://source.example/data")
    client.close()

    assert attempts == 1


def test_response_byte_budget_is_shared_across_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"four", request=request)

    client = SecureSourceClient(
        {"source.example"},
        timeout_seconds=2,
        max_total_bytes=6,
        transport=httpx.MockTransport(handler),
    )
    client._validate_url = lambda _url: None  # type: ignore[method-assign]
    assert client.get("https://source.example/one")[0] == b"four"
    with pytest.raises(SourceRunBudgetExceeded, match="byte limit"):
        client.get("https://source.example/two")
    client.close()

    assert client.request_count == 2
    assert client.response_bytes == 4


def test_expired_run_budget_blocks_network_request() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=b"ok", request=request)

    client = SecureSourceClient(
        {"source.example"},
        timeout_seconds=2,
        max_run_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    client._validate_url = lambda _url: None  # type: ignore[method-assign]
    client.started_at -= 2
    with pytest.raises(SourceRunBudgetExceeded, match="time limit"):
        client.get("https://source.example/data")
    client.close()

    assert attempts == 0


def test_credential_backed_adapter_exposes_stable_rejection_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NaverTrendAdapter(
        "https://source.example/data",
        SecretStr("client-id"),
        SecretStr("client-secret"),
        2,
        1024,
    )

    def reject(*_args: object, **_kwargs: object) -> tuple[bytes, str, str]:
        raise SourceCredentialHttpError("Source rejected credentials with HTTP 403")

    monkeypatch.setattr(adapter.client, "post_json", reject)
    result = adapter.fetch({"targets": [{"country": "KR", "keyword": "서울 여행"}]})
    adapter.client.close()

    assert result.status is SourceStatus.UNAVAILABLE
    assert result.reason_code is FetchReasonCode.CREDENTIAL_REJECTED
    assert result.items == ()


def test_youtube_does_not_publish_partial_statistics_as_complete_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = YouTubeAggregateAdapter(
        "https://source.example",
        SecretStr("api-key"),
        2,
        1024,
    )
    responses = iter(
        (
            b'{"items":[{"id":{"videoId":"one"}},{"id":{"videoId":"two"}}]}',
            b'{"items":[{"id":"one","statistics":{"viewCount":"10",'
            b'"likeCount":"2","commentCount":"1"}}]}',
        )
    )

    def get(*_args: object, **_kwargs: object) -> tuple[bytes, str, str]:
        return next(responses), "application/json", "https://source.example"

    monkeypatch.setattr(adapter.client, "get", get)
    result = adapter.fetch(
        {"targets": [{"country": "JP", "keyword": "韓国旅行"}], "lookback_days": 1}
    )
    adapter.client.close()

    assert result.status is SourceStatus.AVAILABLE
    assert result.items[0].body["post_count"] == 2
    assert result.items[0].body["view_count"] is None
    assert result.items[0].body["reaction_count"] is None
    assert "youtube_region_availability_filter" in result.items[0].body["quality_flags"]
