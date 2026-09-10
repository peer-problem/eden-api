from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree.ElementTree import ParseError

import pytest

from app.sources.alerts import (
    extract_notice_links,
    parse_notice_detail,
    parse_notice_feed,
    sanitize_html,
)
from app.sources.bok import _ecos_rows
from app.sources.http import SecureSourceClient
from app.sources.keta import parse_keta_notice_detail, parse_keta_notice_links
from app.sources.kto_inbound import aggregate_kto_inbound_rows
from app.sources.public_data import (
    PublicDataAuthenticationError,
    parse_json_or_xml,
    public_data_items,
    public_data_page_info,
    public_data_watermark,
    validate_public_data_result,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "sources"
OBSERVED_AT = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)
NOTICE_TARGET = {
    "country": "KR",
    "source_name": "Test Embassy",
    "source_type": "embassy",
    "source_scope": "country",
    "alert_type": "travel",
    "languages": ["en"],
}


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def _fixture_json(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _forbid_network(*args: object, **kwargs: object) -> tuple[bytes, str, str]:
    del args, kwargs
    raise AssertionError("parser fixture test attempted a network request")


@pytest.fixture(autouse=True)
def block_source_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SecureSourceClient, "get", _forbid_network)
    monkeypatch.setattr(SecureSourceClient, "post_form", _forbid_network)
    monkeypatch.setattr(SecureSourceClient, "post_json", _forbid_network)


def test_notice_rss_recovers_valid_entry_and_sanitizes_active_html() -> None:
    items = parse_notice_feed(
        _fixture_bytes("official_notice_feed.xml"),
        "https://embassy.example/rss.xml",
        NOTICE_TARGET,
        {"embassy.example"},
        OBSERVED_AT,
        10,
    )

    assert len(items) == 1
    assert items[0].source_updated_at == datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
    assert isinstance(items[0].body, dict)
    body = str(items[0].body["body"])
    assert "Visitors should review" in body
    assert "active-script-secret" not in body
    assert "form-secret" not in body
    assert "<script" not in body
    assert "<form" not in body


@pytest.mark.parametrize(
    ("fixture_name", "expected_count"),
    [
        ("official_notice_feed_empty.xml", 0),
        ("official_notice_feed_malformed.xml", 0),
    ],
)
def test_notice_rss_empty_and_malformed_are_bounded(
    fixture_name: str,
    expected_count: int,
) -> None:
    items = parse_notice_feed(
        _fixture_bytes(fixture_name),
        "https://embassy.example/rss.xml",
        NOTICE_TARGET,
        {"embassy.example"},
        OBSERVED_AT,
        10,
    )

    assert len(items) == expected_count


@pytest.mark.parametrize(
    ("fixture_name", "expected_paths"),
    [
        ("official_notice_listing.html", ["/notices/entry-requirements"]),
        ("official_notice_listing_empty.html", []),
        ("official_notice_listing_malformed.html", ["/news/travel-warning"]),
    ],
)
def test_notice_html_link_parser_recovers_only_allowlisted_entries(
    fixture_name: str,
    expected_paths: list[str],
) -> None:
    links = extract_notice_links(
        _fixture_bytes(fixture_name),
        "https://embassy.example/notices/",
        {"embassy.example"},
        10,
    )

    assert [urlparse(url).path for url, _title in links] == expected_paths


def test_notice_html_detail_removes_active_content_and_forms() -> None:
    item = parse_notice_detail(
        _fixture_bytes("official_notice_detail.html"),
        "https://embassy.example/notices/entry-requirements",
        "",
        NOTICE_TARGET,
        OBSERVED_AT,
    )

    assert item.source_updated_at == datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
    assert isinstance(item.body, dict)
    body = str(item.body["body"])
    assert "Visitors should review" in body
    for removed in (
        "active-head-secret",
        "active-body-secret",
        "form-secret",
        "iframe-secret",
        "object-secret",
        ".hidden",
    ):
        assert removed not in body
    assert "<" not in body


def test_notice_html_malformed_detail_is_rejected_after_sanitization() -> None:
    sanitized = sanitize_html(_fixture_bytes("official_notice_detail_malformed.html"))

    assert "active-secret" not in sanitized
    with pytest.raises(ValueError, match="enough text"):
        parse_notice_detail(
            _fixture_bytes("official_notice_detail_malformed.html"),
            "https://embassy.example/notices/short",
            "Short notice",
            NOTICE_TARGET,
            OBSERVED_AT,
        )


@pytest.mark.parametrize(
    ("fixture_name", "expected_ids"),
    [
        ("keta_notice_list.html", ["1201", "1202"]),
        ("keta_notice_list_empty.html", []),
        ("keta_notice_list_malformed.html", ["1301"]),
    ],
)
def test_keta_list_parser_recovers_valid_unique_notice_ids(
    fixture_name: str,
    expected_ids: list[str],
) -> None:
    links = parse_keta_notice_links(_fixture_bytes(fixture_name))

    assert [
        (parse_qs(urlparse(url).query).get("bbsSn") or [""])[0] for url in links
    ] == expected_ids


def test_keta_detail_parser_sanitizes_body_and_normalizes_time() -> None:
    url = "https://www.k-eta.go.kr/portal/board/viewboarddetail.do?bbsSn=1201"
    notice = parse_keta_notice_detail(_fixture_bytes("keta_notice_detail.html"), url)

    assert notice.external_key == "1201"
    assert notice.title == "K-ETA 시스템 점검 안내"
    assert notice.published_at == datetime(2026, 8, 27, 15, 0, tzinfo=UTC)
    assert "안정적인 서비스" in notice.body
    for removed in (
        "active-script-secret",
        "form-secret",
        "iframe-secret",
        ".secret",
    ):
        assert removed not in notice.body
    assert "<" not in notice.body


@pytest.mark.parametrize(
    "fixture_name",
    ["keta_notice_detail_empty.html", "keta_notice_detail_malformed.html"],
)
def test_keta_empty_or_schema_drift_detail_is_rejected(fixture_name: str) -> None:
    url = "https://www.k-eta.go.kr/portal/board/viewboarddetail.do?bbsSn=1201"

    with pytest.raises(ValueError):
        parse_keta_notice_detail(_fixture_bytes(fixture_name), url)


def test_kto_inbound_parser_aggregates_duplicate_month_rows() -> None:
    document = _fixture_json("kto_inbound_normal.json")

    assert isinstance(document, dict)
    assert aggregate_kto_inbound_rows(document) == {"202605": 15, "202606": 8}


def test_kto_inbound_empty_result_stays_empty() -> None:
    document = _fixture_json("kto_inbound_empty.json")

    assert isinstance(document, dict)
    assert aggregate_kto_inbound_rows(document) == {}


def test_kto_inbound_schema_drift_is_not_silently_coerced() -> None:
    document = _fixture_json("kto_inbound_schema_drift.json")

    assert isinstance(document, dict)
    with pytest.raises(ValueError, match="invalid 기준년월"):
        aggregate_kto_inbound_rows(document)


@pytest.mark.parametrize(
    ("fixture_name", "content_type", "expected_ids"),
    [
        ("public_data_normal.json", "application/json", ["1001", "1002"]),
        ("public_data_normal.xml", "application/xml", ["2001", "2002"]),
    ],
)
def test_public_data_json_and_xml_share_the_page_contract(
    fixture_name: str,
    content_type: str,
    expected_ids: list[str],
) -> None:
    document = parse_json_or_xml(_fixture_bytes(fixture_name), content_type)

    validate_public_data_result(document)
    assert public_data_page_info(document) == (2, 2)
    assert [str(item["contentid"]) for item in public_data_items(document)] == expected_ids


def test_public_data_empty_result_stays_empty() -> None:
    document = parse_json_or_xml(
        _fixture_bytes("public_data_empty.json"),
        "application/json",
    )

    validate_public_data_result(document)
    assert public_data_page_info(document) == (0, 0)
    assert public_data_items(document) == []


def test_public_data_schema_drift_rejects_scalar_rows() -> None:
    document = parse_json_or_xml(
        _fixture_bytes("public_data_schema_drift.json"),
        "application/json",
    )

    with pytest.raises(ValueError, match="record shape"):
        public_data_items(document)


@pytest.mark.parametrize(
    ("fixture_name", "content_type"),
    [
        ("public_data_malformed.json", "application/json"),
        ("public_data_malformed.xml", "application/xml"),
    ],
)
def test_public_data_malformed_payload_is_rejected(
    fixture_name: str,
    content_type: str,
) -> None:
    with pytest.raises((ValueError, ParseError)):
        parse_json_or_xml(_fixture_bytes(fixture_name), content_type)


def test_public_data_authentication_error_is_classified() -> None:
    document = parse_json_or_xml(
        _fixture_bytes("public_data_auth_error.xml"),
        "application/xml",
    )

    with pytest.raises(PublicDataAuthenticationError, match="code 30"):
        validate_public_data_result(document)


def test_public_data_monthly_watermark_uses_source_period_not_fetch_time() -> None:
    document = _fixture_json("public_data_empty.json")
    assert public_data_watermark(
        {"watermark": {"param": "baseYm", "format": "%Y%m"}},
        {"baseYm": "202605"},
        document,
    ) == datetime(2026, 5, 1, tzinfo=UTC)


def test_public_data_watermark_uses_latest_response_period() -> None:
    document = {
        "response": {
            "body": {
                "items": {
                    "item": [
                        {"baseYmd": "20260825"},
                        {"baseYmd": "20260827"},
                    ]
                }
            }
        }
    }
    assert public_data_watermark(
        {
            "watermark": {
                "response_field": "baseYmd",
                "format": "%Y%m%d",
            }
        },
        {},
        document,
    ) == datetime(2026, 8, 27, tzinfo=UTC)


def test_kma_watermark_converts_naive_seoul_cycle_to_utc() -> None:
    assert public_data_watermark(
        {
            "watermark": {
                "params": ["base_date", "base_time"],
                "format": "%Y%m%d%H%M",
            }
        },
        {"base_date": "20260910", "base_time": "1100"},
        {},
        source_id="SRC_KMA_FORECAST",
    ) == datetime(2026, 9, 10, 2, tzinfo=UTC)


def test_local_watermark_preserves_an_explicit_utc_offset() -> None:
    assert public_data_watermark(
        {
            "watermark": {
                "response_field": "modifiedtime",
                "format": "%Y%m%d%H%M%S%z",
            }
        },
        {},
        {"item": {"modifiedtime": "20260910110000+0000"}},
        source_id="SRC_TOUR_KO",
    ) == datetime(2026, 9, 10, 11, tzinfo=UTC)


def test_holiday_request_month_is_not_a_publication_watermark() -> None:
    assert (
        public_data_watermark(
            {
                "watermark": {
                    "params": ["solYear", "solMonth"],
                    "format": "%Y%m",
                }
            },
            {"solYear": "2026", "solMonth": "10"},
            {},
            source_id="SRC_HOLIDAY",
        )
        is None
    )


def test_bok_ecos_parser_accepts_normal_and_empty_rows() -> None:
    normal = _fixture_json("bok_ecos_normal.json")
    empty = _fixture_json("bok_ecos_empty.json")

    assert len(_ecos_rows(normal)) == 2
    assert _ecos_rows(empty) == []


@pytest.mark.parametrize(
    ("fixture_name", "message"),
    [
        ("bok_ecos_schema_drift.json", "non-object"),
        ("bok_ecos_error.json", "error code INFO-200"),
    ],
)
def test_bok_ecos_schema_drift_and_source_errors_are_rejected(
    fixture_name: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _ecos_rows(_fixture_json(fixture_name))
