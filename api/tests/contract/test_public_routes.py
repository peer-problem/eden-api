from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import Availability, SpatialResolution
from app.readmodels.results import ReadResult

PUBLIC_REQUESTS = (
    (
        "get",
        "/v1/trends",
        {"params": {"keyword": "제주"}},
        "trends",
        {
            "keyword": "제주",
            "country": "all",
            "period": "30d",
            "time_unit": "day",
            "limit": 10,
        },
    ),
    (
        "get",
        "/v1/regions/11/insights",
        {},
        "region_insights",
        {
            "area_code": "eden-area:11",
            "period": "30d",
            "visitor_type": "all",
            "include": ["demand", "diversity", "visitors"],
        },
    ),
    (
        "get",
        "/v1/places/tour-1",
        {},
        "place_detail",
        {
            "content_id": "eden-place:tour-1",
            "lang": "ko",
            "radius_m": 1000,
            "related_limit": 5,
            "shops_limit": 5,
            "include": ["hub", "related", "shops"],
        },
    ),
    (
        "get",
        "/v1/forecasts/visitors",
        {"params": {"area_code": "11"}},
        "visitor_forecast",
        {
            "area_code": "eden-area:11",
            "days": 7,
            "include": ["festivals", "holidays", "weather"],
        },
    ),
    (
        "get",
        "/v1/visitors/timeseries",
        {"params": {"area_code": "11"}},
        "visitor_timeseries",
        {
            "area_code": "eden-area:11",
            "period": "30d",
            "granularity": "day",
            "visitor_type": "all",
        },
    ),
    (
        "get",
        "/v1/markets/inbound",
        {"params": [("countries", "us"), ("countries", "jp")]},
        "inbound_markets",
        {
            "countries": ["JP", "US"],
            "period": "12m",
            "forecast_days": 7,
            "include": [
                "flight_schedule",
                "flights",
                "fx",
                "social_interest",
                "tourism_balance",
                "visitors",
            ],
        },
    ),
    (
        "get",
        "/v1/markets/us/alerts",
        {},
        "market_alerts",
        {
            "country": "US",
            "source_scope": "all",
            "language": "ko",
            "limit": 20,
        },
    ),
    (
        "post",
        "/v1/recommendations/destinations",
        {"json": {"target_country": "us", "travel_window": {"season": "spring", "days": 3}}},
        "recommendations",
        {
            "target_country": "US",
            "travel_window": {"season": "spring", "days": 3},
        },
    ),
)


def _assert_error(response: Any, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert payload["request_id"] == "contract-request-id"
    assert response.headers["X-Request-ID"] == "contract-request-id"
    assert payload["error"]["code"] == code
    assert isinstance(payload["error"]["message"], str)
    assert payload["error"]["message"]


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "endpoint", "expected_scope"),
    PUBLIC_REQUESTS,
    ids=[case[3] for case in PUBLIC_REQUESTS],
)
def test_all_public_routes_use_only_the_published_read_repository(
    contract_client: TestClient,
    fake_read_repository: Any,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    endpoint: str,
    expected_scope: dict[str, Any],
) -> None:
    response = contract_client.request(
        method,
        path,
        headers={"X-Request-ID": "contract-request-id"},
        **kwargs,
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "contract-request-id"
    assert response.json()["data"] is None
    meta = response.json()["meta"]
    assert {
        key: meta[key]
        for key in (
            "request_id",
            "timezone",
            "spatial_resolution",
            "stale",
            "availability",
            "reason",
            "formula_versions",
            "sources",
        )
    } == {
        "request_id": "contract-request-id",
        "timezone": "Asia/Seoul",
        "spatial_resolution": "none",
        "stale": False,
        "availability": "unavailable",
        "reason": "테스트용 게시 데이터가 없습니다.",
        "formula_versions": {},
        "sources": [],
    }
    assert response.json()["meta"]["freshness"] == {
        "status": "unavailable",
        "age_seconds": None,
        "max_acceptable_age_seconds": None,
    }
    assert fake_read_repository.calls[-1].endpoint == endpoint
    assert fake_read_repository.calls[-1].scope == expected_scope


def _trend_data() -> dict[str, Any]:
    return {
        "keyword": "제주",
        "area_code": None,
        "country": "all",
        "period": "30d",
        "time_unit": "day",
        "interest_index": 42.5,
        "change_rate": 2.5,
        "source_metrics": [],
        "source_availability": {},
        "series": [],
        "rising_keywords": [],
        "sources": ["SRC_NAVER_TREND"],
    }


@pytest.mark.parametrize(
    ("availability", "age", "max_age", "expected_stale", "freshness"),
    (
        (Availability.AVAILABLE, timedelta(seconds=5), 3600, False, "fresh"),
        (Availability.PARTIAL, timedelta(seconds=5), 3600, False, "fresh"),
        (Availability.AVAILABLE, timedelta(hours=2), 3600, True, "stale"),
    ),
)
def test_available_partial_and_stale_use_the_same_envelope_contract(
    contract_client: TestClient,
    fake_read_repository: Any,
    availability: Availability,
    age: timedelta,
    max_age: int,
    expected_stale: bool,
    freshness: str,
) -> None:
    now = datetime.now(UTC)
    fake_read_repository.results["trends"] = ReadResult(
        data=_trend_data(),
        availability=availability,
        reason="일부 선택 원천이 없습니다." if availability is Availability.PARTIAL else None,
        as_of=now - age,
        calculated_at=now,
        max_acceptable_age_seconds=max_age,
        spatial_resolution=SpatialResolution.NONE,
        formula_versions={"interest_index": "interest-v1"},
        sources=[
            {
                "source_id": "SRC_NAVER_TREND",
                "status": "available",
                "data_as_of": now - age,
                "last_success_at": now - age,
                "stale": False,
                "reason": None,
            }
        ],
    )

    response = contract_client.get("/v1/trends", params={"keyword": "제주"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == _trend_data()
    assert payload["meta"]["availability"] == availability
    assert payload["meta"]["stale"] is expected_stale
    assert payload["meta"]["freshness"]["status"] == freshness
    assert payload["meta"]["freshness"]["max_acceptable_age_seconds"] == max_age
    assert payload["meta"]["formula_versions"] == {"interest_index": "interest-v1"}
    assert payload["meta"]["sources"][0]["source_id"] == "SRC_NAVER_TREND"


@pytest.mark.parametrize(
    ("path", "missing_kind", "code"),
    (
        ("/v1/regions/missing/insights", "area", "AREA_NOT_FOUND"),
        ("/v1/places/missing", "place", "PLACE_NOT_FOUND"),
    ),
)
def test_missing_eden_id_or_alias_has_stable_404_error(
    contract_client: TestClient,
    fake_read_repository: Any,
    path: str,
    missing_kind: str,
    code: str,
) -> None:
    if missing_kind == "area":
        fake_read_repository.missing_areas.add("missing")
    else:
        fake_read_repository.missing_places.add("missing")

    response = contract_client.get(path, headers={"X-Request-ID": "contract-request-id"})

    _assert_error(response, 404, code)


INVALID_REQUESTS = (
    ("get", "/v1/trends", {}, "VALIDATION_ERROR"),
    (
        "get",
        "/v1/trends",
        {"params": {"keyword": "x", "country": "ZZ"}},
        "VALIDATION_ERROR",
    ),
    ("get", "/v1/trends", {"params": {"keyword": "x", "period": "1d"}}, "VALIDATION_ERROR"),
    ("get", "/v1/trends", {"params": {"keyword": "x", "limit": 101}}, "VALIDATION_ERROR"),
    ("get", "/v1/places/place", {"params": {"radius_m": 99}}, "VALIDATION_ERROR"),
    (
        "get",
        "/v1/forecasts/visitors",
        {"params": {"area_code": "11", "days": 31}},
        "VALIDATION_ERROR",
    ),
    (
        "get",
        "/v1/forecasts/visitors",
        {"params": {"area_code": "11", "nx": 60}},
        "GRID_PAIR_REQUIRED",
    ),
    (
        "get",
        "/v1/visitors/timeseries",
        {"params": {"area_code": "11", "granularity": "hour"}},
        "VALIDATION_ERROR",
    ),
    ("get", "/v1/markets/inbound", {"params": {"countries": "USA"}}, "VALIDATION_ERROR"),
    (
        "get",
        "/v1/markets/inbound",
        {"params": {"countries": "US", "currency": "WON"}},
        "VALIDATION_ERROR",
    ),
    ("get", "/v1/markets/us/alerts", {"params": {"since": "not-a-date"}}, "VALIDATION_ERROR"),
    (
        "post",
        "/v1/recommendations/destinations",
        {
            "json": {
                "target_country": "US",
                "travel_window": {"season": "spring", "days": 3},
                "limit": 21,
            }
        },
        "VALIDATION_ERROR",
    ),
    (
        "post",
        "/v1/recommendations/destinations",
        {
            "json": {
                "target_country": "US",
                "travel_window": {"season": "spring", "days": 3},
                "unknown": True,
            }
        },
        "VALIDATION_ERROR",
    ),
)


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "code"),
    INVALID_REQUESTS,
)
def test_input_contract_violations_have_stable_422_errors(
    contract_client: TestClient,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    code: str,
) -> None:
    response = contract_client.request(
        method,
        path,
        headers={"X-Request-ID": "contract-request-id"},
        **kwargs,
    )

    _assert_error(response, 422, code)
    details = response.json()["error"].get("details")
    if details is not None:
        assert all("input" not in detail and "ctx" not in detail for detail in details)


def test_oversized_body_has_stable_413_error(contract_client: TestClient) -> None:
    response = contract_client.post(
        "/v1/recommendations/destinations",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": "2048",
            "X-Request-ID": "contract-request-id",
        },
    )

    _assert_error(response, 413, "REQUEST_TOO_LARGE")


def test_chunked_oversized_body_has_stable_413_error(contract_client: TestClient) -> None:
    def chunks():
        yield b"{" + (b'"padding":"' + b"x" * 2048)
        yield b'"}'

    response = contract_client.post(
        "/v1/recommendations/destinations",
        content=chunks(),
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": "contract-request-id",
        },
    )

    _assert_error(response, 413, "REQUEST_TOO_LARGE")


def test_unexpected_repository_failure_has_stable_safe_500_error(
    contract_client: TestClient,
    fake_read_repository: Any,
) -> None:
    fake_read_repository.fetch_error = RuntimeError("mysql://user:secret@private-host/source-body")

    response = contract_client.get(
        "/v1/trends",
        params={"keyword": "제주"},
        headers={"X-Request-ID": "contract-request-id"},
    )

    _assert_error(response, 500, "INTERNAL_ERROR")
    serialized = response.text
    assert "secret" not in serialized
    assert "private-host" not in serialized
    assert "source-body" not in serialized


def test_all_eight_route_calls_are_socket_free(
    contract_client: TestClient,
    fake_read_repository: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_socket_connect(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("public API request attempted a socket connection")

    monkeypatch.setattr(socket.socket, "connect", reject_socket_connect)

    for method, path, kwargs, *_ in PUBLIC_REQUESTS:
        response = contract_client.request(method, path, **kwargs)
        assert response.status_code == 200, (method, path, response.text)

    assert len(fake_read_repository.calls) == 8


@pytest.mark.parametrize("keyword", ["   ", "\t\n", "\u3000"])
def test_blank_keyword_is_invalid_after_unicode_normalization(contract_client, keyword):
    response = contract_client.get("/v1/trends", params={"keyword": keyword})
    assert response.status_code == 422


def test_keyword_nfkc_and_country_error_location(contract_client, fake_read_repository):
    response = contract_client.get("/v1/trends", params={"keyword": " Ｋｏｒｅａ travel "})
    assert response.status_code == 200
    assert fake_read_repository.calls[-1].scope["keyword"] == "Korea travel"
    response = contract_client.get("/v1/trends", params={"keyword": "x", "country": "ZZ"})
    assert response.status_code == 422
    assert all(
        error["loc"] == ["query", "country"] for error in response.json()["error"]["details"]
    )


def test_since_requires_timezone(contract_client):
    assert (
        contract_client.get(
            "/v1/markets/JP/alerts", params={"since": "2026-09-11T12:00:00"}
        ).status_code
        == 422
    )
    assert (
        contract_client.get(
            "/v1/markets/JP/alerts", params={"since": "2026-09-11T12:00:00+09:00"}
        ).status_code
        == 200
    )


def test_recommendation_days_and_party_are_optional_and_not_filled_in(
    contract_client, fake_read_repository
):
    response = contract_client.post(
        "/v1/recommendations/destinations",
        json={"target_country": "JP", "travel_window": {"season": "autumn"}},
    )
    assert response.status_code == 200
    scope = fake_read_repository.calls[-1].scope
    assert "days" not in scope["travel_window"]
    assert "party_size" not in scope
