from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.sources.plans import (
    KTO_ADMINISTRATIVE_AREA_CODES,
    KTO_RELATED_PLACES_PER_RUN,
    PUBLIC_DATA_REFRESH_SCOPES,
    kto_related_place_operations,
    kto_sigungu_operations,
    social_refresh_scope,
)
from app.sources.public_data import _rotating_operation_batch


def test_regional_visitor_scope_stays_within_recent_bounded_window() -> None:
    operations = PUBLIC_DATA_REFRESH_SCOPES["SRC_KTO_REGIONAL_VISITORS"]["operations"]

    assert {operation["operation"] for operation in operations} == {
        "metcoRegnVisitrDDList",
        "locgoRegnVisitrDDList",
    }
    assert all(operation["params"]["startYmd"] == "$today_minus_7d" for operation in operations)
    assert all(operation["max_pages"] == 20 for operation in operations)


def test_source_watermarks_use_published_response_fields() -> None:
    festival = PUBLIC_DATA_REFRESH_SCOPES["SRC_FESTIVAL"]["operations"][0]
    assert festival["watermark"] == {
        "response_field": "referenceDate",
        "format": "%Y-%m-%d",
    }

    forecast = kto_sigungu_operations(
        "SRC_KTO_VISITOR_FORECAST", ["5113000000"]
    )[0]
    assert forecast["watermark"] == {
        "response_field": "baseYmd",
        "format": "%Y%m%d",
    }


def test_related_place_scope_requires_a_real_place_keyword() -> None:
    assert kto_sigungu_operations("SRC_KTO_PLACE_RELATED", ["5113000000"]) == []
    assert kto_related_place_operations(
        [
            ("place-valid", "5113000000", " 뮤지엄산 "),
            ("place-province", "5100000000", "invalid province"),
            ("place-blank", "5113000000", "  "),
        ]
    ) == [
        {
            "operation": "searchKeyword1",
            "external_key": (
                "searchKeyword1:place=place-valid:month=$month_minus_2"
            ),
            "params": {
                "MobileOS": "ETC",
                "MobileApp": "EDEN",
                "baseYm": "$month_minus_2",
                "areaCd": "51",
                "signguCd": "51130",
                "keyword": "뮤지엄산",
            },
            "watermark": {"param": "baseYm", "format": "%Y%m"},
            "max_pages": 1,
        }
    ]


def test_related_place_scope_is_bounded_per_run() -> None:
    places = [
        (f"place-{index}", "5113000000", f"keyword-{index}")
        for index in range(KTO_RELATED_PLACES_PER_RUN + 1)
    ]

    assert len(kto_related_place_operations(places)) == KTO_RELATED_PLACES_PER_RUN


def test_social_scopes_exclude_user_rejected_sources() -> None:
    for source_id in (
        "SRC_X",
        "SRC_TIKTOK",
        "SRC_WEIBO",
        "SRC_DOUYIN",
        "SRC_XIAOHONGSHU",
        "SRC_LINE",
    ):
        assert social_refresh_scope(source_id) == {}

    for source_id in (
        "SRC_NAVER_TREND",
        "SRC_YOUTUBE",
        "SRC_INSTAGRAM",
        "SRC_FACEBOOK",
        "SRC_REDDIT",
    ):
        assert social_refresh_scope(source_id)["targets"]


@pytest.mark.parametrize(
    ("source_id", "operations_per_month"),
    [
        ("SRC_KTO_RESOURCE_DEMAND", 34),
        ("SRC_KTO_DEMAND_INTENSITY", 34),
        ("SRC_KTO_DIVERSITY", 51),
    ],
)
def test_monthly_statistics_rotate_complete_national_month_cohorts(
    source_id: str,
    operations_per_month: int,
) -> None:
    scope = PUBLIC_DATA_REFRESH_SCOPES[source_id]
    operations = scope["operations"]
    base = datetime(2026, 9, 10, tzinfo=UTC)
    selected_months: set[str] = set()

    for offset in range(3):
        selected, notice, error = _rotating_operation_batch(
            operations,
            scope,
            base + timedelta(days=offset),
            60,
        )
        assert error is None
        assert notice and "rotating_operation_group" in notice
        assert len(selected) == operations_per_month
        months = {operation["params"]["baseYm"] for operation in selected}
        assert len(months) == 1
        selected_months.update(months)
        assert {operation["params"]["areaCd"] for operation in selected} == set(
            KTO_ADMINISTRATIVE_AREA_CODES
        )

    assert selected_months == {"$month_minus_2", "$month_minus_3", "$month_minus_4"}


def test_monthly_statistical_group_fails_closed_when_request_budget_is_too_small() -> None:
    scope = PUBLIC_DATA_REFRESH_SCOPES["SRC_KTO_DIVERSITY"]

    selected, notice, error = _rotating_operation_batch(
        scope["operations"],
        scope,
        datetime(2026, 9, 10, tzinfo=UTC),
        50,
    )

    assert selected == []
    assert notice and "rotating_operation_group" in notice
    assert error == "source_run:rotation_group_request_limit_exceeded"
