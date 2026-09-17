from __future__ import annotations

import json

import httpx
from pydantic import SecretStr

from app.domain.enums import SourceStatus
from app.sources.public_data import PublicDataAdapter


def _response(page: int, *, total: int = 1, count: int = 1) -> bytes:
    items = [
        {"id": f"row-{page}-{index}", "baseYmd": f"202609{page:02d}"}
        for index in range(count)
    ]
    return json.dumps(
        {
            "response": {
                "header": {"resultCode": "00"},
                "body": {"totalCount": total, "items": {"item": items}},
            }
        }
    ).encode()


def _adapter(
    handler,
    *,
    max_requests: int = 20,
    max_records: int = 10_000,
) -> PublicDataAdapter:
    adapter = PublicDataAdapter(
        "SRC_TEST",
        "https://source.example",
        SecretStr("service-key"),
        2,
        2 * 1024 * 1024,
        max_requests,
        8 * 1024 * 1024,
        max_records,
        120,
    )
    adapter.client.client.close()
    adapter.client.client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter.client._validate_url = lambda _url: None  # type: ignore[method-assign]
    return adapter


def test_request_cap_preserves_useful_pages_and_marks_partial() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("pageNo", "1"))
        return httpx.Response(200, content=_response(page), request=request)

    adapter = _adapter(handler, max_requests=2)
    result = adapter.fetch(
        {
            "operations": [
                {"operation": f"operation-{index}", "max_pages": 1}
                for index in range(3)
            ]
        }
    )
    adapter.client.close()

    assert result.status is SourceStatus.DEGRADED
    assert len(result.items) == 2
    assert adapter.client.request_count == 2
    assert any("SourceRunBudgetExceeded" in error for error in result.partial_errors)


def test_kma_complete_horizon_above_one_thousand_rows_uses_one_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["numOfRows"] == "2000"
        return httpx.Response(200, content=_response(1, total=1052, count=1052), request=request)

    adapter = _adapter(handler, max_requests=1)
    adapter.source_id = "SRC_KMA_FORECAST"
    result = adapter.fetch({"operations": [{
        "operation": "getVilageFcst",
        "params": {"numOfRows": 2000, "base_date": "20260911", "base_time": "1700"},
        "watermark": {"params": ["base_date", "base_time"], "format": "%Y%m%d%H%M"},
        "max_pages": 1,
    }]})
    adapter.client.close()

    assert result.status is SourceStatus.AVAILABLE
    assert len(result.items) == 1
    assert adapter.client.request_count == 1
    assert result.partial_errors == ()


def test_record_cap_stops_before_requesting_an_incomplete_next_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("pageNo", "1"))
        return httpx.Response(
            200,
            content=_response(page, total=3, count=2 if page == 1 else 1),
            request=request,
        )

    adapter = _adapter(handler, max_records=2)
    result = adapter.fetch(
        {
            "operations": [
                {
                    "operation": "bounded",
                    "params": {"numOfRows": 2},
                    "max_pages": 20,
                }
            ]
        }
    )
    adapter.client.close()

    assert result.status is SourceStatus.DEGRADED
    assert len(result.items) == 1
    assert adapter.client.request_count == 1
    assert "source_run:record_limit_exceeded" in result.partial_errors


def test_planned_operation_rotation_is_available_without_failure_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_response(1), request=request)

    adapter = _adapter(handler)
    result = adapter.fetch(
        {
            "rotate_operations": True,
            "max_operations_per_run": 20,
            "rotation_seconds": 3600,
            "operations": [
                {
                    "operation": f"operation-{index}",
                    "max_pages": 1,
                    "watermark": {
                        "response_field": "baseYmd",
                        "format": "%Y%m%d",
                    },
                }
                for index in range(45)
            ],
        }
    )
    adapter.client.close()

    assert result.status is SourceStatus.AVAILABLE
    assert result.reason is None
    assert len(result.items) <= 20
    assert adapter.client.request_count <= 20
    assert any("rotating_operation_batch" in error for error in result.partial_errors)


def test_planned_page_rotation_is_available_and_does_not_repeat_only_page_one() -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("pageNo", "1"))
        requested_pages.append(page)
        return httpx.Response(
            200,
            content=_response(page, total=50, count=10),
            request=request,
        )

    adapter = _adapter(handler, max_requests=3)
    result = adapter.fetch(
        {
            "operations": [
                {
                    "operation": "catalog",
                    "params": {"numOfRows": 10},
                    "max_pages": 3,
                    "rotate_pages": True,
                    "rotation_seconds": 10**12,
                    "watermark": {
                        "response_field": "baseYmd",
                        "format": "%Y%m%d",
                    },
                }
            ]
        }
    )
    adapter.client.close()

    assert result.status is SourceStatus.AVAILABLE
    assert result.reason is None
    assert len(result.items) == 2
    assert len(requested_pages) <= 3
    assert len({item.external_key for item in result.items}) == 2
    assert any("rotating_page_batch" in error for error in result.partial_errors)
    assert not any("pagination_limit_exceeded" in error for error in result.partial_errors)


def test_detail_operations_bypass_the_essential_catalog_selection() -> None:
    from app.sources.plans import tour_detail_operations

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/detailCommon2")
        assert request.url.params.get("contentId") == "126508"
        payload = {
            "response": {
                "header": {"resultCode": "0000", "resultMsg": "OK"},
                "body": {
                    "totalCount": 1,
                    "items": {
                        "item": [
                            {
                                "contentid": "126508",
                                "title": "경복궁",
                                "overview": "조선 왕조의 법궁",
                                "modifiedtime": "20260520091252",
                            }
                        ]
                    },
                },
            }
        }
        return httpx.Response(200, json=payload)

    adapter = _adapter(handler)
    result = adapter.fetch(
        {
            "essential_catalog": True,
            "allowed_content_ids": {"1": ["999"]},
            "operations": tour_detail_operations(["126508"]),
        }
    )

    assert result.status is SourceStatus.AVAILABLE
    assert len(result.items) == 1
    item = result.items[0]
    assert item.external_key == "detailCommon2:content=126508:1"
    from app.sources.public_data import public_data_items

    rows = public_data_items(item.body["response"])
    assert rows[0]["overview"] == "조선 왕조의 법궁"
    assert item.body["response"].get("scope") != "bounded_province_catalog"
