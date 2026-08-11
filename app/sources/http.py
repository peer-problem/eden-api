from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential


class SourceHttpError(RuntimeError):
    pass


class SourceTransientHttpError(SourceHttpError):
    """A bounded retry may succeed without changing the source request."""


class SecureSourceClient:
    """Bounded, allowlisted HTTP client for scheduler-side source adapters."""

    def __init__(
        self,
        allowed_hosts: Iterable[str],
        timeout_seconds: float = 20,
        max_response_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.max_response_bytes = max_response_bytes
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "EDEN-Ingestion/0.1 (+https://api.edenapi.org)"},
        )

    def close(self) -> None:
        self.client.close()

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
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise SourceHttpError(f"Registered host resolved to a non-public address: {host}")

    @retry(
        retry=retry_if_exception_type(SourceTransientHttpError),
        wait=wait_random_exponential(multiplier=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kwargs: object) -> tuple[bytes, str, str]:
        current_url = url
        current_method = method
        current_kwargs = kwargs
        try:
            for _ in range(6):
                self._validate_url(current_url)
                with self.client.stream(current_method, current_url, **current_kwargs) as response:
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
                    if response.status_code in {408, 425, 429} or response.status_code >= 500:
                        raise SourceTransientHttpError(
                            f"Source returned transient HTTP {response.status_code}"
                        )
                    if response.status_code >= 400:
                        raise SourceHttpError(f"Source returned HTTP {response.status_code}")
                    chunks: list[bytes] = []
                    length = 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        if length > self.max_response_bytes:
                            raise SourceHttpError("Source response exceeded configured byte limit")
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
