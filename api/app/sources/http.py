from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from random import SystemRandom
from time import monotonic
from urllib.parse import urljoin, urlparse

import httpx

_JITTER = SystemRandom()
_NAT64_WELL_KNOWN_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


class SourceHttpError(RuntimeError):
    pass


class SourceTransientHttpError(SourceHttpError):
    """A bounded retry may succeed without changing the source request."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        max_attempts: int = 3,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.max_attempts = max_attempts


class SourceCredentialHttpError(SourceHttpError):
    """The source rejected configured credentials or approval scope."""


class SourceRunBudgetExceeded(SourceHttpError):
    """The source run exhausted its shared request, byte, or time budget."""


def _retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdecimal():
        return float(stripped)
    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return max(0.0, (retry_at.astimezone(UTC) - current.astimezone(UTC)).total_seconds())


class SecureSourceClient:
    """Bounded, allowlisted HTTP client for scheduler-side source adapters."""

    def __init__(
        self,
        allowed_hosts: Iterable[str],
        timeout_seconds: float = 20,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_requests: int = 20,
        max_total_bytes: int = 8 * 1024 * 1024,
        max_run_seconds: float = 120.0,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_requests < 1:
            raise ValueError("Source request budget must be positive")
        if max_total_bytes < 1:
            raise ValueError("Source byte budget must be positive")
        if max_run_seconds <= 0:
            raise ValueError("Source time budget must be positive")
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.max_response_bytes = max_response_bytes
        self.max_requests = max_requests
        self.max_total_bytes = max_total_bytes
        self.max_run_seconds = max_run_seconds
        self.timeout_seconds = timeout_seconds
        self.request_count = 0
        self.response_bytes = 0
        self.started_at = monotonic()
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "EDEN-Ingestion/0.1 (+https://api.edenapi.org)"},
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def _remaining_seconds(self) -> float:
        return self.max_run_seconds - (monotonic() - self.started_at)

    def _reserve_request(self) -> float:
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise SourceRunBudgetExceeded("Source run exceeded configured time limit")
        if self.request_count >= self.max_requests:
            raise SourceRunBudgetExceeded("Source run exceeded configured request limit")
        self.request_count += 1
        return remaining

    def _record_response_chunk(self, length: int, response_length: int) -> None:
        if self._remaining_seconds() <= 0:
            raise SourceRunBudgetExceeded("Source run exceeded configured time limit")
        if response_length > self.max_response_bytes:
            raise SourceHttpError("Source response exceeded configured byte limit")
        if self.response_bytes + length > self.max_total_bytes:
            raise SourceRunBudgetExceeded("Source run exceeded configured byte limit")
        self.response_bytes += length

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or host not in self.allowed_hosts:
            raise SourceHttpError(f"URL host is not registered: {host or '<missing>'}")
        try:
            addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise SourceTransientHttpError(
                f"DNS lookup failed for registered host: {host}"
            ) from exc
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            # DNS64 can synthesize a reserved IPv6 address for a public IPv4
            # origin. Validate its actual destination, including private IPv4
            # addresses embedded in the well-known translation prefix.
            if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WELL_KNOWN_PREFIX:
                ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            if not ip.is_global or ip.is_multicast or ip.is_reserved:
                raise SourceHttpError(f"Registered host resolved to a non-public address: {host}")

    def _request(self, method: str, url: str, **kwargs: object) -> tuple[bytes, str, str]:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._request_once(method, url, **kwargs)
            except SourceTransientHttpError as exc:
                if attempt >= exc.max_attempts:
                    raise
                if exc.retry_after_seconds is not None:
                    if exc.retry_after_seconds > self.timeout_seconds:
                        raise
                    delay = exc.retry_after_seconds
                else:
                    delay = _JITTER.uniform(0.5, min(8.0, 0.5 * (2 ** (attempt - 1))))
                if delay >= self._remaining_seconds():
                    raise SourceRunBudgetExceeded(
                        "Source run exceeded configured time limit"
                    ) from exc
                time.sleep(delay)

    def _request_once(self, method: str, url: str, **kwargs: object) -> tuple[bytes, str, str]:
        current_url = url
        current_method = method
        current_kwargs = kwargs
        try:
            for _ in range(6):
                self._validate_url(current_url)
                remaining = self._reserve_request()
                request_kwargs = dict(current_kwargs)
                request_kwargs["timeout"] = min(self.timeout_seconds, remaining)
                with self.client.stream(
                    current_method,
                    current_url,
                    **request_kwargs,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceHttpError("Redirect response did not include Location")
                        current_url = urljoin(current_url, location)
                        if response.status_code in {301, 302, 303} and current_method != "GET":
                            current_method = "GET"
                            current_kwargs = {
                                key: value
                                for key, value in current_kwargs.items()
                                if key not in {"content", "data", "files", "json"}
                            }
                        continue
                    if response.status_code in {401, 403}:
                        raise SourceCredentialHttpError(
                            f"Source rejected credentials with HTTP {response.status_code}"
                        )
                    if response.status_code == 429:
                        raise SourceTransientHttpError(
                            "Source returned rate limit HTTP 429",
                            retry_after_seconds=_retry_after_seconds(
                                response.headers.get("retry-after")
                            ),
                            max_attempts=2,
                        )
                    if response.status_code in {408, 425} or response.status_code >= 500:
                        raise SourceTransientHttpError(
                            f"Source returned transient HTTP {response.status_code}"
                        )
                    if response.status_code >= 400:
                        raise SourceHttpError(f"Source returned HTTP {response.status_code}")
                    chunks: list[bytes] = []
                    length = 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        self._record_response_chunk(len(chunk), length)
                        chunks.append(chunk)
                    return (
                        b"".join(chunks),
                        response.headers.get("content-type", ""),
                        current_url,
                    )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise SourceTransientHttpError("Transient source transport failure") from exc
        raise SourceHttpError("Source exceeded redirect limit")

    def get(self, url: str, **kwargs: object) -> tuple[bytes, str, str]:
        return self._request("GET", url, **kwargs)

    def post_form(
        self,
        url: str,
        data: dict[str, object],
        **kwargs: object,
    ) -> tuple[bytes, str, str]:
        return self._request("POST", url, data=data, **kwargs)

    def post_json(
        self,
        url: str,
        body: dict[str, object],
        **kwargs: object,
    ) -> tuple[bytes, str, str]:
        return self._request("POST", url, json=body, **kwargs)
