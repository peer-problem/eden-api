from __future__ import annotations

from typing import Any

from app.repositories.models import Base
from app.sources.catalog import SOURCES

# Explicitly approved projections. Never expose newly added columns implicitly.
TABLES = {
    "area": (
        "지역",
        "기준 정보",
        "eden_area_id administrative_code name_ko name_en parent_area_id level active",
    ),
    "country": (
        "국가",
        "기준 정보",
        "eden_country_id iso_alpha2 name_ko name_en default_language default_currency",
    ),
    "place": (
        "장소",
        "기준 정보",
        "eden_place_id area_id category lat lng merge_status canonical_place_id",
    ),
    "place_localization": (
        "장소 번역",
        "기준 정보",
        "id eden_place_id language title address is_fallback",
    ),
    "area_source_map": (
        "지역 식별자 연결",
        "기준 정보",
        "id source_id external_area_code eden_area_id spatial_resolution",
    ),
    "place_source_map": (
        "장소 식별자 연결",
        "기준 정보",
        "id source_id external_content_id eden_place_id",
    ),
    "place_relation": (
        "장소 관계",
        "정규화",
        "relation_id from_place_id to_place_id relation_type rank score source_id observed_at "
        "availability",
    ),
    "source_registry": (
        "데이터 출처",
        "수집",
        "source_id owner_name access_method storage_mode status status_reason enabled docs_url",
    ),
    "source_state": (
        "출처별 수집 상태",
        "수집",
        "id source_id scope_key last_attempt_at last_success_at data_as_of "
        "consecutive_failures status",
    ),
    "refresh_policy": (
        "수집 주기",
        "수집",
        "source_id interval_seconds expected_publish_lag_seconds max_acceptable_age_seconds "
        "retry_limit",
    ),
    "ingestion_run": (
        "수집 실행 이력",
        "수집",
        "run_id job_id source_id status started_at finished_at raw_count normalized_count",
    ),
    "raw_record": (
        "원본 레코드 메타데이터",
        "수집",
        "raw_record_id source_id run_id external_key observed_at source_updated_at "
        "ingested_at content_type content_hash tombstone",
    ),
    "regional_visit_observation": (
        "지역 방문 관측",
        "정규화",
        "observation_id area_id source_id period_start subject_type subject_key visitor_type "
        "grain visitor_count concentration_rate availability",
    ),
    "regional_demand_observation": (
        "관광 수요 관측",
        "정규화",
        "observation_id area_id source_id period_start stay_index spend_index lodging_index "
        "avg_stay_nights availability",
    ),
    "regional_diversity_observation": (
        "관광 다양성 관측",
        "정규화",
        "observation_id area_id source_id period_start age_index nationality_index availability",
    ),
    "forecast_input": (
        "예측 입력",
        "정규화",
        "input_id area_id place_id forecast_date source_id source_forecast weather festivals "
        "holiday availability",
    ),
    "social_observation": (
        "관심도 관측",
        "정규화",
        "observation_id raw_record_id source_id keyword country_id area_id bucket_start "
        "post_count view_count availability",
    ),
    "inbound_visitor_observation": (
        "방한 방문 관측",
        "정규화",
        "observation_id country_id source_id period_start visitor_count availability",
    ),
    "flight_observation": (
        "항공 관측",
        "정규화",
        "observation_id country_id source_id period_start grain arriving_flights passengers "
        "availability",
    ),
    "fx_observation": (
        "환율 관측",
        "정규화",
        "observation_id currency source_id rate_date krw_rate availability",
    ),
    "tourism_balance_observation": (
        "관광수지 관측",
        "정규화",
        "observation_id source_id period_start receipt_usd expenditure_usd balance_usd "
        "availability",
    ),
    "provenance_edge": (
        "레코드 근거 연결",
        "처리·게시",
        "provenance_id output_type output_id raw_record_id formula_version created_at",
    ),
    "product_refresh_request": (
        "데이터 제품 갱신 이력",
        "처리·게시",
        "family status requested_at claimed_at next_attempt_at completed_at attempt_count",
    ),
    "read_model_snapshot": (
        "게시 스냅샷",
        "처리·게시",
        "snapshot_id endpoint snapshot_version payload_id observed_at source_updated_at "
        "ingested_at calculated_at as_of availability state",
    ),
    "read_model_head": (
        "현재 게시 버전",
        "처리·게시",
        "endpoint lookup_key_hash snapshot_id updated_at",
    ),
    "read_model_payload": (
        "게시 데이터 저장 정보",
        "처리·게시",
        "payload_id encoding uncompressed_bytes compressed_bytes created_at",
    ),
}

# Explorer-only descriptions of external response fields. These do not invent
# database relationships: they document the fields read by the normalizers so
# the graph can terminate lineage lines at the relevant table columns.
SOURCE_GRAPH_DETAILS = {
    "SRC_KTO_REGIONAL_VISITORS": {
        "label": "지역별 방문자 수",
        "operation": "metcoRegnVisitrDDList",
        "fields": (
            {
                "name": "baseYmd",
                "label": "방문 기준일",
                "target_table": "regional_visit_observation",
                "target_column": "period_start",
            },
            {
                "name": "areaCd / signguCd",
                "label": "지역 코드",
                "target_table": "regional_visit_observation",
                "target_column": "area_id",
            },
            {
                "name": "touDivNm",
                "label": "방문자 구분",
                "target_table": "regional_visit_observation",
                "target_column": "visitor_type",
            },
            {
                "name": "touNum",
                "label": "방문자 수",
                "target_table": "regional_visit_observation",
                "target_column": "visitor_count",
            },
        ),
    },
    "SRC_KTO_DEMAND_INTENSITY": {
        "label": "지역별 관광 수요 강도",
        "operation": "체류 강도 · 소비 강도",
        "fields": (
            {
                "name": "baseYm",
                "label": "기준월",
                "target_table": "regional_demand_observation",
                "target_column": "period_start",
            },
            {
                "name": "areaCd",
                "label": "지역 코드",
                "target_table": "regional_demand_observation",
                "target_column": "area_id",
            },
            {
                "name": "tarSjrnDsIxVal",
                "label": "관광 체류 강도",
                "target_table": "regional_demand_observation",
                "target_column": "stay_index",
            },
            {
                "name": "tarExpDsIxVal",
                "label": "관광 소비 강도",
                "target_table": "regional_demand_observation",
                "target_column": "spend_index",
            },
        ),
    },
    "SRC_KTO_DIVERSITY": {
        "label": "지역별 관광 다양성",
        "operation": "관광객 · 관광 소비 · 국제적 다양성",
        "fields": (
            {
                "name": "baseYm",
                "label": "기준월",
                "target_table": "regional_diversity_observation",
                "target_column": "period_start",
            },
            {
                "name": "areaCd",
                "label": "지역 코드",
                "target_table": "regional_diversity_observation",
                "target_column": "area_id",
            },
            {
                "name": "touDivIxVal",
                "label": "관광객 다양성",
                "raw_only": True,
            },
            {
                "name": "expDivIxVal",
                "label": "관광 소비 다양성",
                "raw_only": True,
            },
            {
                "name": "intlDivIxVal",
                "label": "국제적 다양성",
                "target_table": "regional_diversity_observation",
                "target_column": "nationality_index",
            },
        ),
    },
    "SRC_TOUR_KO": {"label": "관광지 정보 · 한국어", "operation": "areaBasedList2"},
    "SRC_TOUR_EN": {"label": "관광지 정보 · 영어", "operation": "areaBasedList2"},
    "SRC_TOUR_JA": {"label": "관광지 정보 · 일본어", "operation": "areaBasedList2"},
    "SRC_TOUR_ZH_CN": {"label": "관광지 정보 · 중국어", "operation": "areaBasedList2"},
    "SRC_KTO_RESOURCE_DEMAND": {"label": "관광 자원 수요", "operation": "서비스 · 문화자원 수요"},
    "SRC_KTO_PLACE_HUB": {"label": "관광지 기본 정보", "operation": "areaBasedList1"},
    "SRC_KTO_PLACE_RELATED": {"label": "연관 관광지", "operation": "searchKeyword1"},
    "SRC_KTO_VISITOR_FORECAST": {"label": "방문자 예측", "operation": "tatsCnctrRatedList"},
    "SRC_KTO_INBOUND_STATS": {"label": "국가별 방한객 통계", "operation": "국가·월별 통계"},
    "SRC_KTO_MARKET_TREND": {"label": "해외시장 동향", "operation": "관광데이터랩 공지"},
}

# These routes are intentionally explicit. Foreign keys describe storage, but
# they do not explain which external service was called or how several inputs
# become one published product.
FLOW_STEPS = (
    {
        "id": "normalize_regional_visitors",
        "label": "지역 방문 관측 정규화",
        "kind": "transform",
        "sources": ("SRC_KTO_REGIONAL_VISITORS",),
        "inputs": ("raw_record", "area_source_map"),
        "outputs": ("regional_visit_observation",),
        "keys": "외부 지역 코드 → eden_area_id · period_start · visitor_type",
        "detail": "지역 코드를 EDEN 지역으로 대응시키고 방문 수와 집중도를 기간 단위로 집계합니다.",
        "code_ref": "app.normalization.public_data.normalize_regional_visitors_run",
    },
    {
        "id": "normalize_tourism_admission",
        "label": "관광지 입장객 정규화",
        "kind": "transform",
        "sources": ("SRC_TOURISM_ADMISSION",),
        "inputs": ("raw_record", "area"),
        "outputs": ("regional_visit_observation",),
        "keys": "시도·시군구 이름 → area_id · 관광지명 · 월 · 내국인/외국인",
        "detail": "내·외국인 입장객과 합계를 관광지·월별 지역 방문 관측으로 저장합니다.",
        "code_ref": "app.normalization.inbound_sources.normalize_tourism_admission_run",
    },
    {
        "id": "normalize_regional_demand",
        "label": "관광 수요 지수 정규화",
        "kind": "transform",
        "sources": ("SRC_KTO_DEMAND_INTENSITY",),
        "inputs": ("raw_record", "area_source_map"),
        "outputs": ("regional_demand_observation",),
        "keys": "외부 지역 코드 → eden_area_id · baseYm → period_start",
        "detail": "체류·소비·숙박 지수를 지역과 월 기준 관측값으로 변환합니다.",
        "code_ref": "app.normalization.public_data.normalize_regional_demand_run",
    },
    {
        "id": "normalize_regional_diversity",
        "label": "관광 다양성 지수 정규화",
        "kind": "transform",
        "sources": ("SRC_KTO_DIVERSITY",),
        "inputs": ("raw_record", "area_source_map"),
        "outputs": ("regional_diversity_observation",),
        "keys": "외부 지역 코드 → eden_area_id · baseYm → period_start",
        "detail": "연령·국적 다양성 지수를 지역과 월 기준 관측값으로 변환합니다.",
        "code_ref": "app.normalization.public_data.normalize_regional_diversity_run",
    },
    {
        "id": "normalize_social",
        "label": "관광 관심도 정규화",
        "kind": "transform",
        "sources": (
            "SRC_NAVER_TREND",
            "SRC_YOUTUBE",
            "SRC_INSTAGRAM",
            "SRC_FACEBOOK",
            "SRC_REDDIT",
        ),
        "inputs": ("raw_record", "country"),
        "outputs": ("social_observation",),
        "keys": "source_id · keyword · country_id · bucket_start · bucket_grain",
        "detail": "출처 집계를 키워드·국가·기간 버킷으로 맞추되 출처별 값은 유지합니다.",
        "code_ref": "app.normalization.social.normalize_social_run",
    },
    {
        "id": "normalize_resource_demand",
        "label": "지역 자원 관심도 정규화",
        "kind": "transform",
        "sources": ("SRC_KTO_RESOURCE_DEMAND",),
        "inputs": ("raw_record", "area_source_map"),
        "outputs": ("social_observation",),
        "keys": "외부 지역 코드 → area_id · 자원명 · baseYm",
        "detail": "지역 관광 자원별 검색·관심 수요를 월 단위 관심도 관측으로 저장합니다.",
        "code_ref": "app.normalization.public_data.normalize_resource_demand_run",
    },
    {
        "id": "normalize_places",
        "label": "다국어 장소 식별자 통합",
        "kind": "transform",
        "sources": ("SRC_TOUR_KO", "SRC_TOUR_EN", "SRC_TOUR_JA", "SRC_TOUR_ZH_CN"),
        "inputs": ("raw_record", "area_source_map"),
        "outputs": ("place", "place_localization", "place_source_map"),
        "keys": "source_id + external_content_id → eden_place_id · language",
        "detail": "언어별 TourAPI 콘텐츠를 같은 EDEN 장소로 연결하고 번역은 별도 저장합니다.",
        "code_ref": "app.normalization.places.normalize_tour_catalog_run",
    },
    {
        "id": "normalize_place_relations",
        "label": "관광지 관계 연결",
        "kind": "transform",
        "sources": ("SRC_KTO_PLACE_HUB", "SRC_KTO_PLACE_RELATED"),
        "inputs": ("raw_record", "area_source_map", "place_source_map"),
        "outputs": ("place", "place_relation"),
        "keys": "외부 관광지 코드 → eden_place_id · 기준 월 · 관계 유형 · 순위",
        "detail": "관광지 허브와 연관 관광지를 EDEN 장소로 맞추고 순위를 관계 점수로 변환합니다.",
        "code_ref": "app.normalization.places",
    },
    {
        "id": "normalize_inbound",
        "label": "방한 방문자 정규화",
        "kind": "transform",
        "sources": ("SRC_KTO_INBOUND_STATS",),
        "inputs": ("raw_record", "country"),
        "outputs": ("inbound_visitor_observation",),
        "keys": "ISO 국가 코드 → country_id · 월 → period_start",
        "detail": "국가별 월 방문자 행을 합산해 방한 방문 관측으로 저장합니다.",
        "code_ref": "app.normalization.inbound.normalize_kto_inbound_run",
    },
    {
        "id": "normalize_airport_country",
        "label": "국가별 항공 실적 정규화",
        "kind": "transform",
        "sources": ("SRC_AIRPORT_COUNTRY",),
        "inputs": ("raw_record", "country"),
        "outputs": ("flight_observation",),
        "keys": "국가 코드 → country_id · 월 · month grain",
        "detail": "국가별 월간 도착 항공편과 여객 수를 항공 관측으로 저장합니다.",
        "code_ref": "app.normalization.inbound_sources.normalize_airport_country_run",
    },
    {
        "id": "normalize_airport_weekly",
        "label": "주간 항공 일정 정규화",
        "kind": "transform",
        "sources": ("SRC_AIRPORT_WEEKLY",),
        "inputs": ("raw_record", "country"),
        "outputs": ("flight_observation",),
        "keys": "국가 코드 → country_id · 주간 기준일 · 7d_schedule grain",
        "detail": "향후 7일 도착 일정을 국가별 항공편 수로 확장해 저장합니다.",
        "code_ref": "app.normalization.inbound_sources.normalize_airport_weekly_run",
    },
    {
        "id": "normalize_kexim_fx",
        "label": "수출입은행 환율 정규화",
        "kind": "transform",
        "sources": ("SRC_KEXIM_FX",),
        "inputs": ("raw_record",),
        "outputs": ("fx_observation",),
        "keys": "통화 코드 · rate_date",
        "detail": "한국수출입은행 환율을 통화와 고시 일자별 관측으로 저장합니다.",
        "code_ref": "app.normalization.inbound_sources.normalize_fx_run",
    },
    {
        "id": "normalize_bok",
        "label": "ECOS 환율·관광수지 정규화",
        "kind": "transform",
        "sources": ("SRC_BOK_ECOS",),
        "inputs": ("raw_record",),
        "outputs": ("fx_observation", "tourism_balance_observation"),
        "keys": "통화·일자 / 월별 일반여행 수입 − 지출",
        "detail": "ECOS 환율은 일별로 저장하고 일반여행 수입과 지출은 월별 관광수지로 계산합니다.",
        "code_ref": "app.normalization.inbound_sources.normalize_bok_run",
    },
    {
        "id": "normalize_visitor_forecast",
        "label": "방문 예측 정규화",
        "kind": "transform",
        "sources": ("SRC_KTO_VISITOR_FORECAST",),
        "inputs": ("raw_record", "area_source_map", "place_localization"),
        "outputs": ("forecast_input",),
        "keys": "외부 지역 코드 → area_id · 관광지명 → place_id · forecast_date",
        "detail": "관광공사 방문 예측과 혼잡도를 지역·관광지·예측일 기준 입력으로 저장합니다.",
        "code_ref": "app.normalization.forecast.normalize_visitor_forecast_run",
    },
    {
        "id": "normalize_weather",
        "label": "기상 예보 정규화",
        "kind": "transform",
        "sources": ("SRC_KMA_FORECAST",),
        "inputs": ("raw_record", "area"),
        "outputs": ("forecast_input",),
        "keys": "기상 격자 → area_id · forecast_date",
        "detail": "기상청 격자 예보를 지역과 날짜별 날씨 입력으로 저장합니다.",
        "code_ref": "app.normalization.forecast.normalize_weather_run",
    },
    {
        "id": "normalize_festival",
        "label": "축제 일정 정규화",
        "kind": "transform",
        "sources": ("SRC_FESTIVAL",),
        "inputs": ("raw_record", "area"),
        "outputs": ("forecast_input",),
        "keys": "공식 주소 → area_id · 행사 시작일~종료일",
        "detail": "행사 기간의 각 날짜에 해당 축제를 지역 예측 입력으로 연결합니다.",
        "code_ref": "app.normalization.forecast.normalize_festival_run",
    },
    {
        "id": "normalize_holiday",
        "label": "공휴일 정규화",
        "kind": "transform",
        "sources": ("SRC_HOLIDAY",),
        "inputs": ("raw_record", "area"),
        "outputs": ("forecast_input",),
        "keys": "locdate → forecast_date · 전국 지역",
        "detail": "공휴일을 날짜별로 전체 활성 지역의 예측 입력에 연결합니다.",
        "code_ref": "app.normalization.forecast.normalize_holiday_run",
    },
    {
        "id": "build_regional_product",
        "label": "지역 인사이트 구성",
        "kind": "product",
        "sources": (),
        "inputs": (
            "area",
            "place",
            "regional_visit_observation",
            "regional_demand_observation",
            "regional_diversity_observation",
        ),
        "outputs": ("read_model_snapshot",),
        "keys": "area_id · 관측 종류별 최신 period_start와 조회 기간",
        "detail": (
            "지역별 방문·수요·다양성의 최신 가용 기간을 각각 조회해 한 "
            "regional_product 스냅샷으로 게시합니다."
        ),
        "code_ref": "app.products.regional.build_regional_snapshots",
    },
    {
        "id": "build_trend_product",
        "label": "트렌드 제품 구성",
        "kind": "product",
        "sources": (),
        "inputs": ("social_observation", "area", "country"),
        "outputs": ("read_model_snapshot",),
        "keys": "source_id · keyword · country_id · area_id · bucket_start 중 최신 버전",
        "detail": "출처별 최신 관심도 관측을 유지한 채 social_signal 스냅샷으로 게시합니다.",
        "code_ref": "app.products.trends.build_trend_snapshot",
    },
    {
        "id": "build_inbound_product",
        "label": "해외 시장 지표 구성",
        "kind": "product",
        "sources": (),
        "inputs": (
            "inbound_visitor_observation",
            "flight_observation",
            "fx_observation",
            "tourism_balance_observation",
            "social_observation",
            "country",
        ),
        "outputs": ("read_model_snapshot",),
        "keys": "country_id · 국가별 방문 최신월 · 기간 창 · 최신 환율",
        "detail": (
            "방문자·항공·환율·관심도를 국가와 기간 창 기준으로 묶어 "
            "inbound_market_country로 게시합니다."
        ),
        "code_ref": "app.products.inbound.build_inbound_snapshots",
    },
    {
        "id": "build_forecast_product",
        "label": "방문 예측 제품 구성",
        "kind": "product",
        "sources": (),
        "inputs": ("area", "forecast_input"),
        "outputs": ("read_model_snapshot",),
        "keys": "area_id · 서울 달력 기준 30일 forecast_date",
        "detail": (
            "forecast_input의 방문 예측·날씨·축제·공휴일을 지역별 30일 범위 "
            "forecast_product로 묶습니다. 시군구 날씨가 없으면 상위 시도 입력을 사용합니다."
        ),
        "code_ref": "app.products.forecast.build_forecast_snapshots",
    },
    {
        "id": "build_recommendation_product",
        "label": "여행지 추천 특성 구성",
        "kind": "product",
        "sources": (),
        "inputs": (
            "place",
            "place_localization",
            "place_source_map",
            "place_relation",
            "area",
            "country",
            "regional_visit_observation",
            "regional_demand_observation",
        ),
        "outputs": ("read_model_snapshot",),
        "keys": "eden_place_id · area_id · 최신 방문/수요 관측",
        "detail": "장소·관계·지역 방문·수요로 recommendation_feature를 만듭니다.",
        "code_ref": "app.products.recommendations.build_recommendation_snapshot",
    },
)

PIPELINES = (
    {
        "id": "regional",
        "label": "지역 인사이트",
        "steps": (
            "normalize_regional_visitors",
            "normalize_tourism_admission",
            "normalize_regional_demand",
            "normalize_regional_diversity",
            "normalize_places",
            "build_regional_product",
        ),
    },
    {
        "id": "inbound",
        "label": "해외 시장",
        "steps": (
            "normalize_inbound",
            "normalize_airport_country",
            "normalize_airport_weekly",
            "normalize_kexim_fx",
            "normalize_bok",
            "normalize_social",
            "normalize_resource_demand",
            "build_inbound_product",
        ),
    },
    {
        "id": "trends",
        "label": "관광 트렌드",
        "steps": ("normalize_social", "normalize_resource_demand", "build_trend_product"),
    },
    {
        "id": "forecast",
        "label": "방문 예측",
        "steps": (
            "normalize_visitor_forecast",
            "normalize_weather",
            "normalize_festival",
            "normalize_holiday",
            "build_forecast_product",
        ),
    },
    {
        "id": "recommendation",
        "label": "여행지 추천",
        "steps": (
            "normalize_places",
            "normalize_place_relations",
            "normalize_regional_visitors",
            "normalize_regional_demand",
            "build_recommendation_product",
        ),
    },
)


def get_table(name: str):
    if name not in TABLES:
        raise ValueError("조회가 허용되지 않은 테이블입니다.")
    return Base.metadata.tables[name]


def column_names(name: str) -> list[str]:
    get_table(name)
    return TABLES[name][2].split()


def catalog() -> dict[str, Any]:
    tables = []
    for name, (label, group, _) in TABLES.items():
        table = get_table(name)
        names = column_names(name)
        columns = []
        for key in names:
            column = table.c[key]
            columns.append(
                {
                    "name": key,
                    "type": str(column.type),
                    "nullable": column.nullable,
                    "primary_key": column.primary_key,
                    "references": [
                        {"table": fk.column.table.name, "column": fk.column.name}
                        for fk in column.foreign_keys
                        if fk.column.table.name in TABLES
                        and fk.column.name in column_names(fk.column.table.name)
                    ],
                }
            )
        tables.append(
            {
                "name": name,
                "label": label,
                "group": group,
                "columns": columns,
                "primary_key": [c.name for c in table.primary_key],
            }
        )
    return {
        "tables": tables,
        "sources": [
            {
                "source_id": source.source_id,
                "owner_name": source.owner_name,
                "base_url": source.base_url,
                "docs_url": source.docs_url,
                "access_method": source.access_method,
                "storage_mode": source.storage_mode,
                "cadence_tier": source.cadence_tier,
                "graph": {
                    **SOURCE_GRAPH_DETAILS.get(source.source_id, {}),
                    "fields": [
                        dict(field)
                        for field in SOURCE_GRAPH_DETAILS.get(source.source_id, {}).get(
                            "fields", ()
                        )
                    ],
                },
            }
            for source in SOURCES
        ],
        "flow_steps": [
            {
                **step,
                "sources": list(step["sources"]),
                "inputs": list(step["inputs"]),
                "outputs": list(step["outputs"]),
            }
            for step in FLOW_STEPS
        ],
        "pipelines": [{**pipeline, "steps": list(pipeline["steps"])} for pipeline in PIPELINES],
        "basis": "orm_schema_and_code_routes",
        "database_checked": False,
    }
