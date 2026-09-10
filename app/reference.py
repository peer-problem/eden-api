from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.domain.ids import stable_eden_id
from app.inventory import official_notice_targets, seed_source_inventories
from app.products.formulas import (
    CROWD_FORMULA_VERSION,
    INBOUND_FORMULA_VERSION,
    INTEREST_FORMULA_VERSION,
    RECOMMENDATION_FORMULA_VERSION,
    RISING_KEYWORD_FORMULA_VERSION,
)
from app.repositories.models import (
    Area,
    AreaSourceMap,
    Country,
    MarketCohort,
    MetricDefinition,
    Place,
    RefreshPolicy,
    SourceRegistry,
    SourceState,
)
from app.sources.catalog import CADENCE_SECONDS, EXPECTED_PUBLISH_LAG_SECONDS, SOURCES
from app.sources.kto_inbound import DEFAULT_COUNTRIES
from app.sources.plans import (
    DOCUMENTATION_VERIFIED_AT,
    KTO_TOURAPI_AREA_TO_MOIS_PREFIX,
    PUBLIC_DATA_REFRESH_SCOPES,
    public_data_refresh_scope,
    social_refresh_scope,
)
from app.sources.social import EXCLUDED_SOCIAL_SOURCE_IDS

MOIS_STYLE_AREA_SOURCES = {
    "SRC_KTO_RESOURCE_DEMAND",
    "SRC_KTO_REGIONAL_VISITORS",
    "SRC_KTO_DEMAND_INTENSITY",
    "SRC_KTO_DIVERSITY",
    "SRC_KTO_PLACE_HUB",
    "SRC_KTO_PLACE_RELATED",
    "SRC_KTO_VISITOR_FORECAST",
    "SRC_SEMAS_SHOPS",
}
TOURAPI_AREA_SOURCES = {"SRC_TOUR_KO", "SRC_TOUR_EN", "SRC_TOUR_JA", "SRC_TOUR_ZH_CN"}

MARKET_COHORT_V1 = (
    (1, "CN", "중국", "China", "zh-CN", "CNY", 1_374_273),
    (2, "JP", "일본", "Japan", "ja", "JPY", 939_975),
    (3, "TW", "대만", "Taiwan", "zh-TW", "TWD", 542_670),
    (4, "US", "미국", "United States", "en", "USD", 309_168),
    (5, "PH", "필리핀", "Philippines", "en", "PHP", 153_393),
)

COHORT_EVIDENCE = {
    "statistics_period": "2026-Q1",
    "status": "provisional",
    "source_id": "SRC_KTO_INBOUND_STATS",
    "source_url": "https://datalab.visitkorea.or.kr/visualize/getGridData.do",
    "query": {
        "qid": "TS_01_16_010",
        "BASE_YM1": "202601",
        "BASE_YM2": "202603",
        "srchAreaDate": "1",
        "natNm": "전체",
        "adminYn": "N",
        "tabDiv": "2",
    },
    "row_count": 41346,
    "verified_at": "2026-08-11T00:00:00+09:00",
    "official_release": "https://www.mcst.go.kr/site/s_notice/press/pressView.jsp?pSeq=22348",
    "method": "Sum V:인원수 by R:국적; fix the five largest ISO-addressable countries.",
}


def _upsert(session: Session, table, values: dict[str, Any], key_columns: list[str]) -> None:
    statement = insert(table).values(**values)
    update_values = {
        key: value for key, value in values.items() if key not in set(key_columns + ["created_at"])
    }
    session.execute(statement.on_duplicate_key_update(**update_values))


def _insert_once(session: Session, table, values: dict[str, Any], key_columns: list[str]) -> None:
    no_op = {key: getattr(table.c, key) for key in key_columns}
    session.execute(insert(table).values(**values).on_duplicate_key_update(**no_op))


def seed_reference_data(session: Session) -> None:
    now = datetime.now(UTC)
    active_areas = session.execute(
        select(
            Area.administrative_code,
            Area.eden_area_id,
            Area.center_lat,
            Area.center_lng,
        ).where(Area.active.is_(True), Area.administrative_code.is_not(None))
    ).all()
    active_area_codes = [row.administrative_code for row in active_areas]
    area_id_by_code = {row.administrative_code: row.eden_area_id for row in active_areas}
    place_centroids = {
        area_id: (float(lat), float(lng))
        for area_id, lat, lng in session.execute(
            select(Place.area_id, func.avg(Place.lat), func.avg(Place.lng))
            .where(Place.lat.is_not(None), Place.lng.is_not(None))
            .group_by(Place.area_id)
        )
        if lat is not None and lng is not None
    }
    area_locations_by_code = {
        row.administrative_code: (
            row.eden_area_id,
            float(row.center_lat) if row.center_lat is not None else centroid[0],
            float(row.center_lng) if row.center_lng is not None else centroid[1],
        )
        for row in active_areas
        if row.administrative_code is not None
        and (
            row.center_lat is not None
            and row.center_lng is not None
            or (centroid := place_centroids.get(row.eden_area_id)) is not None
        )
    }
    for source in SOURCES:
        interval, max_age = CADENCE_SECONDS[source.cadence_tier]
        expected_publish_lag = EXPECTED_PUBLISH_LAG_SECONDS[source.cadence_tier]
        refresh_scope: dict[str, Any] = public_data_refresh_scope(
            source.source_id,
            [code for code in active_area_codes if code is not None],
            area_id_by_code,
            area_locations_by_code,
        )
        if source.source_id == "SRC_KTO_INBOUND_STATS":
            refresh_scope = {"months": 2, "countries": DEFAULT_COUNTRIES}
        elif source.source_id == "SRC_BOK_ECOS":
            refresh_scope = {"months": 48, "fx_days": 14}
        elif social_scope := social_refresh_scope(source.source_id):
            refresh_scope = social_scope
        elif source.source_id == "SRC_EMBASSY_NOTICE":
            refresh_scope = {"targets": official_notice_targets()}
        elif source.source_id == "SRC_KETA":
            refresh_scope = {"limit": 20}
        elif source.source_id == "SRC_KTO_MARKET_TREND":
            refresh_scope = {
                "targets": [
                    {
                        "countries": list(DEFAULT_COUNTRIES),
                        "source_type": "tourism_board",
                        "source_scope": "korean",
                        "source_name": "한국관광공사 관광데이터랩",
                        "url": "https://datalab.visitkorea.or.kr/site/portal/ex/bbs/List.do?cbIdx=1132",
                        "languages": ["ko"],
                        "max_items": 10,
                        "alert_type": "market_trend",
                    }
                ]
            }
        values = {
            "source_id": source.source_id,
            "owner_name": source.owner_name,
            "base_url": source.base_url,
            "access_method": source.access_method,
            "auth_type": source.auth_type,
            "quota_policy": (
                {
                    "development_daily_requests": 1000,
                    "operation_limit": "source_specific_or_increase_by_approved_use_case",
                    "verification": "official_data_portal_specification",
                }
                if source.source_id in PUBLIC_DATA_REFRESH_SCOPES
                else {
                    "operation_limit": "source_specific_plan_or_approval",
                    "verification": "official_source_documentation_2026-08-11",
                }
            ),
            "supported_countries": list(source.supported_countries),
            "supported_languages": list(source.supported_languages),
            "expected_publish_lag_seconds": expected_publish_lag,
            "max_acceptable_age_seconds": max_age,
            "storage_mode": source.storage_mode,
            "identity_mode": source.identity_mode,
            "content_policy": source.content_policy,
            "status": "unavailable",
            "status_reason": "Initial smoke test has not succeeded.",
            "docs_url": source.docs_url,
            "documentation_checked_at": DOCUMENTATION_VERIFIED_AT,
            "smoke_tested_at": None,
            "evidence": {
                "cadence_tier": source.cadence_tier,
                "refresh_scope": refresh_scope,
                "operation_verification": (
                    "official_data_portal_specification_2026-08-11"
                    if source.source_id in PUBLIC_DATA_REFRESH_SCOPES
                    else "official_source_documentation_2026-08-11"
                ),
                "documented_access_method": source.access_method,
                "documented_auth_type": source.auth_type,
                "documentation_url": source.docs_url,
            },
            "enabled": source.source_id not in EXCLUDED_SOCIAL_SOURCE_IDS,
            "created_at": now,
            "updated_at": now,
        }
        source_update = {
            key: value
            for key, value in values.items()
            if key
            not in {
                "source_id",
                "status",
                "status_reason",
                "smoke_tested_at",
                "enabled",
                "created_at",
            }
        }
        if source.source_id in EXCLUDED_SOCIAL_SOURCE_IDS:
            source_update["enabled"] = False
        session.execute(
            insert(SourceRegistry).values(**values).on_duplicate_key_update(**source_update)
        )
        _upsert(
            session,
            RefreshPolicy.__table__,
            {
                "source_id": source.source_id,
                "interval_seconds": interval,
                "expected_publish_lag_seconds": expected_publish_lag,
                "max_acceptable_age_seconds": max_age,
                "retry_limit": 3,
                "jitter_seconds": min(300, interval // 20),
                "created_at": now,
                "updated_at": now,
            },
            ["source_id"],
        )
        _insert_once(
            session,
            SourceState.__table__,
            {
                "source_id": source.source_id,
                "scope_key": "global",
                "last_attempt_at": None,
                "last_success_at": None,
                "data_as_of": None,
                "consecutive_failures": 0,
                "status": "unavailable",
                "reason": "Initial smoke test has not succeeded.",
                "created_at": now,
                "updated_at": now,
            },
            ["source_id", "scope_key"],
        )

    # Source-code aliases are materialized only after the MOIS dimension is
    # present.  Re-running the idempotent seed after the MOIS import activates
    # the dependent collection scopes without creating another geography.
    areas = session.execute(
        select(Area.eden_area_id, Area.administrative_code, Area.level).where(
            Area.active.is_(True), Area.administrative_code.is_not(None)
        )
    ).all()
    for eden_area_id, administrative_code, level in areas:
        assert administrative_code is not None
        external_code = administrative_code[:2] if level == "sido" else administrative_code[:5]
        for source_id in MOIS_STYLE_AREA_SOURCES:
            _upsert(
                session,
                AreaSourceMap.__table__,
                {
                    "source_id": source_id,
                    "external_area_code": external_code,
                    "eden_area_id": eden_area_id,
                    "spatial_resolution": level,
                    "created_at": now,
                    "updated_at": now,
                },
                ["source_id", "external_area_code"],
            )
        if level == "sido":
            tour_code = next(
                (
                    code
                    for code, mois_prefix in KTO_TOURAPI_AREA_TO_MOIS_PREFIX.items()
                    if mois_prefix == administrative_code[:2]
                ),
                None,
            )
            if tour_code:
                for source_id in TOURAPI_AREA_SOURCES:
                    _upsert(
                        session,
                        AreaSourceMap.__table__,
                        {
                            "source_id": source_id,
                            "external_area_code": tour_code,
                            "eden_area_id": eden_area_id,
                            "spatial_resolution": "sido",
                            "created_at": now,
                            "updated_at": now,
                        },
                        ["source_id", "external_area_code"],
                    )
        elif level == "sigungu":
            for source_id in TOURAPI_AREA_SOURCES:
                _upsert(
                    session,
                    AreaSourceMap.__table__,
                    {
                        "source_id": source_id,
                        "external_area_code": administrative_code[:5],
                        "eden_area_id": eden_area_id,
                        "spatial_resolution": "sigungu",
                        "created_at": now,
                        "updated_at": now,
                    },
                    ["source_id", "external_area_code"],
                )

    for rank, code, name_ko, name_en, language, currency, count in MARKET_COHORT_V1:
        country_id = stable_eden_id("country", "ISO3166", code)
        _upsert(
            session,
            Country.__table__,
            {
                "eden_country_id": country_id,
                "iso_alpha2": code,
                "name_ko": name_ko,
                "name_en": name_en,
                "default_language": language,
                "default_currency": currency,
                "created_at": now,
                "updated_at": now,
            },
            ["eden_country_id"],
        )
        _insert_once(
            session,
            MarketCohort.__table__,
            {
                "version": "market_cohort_v1",
                "statistics_period": "2026-Q1",
                "rank": rank,
                "country_id": country_id,
                "visitor_count": count,
                "fixed_at": now,
                "active": True,
                "evidence": COHORT_EVIDENCE,
                "created_at": now,
                "updated_at": now,
            },
            ["version", "rank"],
        )

    metrics = (
        (
            "interest_index",
            "index_0_100",
            INTEREST_FORMULA_VERSION,
            {"method": "normalize_within_source_then_available_weighted_mean"},
            "Observations matching keyword, country, area and time bucket",
            "Requested 7d, 30d or 90d source-relative window",
            "Previous equal-length period",
        ),
        (
            "inbound_score",
            "index_0_100",
            INBOUND_FORMULA_VERSION,
            {"weights": {"visitors": 0.4, "flights": 0.25, "fx": 0.15, "social": 0.2}},
            "Active market_cohort_v1 countries",
            "Requested 3m, 6m, 12m or 24m window",
            "Previous equal-length period",
        ),
        (
            "rising_keywords",
            "index_0_100",
            RISING_KEYWORD_FORMULA_VERSION,
            {
                "method": "per_source_growth_then_available_mean",
                "minimum_observations_per_window": 2,
            },
            "Keywords with both current and previous observations in the selected sources",
            "Requested 7d, 30d or 90d window",
            "Previous equal-length window",
        ),
        (
            "crowd_index",
            "percentile_0_100",
            CROWD_FORMULA_VERSION,
            {"method": "regional_seasonal_percentile"},
            "Same EDEN area and seasonal reference population",
            "Versioned seasonal reference window",
            "Historical observations in reference population",
        ),
        (
            "recommendation_score",
            "index_0_100",
            RECOMMENDATION_FORMULA_VERSION,
            {
                "weights": {
                    "theme_match": 0.35,
                    "demand": 0.2,
                    "market_affinity": 0.15,
                    "budget_fit": 0.15,
                    "crowd_fit": 0.15,
                }
            },
            "Eligible places after deterministic constraints",
            "Published recommendation feature snapshot",
            "Same snapshot and identical request",
        ),
    )
    for metric_id, unit, version, formula, population, window, basis in metrics:
        _upsert(
            session,
            MetricDefinition.__table__,
            {
                "metric_id": metric_id,
                "unit": unit,
                "formula_version": version,
                "formula": formula,
                "population": population,
                "normalization_window": window,
                "comparison_basis": basis,
                "created_at": now,
                "updated_at": now,
            },
            ["metric_id"],
        )

    seed_source_inventories(session, now)
