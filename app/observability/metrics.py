from __future__ import annotations

from prometheus_client import Counter, Histogram

from app.domain.enums import Availability

HTTP_REQUESTS = Counter(
    "eden_http_requests_total",
    "EDEN HTTP requests by stable route and status.",
    ("method", "endpoint", "status_code"),
)
HTTP_DURATION = Histogram(
    "eden_http_request_duration_seconds",
    "EDEN HTTP request duration by stable route.",
    ("method", "endpoint"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
API_AVAILABILITY = Counter(
    "eden_api_responses_total",
    "EDEN public API responses by availability and stale state.",
    ("endpoint", "availability", "stale"),
)


def record_http_request(method: str, endpoint: str, status_code: int, duration: float) -> None:
    HTTP_REQUESTS.labels(method, endpoint, str(status_code)).inc()
    HTTP_DURATION.labels(method, endpoint).observe(duration)


def record_api_availability(
    endpoint: str,
    availability: Availability,
    stale: bool,
) -> None:
    API_AVAILABILITY.labels(endpoint, availability.value, str(stale).lower()).inc()
