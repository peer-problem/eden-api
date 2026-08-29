from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.base import FetchReasonCode
from app.sources.http import (
    SecureSourceClient,
    SourceCredentialHttpError,
    SourceTransientHttpError,
)
from app.sources.social import NaverTrendAdapter


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
    result = adapter.fetch(
        {"targets": [{"country": "KR", "keyword": "서울 여행"}]}
    )
    adapter.client.close()

    assert result.status is SourceStatus.UNAVAILABLE
    assert result.reason_code is FetchReasonCode.CREDENTIAL_REJECTED
    assert result.items == ()
