from __future__ import annotations

from app.domain.enums import Availability
from app.products.trend_views import _sum_optional, build_trend_view


def test_optional_sum_preserves_all_missing_but_not_numeric_zero() -> None:
    assert _sum_optional([None, None]) is None
    assert _sum_optional([None, 0]) == 0


def test_trend_uses_only_explicitly_selected_youtube_source() -> None:
    product = {
        "observations": [
            {
                "source_id": "SRC_YOUTUBE",
                "keyword": "서울",
                "country": "KR",
                "bucket_start": "2026-08-28T00:00:00+00:00",
                "view_count": 10,
            },
            {
                "source_id": "SRC_YOUTUBE",
                "keyword": "서울",
                "country": "KR",
                "bucket_start": "2026-08-29T00:00:00+00:00",
                "view_count": 30,
            },
            {
                "source_id": "SRC_NAVER_TREND",
                "keyword": "서울",
                "country": "KR",
                "bucket_start": "2026-08-28T00:00:00+00:00",
                "search_ratio": 20.0,
            },
            {
                "source_id": "SRC_NAVER_TREND",
                "keyword": "서울",
                "country": "KR",
                "bucket_start": "2026-08-29T00:00:00+00:00",
                "search_ratio": 40.0,
            },
        ]
    }
    scope = {
        "keyword": "서울",
        "country": "all",
        "area_code": None,
        "social_sources": ["youtube"],
        "period": "7d",
        "time_unit": "day",
    }

    data, availability, reason = build_trend_view(product, scope)

    assert data is not None
    assert data["interest_index"] == 50.0
    assert [point["interest_index"] for point in data["series"]] == [0.0, 100.0]
    assert set(data["source_availability"]) == {"SRC_YOUTUBE"}
    youtube = next(row for row in data["source_metrics"] if row["source_id"] == "SRC_YOUTUBE")
    assert youtube["reason"] and "시청자 거주 국가" in youtube["reason"]
    assert availability == Availability.AVAILABLE
    assert reason is None


def test_rising_keywords_use_equal_windows_and_minimum_observations() -> None:
    observations = [
        {
            "source_id": "SRC_YOUTUBE",
            "keyword": "서울",
            "country": "KR",
            "bucket_start": "2026-08-28T00:00:00+00:00",
            "view_count": 10,
        },
        {
            "source_id": "SRC_YOUTUBE",
            "keyword": "서울",
            "country": "KR",
            "bucket_start": "2026-08-29T00:00:00+00:00",
            "view_count": 20,
        },
        *[
            {
                "source_id": "SRC_YOUTUBE",
                "keyword": "부산",
                "country": "KR",
                "bucket_start": timestamp,
                "view_count": views,
            }
            for timestamp, views in (
                ("2026-08-16T00:00:00+00:00", 10),
                ("2026-08-22T00:00:00+00:00", 10),
                ("2026-08-23T00:00:00+00:00", 20),
                ("2026-08-29T00:00:00+00:00", 30),
            )
        ],
        {
            "source_id": "SRC_YOUTUBE",
            "keyword": "표본부족",
            "country": "KR",
            "bucket_start": "2026-08-29T00:00:00+00:00",
            "view_count": 100,
        },
    ]
    scope = {
        "keyword": "서울",
        "country": "all",
        "area_code": None,
        "social_sources": ["youtube"],
        "period": "7d",
        "time_unit": "day",
        "limit": 10,
    }

    data, _availability, _reason = build_trend_view(
        {"observations": observations},
        scope,
    )

    assert data is not None
    assert data["rising_keywords"] == [{"keyword": "부산", "score": 100.0}]


def test_trend_view_never_sums_raw_mentions_across_platforms() -> None:
    product = {
        "observations": [
            {
                "source_id": source_id,
                "keyword": "서울",
                "country": "KR",
                "bucket_start": "2026-08-29T00:00:00+00:00",
                "post_count": count,
            }
            for source_id, count in (
                ("SRC_INSTAGRAM", 10),
                ("SRC_FACEBOOK", 20),
            )
        ]
    }
    scope = {
        "keyword": "서울",
        "country": "all",
        "area_code": None,
        "social_sources": ["instagram", "facebook"],
        "period": "7d",
        "time_unit": "day",
    }

    data, _availability, _reason = build_trend_view(product, scope)

    assert data is not None
    assert data["series"][0]["sns_mentions"] is None
    assert {row["source_id"]: row["posts"] for row in data["source_metrics"]} == {
        "SRC_INSTAGRAM": 10,
        "SRC_FACEBOOK": 20,
    }


def test_trend_view_filters_excluded_observations_from_stale_snapshots() -> None:
    product = {
        "observations": [
            {
                "source_id": "SRC_YOUTUBE",
                "keyword": "서울",
                "country": "KR",
                "bucket_start": "2026-08-29T00:00:00+00:00",
                "view_count": 30,
            },
            {
                "source_id": "SRC_TIKTOK",
                "keyword": "서울",
                "country": "KR",
                "bucket_start": "2026-08-30T00:00:00+00:00",
                "post_count": 999,
            },
        ]
    }
    scope = {
        "keyword": "서울",
        "country": "all",
        "area_code": None,
        "social_sources": None,
        "period": "7d",
        "time_unit": "day",
    }

    data, _availability, _reason = build_trend_view(product, scope)

    assert data is not None
    assert data["sources"] == ["SRC_YOUTUBE"]
    assert data["series"][0]["youtube_views"] == 30
    assert all(row["source_id"] != "SRC_TIKTOK" for row in data["source_metrics"])


def test_official_resource_demand_answers_korean_keywords_and_area_filters() -> None:
    product = {
        "observations": [
            {
                "source_id": "SRC_KTO_RESOURCE_DEMAND",
                "keyword": "경복궁",
                "country": None,
                "area_id": "eden_area_seoul",
                "bucket_start": "2026-06-01T00:00:00+00:00",
                "source_score": 60.0,
            },
            {
                "source_id": "SRC_KTO_RESOURCE_DEMAND",
                "keyword": "경복궁",
                "country": None,
                "area_id": "eden_area_seoul",
                "bucket_start": "2026-07-01T00:00:00+00:00",
                "source_score": 80.0,
            },
            {
                "source_id": "SRC_KTO_RESOURCE_DEMAND",
                "keyword": "해운대",
                "country": None,
                "area_id": "eden_area_busan",
                "bucket_start": "2026-07-01T00:00:00+00:00",
                "source_score": 90.0,
            },
        ]
    }
    scope = {
        "keyword": "경복궁",
        "country": "all",
        "area_code": "eden_area_seoul",
        "social_sources": None,
        "period": "90d",
        "time_unit": "month",
    }

    data, availability, reason = build_trend_view(product, scope)

    assert data is not None
    assert reason is None and availability == Availability.AVAILABLE
    assert data["sources"] == ["SRC_KTO_RESOURCE_DEMAND"]
    assert data["interest_index"] == 70.0
    assert [point["interest_index"] for point in data["series"]] == [60.0, 80.0]
    assert set(data["source_availability"]) == {"SRC_KTO_RESOURCE_DEMAND"}

    # an explicit social request is answered from that sample only
    explicit, explicit_availability, explicit_reason = build_trend_view(
        product, {**scope, "social_sources": ["youtube"]}
    )
    assert explicit is None and explicit_availability == Availability.UNAVAILABLE
    assert explicit_reason == "요청 범위와 일치하는 social signal이 없습니다."


def test_naver_search_ratio_answers_korean_keywords_on_default_requests() -> None:
    product = {
        "observations": [
            {
                "source_id": "SRC_NAVER_TREND",
                "keyword": "제주 여행",
                "country": None,
                "bucket_start": "2026-09-01T00:00:00+00:00",
                "search_ratio": 10.6,
            },
            {
                "source_id": "SRC_NAVER_TREND",
                "keyword": "제주 여행",
                "country": None,
                "bucket_start": "2026-09-02T00:00:00+00:00",
                "search_ratio": 9.6,
            },
        ]
    }
    scope = {
        "keyword": "제주 여행",
        "country": "all",
        "area_code": None,
        "social_sources": None,
        "period": "7d",
        "time_unit": "day",
    }

    data, availability, reason = build_trend_view(product, scope)

    assert data is not None and availability == Availability.AVAILABLE and reason is None
    assert data["sources"] == ["SRC_NAVER_TREND"]
    assert [point["search_ratio"] for point in data["series"]] == [10.6, 9.6]
    assert data["source_metrics"][0]["search_ratio"] == 10.1
    assert data["interest_index"] == 10.1

    other_area = build_trend_view(product, {**scope, "area_code": "eden_area_busan"})
    assert other_area[0] is None and other_area[1] == Availability.UNAVAILABLE


def test_youtube_only_responses_do_not_list_the_official_index_without_observations() -> None:
    product = {
        "observations": [
            {
                "source_id": "SRC_YOUTUBE",
                "keyword": "Seoul travel",
                "country": "US",
                "bucket_start": "2026-08-29T00:00:00+00:00",
                "view_count": 30,
            }
        ]
    }
    scope = {
        "keyword": "Seoul travel",
        "country": "US",
        "area_code": None,
        "social_sources": ["youtube"],
        "period": "7d",
        "time_unit": "day",
    }

    data, _, _ = build_trend_view(product, scope)

    assert data is not None
    assert set(data["source_availability"]) == {"SRC_YOUTUBE"}
