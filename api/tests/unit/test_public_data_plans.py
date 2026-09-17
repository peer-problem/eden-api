from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.sources.plans import (
    KMA_OPERATIONS_PER_RUN,
    KTO_ADMINISTRATIVE_AREA_CODES,
    KTO_RELATED_PLACES_PER_RUN,
    PUBLIC_DATA_REFRESH_SCOPES,
    kto_related_place_operations,
    kto_sigungu_operations,
    public_data_refresh_scope,
    social_refresh_scope,
)
from app.sources.public_data import _rotating_operation_batch, resolve_dynamic_parameter


def test_regional_visitor_scope_stays_within_recent_bounded_window() -> None:
    operations = PUBLIC_DATA_REFRESH_SCOPES["SRC_KTO_REGIONAL_VISITORS"]["operations"]

    assert {operation["operation"] for operation in operations} == {
        "metcoRegnVisitrDDList",
    }
    assert all(operation["params"]["startYmd"] == "$today_minus_39d" for operation in operations)
    assert all(operation["params"]["endYmd"] == "$today_minus_30d" for operation in operations)
    assert all(operation["max_pages"] == 20 for operation in operations)


def test_dynamic_day_offsets_support_lag_aware_windows() -> None:
    now = datetime(2026, 9, 10, 16, 0, tzinfo=UTC)

    assert resolve_dynamic_parameter("$today_minus_39d", now) == "20260803"
    assert resolve_dynamic_parameter("$today_minus_30d", now) == "20260812"
    assert resolve_dynamic_parameter("$today", now) == "20260911"


def test_kma_scope_rotates_complete_province_forecasts_within_record_budget() -> None:
    codes = [f"{prefix}00000000" for prefix in KTO_ADMINISTRATIVE_AREA_CODES]
    area_ids = {code: f"area-{code[:2]}" for code in codes}

    scope = public_data_refresh_scope("SRC_KMA_FORECAST", codes, area_ids)

    assert len(scope["operations"]) == len(KTO_ADMINISTRATIVE_AREA_CODES)
    assert scope["max_operations_per_run"] == KMA_OPERATIONS_PER_RUN == 5
    assert all(
        1052 <= operation["params"]["numOfRows"] <= 10_000 // KMA_OPERATIONS_PER_RUN
        and operation["max_pages"] == 1
        for operation in scope["operations"]
    )


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


def test_semas_operations_carry_the_dataset_reference_month_watermark() -> None:
    from app.sources.plans import semas_place_operations

    operation = semas_place_operations([("eden_place_test", 37.5, 127.0)])[0]

    assert operation["watermark"] == {"response_field": "stdrYm", "format": "%Y%m"}


def test_scheduler_batches_fit_the_run_budgets() -> None:
    from app.sources.plans import (
        PUBLIC_DATA_REQUEST_HEADROOM,
        TOUR_KO_AREAS_PER_RUN,
        rotating_batch,
        scheduler_batch_size,
    )

    # Five 1,000-row province catalogs exceeded the 2 MiB run budget; three fit.
    assert scheduler_batch_size("SRC_TOUR_KO") == TOUR_KO_AREAS_PER_RUN == 3
    assert scheduler_batch_size("SRC_KMA_FORECAST") == KMA_OPERATIONS_PER_RUN == 5
    assert PUBLIC_DATA_REQUEST_HEADROOM >= 1
    provinces = [f"area={code}" for code in range(17)]
    assert rotating_batch(provinces, 15, 3) == ["area=15", "area=16", "area=0"]
    assert rotating_batch(provinces, 0, 5) == provinces[:5]
    assert rotating_batch(provinces[:2], 1, 5) == ["area=1", "area=0"]
    assert rotating_batch([], 3, 5) == []


def test_batched_sources_reserve_request_headroom_for_retries() -> None:
    from app.sources.plans import PUBLIC_DATA_REQUEST_HEADROOM, TOUR_KO_AREAS_PER_RUN
    from app.sources.registry import _batched_request_budget

    configured = 5
    assert (
        _batched_request_budget("SRC_KMA_FORECAST", configured)
        == KMA_OPERATIONS_PER_RUN + PUBLIC_DATA_REQUEST_HEADROOM
    )
    from app.sources.plans import TOUR_KO_DETAILS_PER_RUN

    assert (
        _batched_request_budget("SRC_TOUR_KO", configured)
        == max(TOUR_KO_AREAS_PER_RUN * 2, TOUR_KO_DETAILS_PER_RUN) + PUBLIC_DATA_REQUEST_HEADROOM
    )
    assert _batched_request_budget("SRC_KMA_FORECAST", 40) == 40
    assert _batched_request_budget("SRC_FESTIVAL", configured) == configured
    from app.sources.plans import SEMAS_PLACES_PER_RUN, semas_place_operations

    assert (
        _batched_request_budget("SRC_SEMAS_SHOPS", configured)
        == SEMAS_PLACES_PER_RUN + PUBLIC_DATA_REQUEST_HEADROOM
    )
    places = [(f"place-{index}", 37.5, 127.0) for index in range(SEMAS_PLACES_PER_RUN + 3)]
    assert len(semas_place_operations(places)) == SEMAS_PLACES_PER_RUN


def test_embassy_notice_budget_covers_the_waiting_room_and_five_boards() -> None:
    from app.config import Settings
    from app.sources.registry import EMBASSY_NOTICE_REQUEST_BUDGET, build_adapter

    settings = Settings(
        ENVIRONMENT="test",
        DB_HOST="database.invalid",
        DB_USER="test-only",
        DB_PASSWORD="test-only",  # noqa: S106 - offline adapter construction
        SCHEDULER_ENABLED=False,
        SOURCE_MAX_REQUESTS_PER_RUN=5,
    )
    adapter = build_adapter("SRC_EMBASSY_NOTICE", settings, {"targets": []})
    try:
        # queue pass (redirect chain + polls + reload) plus five boards with two notices each
        assert adapter.client.max_requests == EMBASSY_NOTICE_REQUEST_BUDGET >= 13 + 5 * 3
    finally:
        adapter.client.close()


def test_tour_detail_operations_are_bounded_and_marked_as_details() -> None:
    from app.sources.plans import TOUR_KO_DETAILS_PER_RUN, tour_detail_operations

    content_ids = [str(index) for index in range(TOUR_KO_DETAILS_PER_RUN + 5)]
    operations = tour_detail_operations(content_ids)

    assert len(operations) == TOUR_KO_DETAILS_PER_RUN
    first = operations[0]
    assert first["operation"] == "detailCommon2"
    assert first["external_key"] == "detailCommon2:content=0"
    assert first["params"] == {"MobileOS": "ETC", "MobileApp": "EDEN", "contentId": "0"}
    assert first["watermark"] == {"response_field": "modifiedtime", "format": "%Y%m%d%H%M%S"}
    assert first["detail"] is True and first["paginate"] is False and first["max_pages"] == 1


def test_language_catalogs_share_the_tourapi_batch_and_budget() -> None:
    from app.sources.plans import scheduler_batch_size
    from app.sources.registry import _batched_request_budget

    for source_id in ("SRC_TOUR_EN", "SRC_TOUR_JA", "SRC_TOUR_ZH_CN"):
        assert scheduler_batch_size(source_id) == scheduler_batch_size("SRC_TOUR_KO")
        assert _batched_request_budget(source_id, 5) == _batched_request_budget("SRC_TOUR_KO", 5)


def test_airport_country_scope_collects_flights_and_passengers_for_two_months() -> None:
    scope = public_data_refresh_scope("SRC_AIRPORT_COUNTRY")
    operations = scope["operations"]

    assert [op["operation"] for op in operations] == [
        "getTotalNumberOfFlight",
        "getTotalNumberOfFlight",
        "getTotalNumberOfPassenger",
        "getTotalNumberOfPassenger",
    ]
    assert len({op["external_key"] for op in operations}) == 4
    passenger = operations[2]
    assert passenger["external_key"] == "airport-country-passengers:month=$month_minus_1"
    assert passenger["params"] == {
        "from_month": "$month_minus_1",
        "to_month": "$month_minus_1",
    }
    assert passenger["watermark"] == {"param": "to_month", "format": "%Y%m"}
    assert len(operations) <= scope["max_operations_per_run"]


def test_naver_scope_rotates_korean_province_keywords_five_per_run() -> None:
    from app.sources.plans import NAVER_KEYWORDS, NAVER_KEYWORDS_PER_RUN, naver_targets

    assert NAVER_KEYWORDS_PER_RUN == 5
    assert all(keyword.endswith(" 여행") for keyword in NAVER_KEYWORDS)
    seen: list[str] = []
    for day in range(4):
        batch = naver_targets(day)
        assert 1 <= len(batch) <= NAVER_KEYWORDS_PER_RUN
        assert all(target["country"] == "KR" for target in batch)
        seen.extend(target["keyword"] for target in batch)
    assert seen == list(NAVER_KEYWORDS)
    assert naver_targets(4) == naver_targets(0)
    scope = social_refresh_scope("SRC_NAVER_TREND")
    assert scope["lookback_days"] == 90
    assert len(scope["targets"]) <= NAVER_KEYWORDS_PER_RUN
