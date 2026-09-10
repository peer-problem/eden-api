from __future__ import annotations

from app.sources.plans import (
    KTO_RELATED_PLACES_PER_RUN,
    PUBLIC_DATA_REFRESH_SCOPES,
    kto_related_place_operations,
    kto_sigungu_operations,
    social_refresh_scope,
)


def test_regional_visitor_scope_can_reach_declared_local_result_size() -> None:
    operations = PUBLIC_DATA_REFRESH_SCOPES["SRC_KTO_REGIONAL_VISITORS"]["operations"]

    assert {operation["operation"] for operation in operations} == {
        "metcoRegnVisitrDDList",
        "locgoRegnVisitrDDList",
    }
    assert all(operation["max_pages"] == 400 for operation in operations)


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
            "max_pages": 10,
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
